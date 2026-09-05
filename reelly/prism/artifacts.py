"""ARTIFACTS: hook-dense frames, scene-cut timestamps, and a transcription wav
— everything downstream stages read straight off disk.
"""
import json
import os

from .. import config, media, scenes

FRAME_WIDTH = 360
HOOK_OFFSETS = (0, 0.5, 1, 2, 4)
SCENE_THRESH = 0.3


def _frame_times(duration):
    """Hook-dense offsets plus the midpoint and final second, clamped and deduped."""
    cap = max(duration - 0.01, 0)
    times = [min(t, cap) for t in HOOK_OFFSETS if t <= cap]
    times.append(round(duration / 2, 2))
    times.append(max(duration - 1, 0))
    seen, out = set(), []
    for t in times:
        key = round(t, 2)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return sorted(out)


def extract_frames(video, out_dir, duration=None):
    """[{t, path}] — one 360px-wide jpg per hook-dense timestamp."""
    os.makedirs(out_dir, exist_ok=True)
    duration = duration if duration is not None else media.duration(video)
    frames = []
    for t in _frame_times(duration):
        path = os.path.join(out_dir, f"frame_{t:05.2f}.jpg")
        if not os.path.exists(path):
            media.sh(config.FFMPEG, "-y", "-v", "error", "-ss", str(t), "-i", video,
                      "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-2", "-q:v", "2", path)
        frames.append({"t": t, "path": path})
    return frames


def scene_cuts(video, cache_json=None, force=False):
    """[{t, score}] using PRISM's own (looser) threshold — short-form content
    cuts far more aggressively than the longform screen recordings scenes.py
    was written for. Cached to cache_json: the full-decode ffmpeg pass used to
    re-run on every invocation of the same slug."""
    if cache_json and not force and os.path.exists(cache_json):
        try:
            cuts = json.load(open(cache_json))
            if isinstance(cuts, list):
                return cuts
        except (ValueError, OSError):
            pass
    cuts = scenes.scene_cuts(video, thresh=SCENE_THRESH)
    if cache_json:
        json.dump(cuts, open(cache_json, "w"))
    return cuts


def extract_audio(video, out_wav):
    if not os.path.exists(out_wav):
        media.extract_wav(video, out_wav, rate=16000)
    return out_wav


def build(video, work_dir, force=False):
    """Runs all three artifact steps, caching to work_dir. Returns a manifest dict."""
    frames_dir = os.path.join(work_dir, "frames")
    wav_p = os.path.join(work_dir, "audio.wav")
    duration = media.duration(video)

    if force:
        for f in os.listdir(frames_dir) if os.path.isdir(frames_dir) else []:
            os.remove(os.path.join(frames_dir, f))
        if os.path.exists(wav_p):
            os.remove(wav_p)

    frames = extract_frames(video, frames_dir, duration=duration)
    cuts = scene_cuts(video, cache_json=os.path.join(work_dir, "scene_cuts.json"),
                      force=force)
    extract_audio(video, wav_p)

    return {"duration": duration, "frames": frames, "scene_cuts": cuts, "audio_wav": wav_p}
