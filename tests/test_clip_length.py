"""Clip length window: public default 20-28 s; a brand brief ("clip_len")
or --length / REELLY_CLIP_LEN allow short beat cuts."""
import json
import os

import pytest

from reelly import cli, direct, performance


@pytest.fixture
def brand(tmp_path, monkeypatch):
    d = tmp_path / "kit" / "brands" / "game-a"
    d.mkdir(parents=True)
    (d / "brand.json").write_text(json.dumps({"copy": {"clip_len": [12, 28]}}))
    monkeypatch.setenv("REELLY_BRANDKIT", str(tmp_path / "kit"))
    monkeypatch.setenv("REELLY_BRAND", "game-a")
    monkeypatch.delenv("REELLY_CLIP_LEN", raising=False)


def test_default_window(monkeypatch):
    monkeypatch.delenv("REELLY_CLIP_LEN", raising=False)
    monkeypatch.delenv("REELLY_BRAND", raising=False)
    assert performance.target_len() == performance.TARGET_LEN == (20.0, 28.0)


def test_brand_window_and_env_override(brand, monkeypatch):
    assert performance.target_len() == (12.0, 28.0)
    assert performance.length_verdict(14.0)[0] == "ok"
    monkeypatch.setenv("REELLY_CLIP_LEN", "15-20")
    assert performance.target_len() == (15.0, 20.0)
    assert performance.length_verdict(25.0)[0] == "flag"


@pytest.mark.parametrize("bad", ["20", "30-10", "1-4", "x-y"])
def test_bad_windows_fall_back(monkeypatch, bad):
    monkeypatch.delenv("REELLY_BRAND", raising=False)
    monkeypatch.setenv("REELLY_CLIP_LEN", bad)
    assert performance.target_len() == performance.TARGET_LEN


def test_the_prompts_carry_the_window(brand):
    kw = direct._len_kw()
    vis = direct.VISUAL_REFINE_PROMPT.format(why="w", visuals="v", scenes="s",
                                             peaks="p", duration=60.0, **kw)
    assert "Total duration 12 to 28 seconds" in vis and "ONE BEAT" in vis
    assert "F9 one_beat" in vis and "20 to 28" not in vis
    sp = direct.REFINE_PROMPT.format(why="w", visuals="v", peaks="p", sents="s",
                                     payoffs="x", **kw)
    assert "Total 12 to 28 seconds" in sp


def test_length_flag_anywhere(monkeypatch):
    # let monkeypatch own the variable first, so teardown restores "unset"
    monkeypatch.setenv("REELLY_CLIP_LEN", "placeholder")
    assert cli._global_flags(["direct", "p", "--length", "12-20"]) == ["direct", "p"]
    assert os.environ["REELLY_CLIP_LEN"] == "12-20"
