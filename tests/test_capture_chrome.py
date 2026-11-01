"""Capture chrome: a game recorded inside the Unity editor (or OBS) carries
toolbars and status bars. --crop auto finds the game view, and the render
applies the project's crop so editor UI never reaches a deliverable."""
import json
import os
import subprocess

import numpy as np

from reelly import activity, config, direct, preview


def _editor_capture(path, W=1920, H=1080, box=(148, 110, 1624, 914), n=12):
    """Synthetic 'editor recording': flat static grey chrome, a moving
    picture inside the game-view box, changing 'console text' at the bottom."""
    x, y, w, h = box
    frames = []
    rng = np.random.default_rng(0)
    for i in range(n):
        f = np.full((H, W, 3), 38, np.uint8)
        f[y:y + h, x:x + w] = rng.integers(40, 220, (h, w, 3), dtype=np.uint8)
        f[1050:1070, 200:200 + 40 * (i % 10 + 1)] = 200        # status bar text
        f[20:40, 30:600] = 90                                   # static toolbar
        frames.append(f)
    raw = str(path) + ".raw"
    open(raw, "wb").write(np.stack(frames).tobytes())
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
                    "rgb24", "-s", f"{W}x{H}", "-r", "4", "-i", raw, "-pix_fmt",
                    "yuv420p", "-crf", "12", str(path)], check=True)
    return str(path)


def test_gameview_box_finds_the_game_inside_editor_chrome(tmp_path):
    clip = _editor_capture(tmp_path / "editor.mp4")
    box = activity.gameview_box(clip)
    w, h, x, y = (int(v) for v in box.split(":"))
    assert abs(x - 148) <= 4 and abs(y - 110) <= 4
    assert abs(w - 1624) <= 8 and abs(h - 914) <= 8           # 16:9 snap


def test_no_chrome_returns_none(tmp_path):
    clip = _editor_capture(tmp_path / "full.mp4", box=(0, 0, 1920, 1080))
    assert activity.gameview_box(clip) is None


def test_the_project_crop_reaches_the_render(tmp_path):
    root = tmp_path / "proj"
    (root / "analysis").mkdir(parents=True)
    (root / "analysis" / "source.json").write_text(json.dumps({"crop": "1624:914:148:110"}))
    assert direct.source_crop(str(root)) == "1624:914:148:110"
    vf = preview._reframe_vf(1.5, None, "1624:914:148:110")
    assert vf.startswith("crop=1624:914:148:110,split=2[bg][fg];")


def test_bad_or_missing_crop_is_ignored(tmp_path):
    assert direct.source_crop(str(tmp_path)) is None
    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "source.json").write_text(json.dumps({"crop": "1;rm -rf"}))
    assert direct.source_crop(str(tmp_path)) is None
