"""Photosensitive flash check (WCAG 2.3.1 "three flashes or below").

WHY
Beat flashes, light rings, impact bursts and glitch accents all add sudden
luminance changes; stacked on footage that already flashes, a cut can cross
the line where content may trigger seizures. Every deliverable is checked,
whatever made the flash, so no effect can ship one.

HOW (general-flash rule, simplified and conservative)
- Decode at the file's own frame rate, small (36x64), as luma; convert to
  relative luminance (sRGB curve).
- Measure per region: a 3x4 grid, each cell about 1/12 of the frame, close
  to WCAG's area threshold (25% of a 10-degree field, ~341x256 px of a
  1024x768 screen); a flash confined to less than that is let through.
- A transition is a swing of at least 0.10 relative luminance between local
  extremes where the darker side is below 0.80; a flash is two opposing
  transitions.
- FAIL when any region has more than 3 flashes in any one-second window.
The red-flash rule and EA IRIS-grade spatial analysis are not modelled.
"""
import subprocess

from . import config

W, H = 36, 64
GRID = (3, 4)            # columns x rows
SWING = 0.10
DARK_MAX = 0.80
MAX_PER_S = 3


def _frames(path):
    import numpy as np
    from . import media
    try:
        v = next(s for s in media.probe(path)["streams"] if s.get("codec_type") == "video")
        num, den = (v.get("r_frame_rate") or "30/1").split("/")
        fps = float(num) / max(1.0, float(den))
    except Exception:   # noqa: BLE001
        fps = 30.0
    r = subprocess.run([config.FFMPEG, "-v", "error", "-i", path, "-vf", f"scale={W}:{H}",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
    n = len(r.stdout) // (W * H)
    y = np.frombuffer(r.stdout[: n * W * H], np.uint8).reshape(n, H, W) / 255.0
    lin = np.where(y <= 0.04045, y / 12.92, ((y + 0.055) / 1.055) ** 2.4)
    return lin, fps


def _transitions(series):
    """Frame indices of qualifying transitions in one luminance series: a
    rise of SWING or more from the lowest point since the last transition
    (or a fall from the highest), with the darker side below DARK_MAX, in
    the opposite direction to the previous transition."""
    out, last = [], 0
    lo = hi = series[0]
    for i, v in enumerate(series[1:], start=1):
        lo, hi = min(lo, v), max(hi, v)
        if last != 1 and v - lo >= SWING and lo < DARK_MAX:
            out.append(i)
            last, lo, hi = 1, v, v
        elif last != -1 and hi - v >= SWING and v < DARK_MAX:
            out.append(i)
            last, lo, hi = -1, v, v
    return out


def check(path):
    """{max_flashes_per_s, worst_t, region} for a video file."""
    import numpy as np
    lin, fps = _frames(path)
    if len(lin) < 2:
        return {"max_flashes_per_s": 0.0, "worst_t": None, "region": None}
    cw, ch = W // GRID[0], H // GRID[1]
    worst = (0.0, None, None)
    win = max(1, int(round(fps)))
    for gy in range(GRID[1]):
        for gx in range(GRID[0]):
            series = lin[:, gy * ch:(gy + 1) * ch, gx * cw:(gx + 1) * cw].mean(axis=(1, 2))
            tr = np.array(_transitions(list(series)))
            if len(tr) < 2:
                continue
            for i, t in enumerate(tr):
                inwin = int(np.sum((tr >= t) & (tr < t + win)))
                flashes = inwin / 2.0
                if flashes > worst[0]:
                    worst = (flashes, round(t / fps, 2), (gx, gy))
    return {"max_flashes_per_s": worst[0], "worst_t": worst[1], "region": worst[2]}


def gate(path):
    r = check(path)
    ok = r["max_flashes_per_s"] <= MAX_PER_S
    detail = (f"max {r['max_flashes_per_s']:.1f} flashes in 1 s (limit {MAX_PER_S})"
              + (f" @{r['worst_t']}s region {r['region']}" if not ok else ""))
    return ("flashes", "PASS" if ok else "FAIL", detail)
