"""Graphics overlays + entrance SFX on finished cuts (playbook G1-G6).

Two registers: PRO (brand lower-third) and MEME (hand-marker callouts:
chips, scribble circles, arrows, corner brackets). Placement is the zone
system (G2): cam band for floating text, content band for anchored marks
only, platform chrome margins untouched. Each element renders to a
transparent PNG via headless Chrome, ffmpeg animates its ease-out
entrance and lays a library SFX under it.

Specs live per project in edl/overlay_specs.json:
  {"cut_01": [{"template": "lowerthird", "args": ["COHESION", "one palette"],
               "t": [0.8, 4.8], "ent": "rise", "sfx": ["whoosh.mp3", -16]}, ...]}
Templates: lowerthird(kicker, text) · chip(text, x=None, y=612, size=48,
color, rot) · circle(cx, cy, w, h, label, lx, ly, color) ·
brackets(x, y, w, h, label, lx, ly, color) · raw(html).
"""
import json
import os
import subprocess

from . import config, direct

CHROME = config.CHROME or "google-chrome"
SFX_DIR = os.path.join(config.HOME, "sfx")
ACCENT = "#17cdff"      # brand accent (Blue Smoke), shared with placement.py

# Platform-safe zone for 1080x1920 (TikTok/Reels/Shorts chrome consensus).
# Floating text lives inside these bounds; only content-anchored marks may
# enter the margins, never their labels. (G2)
SAFE_TOP, SAFE_BOTTOM_Y, SAFE_RIGHT_X, SAFE_LEFT_X = 200, 1500, 940, 60
FADE = 0.22

HEAD = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@600&family=Permanent+Marker&display=swap" rel="stylesheet">
<style>* {margin:0; padding:0; box-sizing:border-box;}
body {width:WPXpx; height:HPXpx; background:transparent; overflow:hidden; position:relative;}
.marker {font-family:'Permanent Marker', cursive; color:#ffd60a;
         text-shadow:0 3px 14px rgba(0,0,0,0.9), 0 0 2px rgba(0,0,0,0.9);}
</style></head><body>BODY</body></html>"""

# The vertical feeds are the default because that is what `cut` delivers, but
# the page is not vertical-only: sizzle reels render the same templates at
# 1920x1080, so the frame size is a parameter rather than a constant baked
# into the stylesheet.
FRAME = (1080, 1920)


def lowerthird(kicker, text, x=60, y=610, size=34, color="#FCFCFB",
               scrim=0.9, w=None, **_):
    """Brand lower-third. Position, size, colour and scrim are decided per
    frame by `placement`, not fixed here: a chip pinned to one coordinate lands
    on a face in one cut and in empty sky in the next."""
    a = max(0, min(255, int(round(float(scrim) * 255))))
    width = f"max-width:{int(w)}px;" if w else ""
    return f"""
  <div style="position:absolute; left:{int(x)}px; top:{int(y)}px; display:flex;
              align-items:center; {width}
              background:#090c0a{a:02X}; border:1px solid #3c4440;
              border-left:6px solid {ACCENT};
              border-radius:10px; padding:{int(size*0.62)}px {int(size)}px; gap:{int(size*0.5)}px;">
    <div style="font-family:'IBM Plex Sans',sans-serif; font-weight:600;
                font-size:{int(size*0.86)}px; color:{ACCENT}; letter-spacing:0.08em;
                white-space:nowrap;">{kicker}</div>
    <div style="font-family:'IBM Plex Sans',sans-serif; font-weight:600;
                font-size:{int(size)}px; color:{color};">{text}</div>
  </div>"""


def chip(text, x=None, y=612, size=48, color="#ffd60a", rot=-2, w=None,
         stroke=5, scrim=0.0, **_):
    """Hand-marker callout. `w` wraps the line deliberately instead of letting
    it run the full frame width; `stroke` and `scrim` come from how bright and
    busy the backdrop measured."""
    pos = (f"left:{int(x)}px;" if x is not None
           else f"left:50%; transform:translateX(-50%) rotate({rot}deg);")
    tf = "" if x is None else f"transform:rotate({rot}deg);"
    width = f"width:{int(w)}px;" if w else ""
    sh = (f"text-shadow:0 3px 14px rgba(0,0,0,0.9)," +
          ",".join(f"{dx}px {dy}px 0 rgba(0,0,0,0.92)"
                   for dx, dy in ((-stroke, 0), (stroke, 0), (0, -stroke), (0, stroke)))
          + ";") if stroke else ""
    bg = (f"background:rgba(9,12,10,{scrim}); border-radius:12px; "
          f"padding:{int(size*0.22)}px {int(size*0.34)}px;") if scrim else ""
    return f"""
  <div class="marker" style="position:absolute; {pos} top:{int(y)}px; {width}
       font-size:{int(size)}px; color:{color}; line-height:1.12; {sh} {bg} {tf}">{text}</div>"""


