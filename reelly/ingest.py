"""INGEST: project workspace, file registration, multi-file session sync."""
import os
import subprocess

import numpy as np

from . import config, media

SUBDIRS = ("source", "analysis", "edl",
           "deliverables/full", "deliverables/cuts",
           "deliverables/captions", "deliverables/audio", "qc")


def workspace(name, out_root):
    root = os.path.join(out_root, name)
    for d in SUBDIRS:
        os.makedirs(os.path.join(root, d), exist_ok=True)
    return root


def link_source(root, path, role):
    """Link the original into source/ under a role name.

    Prefer a symlink so large media is not duplicated. If the filesystem
    cannot symlink (typical on Windows without Developer Mode), copy the file.

    The input is fully RESOLVED (os.path.realpath) before linking, and an
    input that already lives inside this project's source/ leaves the
    existing link untouched. Both guards exist because of the same real
    failure (2026-08-01): `reelly analyze <project>/source/screen.mov`
    re-linked the symlink onto itself, and every ffmpeg open then died with
    'Too many levels of symbolic links'.
    """
    ext = os.path.splitext(path)[1]
    dst = os.path.join(root, "source", f"{role}{ext}")
    real = os.path.realpath(os.path.abspath(path))
    src_dir = os.path.realpath(os.path.join(root, "source"))
    if os.path.commonpath([os.path.dirname(real), src_dir]) == src_dir \
            or real == os.path.realpath(dst):
        # already this project's linked source: re-linking would only build
        # a self-loop; keep what exists
        if os.path.islink(dst) or os.path.exists(dst):
            return dst
    if os.path.islink(dst) or os.path.exists(dst):
        os.remove(dst)
    try:
        os.symlink(real, dst)
    except OSError:
        import shutil
        shutil.copy2(real, dst)
    return dst


def _envelope(path, length=240.0, sr=1000):
    """Mono audio energy envelope at sr Hz (for waveform cross-correlation)."""
    r = subprocess.run([config.FFMPEG, "-v", "error", "-t", str(length), "-i", path,
                        "-ac", "1", "-ar", "8000", "-f", "f32le", "-"],
                       capture_output=True)
    x = np.frombuffer(r.stdout, dtype=np.float32)
    if len(x) < 8000:
        return np.zeros(1, dtype=np.float32)
    hop = 8000 // sr
    n = len(x) // hop
    return np.abs(x[:n * hop]).reshape(n, hop).mean(axis=1)


def sync_offset(main, other, window=240.0):
    """Align two recordings of the same session by audio.

    Returns (offset, confidence) where main_time = other_time + offset.
    Confidence is normalized correlation (roughly 0..1). When the facecam
    track is silent or correlation is degenerate, fall back to 0.0: OBS
    source-record starts with the recording, so zero is the right prior
    (stress test caught a -240s garbage offset on a silent cam track).
    """
    from scipy.signal import fftconvolve
    sr = 1000
    a = _envelope(main, window, sr)
    b = _envelope(other, window, sr)
    if float(np.abs(b).max()) < 1e-4 or float(np.abs(a).max()) < 1e-4:
        print("[sync] silent track, falling back to offset 0.0")
        return 0.0, 0.0
    a = a - a.mean()
    b = b - b.mean()
    corr = fftconvolve(a, b[::-1])
    lag = int(np.argmax(corr)) - (len(b) - 1)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    conf = float(corr.max() / denom)
    if conf < 0.2 or abs(lag / sr) > window * 0.95:
        print(f"[sync] low confidence ({conf:.2f}) or window-edge lag, "
              f"falling back to offset 0.0")
        return 0.0, conf
    return lag / sr, conf
