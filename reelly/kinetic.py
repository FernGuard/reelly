"""Kinetic type for the burned-in text layer: the hook and the timed lines.

WHY
The static layer is a Pillow PNG switched on and off by ffmpeg: no entrance,
no emphasis, no exit. It reads as a slideshow. card.py already showed the way
out (draw the type in a browser with CSS motion, photograph it frame by frame)
but only for opaque full-screen cards. This renders ONE line of text on a
TRANSPARENT canvas, as a PNG sequence that finalize composites exactly where
the static PNG used to sit.

THE MOTION (one line, local time 0..dur)
- A line that opens the video (the hook, t = 0) is FULLY READABLE ON FRAME 0:
  no entrance. Feeds autoplay muted and show the first frame while the viewer
  decides, and Shorts loop back to it, so the words must already be there.
  Its only motion is the hot word's pop a beat later.
- Later lines enter in reading order, staggered: rise a third of an em,
  unblur and fade in on the type curve. Never all at once.
- The hot word takes the brand accent and lands with one small scale pop.
- Then everything HOLDS, still, so it can be read. The hold is one capture
  reused for every frame (no re-render of an unchanged page).
- The line leaves on a short ease-out lift and fade that ends at `dur`
  (the end-card CTA does not leave: it holds to the last frame).

WHICH WORD IS HOT (choose_hot; the reason is logged on every render)
1. The plan's `hot` (word or index), with its `hot_reason`: an author or the
   editor brain decided.
2. A call to action: its ACTION, the first content word ("Wishlist on
   Steam"). That is the one thing the viewer is asked to do.
3. Any other line: END FOCUS, the last CONTENT word. English puts the new
   information at the end; function words (is, are, they, the, of...) never
   carry it. "Someone is knocking." -> knocking; "Of course they are." ->
   course.

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

STAGGER = 0.07          # s between word entrances
ENTER = 0.46            # s for one word to arrive
POP_AT, POP = 0.30, 0.36  # hot word: pop starts this long after it lands, lasts POP
# The pop is a LIFT, not a big scale: a word scaled 12% grows wider than the
# word spaces around it and swallows them. Up a little, 6% bigger, back down.
# It grows from its bottom centre, so the growth splits evenly into the two
# word spaces (and a line-end word moves at most ~2% of its width outward).
POP_SCALE = 1.04
POP_LIFT = 0.14           # em
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


# Function words: grammar, not meaning. They never take the accent.
FUNCTION_WORDS = frozenset("""
a an the of to in on at by for from with as into onto over under up down out
off about after before than then so and or but if because while that this
these those it its it's is am are was were be been being have has had do does
did done will would can could should may might must shall i i'm you you're he
she we they they're me him her us them my your his our their mine yours there
here what who whom which when where how just very really too also quite some
any one ones such
""".split())
_PUNCT = ".,!?;:\"'()[]{}…"


def _bare(word):
    return word.lower().strip(_PUNCT)


def _content(words):
    return [i for i, w in enumerate(words)
            if _bare(w) and _bare(w) not in FUNCTION_WORDS]


def choose_hot(words, hot=None, kind="line", reason=None):
    """(index, reason) of the word that carries the accent and the pop.

    See the module doc for the rules. `kind` is "line" or "cta"."""
    if not words:
        return None, "no words"
    if isinstance(hot, int) and not isinstance(hot, bool) and 0 <= hot < len(words):
        return hot, reason or "set in the plan"
    if isinstance(hot, str) and hot.strip():
        want = _bare(hot)
        for i, w in enumerate(words):
            if _bare(w) == want:
                return i, reason or "set in the plan"
    content = _content(words)
    if kind == "cta":
        if content:
            return content[0], "call to action: the action the viewer takes"
        return 0, "call to action: first word (no content word)"
    if content:
        return content[-1], "end focus: the last content word carries the news"
    return len(words) - 1, "end focus: last word (no content word)"


def hot_index(words, hot=None, kind="line"):
    """Index-only form of choose_hot."""
    return choose_hot(words, hot, kind)[0]


def timeline(n_words, dur, hot=None, enter=True, leave=True):
    """(settle_t, exit_t, stagger): when the last motion before the hold
    ends, when the exit starts (dur when the line does not leave) and the word
    stagger. Entrances are squeezed if a short window cannot fit them, so a
    line never exits before it has fully arrived. Without an entrance the
    words are there at t = 0 and the hot word pops at POP_AT."""
    exit_t = max(0.0, dur - EXIT) if leave else dur
    if not enter:
        settle = POP_AT + POP if hot is not None else 0.0
        return min(settle, exit_t), exit_t, 0.0
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
    """The line breaks the static layer would use (captions.wrap_balanced
    with the same font, size and box: even lines, no widow), as lists of
    words. The browser is told these
    breaks instead of wrapping on its own, so the kinetic text has exactly the
    footprint layout and the collision gate measured."""
    from PIL import Image, ImageDraw
    from . import captions
    stroke = captions._stroke_px(None)
    d = ImageDraw.Draw(Image.new("RGBA", (width, 10)))
    lines = captions.wrap_balanced(d, text, captions._font(size, role),
                                   width - 2 * stroke)
    return [ln.split() for ln in lines]


def page_html(text, size, width, height, dur, role="headline", fill=None,
              accent=None, hot=None, lines=None, enter=True, leave=True):
    """The line page. Every animation is declared paused; frames are taken
    by seeking document time (card._SEEK), so the render is deterministic."""
    from . import brandkit, captions
    words = text.split()
    lines = lines or [words]
    hi = hot_index(words, hot)
    settle, exit_t, stagger = timeline(len(words), dur, hi, enter, leave)
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
        anim = (f"kin-in {ENTER:.3f}s {EASE_TYPE} {delay:.3f}s both" if enter
                else "kin-still 1s linear 0s both")
        inner = html.escape(w)
        if i == hi:
            pop_delay = (delay + ENTER + POP_AT) if enter else POP_AT
            inner = (f'<span class="pop" style="transform-origin:50% 100%;'
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
  {f"animation:kin-out {EXIT:.3f}s {EASE_OUT} {exit_t:.3f}s both" if leave else ""}}}
.w{{display:inline-block;will-change:transform,opacity,filter}}
.hot{{color:{accent}}}
.pop{{display:inline-block}}
@keyframes kin-in{{from{{opacity:0;transform:translateY(.34em);filter:blur(8px)}}
  to{{opacity:1;transform:none;filter:blur(0)}}}}
@keyframes kin-pop{{0%{{transform:none}}
  45%{{transform:translateY(-{POP_LIFT}em) scale({POP_SCALE})}}
  100%{{transform:none}}}}
@keyframes kin-still{{from{{opacity:1}}to{{opacity:1}}}}
@keyframes kin-out{{from{{opacity:1;transform:none}}
  to{{opacity:0;transform:translateY(-.22em)}}}}
</style></head><body><div class="line">{' '.join(spans).replace(' <br> ', '<br>')}</div>
<script>/* keeps the page alive for the seek */</script></body></html>"""