def badge(logo, text=None, x=60, y=1200, h=110, scrim=0.55, color="#FCFCFB",
          size=34, w=None, stack=False, **_):
    """Brand badge built from the real logo asset, not typeset lettering.

    A wordmark someone designed carries recognition that Plex Sans set to the
    same words does not. The PNG is inlined as a data URI because the renderer
    screenshots a file:// page and will not fetch siblings off disk.
    """
    import base64
    import mimetypes
    mime = mimetypes.guess_type(logo)[0] or "image/png"
    b64 = base64.b64encode(open(logo, "rb").read()).decode()
    a = max(0, min(255, int(round(float(scrim) * 255))))
    cap = (f'<div style="font-family:\'IBM Plex Sans\',sans-serif; font-weight:600;'
           f' font-size:{int(size)}px; color:{color}; line-height:1.2;">{text}</div>'
           ) if text else ""
    # x=None centres the card in CSS. Estimating its width in Python and then
    # centring on that estimate is off by however wrong the estimate was; the
    # renderer knows the true width, so let it do the arithmetic.
    pos = ("left:50%; transform:translateX(-50%);" if x is None
           else f"left:{int(x)}px;")
    # A logo and a sentence side by side come to ~1490px, which does not fit a
    # 1080px frame: centred, it gets clipped at BOTH ends. A vertical format
    # gets a stacked card. `w` is a hard ceiling, never a suggestion.
    col = "column" if stack else "row"
    width = f"width:{int(w)}px;" if w else ""
    panel = (f"background:#090c0a{a:02X}; border-radius:{int(h*0.18)}px;"
             if a else "")
    return f"""
  <div style="position:absolute; {pos} top:{int(y)}px; display:flex;
              flex-direction:{col}; align-items:center; justify-content:center;
              text-align:center; gap:{int(h*0.26)}px; {width} max-width:{int(w or 960)}px;
              {panel}
              padding:{int(h*0.24)}px {int(h*0.30)}px;">
    <img src="data:{mime};base64,{b64}"
         style="height:{int(h)}px; max-width:100%; object-fit:contain; display:block;"/>
    {cap}
  </div>"""


def circle(cx, cy, w, h, label, lx, ly, color="#ff3b30"):
    return f"""
  <svg style="position:absolute; left:{cx - w // 2}px; top:{cy - h // 2}px;" width="{w}" height="{h}"
       viewBox="0 0 {w} {h}">
    <path d="M{w//2},{h*0.08:.0f} C{w*0.78:.0f},{h*0.02:.0f} {w*0.97:.0f},{h*0.24:.0f} {w*0.95:.0f},{h*0.5:.0f}
             C{w*0.93:.0f},{h*0.84:.0f} {w*0.7:.0f},{h*0.97:.0f} {w*0.46:.0f},{h*0.94:.0f}
             C{w*0.2:.0f},{h*0.91:.0f} {w*0.04:.0f},{h*0.72:.0f} {w*0.07:.0f},{h*0.46:.0f}
             C{w*0.1:.0f},{h*0.2:.0f} {w*0.28:.0f},{h*0.1:.0f} {w*0.54:.0f},{h*0.07:.0f}"
          fill="none" stroke="{color}" stroke-width="8" stroke-linecap="round"
          transform="rotate(-2 {w//2} {h//2})"/>
  </svg>
  <div class="marker" style="position:absolute; left:{lx}px; top:{ly}px; font-size:42px;
       transform:rotate(-3deg);">{label}</div>"""


def _pointer_label_xy(x, y, w, h):
    """A label position just below the pointed-at box, clamped into the frame."""
    lx = max(40, min(x, 1080 - 360))
    ly = y + h + 14
    if ly > 1720:                       # no room below -> sit above instead
        ly = max(40, y - 60)
    return int(lx), int(ly)


