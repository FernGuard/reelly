"""Kinetic type for the burned-in text layer: the hook and the timed lines.

WHY
The static layer is a Pillow PNG switched on and off by ffmpeg: no entrance,
no emphasis, no exit. It reads as a slideshow. card.py already showed the way
out (draw the type in a browser with CSS motion, photograph it frame by frame)
but only for opaque full-screen cards. This renders ONE line of text on a
TRANSPARENT canvas, as a PNG sequence that finalize composites exactly where
the static PNG used to sit.

THE MOTION (one line, local time 0..dur)
- Words enter in reading order, staggered: rise a third of an em, unblur and
  fade in on the type curve. Never all at once.
- The hot word (the plan's `hot`, else the last word) takes the brand accent
  and lands with one small scale pop after it arrives.
- Then everything HOLDS, still, so it can be read. The hold is one capture
  reused for every frame (no re-render of an unchanged page).
- The line leaves on a short ease-out lift and fade that ends at `dur`.

Fonts and colours come from the brand kit (headline face for the hook, ink
for text, accent for the hot word), loaded from local files. Without a kit it
falls back to the same system faces the Pillow layer uses.

REELLY_KINETIC=off restores the static layer.
"""
import asyncio
import html
import os
import shutil
import subprocess

from . import config

FPS = 30
STAGGER = 0.07          # s between word entrances
ENTER = 0.46            # s for one word to arrive
POP_AT, POP = 0.30, 0.36  # hot word: pop starts this long after it lands, lasts POP
POP_SCALE = 1.12
EXIT = 0.26             # s for the line to leave
PAD = 28                # px around the text for the rise, the pop and the shadow
# Same curves as card.py: type settles softly, exits accelerate away.
EASE_TYPE = "cubic-bezier(.16,.84,.28,1)"
EASE_POP = "cubic-bezier(.3,1.6,.5,1)"
EASE_OUT = "cubic-bezier(.4,0,1,1)"
FALLBACK_STACK = "'Arial Black', 'Arial', 'Helvetica Neue', sans-serif"


def enabled():
    """On unless REELLY_KINETIC=off, and only where Chrome exists."""
    if os.environ.get("REELLY_KINETIC", "").strip().lower() in ("off", "0", "no"):
        return False
    ch = config.CHROME
    return bool(ch) and (os.path.isfile(ch) or bool(shutil.which(ch)))


def hot_index(words, hot=None):
    """Which word carries the accent: an explicit index or word from the plan,
    else the last word (short-form lines put the punch at the end)."""
    if not words:
        return None
    if isinstance(hot, int) and 0 <= hot < len(words):
        return hot
    if isinstance(hot, str) and hot.strip():
        want = hot.strip().lower().strip(".,!?;:")
        for i, w in enumerate(words):
            if w.lower().strip(".,!?;:") == want:
                return i
    return len(words) - 1


def timeline(n_words, dur, hot=None):
    """(settle_t, exit_t): when the last motion before the hold ends, and when
    the exit starts. Entrances are squeezed if a short window cannot fit them,
    so a line never exits before it has fully arrived."""
    exit_t = max(0.0, dur - EXIT)
    stagger = STAGGER
    last_in = (n_words - 1) * stagger + ENTER
    if last_in > exit_t * 0.6 and n_words > 1:          # short window: tighten
        stagger = max(0.0, (exit_t * 0.6 - ENTER) / (n_words - 1))
        last_in = (n_words - 1) * stagger + ENTER
    settle = last_in
    if hot is not None:
        settle = max(settle, hot * stagger + ENTER + POP_AT + POP)
    return min(settle, exit_t), exit_t, stagger


def _font_face(role):
    from . import brandkit
    from pathlib import Path
    path = brandkit.font(role)
    if not path:
        return "", FALLBACK_STACK
    uri = Path(path).resolve().as_uri()
    return (f"@font-face{{font-family:'ReellyKinetic';src:url('{uri}');}}",
            f"'ReellyKinetic', {FALLBACK_STACK}")


def wrap_lines(text, size, width, role="headline"):
    """The line breaks the static layer would use (captions._wrap with the
    same font, size and box), as lists of words. The browser is told these
    breaks instead of wrapping on its own, so the kinetic text has exactly the
    footprint layout and the collision gate measured."""
    from PIL import Image, ImageDraw
    from . import captions
    stroke = captions._stroke_px(None)
    d = ImageDraw.Draw(Image.new("RGBA", (width, 10)))
    lines = captions._wrap(d, text, captions._font(size, role), width - 2 * stroke)
    return [ln.split() for ln in lines]


