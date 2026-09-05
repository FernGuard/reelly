"""Bounded per-clip auto-grade: corrective, never creative.

Samples frames via signalstats, measures mean luma / luma range / saturation,
and emits a small eq correction for underexposure, flatness, and desaturation.
Every axis is hard-capped (max ~8 percent) so the output reads "clean", not
"graded" — no LUTs, no color shifts, no taste in code.

Adapted from MIT-licensed browser-use/video-use helpers/grade.py.
See THIRD_PARTY_NOTICES.md.
"""
import hashlib
import json
import os
import subprocess
import tempfile

from . import config

# signalstats decodes the sampled range every time; the result only depends
# on (file identity, range, sample count), so it is cached on disk like
# faceio's face cache — keyed by abspath|mtime|size, self-invalidating.
CACHE_DIR = os.path.join(config.HOME, "cache", "grade")


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


def _frame_stats(video, start, duration, n_samples=10):
    """Cached front for _frame_stats_uncached. Failed analyses are not
    cached so a transient decode error never sticks."""
    params = {"start": round(float(start), 3),
              "duration": round(float(duration), 3), "n": n_samples}
    cached = _cache_get(video, params)
    if isinstance(cached, dict) and "stats" in cached:
        return cached["stats"]
    stats = _frame_stats_uncached(video, start, duration, n_samples)
    if stats is not None:
        _cache_put(video, params, {"stats": stats})
    return stats


def _frame_stats_uncached(video, start, duration, n_samples=10):
    """Average signalstats over sampled frames, normalized to 0..1 by the
    source's native bit depth (8-bit reports 0-255, 10-bit 0-1023)."""
    fps = max(0.5, min(n_samples / max(duration, 0.1), 10.0))
    fd, mpath = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        r = subprocess.run(
            [config.FFMPEG, "-y", "-hide_banner", "-nostats",
             "-ss", f"{start:.3f}", "-i", video, "-t", f"{duration:.3f}",
             "-vf", f"fps={fps:.2f},signalstats,metadata=print:file={mpath}",
             "-f", "null", "-"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if r.returncode != 0:
            return None
        vals = {"YAVG": [], "YMIN": [], "YMAX": [], "SATAVG": []}
        depth = 8
        for line in open(mpath):
            line = line.strip()
            if "signalstats.YBITDEPTH=" in line:
                try:
                    depth = int(float(line.rsplit("=", 1)[1]))
                except ValueError:
                    pass
                continue
            for k in vals:
                if f"signalstats.{k}=" in line:
                    try:
                        vals[k].append(float(line.rsplit("=", 1)[1]))
                    except ValueError:
                        pass
        if not vals["YAVG"]:
            return None
        mx = (2 ** depth) - 1

        def avg(k):
            return sum(vals[k]) / len(vals[k]) / mx

        y_range = (avg("YMAX") - avg("YMIN")) if vals["YMAX"] and vals["YMIN"] else 0.7
        return {"y_mean": avg("YAVG"), "y_range": y_range,
                "sat_mean": avg("SATAVG") if vals["SATAVG"] else 0.25}
    finally:
        os.unlink(mpath)


def auto_grade(video, start, duration):
    """Return (eq_filter_or_empty, stats_or_None) for the given source range."""
    stats = _frame_stats(video, start, duration)
    if stats is None:
        return "", None
    y_mean, y_range, sat_mean = stats["y_mean"], stats["y_range"], stats["sat_mean"]

    # Contrast: target range ~0.72; boost gently if flat, never reduce.
    if y_range < 0.65:
        t = max(0.0, min(1.0, (y_range - 0.50) / 0.15))
        contrast = 1.08 - 0.05 * t
    else:
        contrast = 1.03

    # Gamma: lift if dark (facecams in lamp-lit rooms), tiny pullback if hot.
    gamma = 1.0
    if y_mean < 0.42:
        t = max(0.0, min(1.0, (y_mean - 0.30) / 0.12))
        gamma = 1.10 - 0.08 * t
    elif y_mean > 0.60:
        gamma = 0.97

    # Saturation: most consumer video is slightly over-saturated; tiny pullback
    # by default, modest boost only when genuinely flat.
    sat = 0.98
    if sat_mean < 0.18:
        sat = 1.04
    elif sat_mean > 0.38:
        sat = 0.96

    contrast = max(0.94, min(1.08, contrast))
    gamma = max(0.94, min(1.10, gamma))
    sat = max(0.94, min(1.06, sat))

    parts = []
    if abs(contrast - 1.0) > 0.005:
        parts.append(f"contrast={contrast:.3f}")
    if abs(gamma - 1.0) > 0.005:
        parts.append(f"gamma={gamma:.3f}")
    if abs(sat - 1.0) > 0.005:
        parts.append(f"saturation={sat:.3f}")
    return ("eq=" + ":".join(parts)) if parts else "", stats