def plan_pointers(video, words_tl, project="", frame_at=None,
                  max_pointers=3, min_gap_s=10.0):
    """Transcript-driven pointer overlays (reviewer 2026-08-13): for a few
    narration phrases in a voiced/tutorial cut, locate the thing on screen and
    point at it -- a scribble RING (circle) for a compact thing, CORNER BRACKETS
    for a region -- labelled with the phrase, timed to the narration. Vision only
    emits a pointer when there IS a clear referent, and it is rate-limited
    (max_pointers, min_gap_s) so it stays tasteful, not spammy. Returns overlay
    events. frame_at(t) must return a PIL frame; without it or a transcript,
    returns []."""
    from . import design, speech
    if not words_tl or frame_at is None:
        return []
    events, last_t = [], -1e9
    for cs, ce, wlist in speech.group_cue_words(words_tl):
        if len(events) >= max_pointers:
            break
        if cs - last_t < min_gap_s:
            continue
        phrase = " ".join(w.get("t", "") for w in wlist).strip()
        if len(phrase.split()) < 2:
            continue
        loc = design.locate_referent(frame_at((cs + ce) / 2.0), phrase, project)
        if not loc:
            continue
        x, y, w, h = (int(v) for v in loc["box"])
        label = phrase if len(phrase) <= 24 else phrase[:22].rstrip() + "…"
        lx, ly = _pointer_label_xy(x, y, w, h)
        if loc["shape"] == "brackets":
            args = [x, y, w, h, label, lx, ly]
        else:
            args = [x + w // 2, y + h // 2, w + 44, h + 32, label, lx, ly]
        events.append({"template": loc["shape"], "args": args,
                       "t": [round(cs, 2), round(ce + 0.8, 2)],
                       "why": f"pointer: narrator names {phrase!r}"})
        last_t = cs
    return events


def brackets(x, y, w, h, label, lx, ly, color="#ffd60a"):
    L = 64
    sw = 9

    def corner(cx, cy, dx, dy):
        return (f'<path d="M{cx},{cy+dy*L} L{cx},{cy} L{cx+dx*L},{cy}" fill="none" '
                f'stroke="{color}" stroke-width="{sw}" stroke-linecap="round"/>')

    return f"""
  <svg style="position:absolute; left:{x}px; top:{y}px;" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
    {corner(6,6,1,1)}{corner(w-6,6,-1,1)}{corner(6,h-6,1,-1)}{corner(w-6,h-6,-1,-1)}
  </svg>
  <div class="marker" style="position:absolute; left:{lx}px; top:{ly}px; font-size:44px;
       transform:rotate(-2deg); color:{color};">{label}</div>"""


def namecard(text, sub=None, x=86, y=None, size=52, color="#FCFCFB",
             rule=True, **_):
    """Sizzle name label: an accent rule, the thing's name, an optional credit.

    Distinct from `lowerthird` on purpose. A lower-third is a chip that has to
    survive landing anywhere on a busy vertical frame, so it carries a box and
    a scrim. A sizzle can name its shot in a calm region of a composed 16:9
    frame, with a shadow providing contrast instead of a box.
    """
    top = f"top:{int(y)}px;" if y is not None else "bottom:88px;"
    shadow = "text-shadow:0 3px 22px rgba(0,0,0,.85), 0 1px 3px rgba(0,0,0,.9);"
    bar = (f'<div style="width:{int(size * 0.72)}px; height:5px; '
           f'background:{ACCENT}; border-radius:3px; '
           f'margin-bottom:{int(size * 0.34)}px;"></div>') if rule else ""
    credit = (f'<div style="font-family:\'IBM Plex Sans\',sans-serif; '
              f'font-weight:600; font-size:{int(size * 0.5)}px; '
              f'color:#FCFCFB; opacity:.78; margin-top:{int(size * 0.14)}px; '
              f'{shadow}">{sub}</div>') if sub else ""
    return f"""
  <div style="position:absolute; left:{int(x)}px; {top}">{bar}
    <div style="font-family:'IBM Plex Sans',sans-serif; font-weight:600;
                font-size:{int(size)}px; color:{color};
                letter-spacing:-0.01em; line-height:1.04; {shadow}">{text}</div>
    {credit}
  </div>"""


def brandcard(line, logo=None, w=1080, h=1920, size=64, sub=None,
              logo_frac=0.34, only=None, **_):
    """Full-frame brand moment: the registered wordmark over one line.

    Used for a sizzle's title beat and for its closing card. The wordmark is
    the real logo file, never typeset -- a designed mark carries recognition
    that re-set words do not, and brand marks are compositor assets rather
    than anything a model draws.
    """
    import base64
    # `only` renders ONE element and hides the other with visibility:hidden
    # rather than removing it. The layout is a centred flex column, so
    # dropping an element would re-centre the survivor and the two passes
    # would not line up -- hiding keeps every box exactly where it is, which
    # is what lets the mark and the line be animated on separate timings.
    hide_mark = " visibility:hidden;" if only == "line" else ""
    hide_line = " visibility:hidden;" if only == "mark" else ""
    mark = ""
    if logo and os.path.exists(logo):
        uri = ("data:image/png;base64,"
               + base64.b64encode(open(logo, "rb").read()).decode())
        mark = (f'<img src="{uri}" style="width:{int(w * logo_frac)}px; '
                f'height:auto; display:block; margin:0 auto '
                f'{int(size * 0.62)}px;{hide_mark}">')
    shadow = "text-shadow:0 4px 30px rgba(0,0,0,.9), 0 1px 4px rgba(0,0,0,.95);"
    credit = (f'<div style="font-family:\'IBM Plex Sans\',sans-serif; '
              f'font-weight:600; font-size:{int(size * 0.42)}px; color:{ACCENT};'
              f' letter-spacing:.14em; text-transform:uppercase; '
              f'margin-top:{int(size * 0.42)}px; {shadow}">{sub}</div>')\
        if sub else ""
    return f"""
  <div style="position:absolute; inset:0; display:flex; flex-direction:column;
              align-items:center; justify-content:center;
              padding:0 {int(w * 0.09)}px; text-align:center;">{mark}
    <div style="font-family:'IBM Plex Sans',sans-serif; font-weight:600;
                font-size:{int(size)}px; color:#FCFCFB; line-height:1.12;
                letter-spacing:-0.015em; {shadow}{hide_line}">{line}</div>
    <div style="{hide_line}">{credit}</div>
  </div>"""


def raw(html):
    return html


TEMPLATES = {"lowerthird": lowerthird, "badge": badge, "chip": chip,
             "circle": circle, "brackets": brackets, "namecard": namecard,
             "brandcard": brandcard, "raw": raw}

# "kitcard" events are NOT in TEMPLATES: they carry a ready-made full-frame
# PNG from the brand kit (args[0]) and skip the Chrome render entirely.
KITCARD = "kitcard"


def kit_endcard(product):
    """The kit's pre-built endcard for this product's studio, or None.

    REELLY_ENDCARD=legacy restores the old Gemini plan_endcard + badge-HTML +
    Chrome path even when a kit asset exists (escape hatch for A/Bs and for
    footage the static card reads wrong on)."""
    if os.environ.get("REELLY_ENDCARD", "").lower() == "legacy":
        return None
    if not product:
        return None
    try:
        from . import brandkit
        p = brandkit.endcard(product)
    except Exception:   # a broken kit must never break the overlay pass
        return None
    return p if p and p.endswith(".png") else None


def _render_png(workdir, name, body, size=None):
    w, h = size or FRAME
    os.makedirs(workdir, exist_ok=True)
    hp = os.path.join(workdir, name + ".html")
    open(hp, "w").write(HEAD.replace("WPX", str(w)).replace("HPX", str(h))
                        .replace("BODY", body))
    dst = os.path.join(workdir, name + ".png")
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
                    "--default-background-color=00000000", f"--window-size={w},{h}",
                    "--virtual-time-budget=6000", f"--screenshot={dst}", f"file://{hp}"],
                   check=True, capture_output=True)
    os.remove(hp)
    return dst


def _src_duration(src):
    """Container duration of the file being composited, or None."""
    from . import media
    try:
        return float(media.probe(src)["format"]["duration"])
    except Exception:   # noqa: BLE001 — no duration means no clamp, not a crash
        return None


