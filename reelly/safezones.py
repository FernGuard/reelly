"""Draw the platform chrome (layout.UI_ZONES) over real frames of a render, so
a human can SEE that no text sits under the TikTok / Reels / Shorts header,
caption block or action rail. `reelly safezones <video> --at 0.5,5,9`."""
import os
import subprocess
import tempfile

from . import config, layout

ZONE_RGBA = (230, 40, 40, 90)
EDGE_RGB = (255, 70, 70)


def _frame(video, t, path):
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-ss", f"{t:.3f}",
                    "-i", video, "-frames:v", "1", "-vf", "scale=1080:1920",
                    path], check=True)
    return path


def overlay(png_in, png_out, label=None):
    """One frame with every UI zone shaded and named."""
    from PIL import Image, ImageDraw
    from . import captions
    base = Image.open(png_in).convert("RGBA")
    shade = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shade)
    font = captions._font(30, "caption")
    for name, (x0, y0, x1, y1) in layout.UI_ZONES.items():
        d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=ZONE_RGBA, outline=EDGE_RGB, width=3)
        if x1 - x0 > 150:
            d.text((x0 + 14, y0 + 8), name, font=font, fill=(255, 255, 255, 230))
    if label:
        d.text((24, 1920 - 60), label, font=font, fill=(255, 255, 255, 255))
    Image.alpha_composite(base, shade).convert("RGB").save(png_out)
    return png_out


def sheet(video, times, out, scale=0.5):
    """Frames at `times` with the zones drawn, side by side, scaled."""
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        tiles = []
        for i, t in enumerate(times):
            raw = _frame(video, t, os.path.join(td, f"f{i}.png"))
            tiles.append(Image.open(overlay(raw, os.path.join(td, f"z{i}.png"),
                                            label=f"t = {t:g}s")))
        w, h = int(1080 * scale), int(1920 * scale)
        img = Image.new("RGB", (w * len(tiles), h))
        for i, tile in enumerate(tiles):
            img.paste(tile.resize((w, h), Image.LANCZOS), (i * w, 0))
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        img.save(out)
    return out
