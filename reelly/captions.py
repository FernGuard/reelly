"""Caption and hook text rendered to PNG with Pillow (this ffmpeg build has
no libass/drawtext, so text is burned as overlays; proven approach)."""
import os

from PIL import Image, ImageDraw, ImageFont

FONTS = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/SFNS.ttf",
]

# "Dynamic minimalism" (the 2026 taste bar): clean sans, white text with ONE
# brand accent on the highlighted word, mixed case, smooth fades -- NOT loud
# multi-color karaoke. This layer is consumed by karaoke_png/hook_png/text_png
# without changing their signatures, so finalize/burnin work unchanged.
DEFAULT_STYLE = {
    # brand font from the kit (brandkit.font); None -> the FONTS fallback,
    # i.e. exactly today's system fonts when no kit is installed
    "font_role": "caption",
    # white body + a single accent; the accent is resolved from kit.json
    # (sampled from the managed account wordmark at kit build) when not passed explicitly
    "fill": "white",
    "accent": None,
    # mixed case: ASR words are rendered as spoken, never upper()-coerced
    # (verified: no caller coerces case; this flag pins the contract)
    "mixed_case": True,
    # highlight is a color swap ONLY -- no scale-pop on the spoken word
    "highlight": "color",
    # events built from these PNGs may opacity-fade in/out; finalize's
    # existing event timing can key off this flag later
    "fade": True,
    # THIN outline + soft shadow, not the old 6px black slab (external taste
    # audit: the heavy stroke read dated). ~40% of the old width; legibility
    # on busy footage comes from the blurred drop shadow underneath, which
    # separates text from detail without boxing every glyph in black.
    # Callers that pass an explicit stroke_w still get exactly what they ask.
    "stroke_px": 2,
    "shadow": {"offset": (0, 7), "blur": 12, "alpha": 0.85},
}


def _stroke_px(explicit=None):
    """Stroke width: an explicit arg wins (old call sites keep their look),
    else the style's thin default."""
    if explicit is not None:
        return int(explicit)
    return int(DEFAULT_STYLE.get("stroke_px", 2))


def _shadow_spec():
    sh = DEFAULT_STYLE.get("shadow")
    return sh if isinstance(sh, dict) else None


def _shadow_pad():
    """Extra canvas height so the blurred shadow is not clipped."""
    sh = _shadow_spec()
    if not sh:
        return 0
    return int(sh.get("offset", (0, 4))[1]) + int(sh.get("blur", 6))


def _with_shadow(img, draw_text):
    """Composite a soft drop shadow under the text about to be drawn.

    draw_text(d, dx, dy, fill_override) draws the full text run into d at an
    offset; it is called once here for the dark blurred layer and once by the
    caller for the crisp top layer. Low alpha + blur: separation on busy
    footage without the old slab outline."""
    sh = _shadow_spec()
    if not sh:
        return img
    from PIL import ImageFilter
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dx, dy = sh.get("offset", (0, 4))
    draw_text(ImageDraw.Draw(layer), int(dx), int(dy), (0, 0, 0, 255))
    layer = layer.filter(ImageFilter.GaussianBlur(int(sh.get("blur", 6))))
    alpha = max(0.0, min(1.0, float(sh.get("alpha", 0.45))))
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * alpha)))
    return Image.alpha_composite(img, layer)


def _style_accent(explicit=None):
    """The ONE accent color: an explicit arg wins, then the brand kit's
    sampled accent, then the historical Blue Smoke hardcode."""
    if explicit:
        return explicit
    if DEFAULT_STYLE.get("accent"):
        return DEFAULT_STYLE["accent"]
    try:
        from . import brandkit
        return brandkit.accent()
    except Exception:
        return "#17CDFF"


def _font_paths():
    """Kit font first (graceful: brandkit returns None without a kit), then
    the system fallbacks -- pre-kit behavior is byte-identical."""
    try:
        from . import brandkit
        kit_font = brandkit.font(DEFAULT_STYLE.get("font_role", "caption"))
    except Exception:
        kit_font = None
    return ([kit_font] if kit_font else []) + FONTS


def _font(size):
    for p in _font_paths():
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    lines, cur = [], []
    for w in text.split():
        trial = " ".join(cur + [w])
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines


def text_png(text, path, width=1000, size=58, fill="white", stroke="black",
             stroke_w=None, align="center"):
    """Render wrapped text to a tight transparent PNG.

    stroke_w=None -> DEFAULT_STYLE["stroke_px"] (thin outline) + the style's
    soft shadow; an explicit stroke_w is honored exactly as passed."""
    stroke_w = _stroke_px(stroke_w)
    font = _font(size)
    probe = Image.new("RGBA", (width, 10))
    d = ImageDraw.Draw(probe)
    lines = _wrap(d, text, font, width - 2 * stroke_w)
    lh = int(size * 1.22)
    h = lh * len(lines) + 2 * stroke_w + 8 + _shadow_pad()
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))

    def _draw(d, dx=0, dy=0, override=None):
        for i, ln in enumerate(lines):
            tw = d.textlength(ln, font=font)
            x = (width - tw) / 2 if align == "center" else stroke_w
            d.text((x + dx, stroke_w + i * lh + dy), ln, font=font,
                   fill=override or fill, stroke_width=stroke_w,
                   stroke_fill=override or stroke)

    img = _with_shadow(img, lambda d, dx, dy, c: _draw(d, dx, dy, c))
    _draw(ImageDraw.Draw(img))
    img.save(path)
    return path