def clamp_events(events, dur):
    """Overlay events must NEVER stretch a render past its source.

    Duration-gate failures were exactly
    this: overlay_specs carried card/chip windows computed against a previous
    plan generation (stale spec reuse), ffmpeg's looped-PNG + delayed-SFX
    graph then ran PAST the source video's end to cover them, and the _gfx
    file came out 1.7-4s longer than the plan. An event starting past the
    clip end is dropped loudly; one spanning the end is clipped to it.
    """
    if not dur:
        return list(events)
    kept = []
    for ev in events:
        t0, t1 = float(ev["t"][0]), float(ev["t"][1])
        if t0 >= dur - 0.1:
            print(f"[overlays] DROPPED {ev.get('template', '?')} event "
                  f"[{t0:.2f}-{t1:.2f}]s: starts past the clip end "
                  f"({dur:.2f}s). The spec is stale -- re-run autoplan.")
            continue
        if t1 > dur:
            ev = {**ev, "t": [round(t0, 2), round(dur, 2)]}
        kept.append(ev)
    return kept


def _composite(src, out, events, workdir, music=None, speech=False):
    """Composite overlays + SFX over `src`, preserving its native audio.

    music (a bed .mp3, motion/H3 Max only): mixed UNDER the source's native
    foley/ambience so a cut ships foley + SFX + background music. speech=True
    (the clip carries dialogue) ducks the bed under the native audio instead so
    the voice stays clear. Footage-gfx callers pass no music and are unchanged.
    """
    dur = _src_duration(src)
    events = clamp_events(events, dur)
    if not events:
        import shutil
        shutil.copyfile(src, out)   # nothing legal to draw; ship the source
        return
    pngs = []
    for i, ev in enumerate(events):
        if ev["template"] == KITCARD:
            # ready-made full-frame kit PNG: no HTML, no Chrome
            pngs.append(ev["args"][0])
            continue
        body = TEMPLATES[ev["template"]](*ev.get("args", []), **ev.get("kwargs", {}))
        pngs.append(_render_png(workdir, f"{os.path.basename(out)}_{i}", body))

    sfx_evs = [ev for ev in events if ev.get("sfx")]
    inputs = ["-i", src]
    for p in pngs:
        inputs += ["-loop", "1", "-i", p]
    for ev in sfx_evs:
        inputs += ["-i", os.path.join(SFX_DIR, ev["sfx"][0])]
    if music:
        inputs += ["-i", music]

    n = len(events)
    f, last = [], "0:v"

    # NO FREEZE-HOLD, NO CARD-OVER-CONTENT (designed endings, 2026-08-03).
    # Three timing patches tried to make room for a card INSIDE the content
    # (anchor guessing, tail-silence extension, freeze-hold) and each made
    # cuts worse -- a freeze on a wrong anchor amputates the payoff. New
    # plans carry an APPENDED outro segment (outro.py, built in finalize):
    # autoplan emits no endcard events for them, so nothing here ever
    # covers the payoff. Legacy plans without an outro still composite
    # their payoff-anchored card the old way.
    for i, ev in enumerate(events):
        t0, t1 = ev["t"]
        # The closing card must be at full strength on the last frame. Fading it
        # out means the final thing a viewer sees is the ask half gone, which is
        # the one moment we are asking for a click. fade_out:false holds it.
        # Mirror rule for hooks: a hook that starts at t=0 must be readable on
        # FRAME 1 (it is also the cover frame), so fade_in:false skips the ramp.
        chain = f"[{i+1}:v]format=rgba"
        if ev.get("fade_in", True):
            chain += f",fade=t=in:st={t0}:d={FADE}:alpha=1"
        if ev.get("fade_out", True):
            chain += f",fade=t=out:st={t1-FADE}:d={FADE}:alpha=1"
        f.append(chain + f",setpts=PTS-STARTPTS[o{i}]")
        ent = ev.get("ent", "pop")
        if ent == "rise":      # ease-out rise, 240ms (brand motion rules)
            y = f"if(lt(t,{t0}+0.24), 40*pow(1-(t-{t0})/0.24\\,2), 0)"
        elif ent == "pop":     # marker-slap settle from above
            y = f"if(lt(t,{t0}+0.16), -18*pow(1-(t-{t0})/0.16\\,2), 0)"
        else:
            y = "0"
        nxt = f"v{i}" if i < n - 1 else "vout"
        f.append(f"[{last}][o{i}]overlay=x=0:y='{y}':enable='between(t,{t0},{t1})'[{nxt}]")
        last = nxt

    amix = []
    for i, ev in enumerate(sfx_evs):
        ms = max(0, int((ev["t"][0] - 0.05) * 1000))
        f.append(f"[{1+n+i}:a]volume={ev['sfx'][1]}dB,adelay={ms}|{ms}[a{i}]")
        amix.append(f"[a{i}]")
    # Background music bed (motion/H3 Max): the native audio ([0:a]) is the
    # scene's foley/ambience. By default the bed rides UNDER it at a steady
    # level; when the clip carries dialogue (speech) the bed is sidechain-ducked
    # by the native audio so the voice stays clear. The final enforce_loudness
    # below normalises the summed result to the judge's window.
    src_a = "[0:a]"
    if music:
        midx = 1 + n + len(sfx_evs)
        if speech:
            f.append("[0:a]asplit=2[a0main][a0key]")
            f.append(f"[{midx}:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,volume=0.4[mv]")
            f.append("[mv][a0key]sidechaincompress=threshold=0.02:ratio=10:"
                     "attack=15:release=400[mbed]")
            src_a = "[a0main]"
        else:
            f.append(f"[{midx}:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,volume=0.35[mbed]")
        amix.append("[mbed]")
    if amix:
        # 0.95 (-0.45 dBFS) leaves no room for the AAC overshoot this file is
        # about to pick up; the delivered ceiling is -1.5 dBFS.
        f.append(f"{src_a}{''.join(amix)}amix=inputs={1+len(amix)}:"
                 f"normalize=0,alimiter=limit=0.8414[aout]")
        amap = "[aout]"
    else:
        amap = "0:a"

    # veryfast/crf 20 matches the underlying deliverable (finalize._burn_pass
    # encodes at veryfast/crf 20): encoding the gfx pass at medium/crf 18 was a
    # quality inversion — it spent ~3s/cut polishing a stream whose source was
    # already veryfast/crf 20-21, which no viewer can get back.
    # -t pins the output to the SOURCE length: the looped-PNG inputs and any
    # SFX tail must never extend a deliverable past its plan (duration gate).
    tlim = ["-t", f"{dur:.3f}"] if dur else []
    subprocess.run([config.FFMPEG, "-y", "-v", "error"] + inputs +
                   ["-filter_complex", ";".join(f), "-map", "[vout]", "-map", amap,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", *tlim, out],
                   check=True, capture_output=True)
    # This pass re-encoded the audio, so it can overshoot exactly like the mux
    # in finalize did. Verify the file that was actually written.
    from . import audio_post
    audio_post.enforce_true_peak(out)
    # SFX were mixed in without re-normalising; keep the file inside the
    # loudness window too, then re-check the peak the correction may move.
    audio_post.enforce_loudness(out)


def _composite_variant(gfx_video, src, out, events, workdir):
    """Gfx copy of a sibling mix without re-encoding the video.

    Finalize muxes every mix variant of a cut from ONE burned video with
    -c:v copy, so their video streams are bit-identical. The overlays were
    already composited and encoded once (into gfx_video); this pass copies
    that video stream and runs only this variant's audio through the same
    SFX chain _composite uses. One gfx encode per cut instead of one per mix.
    """
    dur = _src_duration(src)
    sfx_evs = [ev for ev in clamp_events(events, dur) if ev.get("sfx")]
    if not sfx_evs:
        import shutil
        if os.path.abspath(gfx_video) == os.path.abspath(src):
            shutil.copyfile(gfx_video, out)
        else:
            subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", gfx_video,
                            "-i", src, "-map", "0:v", "-map", "1:a",
                            "-c", "copy", "-shortest", out],
                           check=True, capture_output=True)
        return
    inputs = ["-i", gfx_video, "-i", src]
    for ev in sfx_evs:
        inputs += ["-i", os.path.join(SFX_DIR, ev["sfx"][0])]
    f, amix = [], []
    for i, ev in enumerate(sfx_evs):
        ms = max(0, int((ev["t"][0] - 0.05) * 1000))
        f.append(f"[{2 + i}:a]volume={ev['sfx'][1]}dB,adelay={ms}|{ms}[a{i}]")
        amix.append(f"[a{i}]")
    f.append(f"[1:a]{''.join(amix)}amix=inputs={len(sfx_evs) + 1}:normalize=0,"
             f"alimiter=limit=0.8414[aout]")
    tlim = ["-t", f"{dur:.3f}"] if dur else []
    subprocess.run([config.FFMPEG, "-y", "-v", "error"] + inputs +
                   ["-filter_complex", ";".join(f), "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-shortest", *tlim, out], check=True, capture_output=True)
    from . import audio_post
    audio_post.enforce_true_peak(out)
    audio_post.enforce_loudness(out)


