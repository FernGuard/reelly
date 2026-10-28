"""Measured legibility for the burned text of social cuts (playbook D3/D7).

WHY
The hook, the timed lines and the game-name label are burned over whatever
the footage shows at that moment. A thin stroke and a soft shadow carry most
frames, but on a bright flash, a white UI panel or a busy swirl the brand ink
stops separating from the picture. design.contrast_gate measured exactly this
for motion posts; the social text was never measured.

HOW
For each text layer (same geometry the judge uses: judge._text_layers), sample
the PRE-CAPTION cut at the start, middle and end of the layer's window, crop
the text box, and run design.contrast_gate with the brand ink against it. The
worst sample decides. When the ink does not separate, the smallest plate that
reaches a clean luma gap goes UNDER that layer for its window: a rounded panel
in the brand surface colour (D3: an anchor for type that floats over busy
pixels). Only where needed; calm frames keep the bare type.

Results land in qc/text_contrast.json, which judge's text_contrast gate reads.
REELLY_LEGIBILITY=off skips the pass.
"""
import json
import os
import subprocess

from . import config

PAD_X, PAD_Y, RADIUS = 28, 12, 22
SAMPLES = (0.15, 0.5, 0.85)       # where in each layer's window to look


def enabled():
    return os.environ.get("REELLY_LEGIBILITY", "").strip().lower() not in ("off", "0", "no")


def _hex_rgb(h, default=(11, 11, 12)):
    try:
        h = h.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (AttributeError, ValueError):
        return default


def _frame(video, t, cache):
    """The pre-caption cut's frame at t (PIL RGB), cached per time."""
    from PIL import Image
    key = round(t, 2)
    if key not in cache:
        r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", f"{max(0.0, t):.3f}",
                            "-i", video, "-frames:v", "1", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], capture_output=True)
        cache[key] = (Image.frombytes("RGB", (1080, 1920), r.stdout)
                      if len(r.stdout) == 1080 * 1920 * 3 else None)
    return cache[key]


def measure(plan, video, ink_hex):
    """Per text layer: {label, t0, t1, box, luma_gap, scrim, pass}. The box
    is the drawn text (x0, y0, x1, y1) on the 1080x1920 frame."""
    from PIL import Image
    from . import captions, design, judge
    ink = Image.new("RGB", (16, 16), _hex_rgb(ink_hex, (252, 252, 251)))
    shadow = captions._shadow_pad()
    out, cache = [], {}
    for row in judge._text_layers(plan, [], with_x=True):
        t0, t1, y0, y1, label, x0, x1 = row
        if label.startswith("cue@"):
            continue                       # karaoke cues: not measured here (yet)
        box = (max(0, x0 - 6), max(0, y0), min(1080, x1 + 6),
               min(1920, max(y0 + 8, y1 - shadow)))
        worst = None
        for k in SAMPLES:
            fr = _frame(video, t0 + (t1 - t0) * k, cache)
            if fr is None:
                continue
            v = design.contrast_gate(ink, fr.crop(box))
            if worst is None or (v["scrim"], -v["luma_gap"]) > (worst["scrim"], -worst["luma_gap"]):
                worst = v
        if worst is None:
            continue
        out.append({"label": label, "t0": round(t0, 2), "t1": round(t1, 2),
                    "box": list(box), "luma_gap": worst["luma_gap"],
                    "backdrop_luma": worst["backdrop_luma"],
                    "scrim": worst["scrim"] if worst["escalated"] else 0.0,
                    "pass": bool(worst["pass"])})
    return out


def plate_png(w, h, rgb, alpha, path):
    """A rounded plate in the brand surface colour at `alpha`."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=RADIUS,
        fill=(*rgb, int(round(255 * max(0.0, min(1.0, alpha))))))
    img.save(path)
    return path


def plates(results, workdir, surface_hex):
    """Burn events (png, y, t0, t1) for every layer that needs a plate.
    Plates are centred like the text layers, so x is (W-w)/2 in the burn."""
    rgb = _hex_rgb(surface_hex)
    ev = []
    for i, r in enumerate(results):
        if r["scrim"] <= 0:
            continue
        x0, y0, x1, y1 = r["box"]
        half = max(540 - x0, x1 - 540) + PAD_X      # symmetric about the centre
        w = int(min(1080, 2 * half)) // 2 * 2
        h = int(y1 - y0 + 2 * PAD_Y)
        p = plate_png(w, h, rgb, r["scrim"], os.path.join(workdir, f"plate{i}.png"))
        ev.append((p, int(y0 - PAD_Y), r["t0"], r["t1"]))
    return ev


def record(root, cut_id, results):
    """qc/text_contrast.json: {cut_id: [results]} for judge's gate."""
    qc = os.path.join(root, "qc")
    os.makedirs(qc, exist_ok=True)
    p = os.path.join(qc, "text_contrast.json")
    try:
        data = json.load(open(p))
    except (OSError, ValueError):
        data = {}
    data[cut_id] = results
    json.dump(data, open(p, "w"), indent=1)
    return p
