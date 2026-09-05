"""Find the active screen region so the split layout's content band zooms
to the action instead of letterboxing the whole desktop (playbook CO7).

Motion-diff heatmap: sample frames across the cut, accumulate absolute
differences, take the padded bounding box of where things actually change.
Deterministic and free; the winning clips zoom to the action, so do we.
"""
import hashlib
import json
import os
import tempfile

import numpy as np
from PIL import Image

from . import config, faceio

BAND_W, BAND_H = 1080, 1152  # split layout content band

# Heatmaps are deterministic per (file identity, window, params), so the
# result is cached on disk exactly like faceio's face cache: keyed by
# abspath|mtime|size, a re-render invalidates automatically.
CACHE_DIR = os.path.join(config.HOME, "cache", "activity")


def _cache_path(video, params):
    st = os.stat(video)
    ident = "|".join([os.path.abspath(video), str(st.st_mtime),
                      str(st.st_size),
                      json.dumps(params, sort_keys=True, default=str)])
    return os.path.join(CACHE_DIR,
                        hashlib.sha1(ident.encode()).hexdigest() + ".json")


def _cache_get(video, params):
    """Cached value or None. A corrupt or missing cache is never fatal."""
    try:
        with open(_cache_path(video, params)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _cache_put(video, params, value):
    """Store value (JSON-serialisable). Never raises."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(value, f)
        os.replace(tmp, _cache_path(video, params))
    except OSError:
        pass


def _sample_times(segments, samples):
    spans = [(seg[0], seg[1]) for seg in segments]  # tolerate [s, e, speed]
    total = sum(e - s for s, e in spans)
    if total <= 0:
        return []
    step = total / (samples + 1)
    times, acc, target = [], 0.0, step
    for s, e in spans:
        while target <= acc + (e - s):
            times.append(s + (target - acc))
            target += step
        acc += e - s
    return times


def _frames(video, times, width=480):
    """Grayscale float frames at the given times, one batched ffmpeg spawn
    (was one spawn + PNG decode per timestamp)."""
    imgs = []
    for fr, _ in faceio.extract_frames(video, times, max_width=width):
        if fr is not None:
            imgs.append(np.asarray(Image.fromarray(fr).convert("L"),
                                   dtype=np.float32))
    return imgs


def active_box(video, segments, src_wh, samples=10, pad=0.10, min_w_frac=0.5):
    """(x, y, w, h) crop of the busiest screen region, or None for no signal.

    Guarantees: width >= min_w_frac of source (max ~2x zoom on 1080p),
    and w/h wide enough that the crop scaled to 1080 wide fits the band.
    Cached on disk per (file identity, segments, params).
    """
    params = {"segments": segments, "src_wh": list(src_wh),
              "samples": samples, "pad": pad, "min_w_frac": min_w_frac}
    cached = _cache_get(video, params)
    if isinstance(cached, dict) and "box" in cached:
        return tuple(cached["box"]) if cached["box"] is not None else None
    box = _active_box_uncached(video, segments, src_wh, samples, pad,
                               min_w_frac)
    _cache_put(video, params, {"box": list(box) if box is not None else None})
    return box


def _active_box_uncached(video, segments, src_wh, samples, pad, min_w_frac):
    W, H = src_wh
    imgs = _frames(video, _sample_times(segments, samples))
    if len(imgs) < 3:
        return None
    heat = np.zeros_like(imgs[0])
    for a, b in zip(imgs, imgs[1:]):
        if a.shape == b.shape:
            heat += np.abs(a - b)
    thr = heat.mean() + 0.8 * heat.std()
    ys, xs = np.where(heat > thr)
    if len(xs) < 30:  # static screen, nothing to zoom to
        return None
    h, w = heat.shape
    # dense core of the motion mass, not its extremes: scrolls and page
    # transitions smear motion everywhere and inflate a min/max box
    wts = heat[ys, xs]

    def wq(coords, q):
        order = np.argsort(coords)
        cw = np.cumsum(wts[order])
        return float(coords[order[np.searchsorted(cw, q * cw[-1])]])

    x0, x1 = wq(xs, 0.06), wq(xs, 0.94)
    y0, y1 = wq(ys, 0.06), wq(ys, 0.94)
    x0, x1 = max(0.0, x0 - (x1 - x0) * pad), min(w, x1 + (x1 - x0) * pad)
    y0, y1 = max(0.0, y0 - (y1 - y0) * pad), min(h, y1 + (y1 - y0) * pad)
    sx, sy = W / w, H / h
    cx, cy = (x0 + x1) / 2 * sx, (y0 + y1) / 2 * sy
    bw, bh = (x1 - x0) * sx, (y1 - y0) * sy
    bw = max(bw, min_w_frac * W)               # zoom cap
    bh = max(bh, 0.5 * H)                      # never a sliver
    bh = min(bh, bw * BAND_H / BAND_W, H)      # scaled to 1080 wide must fit band
    bw = min(bw, W)
    bx = min(max(0.0, cx - bw / 2), W - bw)
    by = min(max(0.0, cy - bh / 2), H - bh)
    even = lambda v: int(v) // 2 * 2
    return even(bx), even(by), even(bw), even(bh)