def _slots(p, end_tag=2.4, gap=0.3):
    """Two time windows the hook does not own: one early, one late.

    Windows are computed against the CONTENT length: on outro-carrying
    plans duration_s includes the appended outro segment, and no mark may
    ever be scheduled onto it."""
    from . import outro as outro_mod
    dur = outro_mod.content_len(p)
    a = float(p["hook"].get("show_s", 3.6)) + gap
    b = dur - end_tag - 0.2
    if b - a < 3.4:
        return None, None
    pro = (round(a, 2), round(min(a + 3.6, b), 2))
    return pro, ((round(b - 2.8, 2), round(b, 2)) if b - pro[1] >= 2.8 else None)


ENDCARD_S = 3.4          # how long the closing card wants to own the frame
ENDCARD_BREATH_S = 0.25  # small breath between payoff-out and card-in
MIN_ENDCARD_S = 1.8      # a card shorter than this cannot be read
# the card needs at least this much clip AFTER the payoff (breath included in
# the planner's arithmetic separately); when a plan is tighter than this the
# PLANNER extends the duration accounting (direct._breathe_tail) -- the card
# is never pulled back onto the payoff and the render is never stretched
MIN_CARD_AFTER_PAYOFF_S = 1.5
MIN_USABLE_CARD_S = 0.7  # below this a card is a glitch-flash; ship the cut clean instead
ENDCARD_SCRIM_ALPHA = 0.60   # translucent dark pass under every closing card


def payoff_end_local(plan):
    """Where the delivery finishes in clip time, or None when unknown.

    Prefers the explicit delivery_end_s the planner records, then the payoff's
    local_e; legacy plans with only local_t get the payoff segment's length
    added, because local_t is where the payoff STARTS.
    """
    de = plan.get("delivery_end_s")
    if de is not None:
        return float(de)
    p = plan.get("payoff") or {}
    if p.get("local_e") is not None:
        return float(p["local_e"])
    if p.get("local_t") is not None:
        seg = (plan.get("segments") or [[0, 0]])[-1]
        speed = seg[2] if len(seg) > 2 else 1.0
        return float(p["local_t"]) + (seg[1] - seg[0]) / speed
    return None


