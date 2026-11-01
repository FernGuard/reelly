"""Follow the action: a 9:16 crop path through 16:9 gameplay.

WHY
The reframe scaled the picture and cropped it at the CENTRE, always. On a
phone that leaves the gameplay small and, worse, frames whatever happens to
be in the middle, not what is moving. This measures where the picture
changes and steers the crop there, smoothly, and picks how far it can zoom
from how spread out the action is.

THE METHOD (frame difference, after auto-editor's motion detector:
https://github.com/WyattBlue/auto-editor, Unlicense)
- Decode the segment at ANALYSIS_FPS, scaled to ANALYSIS_W wide, grayscale,
  lightly blurred so noise and grain do not count as motion.
- Per frame pair, the absolute difference per pixel; pixels over a small
  threshold count as moving. Summed per COLUMN that is a horizontal motion
  profile.
- ZOOM: over the whole segment, the narrowest band of columns holding
  SPREAD_SHARE of all motion. Concentrated action (one character) can zoom
  in, up to MAX_ZOOM; spread action (two boards, a wide fight) stays wide.
  Never below the plan's own reframe zoom (the old behaviour).
- PATH: per frame, the motion-weighted centre column; a stretch with too
  little motion holds the last target. Then a rolling median, a dead zone
  (the crop does not move for small shifts) and a speed limit, so the crop
  glides instead of jittering. Keyframes every KEY_S seconds, linear between.

The result is a crop expression for ffmpeg, evaluated per frame, in the
segment's own timeline. A plan segment may carry "focus" (0..1, the crop
centre as a fraction of the width) to override the measurement.
REELLY_FOLLOW=off restores the fixed centre crop.
"""
import os
import subprocess

import numpy as np

from . import config

FOLLOW_VERSION = 1      # part of the raw-cut cache key
ANALYSIS_FPS = 10
ANALYSIS_W, ANALYSIS_H = 320, 180
DIFF_THRESHOLD = 10     # 0-255 grayscale step that counts as change
MIN_MOTION = 0.004      # share of moving pixels below which a frame holds
SPREAD_SHARE = 0.8      # motion share the visible band must hold
SPREAD_MARGIN = 1.15    # extra room around that band
MAX_ZOOM = 2.0          # CO7: past 2x the content stops reading
MEDIAN_S = 1.0
DEAD_ZONE = 0.06        # of the source width: ignore smaller shifts
MAX_SPEED = 0.22        # of the source width per second
KEY_S = 0.5
FRAME_W = 1080


def enabled():
    return os.environ.get("REELLY_FOLLOW", "").strip().lower() not in ("off", "0", "no")


def _frames(video, s, e, crop=None):
    """Grayscale analysis frames (n, ANALYSIS_H, ANALYSIS_W) uint8 for [s, e].
    A fixed size on purpose: the column profile does not care about aspect,
    and a known size needs no second decode to learn it."""
    vf = (f"fps={ANALYSIS_FPS},scale={ANALYSIS_W}:{ANALYSIS_H},format=gray,"
          f"gblur=sigma=1.5")
    if crop:
        vf = f"crop={crop}," + vf
    r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", f"{s:.3f}",
                        "-t", f"{max(0.1, e - s):.3f}", "-i", video, "-vf", vf,
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True, check=True)
    size = ANALYSIS_W * ANALYSIS_H
    n = len(r.stdout) // size
    return np.frombuffer(r.stdout[:n * size], np.uint8).reshape(n, ANALYSIS_H, ANALYSIS_W)


def motion_profile(frames):
    """(per-frame column motion (n-1, w), per-frame moving share (n-1,))."""
    if len(frames) < 2:
        return np.zeros((0, frames.shape[-1])), np.zeros(0)
    d = np.abs(np.diff(frames.astype(np.int16), axis=0)) > DIFF_THRESHOLD
    cols = d.sum(axis=1).astype(float)                   # (n-1, w)
    share = d.mean(axis=(1, 2))
    return cols, share


def spread(cols):
    """Narrowest share of the width holding SPREAD_SHARE of all motion."""
    total = cols.sum(axis=0)
    if total.sum() <= 0:
        return 1.0
    w = len(total)
    c = np.concatenate([[0.0], np.cumsum(total)])
    need = SPREAD_SHARE * c[-1]
    best = w
    j = 0
    for i in range(w):
        while j < w and c[j + 1] - c[i] < need:
            j += 1
        if j < w:
            best = min(best, j - i + 1)
    return best / w


def zoom_for(spread_share, base_zoom, max_zoom=MAX_ZOOM):
    """The zoom whose visible band (1/zoom of the width) covers the action
    with a margin; never below the plan's base zoom, never above max."""
    want = 1.0 / max(1e-6, spread_share * SPREAD_MARGIN)
    return round(max(base_zoom, min(max_zoom, want)), 3)


