"""Golden frames: every effect rendered at fixed instants from fixed inputs
and compared with stored reference images, so a change that alters how an
effect LOOKS fails a test instead of shipping quietly.

The references are per renderer tier (GPU output is reproducible on one
machine, not bit-identical across GPUs or against the software renderer):
tests/golden/<tier>/<case>_<i>.png, where tier is a slug of the WebGL
renderer string. Cases whose tier has no references are skipped.

    python -m reelly.golden --update      # (re)write references for this machine
    python -m reelly.golden               # compare, print the worst differences
"""
import json
import os
import re
import sys
import tempfile

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "golden")
TOL = 4.0 / 255.0        # 99th-percentile difference allowed (same machine: ~0)


def _ctrl():
    return {"beats": [0.5, 0.48], "pops": [0.6], "onsets": [0.4, 1.1], "fps": 60,
            "energy": [0.2 + 0.6 * abs(np.sin(i / 9.0)) for i in range(120)],
            "events": [{"t": 0.5, "x": 0.3, "y": 0.2, "s": 1.0}]}


def cases(tmp):
    """{name: (html, w, h, times, premultiplied)} with every input fixed."""
    from PIL import Image
    from . import gputext, kinetic, shaderfx
    bg = os.path.join(tmp, "bg.png")
    im = Image.new("RGB", (540, 960))
    for y in range(960):
        for x in range(0, 540, 60):
            im.paste((40 + y // 12, 20, 70 + x // 6), (x, y, x + 60, y + 1))
    im.save(bg)
    pic, clear = (0.0, 0.34, 1.0, 0.66), (0.66, 0.8)
    t = "Curses bite back."
    lines = [t.split()]
    out = {
        "endcard": (shaderfx.page_html("smoke-endcard", bg, 270, 480, "#a87cf0", pulse_t=0.4,
                                       from_png=bg, ctrl=_ctrl()), 270, 480, (0.1, 0.5, 1.2), False),
        "smoke": (shaderfx.ambient_html("smoke-ambient", 270, 480, "#a87cf0", pic, clear, [0.6],
                                        end=2.0, ctrl=_ctrl()), 270, 480, (0.3, 1.0, 1.9), False),
        "embers": (shaderfx.ambient_html("embers-ambient", 270, 480, "#a87cf0", pic, clear, [0.6],
                                         end=2.0, ctrl=_ctrl()), 270, 480, (0.3, 0.45, 1.5), False),
        "impact": (shaderfx.ambient_html("impact-ambient", 540, 960, "#a87cf0", pic, clear, [],
                                         end=2.0, ctrl=_ctrl()), 540, 960, (0.53, 0.6, 0.7), False),
        "focus": (shaderfx.ambient_html("focus-ambient", 540, 960, "#a87cf0", pic, clear, [],
                                        end=2.0, ctrl=_ctrl()), 540, 960, (0.55, 0.7), False),
    }
    for motion in ("rise", "blocklock"):
        os.environ["REELLY_TEXT_MOTION"] = motion
        html, _, _, off = gputext.page(t, 64, 800, 180, 2.0, "headline", 1, lines, True, True,
                                       0.9, "#FCFCFB", "#a87cf0")
        out[f"text_{motion}"] = (html, 800 + 2 * off, 180 + 2 * off, (0.15, 0.5, 1.0), True)
    os.environ["REELLY_TEXT_EXIT"] = "petals"
    html, _, _, off = gputext.page(t, 64, 800, 180, 2.0, "headline", 1, lines, True, True,
                                   0.9, "#FCFCFB", "#a87cf0", aura="smoke-aura")
    out["text_aura_petals"] = (html, 800 + 2 * off, 180 + 2 * off, (0.5, 1.45, 1.7), False)
    for k in ("REELLY_TEXT_MOTION", "REELLY_TEXT_EXIT"):
        os.environ.pop(k, None)
    return out


def _grab(html, w, h, times, tmp, name):
    """Frames at `times` as float RGBA arrays (straight alpha, top-down)."""
    from . import shaderfx
    d = os.path.join(tmp, name)
    os.makedirs(d, exist_ok=True)
    fps = 60
    dur = max(times) + 2.0 / fps
    shaderfx.render_frames(html, d, w, h, dur, fps)
    out = []
    from PIL import Image
    for t in times:
        im = Image.open(os.path.join(d, f"f{int(round(t * fps)):05d}.tga")).convert("RGBA")
        out.append(np.asarray(im).astype(np.float32) / 255.0)
    return out


def tier():
    from . import shaderfx
    r = shaderfx._RENDERER[0] or "unknown"
    return re.sub(r"[^a-z0-9]+", "-", r.lower()).strip("-")[:60]


_NEUTRAL = ("REELLY_HOME", "REELLY_BRAND", "REELLY_TEXT_MOTION", "REELLY_TEXT_EXIT",
            "REELLY_PROFILE", "REELLY_FX_LITE", "REELLY_OUTRO_FX", "REELLY_AMBIENT_FX",
            "REELLY_TEXT_FX", "REELLY_FINISH_FX")


def run(update=False, only=None):
    """Compare (or with update=True, write) every case. Returns
    {case: worst mean abs diff} (None when no reference exists).

    Always in a NEUTRAL environment: no brand kit (system fallback font,
    default colours) and no effect overrides, so the references do not
    depend on whose machine or which brand ran them."""
    saved = {k: os.environ.get(k) for k in _NEUTRAL}
    tmp = tempfile.mkdtemp(prefix="reelly-golden-")
    for k in _NEUTRAL:
        os.environ.pop(k, None)
    os.environ["REELLY_HOME"] = os.path.join(tmp, "home")
    try:
        return _run(update, only, tmp)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _run(update, only, tmp):
    from PIL import Image
    results = {}
    for name, (html, w, h, times, _pm) in cases(tmp).items():
        if only and name not in only:
            continue
        frames = _grab(html, w, h, times, tmp, name)
        d = os.path.join(ROOT, tier())
        worst = None
        for i, fr in enumerate(frames):
            ref = os.path.join(d, f"{name}_{i}.png")
            if update:
                os.makedirs(d, exist_ok=True)
                Image.fromarray((fr * 255 + 0.5).astype(np.uint8), "RGBA").save(ref)
                continue
            if not os.path.exists(ref):
                continue
            r = np.asarray(Image.open(ref).convert("RGBA")).astype(np.float32) / 255.0
            if r.shape != fr.shape:
                diff = 1.0
            else:
                # only where the effect draws: averaging over a mostly
                # transparent layer diluted a real change to nothing
                live = np.maximum(r[..., 3], fr[..., 3]) > 0.01
                ad = np.abs(r - fr).max(axis=2)
                # the 99th percentile over drawn pixels: a mean let wide, faint
                # edges dilute a clear change in the body of the effect
                diff = float(np.percentile(ad[live] if live.any() else ad, 99))
            worst = diff if worst is None else max(worst, diff)
        results[name] = worst
    return results


if __name__ == "__main__":
    upd = "--update" in sys.argv
    res = run(update=upd)
    print("tier:", tier())
    for k, v in res.items():
        print(f"  {k:<18} {'written' if upd else ('no reference' if v is None else f'{v:.4f}')}")
    if not upd and any(v is not None and v > TOL for v in res.values()):
        sys.exit(1)
