"""Follow the action: frame-difference motion profile -> zoom + smoothed
crop path for the 9:16 reframe."""
import subprocess

import numpy as np
import pytest

from reelly import config, follow, preview


def _moving_box(n=20, w=320, h=180, size=24, start=40, end=260):
    frames = np.zeros((n, h, w), np.uint8)
    for i in range(n):
        x = int(start + (end - start) * i / (n - 1))
        frames[i, 70:70 + size, x:x + size] = 255
    return frames


def test_profile_finds_the_moving_columns():
    cols, share = follow.motion_profile(_moving_box())
    assert cols.shape == (19, 320) and share.max() > 0
    assert cols[0][:30].sum() == 0 and cols[0][40:70].sum() > 0


def test_concentrated_action_zooms_in_and_spread_action_stays_wide():
    cols, _ = follow.motion_profile(_moving_box(start=150, end=170))
    assert follow.zoom_for(follow.spread(cols), 1.5) == follow.MAX_ZOOM
    two = np.zeros((10, 180, 320), np.uint8)
    for i in range(10):                      # two boards changing at both edges
        two[i, :, 5:40] = 255 * (i % 2)
        two[i, :, 280:315] = 255 * ((i + 1) % 2)
    cols2, _ = follow.motion_profile(two)
    assert follow.zoom_for(follow.spread(cols2), 1.5) == 1.5


def test_path_follows_left_to_right_smoothly_and_within_bounds():
    frames = _moving_box(n=60, start=20, end=290)
    cols, share = follow.motion_profile(frames)
    z = 2.0
    path = follow.centre_path(cols, share, z)
    assert path[-1] > path[0] + 0.2                       # it travelled right
    assert np.all(np.diff(path) >= -1e-9)                 # never backwards
    assert np.max(np.diff(path)) <= follow.MAX_SPEED / follow.ANALYSIS_FPS + 1e-9
    assert path.min() >= 0.25 - 1e-9 and path.max() <= 0.75 + 1e-9


def test_small_wobble_inside_the_dead_zone_does_not_move_the_crop():
    frames = np.zeros((30, 180, 320), np.uint8)
    for i in range(30):
        x = 150 + (4 if i % 2 else -4)
        frames[i, 80:100, x:x + 20] = 255
    cols, share = follow.motion_profile(frames)
    path = follow.centre_path(cols, share, 1.8)
    assert np.ptp(path) < 1e-6


def test_crop_expression_is_linear_between_keys_and_clamped():
    keys = [(0.0, 0.5), (1.0, 0.75), (2.0, 0.75)]
    expr = follow.crop_x_expr(keys, 2.0)       # scaled width 2160, pan 0..1080
    assert expr.startswith("if(lt(t\\,1.000)\\,540.0+(540.0)*(t-0.000)/1.000")
    assert follow.crop_x_expr([(0.0, 0.0)], 2.0) == "0.0"
    assert follow.crop_x_expr([(0.0, 1.0)], 2.0) == "1080.0"


def test_keyframes_merge_still_runs():
    keys = follow.keyframes(np.full(50, 0.5), 5.0)
    assert len(keys) == 2 and keys[0] == (0.0, 0.5)


def test_plan_focus_pins_the_crop():
    fp = follow.plan_segment("unused.mp4", 0, 5, base_zoom=1.5, focus=0.9)
    assert fp["source"] == "plan focus"
    assert fp["keys"][0][1] == pytest.approx(1 - 0.5 / 1.5, abs=1e-3)


def test_follow_is_off_for_uncropped_reframes_and_by_switch(monkeypatch):
    assert preview._follow_plan("v.mp4", 0, 5, 1.0, None) == (1.0, None)
    monkeypatch.setenv("REELLY_FOLLOW", "off")
    assert preview._follow_plan("v.mp4", 0, 5, 1.5, None) == (1.5, None)


def test_reframe_filter_uses_the_moving_crop():
    vf = preview._reframe_vf(2.0, "if(lt(t\\,1)\\,0\\,10)")
    assert "scale=w=2160:h=-2,crop=1080:ih:if(lt(t\\,1)\\,0\\,10):0" in vf
    assert "crop=1080:ih:(iw-1080)/2:0" in preview._reframe_vf(1.5)


def test_real_video_through_ffmpeg(tmp_path):
    """A clip whose only motion is a box sliding right: the measured path
    moves right and the expression renders through ffmpeg."""
    frames = _moving_box(n=40, w=320, h=180, start=30, end=270)
    raw = tmp_path / "f.raw"
    raw.write_bytes(frames.tobytes())
    clip = str(tmp_path / "box.mp4")
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "rawvideo",
                    "-pix_fmt", "gray", "-s", "320x180", "-r", "10", "-i", str(raw),
                    "-vf", "scale=1280:720", "-pix_fmt", "yuv420p", clip], check=True)
    fp = follow.plan_segment(clip, 0.0, 4.0, base_zoom=1.5)
    assert fp["source"] == "motion"
    assert fp["keys"][-1][1] > fp["keys"][0][1]
    out = str(tmp_path / "out.mp4")
    r = subprocess.run([config.FFMPEG, "-v", "error", "-y", "-i", clip, "-vf",
                        preview._reframe_vf(fp["zoom"], fp["x_expr"]),
                        "-frames:v", "5", out], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
