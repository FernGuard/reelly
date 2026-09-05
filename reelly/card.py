"""Motion cards: draw it, then photograph it, one frame at a time.

WHY THIS EXISTS
Threads, X, Facebook and Reddit take text on its own. Instagram, TikTok and
YouTube do not: they need media, a still is second-class on all three, and
Shorts will not take one at all. So a text-only post reaches under half the
network list. This turns copy into a short vertical video.

HOW IT WORKS, AND WHY IT IS NOT THE OBVIOUS WAY
The obvious way is to render static text with Pillow and animate it with ffmpeg
expressions. That is what the first version of this file did, and it produces
exactly the failure everyone recognises as AI motion: stiff, with everything
moving at the same speed, because every element shares the one motion
vocabulary an ffmpeg expression can express.

So the motion lives in CSS instead, and the browser is photographed frame by
frame. Cubic-bezier easing, per-element choreography, blur, transforms and
staggered delays are all things CSS already does properly. Nothing is generated
and hoped over: the page is drawn, then screenshotted, then stitched.

THE SEEK TRICK
Headless Chrome screenshots one moment, not a timeline. So each worker opens the
page ONCE over the DevTools protocol, then for every frame sets
`animation.currentTime` on every running animation and screenshots the result.
The Web Animations API is doing the seeking, which is what it is for: the
browser lays out that exact instant and holds it, deterministically.

The naive version of this shells out to `chrome --screenshot` once per frame,
with the seek baked into negative animation-delays. It works and it is
unusably slow: every frame pays a cold browser start, a fresh profile and
another round trip for the webfont. Measured at ~60s per frame, so ~3 hours for
a 6-second card. Reusing one browser per worker takes the same card to well
under a minute. Frames are still split across workers, since each frame is an
independent deterministic render.

MOTION RULES (brand + playbook G1)
- Ease-out only, and NOT the same curve for everything: type settles on a soft
  cubic-bezier, the accent rule snaps on a tighter one, the backdrop drifts on
  a slow sine. Different elements moving at different speeds is the whole
  difference between motion design and a slideshow.
- Words rise and unblur in reading order, staggered, never together.
- Everything is still for the last third. A card still moving while it is being
  read feels unfinished.
"""
import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import urllib.request

from . import config

W, H, FPS = 1080, 1920, 30
INK, PAPER, ACCENT, MUTED = "#090c0a", "#FCFCFB", "#17cdff", "#8b9691"
CHROME = config.CHROME or "google-chrome"

# Two different curves on purpose. Type settles; the rule arrives.
EASE_TYPE = "cubic-bezier(.16,.84,.28,1)"
EASE_RULE = "cubic-bezier(.2,.9,.2,1)"
EASE_OUT = "cubic-bezier(.4,0,1,1)"
EASE_WIPE = "cubic-bezier(.76,0,.24,1)"

# Fallback only. A card with a picture takes its colour FROM the picture; this
# is what a card with no picture gets.
BRAND = {"accent": ACCENT, "ink": INK,
         "blobs": ("#17cdff", "#0a5f7a", "#112233", "#17cdff")}


HOLD, LAST_HOLD, EXIT, LEAD = 2.3, 3.0, 0.52, 0.34
# Dwell scales with the number of words. A fixed hold reads fine on a
# three-word beat and is gone before a twelve-word one has been read.
PER_WORD = 0.34

# A beat is only as big as it can afford to be. One fixed headline size either
# overflows the long beats or wastes the short ones; a short beat SHOULD hit
# harder than a long one, so size tracks length instead of being set once.
STEPS = ((28, 96), (48, 80), (72, 66), (110, 56))
FLOOR = 48

# Per-beat Ken Burns. Each entry is (from-scale, to-scale, from-x, from-y, to-x,
# to-y, origin). Cycling through them means consecutive shots never drift the
# same way, which is what makes a run of stills read as edited rather than as a
# slideshow with one effect stuck on it.
MOVES = (
    (1.06, 1.18, -14, 8, 10, -12, "50% 40%"),
    (1.16, 1.04, 12, -10, -8, 6, "40% 60%"),
    (1.04, 1.15, 0, 14, 6, -10, "60% 50%"),
    (1.14, 1.05, -10, -8, 8, 10, "50% 55%"),
)


