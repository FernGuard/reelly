"""Designed endings: the appended brand outro segment (2026-08-03).

THE ARCHITECTURE CHANGE
Three timing patches in a row (anchor guessing, tail-silence extension,
freeze-hold under the card) tried to find room for a card INSIDE the
content, and each one made cuts worse -- a freeze on a wrong anchor
amputates the payoff it was protecting. The end card is NO LONGER an
overlay on content. After the content's last frame, every deliverable
whose plan carries an `outro` block gains a dedicated APPENDED outro
segment (~2.8s): the kit card rendered over a designed backdrop.

- Backdrop default: the content's FINAL frame, heavily blurred and
  darkened. It reads as a card background, never as a visible freeze of
  mid-action footage.
- Backdrop alternative: the brand gradient, when kit.json carries
  {"outro_style": "gradient"} (or when no frame can be extracted).
- The music bed continues across the outro (finalize pads the voice stem
  and lets the ducked bed open back up); the voice never does -- the
  outro is after content by construction.
- The segment is encoded with the SAME codec parameters as finalize's
  burn pass, so the append is a -c copy concat; a filtergraph re-encode
  is the verified fallback, never the default.

Plan accounting: `duration_s` = content + outro (what the duration QC
gate measures on the file), `content_s` = the content alone, and
`plan["outro"]` records {len_s, style}. REELLY_OUTRO=off is the escape
hatch: the planner stops adding outro blocks and finalize/judge treat
existing ones as absent.
"""
import json
import os
import subprocess

from . import config

OUTRO_S = 2.8            # the designed outro length (2.5-3.0s band)
TAIL_BREATH_S = 0.6      # natural breath the CONTENT ends on after the payoff
FADE_IN_S = 0.25         # card fade-in at outro start

# backdrop treatment: heavy blur + darken so the final frame reads as a
# card background, not as a freeze of the action
_BACKDROP_VF = ("scale=1080:1920:force_original_aspect_ratio=increase,"
                "crop=1080:1920,boxblur=luma_radius=24:luma_power=2:"
                "chroma_radius=12:chroma_power=2,"
                "eq=brightness=-0.28:saturation=0.5")

INK = (9, 12, 10)        # brand ink, same base as the endcard scrim


def enabled():
    """REELLY_OUTRO=off disables the whole architecture (planner + render
    + gates); anything else means on."""
    return os.environ.get("REELLY_OUTRO", "").lower() != "off"


def style_from_kit():
    """'gradient' when the brand kit asks for it, else 'final_frame'."""
    try:
        from . import brandkit
        if brandkit._kit_json().get("outro_style") == "gradient":
            return "gradient"
    except Exception:   # noqa: BLE001 -- a broken kit never breaks planning
        pass
    return "final_frame"


def plan_block():
    """The `outro` block a new plan carries: {len_s, style}."""
    return {"len_s": OUTRO_S, "style": style_from_kit()}


def content_len(plan):
    """The CONTENT length of a plan in seconds: duration_s minus the outro.

    Legacy plans (no outro block) return duration_s unchanged, so every
    consumer that schedules against the content end works on both shapes.
    Plans that record content_s explicitly get it back verbatim.
    """
    if plan.get("content_s") is not None:
        return float(plan["content_s"])
    dur = float(plan["duration_s"])
    ob = plan.get("outro") or {}
    try:
        return round(dur - float(ob.get("len_s", 0.0)), 2)
    except (TypeError, ValueError):
        return dur


def expected_duration(plan):
    """What the delivered file's duration should measure: full duration_s
    normally; the content alone when REELLY_OUTRO=off skipped the append."""
    ob = plan.get("outro")
    if ob and not enabled():
        return content_len(plan)
    return float(plan["duration_s"])


# ------------------------------------------------------------- construction