def payoff_anchor_local(plan):
    """Where the payoff actually ends in clip time: the MEASURED anchor when
    the plan carries a resolved one (beats.payoff_anchor, stored at plan time
    or refreshed by autoplan), else the plan's own estimated numbers."""
    anc = plan.get("payoff_anchor") or {}
    if anc.get("resolved") and anc.get("t_anchor") is not None:
        return float(anc["t_anchor"])
    return payoff_end_local(plan)


def endcard_window(plan):
    """(card_in, card_out) anchored to payoff-END, not clip-end (gap 13).

    LEGACY PLANS ONLY since the designed-endings change (2026-08-03): plans
    carrying an `outro` block never composite a card over content (the card
    lives in the appended outro segment) and autoplan skips this entirely
    for them. Kept for old plan files and as a diagnostic.

    the reviewer's rule (2026-08-02): the card appears only AFTER the payoff has
    fully played -- payoff_end plus a small breath -- and holds to the cut's
    end. It is NEVER pulled back onto the payoff: the old MIN_ENDCARD_S
    clamp did exactly that on tight plans ("we are obscuring the payoff...")
    and is gone for payoff-carrying plans. Since 2026-08-03 the payoff end
    is the MEASURED anchor (beats.payoff_anchor: last payoff word / visual
    settle on the actual footage) whenever one resolved; plan estimates are
    the fallback, which is exactly what had cards starting over payoffs.
    The planner's tail extension (direct._breathe_tail) consumes the same
    anchor, so the card room it planned is the room the render finds; when
    a legacy plan left none, the card takes whatever remains after the
    payoff (the composite clamps it to the real clip end, extending
    nothing), never the delivery's frames.
    """
    dur = float(plan["duration_s"])
    pe = payoff_anchor_local(plan)
    if pe is None:
        # no payoff data: legacy clip-end window
        return (round(max(0.0, dur - ENDCARD_S), 2), round(dur, 2))
    t0 = max(dur - ENDCARD_S, pe + ENDCARD_BREATH_S)
    t0 = max(0.0, min(t0, dur))
    if dur - t0 < MIN_USABLE_CARD_S - 1e-6:
        # A card needs enough frames to read as a card. Legacy plans that
        # let the payoff run to the final frame leave no legal room -- a
        # sub-MIN_USABLE_CARD_S flash fails the endcard_timing gate and
        # reads as a glitch. Ship the cut clean instead (the reviewer's rule:
        # the payoff is never covered, the render is never stretched); a
        # replan reserves proper card room via direct._breathe_tail.
        print(f"[overlays] {plan.get('id', '?')}: only {dur - t0:.2f}s after "
              f"the measured payoff anchor ({pe:.2f}s) -- NO ROOM for a card; "
              f"skipping the endcard on this cut (replan to reserve tail room)")
        return None
    if dur - t0 < MIN_CARD_AFTER_PAYOFF_S - 1e-6:
        print(f"[overlays] {plan.get('id', '?')}: only {dur - t0:.2f}s of card "
              f"room after the measured payoff anchor ({pe:.2f}s); the card "
              f"takes what exists -- the render is never stretched and the "
              f"payoff is never covered")
    return (round(t0, 2), round(dur, 2))


def clear_for_endcard(win, dur, has_cta, min_len=1.6, card_t0=None):
    """Trim a mid-clip window so it is gone before the closing card arrives.

    The end card is the one moment we are asking for a click. A marker callout
    still on screen when it lands splits the attention we just spent the whole
    cut earning (G1). `card_t0` is the payoff-aware card start from
    endcard_window; the pre-gap-13 clip-end anchor is only a fallback.
    """
    if not win or not has_cta or dur <= 6:
        return win
    limit = (card_t0 if card_t0 is not None else dur - ENDCARD_S) - 0.3
    win = (win[0], min(win[1], limit))
    return win if win[1] - win[0] >= min_len else None


def card_scrim_event(root, card_w):
    """Full-frame translucent scrim event scheduled UNDER a legacy closing
    card (badge/lowerthird). Kit endcards carry their scrim baked into the
    RGBA PNG; the legacy paths get this companion so a logo can NEVER render
    without the darkening layer beneath it -- reviewer caught exactly that on
    cuts where the card applied but the burn scrim did not line up.
    One layering, owned by the overlay pass."""
    from . import captions
    d = os.path.join(root, "deliverables", "_gfx")
    os.makedirs(d, exist_ok=True)
    p = captions.scrim_png(os.path.join(d, "endcard_scrim.png"),
                           alpha=ENDCARD_SCRIM_ALPHA)
    return {"template": KITCARD, "args": [p],
            "t": [card_w[0], card_w[1]], "ent": "none", "fade_out": False,
            "why": "translucent scrim beneath the closing card (unified "
                   "layering: no logo without the dark pass under it)"}


def _meme_text(p):
    """Last fragment of the cut's own lore-tease caption."""
    import re
    frags = [f.strip() for f in re.split(r"[.!?]", p.get("caption") or "") if f.strip()]
    if not frags:
        return None
    words = frags[-1].split()
    return " ".join(words[:6]).lower() if 1 <= len(words) <= 8 else None