def _palette(path, ffmpeg=None):
    """Pull a colour scheme out of the image the card is actually built on.

    Every card sharing one hardcoded accent makes a feed of them look like a
    single template with the words swapped, which is exactly what we do not want
    a run of posts to look like. So the accent and the background blooms come
    from the source frame: the card is coloured by its own content and no two
    cards agree unless their images do.
    """
    import colorsys
    from PIL import Image

    tmp = None
    try:
        if path.lower().endswith((".mp4", ".mov", ".m4v")):
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
            subprocess.run([ffmpeg or config.FFMPEG, "-y", "-v", "error", "-ss", "1",
                            "-i", path, "-frames:v", "1", tmp], check=True)
            path = tmp
        im = Image.open(path).convert("RGB")
        im.thumbnail((180, 180))
        q = im.quantize(colors=14, method=Image.MEDIANCUT).convert("RGB")
        counts = sorted(q.getcolors(1 << 16) or [], reverse=True)
    except Exception:
        return dict(BRAND)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)

    if not counts:
        return dict(BRAND)

    hls = []
    for n, (r, g, b) in counts:
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        hls.append((n, h, l, s))
    total = sum(n for n, _, _, _ in hls) or 1

    def hexof(h, l, s):
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))

    # The accent is the COMPLEMENT of the picture's dominant hue, not the hue
    # itself. Matching the dominant colour is the obvious move and it is wrong:
    # blue art gives blue type, the card reads as one flat colour, and a run of
    # them looks identical no matter how different the imagery was. The
    # complement is the colour the picture is missing, so the type separates
    # from the background instead of dissolving into it.
    best, score_best = None, -1
    for n, h, l, s in hls:
        score = s * (n / total) ** 0.3 * (1.0 - abs(l - 0.5))
        if score > score_best:
            best, score_best = (h, l, s), score
    dominant = best[0]
    h = (dominant + 0.5) % 1.0
    # Greens read as sickly at accent brightness and yellow-greens vanish against
    # a warm scrim, so nudge out of that band toward gold.
    if 0.18 < h < 0.42:
        h = 0.12 if h < 0.30 else 0.47
    accent = hexof(h, 0.62, max(0.78, min(0.95, best[2])))

    # Blooms keep the PICTURE's hues (not the complement), pushed dark. The
    # frame stays in harmony with its imagery; only the type contrasts.
    blobs, used = [], []
    for n, hh, ll, ss in sorted(hls, key=lambda x: -x[0]):
        if any(min(abs(hh - u), 1 - abs(hh - u)) < 0.06 for u in used):
            continue
        used.append(hh)
        blobs.append(hexof(hh, 0.20 + 0.06 * len(blobs), max(0.35, min(0.8, ss))))
        if len(blobs) == 3:
            break
    while len(blobs) < 4:
        blobs.append(accent)
    return {"accent": accent, "ink": hexof(h, 0.045, 0.30), "blobs": tuple(blobs[:4])}


def _size(text, cap):
    for limit, size in STEPS:
        if len(text) <= limit:
            return min(cap, size)
    return min(cap, FLOOR)


def _words(text):
    """Split into words, honouring **emphasis** so a beat can have a hot word."""
    out, inside = [], False
    for raw in text.split():
        opens = raw.startswith("**")
        closes = raw.endswith("**") and not (opens and raw == "**")
        hot = inside or opens
        if opens and not closes:
            inside = True
        elif closes:
            inside = False
        out.append((raw.replace("**", ""), hot))
    return out


def _plan(beats, head_cap=96):
    """Lay the beats out on one timeline.

    Each beat is timed from its own word count rather than given a fixed slot,
    so a three-word line does not sit there dead for the same duration as a
    twelve-word one. Reading time is what sets the pace.
    """
    out, t0 = [], 0.0
    for i, b in enumerate(beats):
        words = _words(b["text"])
        n = len(words)
        rule_at = 0.30 + 0.065 * (n - 1) + 0.44
        sub_at = rule_at + 0.24
        content_end = (sub_at + 0.7) if b.get("sub") else (rule_at + 0.5)
        last = i == len(beats) - 1
        dur = content_end + max(LAST_HOLD if last else HOLD, PER_WORD * n)
        out.append({"words": words, "sub": b.get("sub"), "start": t0,
                    "rule_at": rule_at, "sub_at": sub_at, "dur": dur,
                    "last": last, "size": _size(b["text"], head_cap),
                    "media": b.get("img") or b.get("clip"),
                    "is_clip": bool(b.get("clip")), "move": MOVES[i % len(MOVES)],
                    # A fitted still occupies the upper half of the frame, so
                    # its type goes low rather than on top of the picture.
                    "fit": bool(b.get("img")) and i % 2 == 0,
                    "pos": ("low" if (b.get("img") and i % 2 == 0)
                            else "mid" if (last or i % 2 == 0) else "low"),
                    "at": b.get("at")})
        t0 += dur
    return out, t0