def cue_png(text, path):
    return text_png(text, path, width=960, size=56)


def block_height(text, width=1000, size=58, stroke_w=None):
    """Rendered height of a text block, using the SAME wrap as text_png.

    The collision gate needs the real footprint of each burned layer, not a
    guess: a one-line hook and a three-line hook occupy very different bands.
    """
    stroke_w = _stroke_px(stroke_w)
    font = _font(size)
    probe = Image.new("RGBA", (width, 10))
    d = ImageDraw.Draw(probe)
    lines = _wrap(d, text, font, width - 2 * stroke_w)
    lh = int(size * 1.22)
    return lh * len(lines) + 2 * stroke_w + 8 + _shadow_pad()


def circle_mask(path, size=512, feather=3):
    """Soft-edged circular alpha mask for the facecam insert."""
    img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(img)
    d.ellipse([feather, feather, size - feather, size - feather], fill=255)
    from PIL import ImageFilter
    img = img.filter(ImageFilter.GaussianBlur(feather))
    img.save(path)
    return path


def rounded_mask(path, size=512, radius=72, feather=3):
    """Rounded-rectangle alpha mask, the other common cam-insert shape."""
    img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([feather, feather, size - feather, size - feather],
                        radius=radius, fill=255)
    from PIL import ImageFilter
    img = img.filter(ImageFilter.GaussianBlur(feather))
    img.save(path)
    return path


def cam_mask(shape, path, size=512):
    return rounded_mask(path, size) if shape == "rounded" else circle_mask(path, size)


# The karaoke word-highlight color (reviewer 2026-08-13): Hot Pink, a fixed
# caption signature rather than the per-studio brand accent. Env-tunable.
KARAOKE_HIGHLIGHT = os.environ.get("REELLY_KARAOKE_COLOR", "#FF69B4")


def karaoke_png(word_texts, hi_index, path, width=960, size=56,
                accent=None, fill="white", stroke="black", stroke_w=None):
    """Cue text with the currently spoken word highlighted in Hot Pink (M4
    word-highlight captions, the researched short-form norm).

    Dynamic minimalism: white body, ONE highlight color (Hot Pink, the caption
    signature -- reviewer 2026-08-13), color-swap highlight only -- no scale pop
    -- and mixed case exactly as spoken. accent=None means the karaoke highlight
    (Hot Pink); pass an explicit accent to override. stroke_w=None means the
    style's thin outline + soft shadow (the old 6px slab read dated)."""
    accent = accent or KARAOKE_HIGHLIGHT
    stroke_w = _stroke_px(stroke_w)
    font = _font(size)
    probe = Image.new("RGBA", (width, 10))
    d = ImageDraw.Draw(probe)
    # wrap words preserving indices
    lines, cur = [], []
    for i, w in enumerate(word_texts):
        trial = " ".join(t for _, t in cur + [(i, w)])
        if d.textlength(trial, font=font) <= width - 2 * stroke_w or not cur:
            cur.append((i, w))
        else:
            lines.append(cur)
            cur = [(i, w)]
    if cur:
        lines.append(cur)
    lh = int(size * 1.22)
    h = lh * len(lines) + 2 * stroke_w + 8 + _shadow_pad()
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    space = ImageDraw.Draw(probe).textlength(" ", font=font)

    def _draw(d, dx=0, dy=0, override=None):
        for li, line in enumerate(lines):
            total = (sum(d.textlength(t, font=font) for _, t in line)
                     + space * (len(line) - 1))
            x = (width - total) / 2
            for i, t in line:
                d.text((x + dx, stroke_w + li * lh + dy), t, font=font,
                       fill=override or (accent if i == hi_index else fill),
                       stroke_width=stroke_w, stroke_fill=override or stroke)
                x += d.textlength(t, font=font) + space

    img = _with_shadow(img, lambda d, dx, dy, c: _draw(d, dx, dy, c))
    _draw(ImageDraw.Draw(img))
    img.save(path)
    return path


def full_frame_overlay(text, path, kind="cue", canvas=(1080, 1920)):
    """Transparent full-frame PNG with the text placed exactly where the
    preview burner puts it, so Resolve timelines match the previews."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tp = os.path.join(td, "t.png")
        if kind == "hook":
            hook_png(text, tp)
            y = 40
        else:
            cue_png(text, tp)
            y = 820
        t = Image.open(tp)
        img = Image.new("RGBA", canvas, (0, 0, 0, 0))
        img.paste(t, ((canvas[0] - t.width) // 2, y), t)
        img.save(path)
    return path


def hook_png(text, path):
    return text_png(text, path, width=980, size=74, fill="#FCFCFB")


def scrim_png(path, alpha=0.80, w=1080, h=1920):
    """Full-frame dim pass for the closing card.

    Composited before any text so captions and cues sit above it, undimmed:
    the scrim exists to control what the CARD reads against, not to mute the
    copy that is still doing its job.
    """
    from PIL import Image
    Image.new("RGBA", (w, h), (9, 12, 10, int(max(0.0, min(1.0, alpha)) * 255))).save(path)
    return path