def autoplan(project, kicker=None, label=None, meme=True, src_dir=None,
             logo=None, cta=None, product=None, cut_id=None, tag=None):
    """Propose overlay events AND decide their placement from the picture.

    For each mark this samples the frame it will actually sit on and asks
    `placement` where the calm space is, how big the text should be, and what
    colour survives that backdrop. The decision and its evidence (calm score,
    backdrop luminance and detail) are written into the spec so a human can
    read why a mark is where it is, and so a bad call is arguable rather than
    mysterious.
    """
    from . import direct, placement, products
    # One logo source of truth: an explicit path wins, otherwise the product's
    # registered wordmark, so the CLI and `cut` never disagree.
    if not logo and product:
        logo = products.brand_logo(product)
    if not label and product:
        label = products.PRODUCTS[product]["name"]
    root = direct.resolve_project(project)
    # A tagged run (cut/finalize --tag) plans against ITS plan set and ITS
    # final dir; before this, autoplan always read the untagged files, so
    # tagged A/B runs got graphics planned against the wrong plans. The spec
    # file itself stays untagged: judge and apply read that one path.
    sfx = f"_{tag}" if tag else ""
    plans = json.load(open(os.path.join(root, "edl", f"cut_plans{sfx}.json")))
    src_dir = src_dir or os.path.join(root, "deliverables", f"final{sfx}")
    name = os.path.basename(root)
    label = label or name.replace("as-", "").replace("ascap-", "").replace("-", " ").title()
    # Planning samples frames per mark, so re-planning a whole project to change
    # one cut is the slow half of an iteration loop. cut_id keeps the existing
    # specs and replaces only the one asked for.
    spec_path = os.path.join(root, "edl", "overlay_specs.json")
    specs = {}
    if cut_id and os.path.exists(spec_path):
        specs = json.load(open(spec_path))
    # measured payoff anchors: plans written since 2026-08-03 carry one from
    # plan time; legacy plans get theirs measured here, against the same
    # analysis artifacts, so the card window below is footage-true either way
    from . import beats
    an_dir = os.path.join(root, "analysis")
    try:
        src_video = direct._source_video(root)
    except (SystemExit, OSError):
        src_video = None
    for p in plans:
        if cut_id and p["id"] != cut_id:
            continue
        vid = os.path.join(src_dir, f"{p['id']}.mp4")
        if not os.path.exists(vid):
            continue
        if not p.get("payoff_anchor"):
            try:
                p["payoff_anchor"] = beats.payoff_anchor(p, an_dir,
                                                         video=src_video)
            except Exception as e:  # noqa: BLE001 -- degrade, never crash
                print(f"[beats] {p['id']}: anchor probe failed ({e}); "
                      f"plan-based card timing")
        pro_w, meme_w = _slots(p)
        events, placed = [], []
        # Designed endings: an outro-carrying plan gets NO closing-card
        # events at all -- the card lives in the appended outro segment
        # (outro.py), after the content, by construction. endcard_window /
        # the payoff anchor stay as diagnostics for legacy plans only.
        from . import outro as outro_mod
        has_outro = bool(p.get("outro")) and outro_mod.enabled()
        cdur = outro_mod.content_len(p)
        # MEME register mid-clip: the fun layer, marker text over a calm patch.
        ask_text = (p.get("cta") or cta or "").strip()
        card_w = None if has_outro else endcard_window(p)
        if not has_outro:
            meme_w = clear_for_endcard(meme_w, cdur, bool(ask_text),
                                       card_t0=card_w[0] if card_w else None)
        mt = _meme_text(p) if meme else None
        if mt and meme_w:
            # SINGLE LAYOUT AUTHORITY (layout.py): the meme callout takes the
            # CENTER slot, STAGGERED after the hook so the two never share it,
            # and reads the SAME positions finalize burns -- no frame sampling
            # (was placement.plan_mark: slow + used the stale occupied_bands,
            # the collision source). Deterministic and fast.
            from . import layout as _layout
            tmpl = (p.get("composition") or {}).get("cam")
            mslot = _layout.plan_text(
                p, tmpl, meme_windows=[list(meme_w)]).get("meme")
            if mslot:
                win = mslot["windows"][0]
                my = mslot["y"]
                mw = _layout.SAFE_RIGHT - _layout.SAFE_LEFT
                events.append({
                    "template": "chip",
                    "args": [mt],
                    "kwargs": {"x": None, "y": my, "size": 52,
                               "color": "#ffd60a", "w": mw, "stroke": 8,
                               "scrim": 0.0},
                    "t": list(win), "ent": "pop", "sfx": ["pop.mp3", -14],
                    "role": "tease",
                    "why": (f"layout center slot y={my}, staggered to "
                            f"{win} after the hook (deterministic, no sampling)")})
                placed.append((_layout.SAFE_LEFT, my, mw, _layout.LINE_H))

        # ONE closing card: the logo and the ask together, in the lower third.
        # Brand attribution and the call to action are the same job; splitting
        # them into two blocks makes them compete at the moment we want a click.
        ask = (p.get("cta") or cta or "").strip()
        if ask and cdur > 6 and card_w is not None and not has_outro:
            kit_png = kit_endcard(product)
            if kit_png:
                # Pre-built kit endcard: full-frame 1080x1920 PNG (scrim +
                # wordmark + CTA baked at kit-build time), composited over the
                # payoff-anchored endcard window with the standard short
                # opacity fade-in. Zero Gemini placement calls, zero Chrome
                # renders. REELLY_ENDCARD=legacy or a kit without this studio
                # falls through to the old plan_endcard + badge path below.
                events.append({
                    "template": KITCARD,
                    "args": [kit_png],
                    "t": [card_w[0], card_w[1]],
                    "ent": "none", "sfx": ["ding.mp3", -18], "fade_out": False,
                    "role": "endcard", "anchor": p.get("payoff_anchor"),
                    "why": ("kit endcard (pre-built, $0): full-frame brand "
                            "card from ~/.reelly/brandkit, anchored to the "
                            "measured payoff anchor + breathing gap")})
            # sample the frame the card will actually sit on
            t = max(0.5, (card_w[0] + card_w[1]) / 2)
            if kit_png:
                pass    # kit card appended above; no frame sampling needed
            elif logo and os.path.exists(logo):
                from PIL import Image
                iw, ih = Image.open(logo).size
                # the card is logo + text, so it is wider than the logo alone
                d = placement.plan_endcard(vid, t, p, iw / max(1, ih), text=ask)
                events.append(card_scrim_event(root, card_w))
                events.append({
                    "template": "badge",
                    "args": [logo],
                    "kwargs": {"text": ask, "x": None, "y": d["y"], "h": d["h"],
                               "w": d["w"], "stack": True, "size": d["size"],
                               # no panel: the full-frame scrim already gives
                               # the card its contrast, and a box on a dimmed
                               # screen is a box on a box
                               "scrim": 0.0},
                    "t": [card_w[0], card_w[1]],
                    "ent": "rise", "sfx": ["ding.mp3", -18], "fade_out": False,
                    "role": "endcard", "anchor": p.get("payoff_anchor"),
                    "why": ("single end card, logo + ask, lower third above platform "
                            f"chrome (detail {d['backdrop_detail']}, luma {d['backdrop_luma']}); "
                            "anchored to payoff-end + breathing gap (gap-13), not clip-end; "
                            "finalize's end tag is suppressed so only one message closes")})
            else:
                d = placement.plan_mark(vid, t, p, ask, "pro", avoid=placed)
                events.append(card_scrim_event(root, card_w))
                events.append({
                    "template": "lowerthird",
                    "args": [kicker or "MADE WITH", ask],
                    "kwargs": {"x": d["x"], "y": d["y"], "size": d["size"],
                               "color": d["color"], "scrim": max(0.6, d["scrim"]),
                               "w": d["w"]},
                    "t": [card_w[0], card_w[1]],
                    "ent": "rise", "sfx": ["ding.mp3", -18], "fade_out": False,
                    "role": "endcard", "anchor": p.get("payoff_anchor"),
                    "why": "single end card: the ask, no separate attribution line; "
                           "anchored to the measured payoff anchor + breathing gap"})
        if events:
            specs[p["id"]] = events
        # Inspection record ("_meta", never composited): the measured payoff
        # anchor, the card window it produced, and every burned overlay line
        # with its role + resolved reveal anchor. This is what the QC gates
        # (endcard_timing, reveal_spoiler) read, and what a human opens when
        # a card's timing is argued about.
        specs.setdefault("_meta", {})[p["id"]] = {
            "payoff_anchor": p.get("payoff_anchor"),
            "outro": p.get("outro"),
            "content_s": cdur,
            "endcard_t0": (card_w[0] if events and card_w else None),
            "lines": [{"t": o.get("t"), "text": o.get("text"),
                       "role": o.get("role", "tease"),
                       "anchor": o.get("anchor")}
                      for o in p.get("overlay_lines") or []],
        }
    json.dump(specs, open(spec_path, "w"), indent=1)
    out = spec_path
    total = sum(len(v) for k, v in specs.items() if k != "_meta")
    print(f"[autoplan] {len(specs)} cuts, {total} marks placed from frame content -> {out}")
    return specs