def _gradient_png(path, size=(1080, 1920)):
    """Brand gradient backdrop: ink at the top into an accent-tinted dark
    floor. Built with PIL so it needs no network and no Chrome."""
    from PIL import Image
    from . import brandkit
    acc = brandkit.accent().lstrip("#")
    ar, ag, ab = (int(acc[i:i + 2], 16) for i in (0, 2, 4))
    W, H = size
    img = Image.new("RGB", (1, H))
    for y in range(H):
        f = (y / max(1, H - 1)) * 0.22     # accent stays a tint, never a wash
        img.putpixel((0, y), (int(INK[0] * (1 - f) + ar * f),
                              int(INK[1] * (1 - f) + ag * f),
                              int(INK[2] * (1 - f) + ab * f)))
    img.resize((W, H)).save(path)
    return path


def _final_frame_png(video, path):
    """The video's final frame, blurred + darkened into a card backdrop.
    Returns None when the frame cannot be extracted (caller falls back to
    the gradient)."""
    from . import media
    try:
        dur = float(media.probe(video)["format"]["duration"])
    except Exception:   # noqa: BLE001
        return None
    for back in (0.1, 0.5, 1.0):    # container durations overshoot sometimes
        r = subprocess.run(
            [config.FFMPEG, "-y", "-v", "error",
             "-ss", f"{max(0.0, dur - back):.3f}", "-i", video,
             "-frames:v", "1", "-vf", _BACKDROP_VF, path],
            capture_output=True)
        if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path):
            return path
    return None


def backdrop_png(video, style, path):
    """The outro backdrop PNG per the plan's style, gradient as fallback."""
    if style != "gradient" and video and os.path.exists(video):
        p = _final_frame_png(video, path)
        if p:
            return p, "final_frame"
        print("[outro] could not extract the final frame; gradient backdrop")
    return _gradient_png(path), "gradient"


def _typeset_card(cta, path, size=(1080, 1920)):
    """Last-resort card when no kit endcard and no registered wordmark
    exist: translucent scrim + the CTA line (memory: typeset CTA only as
    fallback)."""
    from PIL import Image, ImageDraw
    from . import captions
    W, H = size
    img = Image.new("RGBA", (W, H), (*INK, 153))     # ~60% scrim
    d = ImageDraw.Draw(img)
    f = captions._font(52)
    tw = d.textlength(cta, font=f)
    d.text(((W - tw) / 2, int(H * 0.47)), cta, font=f, fill=(252, 252, 251))
    img.save(path)
    return path


def card_png(plan, product, workdir):
    """The full-frame RGBA card for the outro: the kit endcard first ($0),
    else a card rendered from the registered wordmark + the plan's ask,
    else the typeset fallback."""
    try:
        from . import brandkit, products
        key = products.ALIASES.get(product, product) if product else None
        cta = (plan.get("cta") or "").strip()
        src = str(plan.get("cta_source", "")).strip().lower()
        authored = "manual" in src or "hand-authored" in src
        # A per-cut, human-authored CTA renders dynamically (wordmark + the
        # ask), NOT the baked studio endcard: the static house card (e.g.
        # the managed account's "play what matters. example.invalid") cannot carry a per-cut ask
        # (dynamic-CTA gap, reviewer 2026-08-15). Without an authored CTA the
        # baked kit card wins exactly as before.
        if not (authored and cta):
            p = brandkit.endcard(product) if product else None
            if p and p.endswith(".png"):
                return p
        logo = products.brand_logo(key) if key else None
        if not cta and key and key in products.PRODUCTS:
            cta = products.PRODUCTS[key]["end_tag"]
        dst = os.path.join(workdir, f"{plan.get('id', 'cut')}_outro_card.png")
        if logo and os.path.exists(logo):
            brandkit._render_endcard_png(logo, cta, dst)
            return dst
        return _typeset_card(cta or "edited with Reelly", dst)
    except Exception as e:   # noqa: BLE001
        dst = os.path.join(workdir, f"{plan.get('id', 'cut')}_outro_card.png")
        print(f"[outro] card render degraded to typeset fallback ({e})")
        return _typeset_card((plan.get("cta") or "").strip() or "Reelly",
                             dst)