def _proxy(src, at, need, dst):
    """Cut a small 9:16 proxy of just the window a beat actually uses.

    World captures are minutes long, 2560x1440 and gigabytes on disk. Handing
    one straight to a <video> means every worker decodes the whole file and
    seeks inside it for every single frame, which stalls the page long enough
    that frames come back black. A beat only ever shows a few seconds, so cut
    exactly those seconds down to frame size once and let the browser hold the
    whole thing in memory.
    """
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-ss", f"{at:.2f}",
                    "-i", src, "-t", f"{need:.2f}", "-an",
                    "-vf", (f"crop='min(iw,ih*{W}/{H})*0.84':'ih*0.84',"
                            f"scale={W}:{H},fps=30"),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-pix_fmt", "yuv420p", dst], check=True)
    return dst


def _media_layer(b, i, total):
    """One full-bleed shot, wiped in and never still.

    Shots stack rather than cross-dissolve: each new one is revealed by a
    clip-path wipe over the shots before it. A dissolve is what you reach for
    when you have no idea what to do between two pictures; a wipe is a cut with
    an opinion, and alternating its direction stops the sequence developing a
    tic. The scale punch underneath it is the thing that sells it as a cut:
    real footage never changes shot without the framing changing too.
    """
    lo, hi, fx, fy, tx, ty, origin = b["move"]
    at = b["start"]
    run = max(2.0, total - at + 2.0)
    inner = (f'<video class="ph" src="file://{b["media"]}" data-start="{at:.3f}" '
             f'muted playsinline preload="auto"></video>' if b["is_clip"]
             else (f'<div class="phb" style="background-image:url(file://{b["media"]})">'
                   f'</div><div class="phc" '
                   f'style="background-image:url(file://{b["media"]})"></div>'
                   if b["fit"] else
                   f'<div class="ph" style="background-image:url(file://{b["media"]});'
                   f'background-size:cover;background-position:center"></div>'))
    wipe = ("wipeL", "wipeU", "wipeR", "wipeD")[i % 4]
    return (f'<div class="media" style="animation:{wipe} .62s {EASE_WIPE};'
            f'animation-delay:{at:.2f}s;animation-fill-mode:both">'
            f'<div class="punch" style="animation:punch .9s {EASE_TYPE};'
            f'animation-delay:{at:.2f}s;animation-fill-mode:both">'
            f'<div class="kb" style="transform-origin:{origin};'
            f'animation:kb{i} {run:.2f}s linear;animation-delay:{at:.2f}s;'
            f'animation-fill-mode:both">'
            f'{inner}</div></div><div class="mscrim"></div></div>'), (
        f'@keyframes kb{i}{{from{{transform:scale({lo}) translate({fx}px,{fy}px)}}'
        f'to{{transform:scale({hi}) translate({tx}px,{ty}px)}}}}')


def _still_start(plan, total):
    """The moment the page is VISUALLY settled: every entry animation of the
    last beat has finished and the logo rise has landed. From here to the end
    of the card only ambient motion remains (blob drift, the tail of a Ken
    Burns, the progress bar), so frames are near-identical: make() captures
    ONE frame at this point and duplicates it for the rest of the hold
    instead of re-screenshotting ~90 frames of the same picture. The progress
    bar is timed (in _html) to complete exactly here so nothing visibly
    freezes mid-motion."""
    b = plan[-1]
    t0 = b["start"]
    n = len(b["words"])
    t = t0 + 0.30 + (n - 1) * 0.065 + 0.82            # last word landed
    if any(hot for _, hot in b["words"]):
        t = max(t, t0 + 0.30 + (n - 1) * 0.065 + 0.42 + 0.52)   # pop done
    t = max(t, t0 + b["rule_at"] + 0.55)               # accent rule drawn
    if b["sub"]:
        t = max(t, t0 + b["sub_at"] + 0.75)            # sub landed
    t = max(t, max(0.3, total - LAST_HOLD - 0.8) + 0.8)   # logo rise complete
    return min(t, total)