def centre_path(cols, share, zoom):
    """Smoothed crop-centre (fraction of the width) per analysis frame."""
    w = cols.shape[1] if cols.ndim == 2 else ANALYSIS_W
    half = 0.5 / zoom
    lo, hi = half, 1.0 - half
    xs = (np.arange(w) + 0.5) / w
    raw, last = [], 0.5
    for row, sh in zip(cols, share):
        if sh >= MIN_MOTION and row.sum() > 0:
            last = float((row * xs).sum() / row.sum())
        raw.append(min(hi, max(lo, last)))
    if not raw:
        return np.array([0.5])
    raw = np.array(raw)
    k = max(1, int(round(MEDIAN_S * ANALYSIS_FPS)))
    med = np.array([np.median(raw[max(0, i - k // 2): i + k // 2 + 1])
                    for i in range(len(raw))])
    out, cur = [], float(med[0])
    step = MAX_SPEED / ANALYSIS_FPS
    for target in med:
        gap = target - cur
        if abs(gap) > DEAD_ZONE:
            # move toward the edge of the dead zone, not all the way: settles
            cur += max(-step, min(step, gap - np.sign(gap) * DEAD_ZONE * 0.5))
        out.append(min(hi, max(lo, cur)))
    return np.array(out)


def keyframes(path, dur):
    """[(t, centre)] every KEY_S seconds over [0, dur], from the frame path."""
    if len(path) == 0:
        return [(0.0, 0.5)]
    keys = []
    t = 0.0
    while t <= dur + 1e-6:
        i = min(len(path) - 1, int(round(t * ANALYSIS_FPS)))
        keys.append((round(t, 3), round(float(path[i]), 4)))
        t += KEY_S
    # merge runs that do not move (shorter expression, same picture)
    merged = [keys[0]]
    for i in range(1, len(keys)):
        nxt = keys[i + 1][1] if i + 1 < len(keys) else None
        if abs(keys[i][1] - merged[-1][1]) < 1e-3 and (nxt is None or abs(nxt - keys[i][1]) < 1e-3):
            continue
        merged.append(keys[i])
    if merged[-1][0] < keys[-1][0]:
        merged.append(keys[-1])
    return merged


def crop_x_expr(keys, zoom):
    """ffmpeg crop x (pixels into the scaled frame) as a per-frame
    expression: linear between keyframes, held at the ends."""
    scaled_w = round(FRAME_W * zoom)
    pan = scaled_w - FRAME_W

    def px(c):
        # centre fraction -> left edge in scaled pixels, clamped to the frame
        return max(0.0, min(pan, c * scaled_w - FRAME_W / 2))

    if len(keys) == 1 or pan <= 0:
        return f"{px(keys[0][1]):.1f}"
    expr = f"{px(keys[-1][1]):.1f}"
    for (t0, c0), (t1, c1) in reversed(list(zip(keys, keys[1:]))):
        x0, x1 = px(c0), px(c1)
        seg = (f"{x0:.1f}" if abs(x1 - x0) < 0.05 else
               f"{x0:.1f}+({x1 - x0:.1f})*(t-{t0:.3f})/{max(1e-3, t1 - t0):.3f}")
        expr = f"if(lt(t\\,{t1:.3f})\\,{seg}\\,{expr})"
    return expr


def plan_segment(video, s, e, base_zoom=1.5, focus=None, crop=None):
    """{'zoom', 'x_expr', 'keys', 'spread'} for one source segment [s, e]."""
    if focus is not None:
        z = max(1.0, min(MAX_ZOOM, base_zoom))
        f = max(0.5 / z, min(1 - 0.5 / z, float(focus)))
        keys = [(0.0, round(f, 4))]
        return {"zoom": z, "x_expr": crop_x_expr(keys, z), "keys": keys,
                "spread": None, "source": "plan focus"}
    frames = _frames(video, s, e, crop)
    cols, share = motion_profile(frames)
    if cols.size == 0 or share.max(initial=0) < MIN_MOTION:
        z = max(1.0, base_zoom)
        keys = [(0.0, 0.5)]
        return {"zoom": z, "x_expr": crop_x_expr(keys, z), "keys": keys,
                "spread": 1.0, "source": "no motion: centre"}
    sp = spread(cols)
    z = zoom_for(sp, max(1.0, base_zoom))
    path = centre_path(cols, share, z)
    keys = keyframes(path, e - s)
    return {"zoom": z, "x_expr": crop_x_expr(keys, z), "keys": keys,
            "spread": round(sp, 3), "source": "motion"}
