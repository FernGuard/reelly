"""`reelly fx list` and `reelly fx preview`: every preset discoverable, and a
real preview renders as a 1080x1920 MP4 (where Chrome is available)."""
import json
import os
import subprocess

import pytest

from reelly import config, fxpreview, shaderfx


def test_the_list_names_every_preset_and_its_parameters():
    text = fxpreview.listing()
    for pid in shaderfx.presets():
        placement, name = pid.split("/", 1)
        assert f"\n  {name}\n" in text or text.startswith(f"{placement}\n  {name}\n")
    assert "u_density" in text and "(brand accent)" in text and "u_pic" not in text  # host inputs hidden


def test_names_resolve_or_explain():
    assert shaderfx.resolve("embers") == "ambient/embers"
    assert shaderfx.resolve("endcard/smoke") == "endcard/smoke"
    with pytest.raises(shaderfx.EffectError, match="several placements"):
        shaderfx.resolve("smoke")
    with pytest.raises(shaderfx.EffectError, match="no preset"):
        shaderfx.resolve("lasers")


def test_tried_parameters_are_checked_and_never_stick():
    with pytest.raises(shaderfx.EffectError):
        fxpreview.preview("embers", params={"u_densty": 0.5})
    assert shaderfx._TRY == {}
    shaderfx._TRY["ambient/embers"] = {"u_density": 0.9}
    try:
        assert shaderfx.brand_params("ambient/embers")["u_density"] == 0.9
    finally:
        shaderfx._TRY.clear()


@pytest.mark.skipif(not config.CHROME, reason="no Chrome")
def test_a_preview_renders(tmp_path):
    out = str(tmp_path / "embers.mp4")
    assert fxpreview.preview("embers", out, seconds=1.0, params={"u_density": 0.6}) == out
    info = json.loads(subprocess.run(
        [config.FFPROBE, "-v", "error", "-show_entries", "stream=width,height:format=duration",
         "-of", "json", out], capture_output=True, text=True).stdout)
    st = info["streams"][0]
    assert (st["width"], st["height"]) == (1080, 1920)
    assert abs(float(info["format"]["duration"]) - 1.0) < 0.1
    assert shaderfx._TRY == {} and os.path.getsize(out) > 10_000