def build_segment(backdrop, card, len_s, dst, fps=30):
    """Encode the outro segment: static backdrop, card fading in, no audio.

    Codec parameters MIRROR finalize._burn_pass (libx264 veryfast crf 20,
    -r 30, yuv420p) so the concat with the burned content stream can run
    with -c copy.
    """
    fc = (f"[0:v]scale=1080:1920,setsar=1[b];"
          f"[1:v]scale=1080:1920,format=rgba,"
          f"fade=t=in:st=0.03:d={FADE_IN_S}:alpha=1[c];"
          f"[b][c]overlay=0:0,format=yuv420p[v]")
    subprocess.run(
        [config.FFMPEG, "-y", "-v", "error",
         "-loop", "1", "-framerate", str(fps), "-i", backdrop,
         "-loop", "1", "-framerate", str(fps), "-i", card,
         "-filter_complex", fc, "-map", "[v]", "-t", f"{len_s:.3f}",
         "-r", str(fps), "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", dst],
        check=True, capture_output=True)
    return dst


def _concat_copy(a, b, dst, workdir):
    lst = os.path.join(workdir, os.path.basename(dst) + ".concat.txt")
    with open(lst, "w") as f:
        f.write(f"file '{a}'\nfile '{b}'\n")
    r = subprocess.run([config.FFMPEG, "-y", "-v", "error", "-f", "concat",
                        "-safe", "0", "-i", lst, "-c", "copy", dst],
                       capture_output=True)
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst)


def _concat_reencode(a, b, dst):
    """Verified fallback: single-graph append (one extra encode, always
    correct)."""
    subprocess.run(
        [config.FFMPEG, "-y", "-v", "error", "-i", a, "-i", b,
         "-filter_complex",
         "[0:v]settb=AVTB,setpts=PTS-STARTPTS[v0];"
         "[1:v]settb=AVTB,setpts=PTS-STARTPTS[v1];"
         "[v0][v1]concat=n=2:v=1:a=0,format=yuv420p[v]",
         "-map", "[v]", "-r", "30", "-an",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", dst],
        check=True, capture_output=True)
    return dst


def append(content_video, backdrop_source, plan, product, workdir):
    """content video (video-only burn output) + outro segment -> one file.

    `backdrop_source` is the PRE-CAPTION raw cut: its last frame carries no
    burned text, so the darkened backdrop never shows half a caption.
    Returns the appended video's path (inside workdir).
    """
    from . import media
    ob = plan.get("outro") or {}
    len_s = float(ob.get("len_s", OUTRO_S))
    style = ob.get("style") or style_from_kit()
    bp, used_style = backdrop_png(backdrop_source or content_video, style,
                                  os.path.join(workdir, "outro_backdrop.png"))
    card = card_png(plan, product, workdir)
    seg = build_segment(bp, card, len_s,
                        os.path.join(workdir, "outro_seg.mp4"))
    dst = os.path.join(workdir, "content_outro.mp4")
    try:
        content_d = float(media.probe(content_video)["format"]["duration"])
    except Exception:   # noqa: BLE001
        content_d = None
    ok = _concat_copy(content_video, seg, dst, workdir)
    if ok and content_d is not None:
        try:
            got = float(media.probe(dst)["format"]["duration"])
            ok = abs(got - (content_d + len_s)) <= 0.35
        except Exception:   # noqa: BLE001
            ok = False
    if not ok:
        print(f"[outro] {plan.get('id', '?')}: -c copy concat rejected; "
              f"single-graph append instead")
        _concat_reencode(content_video, seg, dst)
    print(f"[outro] {plan.get('id', '?')}: outro appended "
          f"({len_s:.1f}s, {used_style} backdrop)")
    return dst


# ------------------------------------------------------------- qc records

def record_verdict(root, cut_id, entry):
    """Append one ending-check record into qc/ending_check.json (the judge
    gate and humans read this; VERDICTS.md stays human-curated)."""
    qc = os.path.join(root, "qc")
    os.makedirs(qc, exist_ok=True)
    p = os.path.join(qc, "ending_check.json")
    try:
        data = json.load(open(p))
    except (OSError, ValueError):
        data = {}
    data[cut_id] = entry
    json.dump(data, open(p, "w"), indent=1)
    return p


def load_verdicts(root):
    p = os.path.join(root, "qc", "ending_check.json")
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return {}
