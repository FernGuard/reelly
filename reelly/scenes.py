"""UNDERSTAND: visual scene-cut detection so edits never land mid-shot.

Screen recordings change only part of the frame (a video plays inside a
window), so scene scores run far lower than filmed content. We detect at a
sensitive threshold and keep each cut's score; downstream stages filter by
strength (>= STRONG is a hard transition, below is in-window motion).
"""
from . import config, media

SENSITIVE = 0.06
STRONG = 0.12


def scene_cuts(video, thresh=SENSITIVE):
    """[{t, score}] for visual cuts. Decodes at 320px wide for speed."""
    r = media.sh(config.FFMPEG, "-v", "info", *config.hwdecode_args(),
                 "-i", video, "-an",
                 "-vf", f"scale=320:-2,select='gt(scene,{thresh})',metadata=print",
                 "-fps_mode", "passthrough", "-f", "null", "-")
    cuts = []
    t = None
    for line in r.stderr.splitlines():
        if "pts_time:" in line:
            try:
                t = round(float(line.split("pts_time:")[1].split()[0]), 2)
            except (ValueError, IndexError):
                t = None
        elif "lavfi.scene_score=" in line and t is not None:
            try:
                cuts.append({"t": t, "score": round(float(line.split("=")[1]), 3)})
            except (ValueError, IndexError):
                pass
            t = None
    return cuts