def page_html(text, size, width, height, dur, role="headline", fill=None,
              accent=None, hot=None, lines=None):
    """The line page. Every animation is declared paused; frames are taken
    by seeking document time (card._SEEK), so the render is deterministic."""
    from . import brandkit, captions
    words = text.split()
    lines = lines or [words]
    hi = hot_index(words, hot)
    settle, exit_t, stagger = timeline(len(words), dur, hi)
    face, stack = _font_face(role)
    fill = fill or captions.ink("#FCFCFB")
    accent = accent or brandkit.accent()
    spans = []
    flat = [w for ln in lines for w in ln]
    if len(flat) != len(words):
        lines, flat = [words], words
    breaks, acc = set(), 0
    for ln in lines[:-1]:
        acc += len(ln)
        breaks.add(acc)          # a line starts at this word index
    for i, w in enumerate(words):
        if i in breaks:
            spans.append("<br>")
        delay = i * stagger
        anim = f"kin-in {ENTER:.3f}s {EASE_TYPE} {delay:.3f}s both"
        inner = html.escape(w)
        if i == hi:
            pop_delay = delay + ENTER + POP_AT
            # Grow toward the inside of the line: a word at a line end that
            # scaled from its centre would push past the safe edge.
            starts = i == 0 or i in breaks
            ends = i == len(words) - 1 or (i + 1) in breaks
            origin = ("50%" if starts and ends else "20%" if starts
                      else "80%" if ends else "50%")
            inner = (f'<span class="pop" style="transform-origin:{origin} 75%;'
                     f'animation:kin-pop {POP:.3f}s {EASE_POP} {pop_delay:.3f}s '
                     f'both">{inner}</span>')
        spans.append(f'<span class="w{" hot" if i == hi else ""}" '
                     f'style="animation:{anim}">{inner}</span>')
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
{face}
*{{margin:0;padding:0;box-sizing:border-box}}
html,body{{width:{width}px;height:{height}px;background:transparent;overflow:hidden}}
*,*::before,*::after{{animation-play-state:paused!important}}
.line{{position:absolute;left:{PAD}px;right:{PAD}px;top:{PAD}px;text-align:center;
  white-space:nowrap;
  font-family:{stack};font-weight:800;font-size:{size}px;line-height:1.22;
  color:{fill};-webkit-text-stroke:2px rgba(0,0,0,.9);paint-order:stroke fill;
  text-shadow:0 7px 12px rgba(0,0,0,.85);
  animation:kin-out {EXIT:.3f}s {EASE_OUT} {exit_t:.3f}s both}}
.w{{display:inline-block;will-change:transform,opacity,filter}}
.hot{{color:{accent}}}
.pop{{display:inline-block}}
@keyframes kin-in{{from{{opacity:0;transform:translateY(.34em);filter:blur(8px)}}
  to{{opacity:1;transform:none;filter:blur(0)}}}}
@keyframes kin-pop{{0%{{transform:scale(1)}}45%{{transform:scale({POP_SCALE})}}
  100%{{transform:scale(1)}}}}
@keyframes kin-out{{from{{opacity:1;transform:none}}
  to{{opacity:0;transform:translateY(-.22em)}}}}
</style></head><body><div class="line">{' '.join(spans).replace(' <br> ', '<br>')}</div>
<script>/* keeps the page alive for the seek */</script></body></html>"""


async def _render(page, frames, width, height, out_dir, still):
    """Capture `frames` [(n, t)]; frames inside the still hold reuse one
    capture (hard link)."""
    from . import card
    async with card._Browser(os.path.join(out_dir, ".profile")) as b:
        await b("Emulation.setDeviceMetricsOverride",
                {"width": width, "height": height, "deviceScaleFactor": 1,
                 "mobile": False})
        await b("Emulation.setDefaultBackgroundColorOverride",
                {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        await b.load("file://" + page)
        held = None
        for n, t in frames:
            dst = os.path.join(out_dir, f"k{n:05d}.png")
            if still and still[0] <= t < still[1]:
                if held is None:
                    await b.shoot(still[0], dst)
                    held = dst
                else:
                    try:
                        os.link(held, dst)
                    except OSError:
                        shutil.copyfile(held, dst)
                continue
            await b.shoot(t, dst)


def render_line(text, out_dir, dur, size, width, role="headline", hot=None,
                fps=FPS):
    """Render one kinetic line to out_dir/k%05d.png covering local 0..dur.

    Returns (pattern, canvas_w, canvas_h, pad): the PNG sequence pattern for
    ffmpeg, its size, and the padding around the text box (the caller places
    the canvas `pad` px up and left of where the static PNG would sit)."""
    from . import captions
    os.makedirs(out_dir, exist_ok=True)
    words = text.split()
    hi = hot_index(words, hot)
    settle, exit_t, _ = timeline(len(words), dur, hi)
    text_h = captions.block_height(text, width=width, size=size, role=role)
    cw, ch = width + 2 * PAD, text_h + 2 * PAD
    page = os.path.join(out_dir, "line.html")
    with open(page, "w") as f:
        f.write(page_html(text, size, cw, ch, dur, role=role, hot=hi,
                          lines=wrap_lines(text, size, width, role)))
    n = max(1, int(round(dur * fps)))
    frames = [(i, i / fps) for i in range(n)]
    asyncio.run(_render(page, frames, cw, ch, out_dir,
                        (settle, exit_t) if exit_t > settle else None))
    shutil.rmtree(os.path.join(out_dir, ".profile"), ignore_errors=True)
    made = len([f for f in os.listdir(out_dir) if f.startswith("k") and f.endswith(".png")])
    if made < n:
        raise RuntimeError(f"kinetic: only {made}/{n} frames rendered for {text!r}")
    return os.path.join(out_dir, "k%05d.png"), cw, ch, PAD


def hook_line(text, out_dir, dur, hot=None):
    """The hook / timed line, kinetic: same box, size fitting and font role as
    captions.hook_png, so layout and the collision gate measure the same text."""
    from . import captions
    return render_line(text, out_dir, dur, size=captions.hook_size(text),
                       width=captions.HOOK_WIDTH, role=captions.HOOK_ROLE,
                       hot=hot)


def is_sequence(path):
    return "%0" in os.path.basename(str(path))


def sequence_input(pattern, fps=FPS):
    """ffmpeg input args for a PNG sequence (alpha preserved)."""
    return ["-framerate", str(fps), "-i", pattern]


def probe_ok():
    """Cheap self-check used by tests and `setup`: Chrome can be launched."""
    try:
        r = subprocess.run([config.CHROME, "--version"], capture_output=True,
                           text=True, timeout=20)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError, TypeError):
        return False