def _html(plan, total, logo_uri, head_size, sub_size, bg_uri=None, pal=None,
          still_at=None):
    """The whole sequence as one page, paused at t=0. Seeking happens over CDP.

    Every beat is a stage stacked in the same place; each one's children are
    invisible until their own delay and the words wipe themselves away when the
    beat ends, so exactly one is legible at a time. Building it as a single page
    rather than one clip per beat is what keeps the backdrop drifting
    continuously instead of snapping back to its start on every cut.

    The type does not fade in. Each word sits in its own clipped box and slides
    up out of it, which is the difference between motion design and a
    transition: you read an edge, not an opacity ramp. Tracking opens slightly
    wide and settles as the line lands, so the type arrives rather than appears.
    """
    pal = pal or dict(BRAND)
    acc = pal["accent"]
    bl = pal["blobs"]
    stages, media, kbs = [], [], []
    for i, b in enumerate(plan):
        t0, last = b["start"], b["last"]
        out_at = t0 + b["dur"] - EXIT
        if b["media"]:
            layer, kb = _media_layer(b, i, total)
            media.append(layer)
            kbs.append(kb)

        spans = []
        for j, (w, hot) in enumerate(b["words"]):
            enter = t0 + 0.30 + j * 0.065
            # Words leave in the order they arrived, but tighter: a slow exit
            # reads as hesitation.
            leave = out_at + j * 0.028
            anim = (f"animation:wordin .82s {EASE_TYPE};animation-delay:{enter:.2f}s;animation-fill-mode:both"
                    if last else
                    f"animation:wordin .82s {EASE_TYPE},wordout .42s {EASE_OUT};"
                    f"animation-delay:{enter:.2f}s,{leave:.2f}s;animation-fill-mode:both,forwards")
            pop = (f' style="animation:pop .52s {EASE_TYPE};'
                   f'animation-delay:{enter + 0.42:.2f}s;animation-fill-mode:both"') if hot else ""
            spans.append(f'<span class="w"{pop}><i class="{"hot" if hot else ""}" '
                         f'style="{anim}">{w}</i></span>')

        sub = ""
        if b["sub"]:
            sa = (f"animation:subin .75s {EASE_TYPE};animation-delay:{t0 + b['sub_at']:.2f}s;animation-fill-mode:both"
                  if last else
                  f"animation:subin .75s {EASE_TYPE},subout .38s {EASE_OUT};"
                  f"animation-delay:{t0 + b['sub_at']:.2f}s,{out_at:.2f}s;animation-fill-mode:both,forwards")
            sub = f'<div class="sub"><i style="{sa}">{b["sub"]}</i></div>'

        ra = (f"animation:wipe .55s {EASE_RULE};animation-delay:{t0 + b['rule_at']:.2f}s;animation-fill-mode:both"
              if last else
              f"animation:wipe .55s {EASE_RULE},unwipe .34s {EASE_OUT};"
              f"animation-delay:{t0 + b['rule_at']:.2f}s,{out_at:.2f}s;animation-fill-mode:both,forwards")
        stages.append(
            f'<div class="stage {b["pos"]}">'
            f'<div class="head" style="font-size:{b["size"]}px">'
            f'{"".join(spans)}</div>'
            f'<div class="rule" style="{ra}"></div>{sub}</div>')

    # The logo rise completes as the final hold begins: the last third of the
    # card really is still (the stated motion rule), and the whole hold can be
    # served from ONE screenshot (see _still_start) instead of re-capturing
    # ~LAST_HOLD*fps frames of an unchanged end card.
    logo_at = max(0.3, total - LAST_HOLD - 0.8)
    bg = (f'<div class="media" style="clip-path:inset(0)"><div class="kb" '
          f'style="animation:kbbg 26s linear infinite alternate">'
          f'<div class="ph" style="background-image:url(file://{bg_uri})"></div></div>'
          f'<div class="mscrim"></div></div>' if bg_uri else "")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@600;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:{pal['ink']};
  font-family:'IBM Plex Sans',sans-serif}}
/* Paused globally so a screenshot is a seek, not a race. Fill-mode is set
   per element instead of forced here: an element with both an entry and an
   exit needs `backwards, forwards`, not `both, both`, or the exit's start
   state fills backwards over the entry and shows before its beat. */
*,*::before,*::after{{animation-play-state:paused!important}}

/* Backdrop: four blurred blooms drifting at different rates, coloured from the
   card's own imagery. This is what shows through before the first shot lands. */
.blob{{position:absolute;border-radius:50%;filter:blur(120px);opacity:.55;
  animation:drift 24s ease-in-out infinite alternate;animation-fill-mode:both}}
