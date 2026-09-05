"""Shared frame extraction and detection caching for face/speaker.

Two jobs, both about not paying twice:

- extract_frames: pull N stills out of a video in one ffmpeg spawn (not N),
  downscaled so the detector never chews on 4K pixels. Returns the scale so
  boxes map back to source coordinates.
- cache_get/cache_put: detection results on disk, keyed by the file's
  identity (abspath|mtime|size), so a preview -> finalize -> handoff run
  detects each window once, not three times.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import threading

import numpy as np

from . import config

CACHE_DIR = os.path.join(config.HOME, "cache", "faces")

# cache_put is read-modify-write; parallel render workers writing different
# keys for the same video would otherwise drop each other's entries. One
# in-process lock is enough: cross-process raciness only costs a deterministic
# recompute, never a wrong answer.
_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------- extraction

def _probe_dims(video):
    """(width, height) of the first video stream, or None."""
    try:
        r = subprocess.run(
            [config.FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", video],
            capture_output=True, text=True)
        w, h = r.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except (ValueError, OSError):
        return None


def _out_dims(src_wh, max_width):
    """Downscaled (w, h), even, aspect kept; never upscales."""
    W, H = src_wh
    w = min(W, max_width)
    w = int(w // 2 * 2)
    h = int(round(w * H / W / 2) * 2)
    return max(2, w), max(2, h)


def _read_raw(stdout, n_frames, w, h):
    """Split an rgb24 rawvideo byte stream into frames; None-pad to n."""
    frame_bytes = w * h * 3
    n = len(stdout) // frame_bytes
    frames = [np.frombuffer(stdout[i * frame_bytes:(i + 1) * frame_bytes],
                            dtype=np.uint8).reshape(h, w, 3)
              for i in range(min(n, n_frames))]
    return frames + [None] * (n_frames - len(frames))


def _uniform_step(times, tol=0.05):
    """The common step if times are evenly spaced, else None."""
    if len(times) < 2:
        return None
    steps = np.diff(times)
    step = float(np.median(steps))
    if step <= 0 or np.any(np.abs(steps - step) > tol):
        return None
    return step


def _spawn(cmd):
    return subprocess.run(cmd, capture_output=True)


def extract_frames(video, times, max_width=640):
    """RGB stills at the given timestamps: [(frame, scale), ...].

    One ffmpeg spawn for evenly spaced times (every call site here), one
    spawn per contiguous run otherwise -- never one per timestamp. Frames
    are numpy uint8 HxWx3, downscaled to max_width (aspect kept, never
    upscaled). scale = source_px / frame_px: multiply detected coordinates
    by it to land in source pixels. A timestamp past EOF yields (None, scale).
    """
    times = [float(t) for t in times]
    if not times:
        return []
    dims = _probe_dims(video)
    if dims is None:
        return [(None, 1.0)] * len(times)
    w, h = _out_dims(dims, max_width)
    scale = dims[0] / w

    step = _uniform_step(times)
    if step is not None or len(times) == 1:
        runs = [times]
    else:  # sparse, uneven: batch maximal uniform runs, one spawn each
        runs = [[times[0]]]
        for prev, t in zip(times, times[1:]):
            run = runs[-1]
            if len(run) == 1 or abs((t - prev) - (run[1] - run[0])) <= 0.05:
                run.append(t)
            else:
                runs.append([t])

    frames = []
    for run in runs:
        if len(run) == 1:
            r = _spawn([config.FFMPEG, "-y", "-v", "error",
                        "-ss", f"{run[0]:.3f}", "-i", video,
                        "-frames:v", "1", "-vf", f"scale={w}:{h}",
                        "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
        else:
            rstep = run[1] - run[0]
            r = _spawn([config.FFMPEG, "-y", "-v", "error",
                        "-ss", f"{run[0]:.3f}", "-i", video,
                        "-vf", f"fps=1/{rstep:.6f},scale={w}:{h}",
                        "-frames:v", str(len(run)),
                        "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
        out = r.stdout if r.returncode == 0 else b""
        frames.extend(_read_raw(out, len(run), w, h))
    return [(f, scale) for f in frames]


# --------------------------------------------------------------------- cache

def _cache_path(video):
    """Cache file for this exact video: path+mtime+size in the name means a
    re-render or re-download invalidates automatically."""
    st = os.stat(video)
    ident = f"{os.path.abspath(video)}|{st.st_mtime}|{st.st_size}"
    return os.path.join(CACHE_DIR,
                        hashlib.sha1(ident.encode()).hexdigest() + ".json")


def cache_get(video, key):
    """Cached value for (video, key), or None. Never raises."""
    try:
        with open(_cache_path(video)) as f:
            return json.load(f).get(key)
    except (OSError, ValueError):
        return None


def cache_put(video, key, value):
    """Store value (JSON-serialisable) for (video, key). Never raises."""
    try:
        with _CACHE_LOCK:
            path = _cache_path(video)
            os.makedirs(CACHE_DIR, exist_ok=True)
            try:
                with open(path) as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = {}
            data[key] = value
            fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, path)
    except OSError:
        pass