async def _render(page, frames, width, height, out_dir, still, profile):
    """Capture `frames` [(n, t)]; frames inside the still hold reuse one
    capture (hard link)."""
    from . import card
    async with card._Browser(profile) as b:
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
                fps=None, enter=True, leave=True, kind="line", hot_reason=None,
                label=None):
    """Render one kinetic line to out_dir/k%05d.png covering local 0..dur.

    Returns (pattern, canvas_w, canvas_h, pad): the PNG sequence pattern for
    ffmpeg, its size, and the padding around the text box (the caller places
    the canvas `pad` px up and left of where the static PNG would sit)."""
    from . import captions
    os.makedirs(out_dir, exist_ok=True)
    words = text.split()
    hi, why = choose_hot(words, hot, kind, hot_reason)
    if hi is not None:
        print(f"        kinetic {label or kind}: hot '{_bare(words[hi])}' ({why})"
              + ("" if enter else "; readable on frame 0"))
    settle, exit_t, _ = timeline(len(words), dur, hi, enter, leave)
    text_h = captions.block_height(text, width=width, size=size, role=role)
    cw, ch = width + 2 * PAD, text_h + 2 * PAD
    page = os.path.join(out_dir, "line.html")
    with open(page, "w") as f:
        f.write(page_html(text, size, cw, ch, dur, role=role, hot=hi,
                          lines=wrap_lines(text, size, width, role),
                          enter=enter, leave=leave))
    fps = fps or config.output_fps()
    n = max(1, int(round(dur * fps)))
    frames = [(i, i / fps) for i in range(n)]
    hold_end = exit_t if leave else dur + 1.0
    # The browser profile lives OUTSIDE out_dir and is removed best-effort: a
    # lingering Chrome process can hold its files (Windows), and that must
    # never fail the caller's own work-folder cleanup.
    import tempfile
    profile = tempfile.mkdtemp(prefix="reelly-chrome-")
    try:
        asyncio.run(_render(page, frames, cw, ch, out_dir,
                            (settle, hold_end) if hold_end > settle else None,
                            profile))
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    made = len([f for f in os.listdir(out_dir) if f.startswith("k") and f.endswith(".png")])
    if made < n:
        raise RuntimeError(f"kinetic: only {made}/{n} frames rendered for {text!r}")
    return os.path.join(out_dir, "k%05d.png"), cw, ch, PAD


def hook_line(text, out_dir, dur, hot=None, enter=True, hot_reason=None,
              label=None):
    """The hook / timed line, kinetic: same box, size fitting and font role as
    captions.hook_png, so layout and the collision gate measure the same text.
    enter=False for a line on screen at t = 0 (readable on frame 0)."""
    from . import captions
    return render_line(text, out_dir, dur, size=captions.hook_size(text),
                       width=captions.HOOK_WIDTH, role=captions.HOOK_ROLE,
                       hot=hot, enter=enter, hot_reason=hot_reason, label=label)


def cta_line(text, out_dir, dur, hot=None, hot_reason=None, y=902):
    """The end-card call to action: words enter, the ACTION word pops in the
    accent, and the line holds to the last frame (it never leaves). Its box is
    the centred width the layout allows at its height (beside the rail)."""
    from . import captions, layout
    width = layout.centered_width(y, y + 120)
    return render_line(text, out_dir, dur, size=captions.hook_size(text, width),
                       width=width, role=captions.HOOK_ROLE,
                       hot=hot, enter=True, leave=False, kind="cta",
                       hot_reason=hot_reason, label="cta")


def is_sequence(path):
    return "%0" in os.path.basename(str(path))


def sequence_input(pattern, fps=None):
    """ffmpeg input args for a PNG sequence (alpha preserved)."""
    return ["-framerate", str(fps or config.output_fps()), "-i", pattern]


def probe_ok():
    """Cheap self-check used by tests and `setup`: a Chrome binary exists.
    (`chrome --version` prints nothing on Windows, so it is not a test.)"""
    ch = config.CHROME
    return bool(ch) and (os.path.isfile(ch) or bool(shutil.which(ch)))