.b1{{width:900px;height:900px;background:{bl[0]};left:-260px;top:-200px}}
.b2{{width:760px;height:760px;background:{bl[1]};right:-240px;top:340px;opacity:.5;
  animation-duration:31s;animation-delay:-6s}}
.b3{{width:680px;height:680px;background:{bl[2]};left:120px;bottom:-160px;opacity:.7;
  animation-duration:27s;animation-delay:-13s}}
.b4{{width:420px;height:420px;background:{bl[3]};right:60px;bottom:280px;opacity:.28;
  animation-duration:19s;animation-delay:-3s}}
@keyframes drift{{from{{transform:translate3d(0,0,0) scale(1)}}
  to{{transform:translate3d(90px,-70px,0) scale(1.14)}}}}

/* Shots stack and wipe; nothing ever cross-dissolves and nothing is ever still. */
.media{{position:absolute;inset:0;overflow:hidden}}
.punch,.kb{{position:absolute;inset:0}}
.kb{{inset:-7%}}
.ph{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
  filter:saturate(1.06) contrast(1.04)}}
/* A landscape still is fitted whole, over a blurred blown-up copy of itself. */
.phb{{position:absolute;inset:-9%;background-size:cover;background-position:center;
  filter:blur(42px) saturate(1.25) brightness(.5)}}
.phc{{position:absolute;inset:0;background-size:contain;background-repeat:no-repeat;
  background-position:center 33%;filter:saturate(1.06) contrast(1.04)}}
@keyframes wipeL{{from{{clip-path:inset(0 100% 0 0)}}to{{clip-path:inset(0)}}}}
@keyframes wipeR{{from{{clip-path:inset(0 0 0 100%)}}to{{clip-path:inset(0)}}}}
@keyframes wipeU{{from{{clip-path:inset(100% 0 0 0)}}to{{clip-path:inset(0)}}}}
@keyframes wipeD{{from{{clip-path:inset(0 0 100% 0)}}to{{clip-path:inset(0)}}}}
@keyframes punch{{from{{transform:scale(1.09)}}to{{transform:scale(1)}}}}
@keyframes kbbg{{from{{transform:scale(1.04)}}to{{transform:scale(1.16)}}}}
{chr(10).join(kbs)}
/* Legibility is not optional: type sits low, so the scrim is weighted there
   rather than spread flat across the frame. */
.mscrim{{position:absolute;inset:0;background:
  linear-gradient(180deg,rgba(9,12,10,.74) 0%,rgba(9,12,10,.30) 24%,
  rgba(9,12,10,.66) 56%,rgba(9,12,10,.93) 100%)}}