def apply(project, src_dir=None, cut_id=None, suffix="_gfx", tag=None):
    """Composite each spec'd cut's overlays onto its video in src_dir.

    Matches any file starting with the cut id (cut_01*.mp4 covers per-platform
    exports). Writes <name><suffix>.mp4 next to the source.
    """
    from .preview import run_parallel
    from . import timing
    root = direct.resolve_project(project)
    spec_path = os.path.join(root, "edl", "overlay_specs.json")
    specs = json.load(open(spec_path))
    sfx = f"_{tag}" if tag else ""
    src_dir = src_dir or os.path.join(root, "deliverables", f"final{sfx}")
    workdir = os.path.join(root, "deliverables", "_gfx")
    items = [(cid, events) for cid, events in sorted(specs.items())
             if cid != "_meta" and (not cut_id or cid == cut_id)]

    def _apply_one(item):
        cid, events = item
        outs = []
        with timing.stage(f"{cid} gfx", timing.timings_path(root)):
            _apply_files(cid, events, outs)
        return outs

    def _apply_files(cid, events, outs):
        gfx_master = None
        for fn in sorted(os.listdir(src_dir)):
            if fn.startswith(cid) and fn.endswith(".mp4") and suffix not in fn:
                out = os.path.join(src_dir, fn[:-4] + suffix + ".mp4")
                src = os.path.join(src_dir, fn)
                if gfx_master is None:
                    # first variant pays for the video encode ...
                    _composite(src, out, events, workdir)
                    gfx_master = out
                    print(f"  [{cid}] {os.path.basename(out)} ({len(events)} events)")
                else:
                    # ... siblings copy its composited video stream and only
                    # remix their own audio (mixes differ, video does not)
                    _composite_variant(gfx_master, src, out, events, workdir)
                    print(f"  [{cid}] {os.path.basename(out)} "
                          f"({len(events)} events, video reused)")
                outs.append(out)
        return outs

    # per-cut work is independent ffmpeg subprocesses: small thread pool
    done, failed = 0, []
    for (cid, _), outs, err in run_parallel(items, _apply_one, max_workers=3):
        if err:
            print(f"[overlays] {cid} FAILED: {err}")
            failed.append(cid)
        else:
            done += len(outs)
    if failed:
        raise RuntimeError("overlays failed on: " + ", ".join(failed))
    if not done:
        print(f"[overlays] nothing matched in {src_dir}")
