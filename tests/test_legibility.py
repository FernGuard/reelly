"""Measured legibility (D3/D7) for burned social text: a plate goes under a
layer only when the brand ink does not separate from the footage."""
import json
import os
import subprocess

from PIL import Image

from reelly import config, judge, legibility


def _clip(path, color):
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={color}:s=1080x1920:d=3:r=10", "-pix_fmt", "yuv420p",
                    str(path)], check=True)
    return str(path)


def _plan():
    return {"id": "cut_01", "segments": [[0.0, 3.0]], "duration_s": 3.0,
            "content_s": 3.0, "captions": "none",
            "hook": {"text": "The door opens.", "show_s": 2.0},
            "overlay_lines": [], "game_name": "Game Name"}


def test_bright_footage_gets_a_plate_and_dark_does_not(tmp_path, monkeypatch):
    monkeypatch.delenv("REELLY_BRAND", raising=False)
    bright = legibility.measure(_plan(), _clip(tmp_path / "w.mp4", "white"), "#ebeae7")
    dark = legibility.measure(_plan(), _clip(tmp_path / "d.mp4", "0x101018"), "#ebeae7")
    hb = next(r for r in bright if r["label"] == "hook")
    hd = next(r for r in dark if r["label"] == "hook")
    assert hb["scrim"] > 0 and hb["pass"] is True        # rescued by a plate
    assert hd["scrim"] == 0 and hd["luma_gap"] > 150     # bare type reads
    assert {r["label"] for r in bright} == {"hook", "game_name"}


def test_plates_sit_under_the_text_box_centred(tmp_path):
    res = [{"label": "hook", "t0": 0.0, "t1": 2.0, "box": [200, 300, 880, 380],
            "luma_gap": 20, "backdrop_luma": 230, "scrim": 0.6, "pass": True},
           {"label": "game_name", "t0": 0.0, "t1": 3.0, "box": [400, 440, 680, 480],
            "luma_gap": 150, "backdrop_luma": 30, "scrim": 0.0, "pass": True}]
    ev = legibility.plates(res, str(tmp_path), "#0b0b0c")
    assert len(ev) == 1
    png, y, t0, t1 = ev[0]
    im = Image.open(png)
    assert y == 300 - legibility.PAD_Y and (t0, t1) == (0.0, 2.0)
    assert im.width == 2 * (max(540 - 200, 880 - 540) + legibility.PAD_X)
    assert im.getpixel((im.width // 2, im.height // 2))[3] == round(255 * 0.6)
    assert im.getpixel((0, 0))[3] == 0                    # rounded corner


def test_judge_gate_reads_the_measurement(tmp_path):
    root = tmp_path
    (root / "qc").mkdir()
    (root / "qc" / "text_contrast.json").write_text(json.dumps({"cut_01": [
        {"label": "hook", "scrim": 0.42, "pass": True, "backdrop_luma": 210},
        {"label": "game_name", "scrim": 0.0, "pass": True}]}))
    gate, status, detail = judge.text_contrast(str(root), {"id": "cut_01"})
    assert status == "PASS" and "hook (42%)" in detail
    (root / "qc" / "text_contrast.json").write_text(json.dumps({"cut_01": [
        {"label": "hook", "scrim": 0.82, "pass": False, "backdrop_luma": 20}]}))
    assert judge.text_contrast(str(root), {"id": "cut_01"})[1] == "FAIL"
    assert judge.text_contrast(str(root), {"id": "cut_02"})[1] == "SKIP"


def test_off_switch(monkeypatch):
    monkeypatch.setenv("REELLY_LEGIBILITY", "off")
    assert legibility.enabled() is False