/* Grain and vignette: the details nobody notices consciously. */
.grain{{position:absolute;inset:0;opacity:.055;mix-blend-mode:overlay;
  background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence baseFrequency='.85' numOctaves='3'/></filter><rect width='160' height='160' filter='url(%23n)'/></svg>")}}
.vig{{position:absolute;inset:0;
  background:radial-gradient(ellipse at center,transparent 42%,rgba(0,0,0,.72) 100%)}}

/* Consecutive beats never sit in the same place: the eye should have to move. */
.stage{{position:absolute;left:88px;right:88px}}
.stage.mid{{top:50%;transform:translateY(-50%)}}
.stage.low{{bottom:300px}}
.head{{font-weight:700;line-height:1.12;color:{PAPER};letter-spacing:-.022em;
  display:flex;flex-wrap:wrap;gap:0 .26em}}
/* The clipped box is the whole trick: the word is already in position and the
   box is simply not showing it yet. */
.head .w{{display:inline-block;overflow:hidden;padding:.08em .04em .26em;
  margin:0 -.04em -.26em}}
.head .w i{{display:inline-block;font-style:normal;
  transform:translateY(155%) rotate(5deg)}}
.head .w i.hot{{color:{acc}}}
@keyframes wordin{{to{{transform:translateY(0) rotate(0)}}}}
@keyframes wordout{{to{{transform:translateY(-155%) rotate(-4deg)}}}}
@keyframes pop{{0%{{transform:scale(1)}}42%{{transform:scale(1.085)}}
  100%{{transform:scale(1)}}}}

.rule{{height:9px;background:{acc};border-radius:5px;margin-top:38px;width:150px;
  transform-origin:left center;transform:scaleX(0)}}
@keyframes wipe{{to{{transform:scaleX(1)}}}}
@keyframes unwipe{{from{{transform:scaleX(1);transform-origin:right center}}
  to{{transform:scaleX(0);transform-origin:right center}}}}

.sub{{margin-top:28px;max-width:90%;overflow:hidden;padding-bottom:.1em}}
.sub i{{display:block;font-style:normal;font-weight:600;font-size:{sub_size}px;
  line-height:1.34;color:{PAPER};opacity:.82;transform:translateY(145%)}}
@keyframes subin{{to{{transform:translateY(0)}}}}
@keyframes subout{{to{{transform:translateY(-145%)}}}}

/* Centred with auto margins, NOT translateX(-50%): a keyframe ending on
   transform:translateY(0) would replace the whole transform and drop the
   centering, parking the logo half its width right of centre. */
.logo{{position:absolute;left:0;right:0;margin:0 auto;bottom:176px;display:block;
  height:88px;opacity:0;transform:translateY(16px);animation:rise .8s {EASE_TYPE};
  animation-delay:{logo_at:.2f}s;animation-fill-mode:both}}
@keyframes rise{{to{{opacity:1;transform:translateY(0)}}}}

/* A read on how much is left. Cheap, and it measurably holds people to the end.
   Completes at the still point (see _still_start) so the frame-dedupe on the
   final hold never freezes it mid-bar. */
.prog{{position:absolute;top:0;left:0;height:7px;width:100%;background:{acc};
  transform-origin:left center;transform:scaleX(0);
  animation:prog {(still_at or total):.2f}s linear;animation-fill-mode:both}}
@keyframes prog{{to{{transform:scaleX(1)}}}}
</style></head><body>
{bg}
<div class="blob b1"></div><div class="blob b2"></div>
<div class="blob b3"></div><div class="blob b4"></div>
{"".join(media)}
<div class="grain"></div><div class="vig"></div>
{"".join(stages)}
{f'<img class="logo" src="{logo_uri}"/>' if logo_uri else ''}
<div class="prog"></div>
</body></html>"""


def _data_uri(path):
    import base64
    import mimetypes
    mime = mimetypes.guess_type(path)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(open(path,'rb').read()).decode()}"



# ---------------------------------------------------------------- CDP driver

# Seek every animation on the page to `ms`, including ones that have not been
# reached yet. Returns the count so a page that somehow rendered no animations
# fails loudly instead of producing a video of one still frame.
_SEEK = """(function(ms){
  var a = document.getAnimations();
  a.forEach(function(x){ try { x.pause(); x.currentTime = ms; } catch(e){} });
  // Footage is seeked to the same instant, so a clip behind a card advances
  // with the card instead of being a still or a loop running on its own clock.
  var vs = Array.prototype.slice.call(document.querySelectorAll('video'));
  return Promise.all(vs.map(function(v){
    var want = (ms/1000) - parseFloat(v.dataset.start || 0);
    want = Math.max(0, Math.min(want, (v.duration || 0.04) - 0.04));
    if (Math.abs(v.currentTime - want) < 0.004) return 1;
    return new Promise(function(res){
      var done = function(){ v.removeEventListener('seeked', done); res(1); };
      v.addEventListener('seeked', done);
      v.currentTime = want;
      setTimeout(function(){ res(1); }, 500);   // never hang a whole render
    });
  })).then(function(){ return a.length; });
})(%f)"""


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Browser:
    """One headless Chrome, held open, driven over the DevTools protocol."""

    def __init__(self, profile_dir):
        self.port = _free_port()
        self.profile = profile_dir
        self.proc = None
        self.ws = None
        self.sid = None
        self._n = 0

    async def __aenter__(self):
        import websockets
        self.proc = await asyncio.create_subprocess_exec(
            CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
            "--no-first-run", "--no-default-browser-check", "--disable-extensions",
            "--allow-file-access-from-files", "--autoplay-policy=no-user-gesture-required",
            "--force-device-scale-factor=1", f"--window-size={W},{H}",
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}", "about:blank",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)

        url = None
        for _ in range(150):  # Chrome writes the endpoint when it is ready
            await asyncio.sleep(0.1)
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/json/version", timeout=1) as r:
                    url = json.load(r)["webSocketDebuggerUrl"]
                break
            except Exception:
                continue
        if not url:
            raise RuntimeError("headless Chrome never opened a debug port")

        self.ws = await websockets.connect(url, max_size=None)
        tid = (await self("Target.createTarget", {"url": "about:blank"}))["targetId"]
        self.sid = (await self("Target.attachToTarget",
                               {"targetId": tid, "flatten": True}))["sessionId"]
        await self("Emulation.setDeviceMetricsOverride",
                   {"width": W, "height": H, "deviceScaleFactor": 1, "mobile": False})
        await self("Page.enable")
        await self("Runtime.enable")
        return self

    async def __aexit__(self, *_):
        try:
            await self.ws.close()
        except Exception:
            pass
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 5)
            except asyncio.TimeoutError:
                self.proc.kill()

    async def __call__(self, method, params=None):
        self._n += 1
        msg = {"id": self._n, "method": method, "params": params or {}}
        if self.sid:
            msg["sessionId"] = self.sid
        await self.ws.send(json.dumps(msg))
        while True:  # events interleave with replies; match on id
            reply = json.loads(await self.ws.recv())
            if reply.get("id") == self._n:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})

    async def _eval(self, expr, await_promise=False):
        r = await self("Runtime.evaluate",
                       {"expression": expr, "returnByValue": True,
                        "awaitPromise": await_promise})
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("text", "js error"))
        return r["result"].get("value")

    async def load(self, page_url):
        await self("Page.navigate", {"url": page_url})
        for _ in range(200):
            if await self._eval("document.readyState") == "complete":
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("page never finished loading")
        # Without this the first frames render in a fallback face and the type
        # visibly reflows partway through the card.
        await self._eval("document.fonts.ready.then(function(){return 1})", True)
        # A video that has not buffered yet seeks to a black frame, so wait for
        # every clip to be decodable before any frame is taken.
        await self._eval("""Promise.all(Array.prototype.slice.call(
            document.querySelectorAll('video')).map(function(v){
              v.pause();
              if (v.readyState >= 2) return 1;
              return new Promise(function(res){
                v.addEventListener('loadeddata', function(){res(1)});
                setTimeout(function(){res(1)}, 8000);
              });
            })).then(function(){return 1})""", True)

    async def shoot(self, t, path):
        n = await self._eval(_SEEK % (t * 1000.0), True)
        if not n:
            raise RuntimeError("page has no animations to seek")
        # Format follows the extension: intermediate frames are JPEG (q92) --
        # a full-page screenshot is always opaque, nothing downstream
        # composites these frames (they go straight into libx264 yuv420p),
        # so PNG's alpha and its encode cost bought nothing. PNG remains
        # available for any caller that asks for a .png path.
        if path.lower().endswith((".jpg", ".jpeg")):
            shot = await self("Page.captureScreenshot",
                              {"format": "jpeg", "quality": 92})
        else:
            shot = await self("Page.captureScreenshot", {"format": "png"})
        with open(path, "wb") as f:
            f.write(base64.b64decode(shot["data"]))


async def _shoot_all(page_url, shots, td, workers, ext="jpg"):
    """Split the (frame index, time) list across workers; each opens the page
    once. Callers decide WHICH frames need a real capture (make() dedupes the
    still tail before handing the list over)."""
    todo = list(shots)
    lanes = [todo[i::workers] for i in range(workers)]

    async def lane(i, work):
        if not work:
            return
        async with _Browser(os.path.join(td, f"profile{i}")) as b:
            await b.load(page_url)
            for n, t in work:
                await b.shoot(t, os.path.join(td, f"f{n:05d}.{ext}"))

    await asyncio.gather(*(lane(i, w) for i, w in enumerate(lanes)))


def make(text, out, sub=None, beats=None, bg=None, logo=None, dur=None,
         head_size=88, sub_size=42, fps=FPS, workers=4):
    """Render a kinetic motion card, or a multi-beat sequence, from CSS.

    Pass `text`/`sub` for a single card, or `beats` as a list of
    {"text": ..., "sub": ...} for a story that needs more than one screen.
    Frames are independent and deterministic, so the timeline splits across
    `workers`, each holding one browser open for its whole share.

    Deferred (deliberately): concatenating a pre-rendered brand outro from
    brandkit.outro(studio) instead of screenshotting the logo rise per card.
    The accessor exists; wiring waits until the .mov outros are generated
    (make() takes a logo path today, not a studio key -- the outro cut-in
    belongs with that API change). Today's wins are JPEG capture + the
    still-hold dedupe above.
    """
    if beats is None:
        beats = [{"text": text, "sub": sub}]
    plan, total = _plan(beats, head_size)
    # Colour comes from the card's own imagery, so a run of these does not read
    # as one template with the words swapped.
    src = bg or next((b["media"] for b in plan if b["media"]), None)
    pal = _palette(src) if src else dict(BRAND)
    if dur is None:
        dur = round(max(6.0, total), 2)
    frames = int(dur * fps)
    logo_uri = _data_uri(logo) if logo and os.path.exists(logo) else None
    bg_uri = _data_uri(bg) if bg and os.path.exists(bg) else None

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        # Proxy every clip before the browser ever sees it.
        for i, b in enumerate(plan):
            if b["is_clip"]:
                b["media"] = _proxy(b["media"], (b["at"] if b["at"] is not None else 6.0 + i * 5.0),
                                    b["dur"] + 0.6,
                                    os.path.join(td, f"clip{i}.mp4"))

        # Still-frame dedupe: after _still_start the page is settled (the
        # progress bar is timed to complete there), so the whole final hold
        # is ONE screenshot duplicated, not ~LAST_HOLD*fps re-captures.
        still_at = _still_start(plan, total)
        times = [n / fps for n in range(frames)]
        n_still = next((n for n, t in enumerate(times) if t >= still_at), None)
        shots = (list(enumerate(times)) if n_still is None
                 else list(enumerate(times))[:n_still + 1])

        page = os.path.join(td, "card.html")
        with open(page, "w") as f:
            f.write(_html(plan, total, logo_uri, head_size, sub_size,
                          bg_uri, pal, still_at=still_at))

        asyncio.run(_shoot_all("file://" + page, shots, td, workers))

        if n_still is not None and n_still + 1 < frames:
            src = os.path.join(td, f"f{n_still:05d}.jpg")
            for n in range(n_still + 1, frames):
                dst = os.path.join(td, f"f{n:05d}.jpg")
                try:
                    os.link(src, dst)          # same file, zero copies
                except OSError:
                    shutil.copy(src, dst)
            print(f"[card] still-frame dedupe: {frames - n_still - 1} frames "
                  f"duplicated from t={still_at:.2f}s instead of re-captured")

        made = len([f for f in os.listdir(td) if f.endswith(".jpg")])
        if made < frames:
            raise RuntimeError(f"only {made}/{frames} frames rendered")

        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-framerate", str(fps),
                        "-i", os.path.join(td, "f%05d.jpg"),
                        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-map", "0:v", "-map", "1:a", "-t", str(dur),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                        "-shortest", out], check=True)
    return out


def score(video, title="", project="", beats=3, out=None, level=0.62,
          bed_dir=None):
    """Lay a music bed under a finished card.

    Silence is a real cost on vertical feeds: a card with no audio reads as a
    broken upload, and the platforms that most need this format are the ones
    where sound is the default. There is no voice to protect here, so the bed
    plays at full level rather than the ducked level a narrated cut uses.

    Kept separate from make() on purpose: the frames are the expensive part, so
    a card can be scored, re-scored or re-mixed without re-rendering anything.
    """
    from . import audio_post, media

    dur = media.duration(video)
    plan = {"id": os.path.splitext(os.path.basename(video))[0],
            "duration_s": dur, "title": title,
            "segments": [None] * beats, "format": ""}
    out = out or video

    with tempfile.TemporaryDirectory() as td:
        # Beds are cached per project when a directory is given: a card gets
        # re-rendered for editorial reasons far more often than its music needs
        # to change, and regenerating the bed each time costs money and drifts
        # the sound of a post that was already approved.
        if bed_dir:
            os.makedirs(bed_dir, exist_ok=True)
            bed_path = os.path.join(bed_dir, f"{plan['id']}.mp3")
        else:
            bed_path = os.path.join(td, "bed.mp3")
        bed = audio_post.music(plan, bed_path, project)
        mixed = os.path.join(td, "mixed.mp4")
        subprocess.run([
            config.FFMPEG, "-y", "-v", "error", "-i", video, "-i", bed,
            "-filter_complex",
            # Trimmed to the card, not faded arbitrarily: the bed has to end
            # with the last beat rather than being cut off mid-phrase.
            f"[1:a]atrim=0:{dur:.3f},asetpts=N/SR/TB,"
            f"afade=t=in:st=0:d=0.7,afade=t=out:st={max(0, dur - 1.4):.3f}:d=1.4,"
            f"volume={level}[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-shortest", mixed], check=True)
        shutil.copy(mixed, out)

    # Same delivery guarantees as any other Reelly output: AAC encoding adds
    # peak after a correct mix, so both are enforced on the delivered file.
    audio_post.enforce_loudness(out)
    audio_post.enforce_true_peak(out)
    return out
