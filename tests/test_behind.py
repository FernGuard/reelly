"""Text behind the subject: opt-in per brand, faces-only profiles, and a
readability guard that keeps heavily covered text on top."""
import numpy as np
import pytest
from PIL import Image

from reelly import behind, shaderfx


def _masks(tmp_path, n, box_cover):
    """n masks 540x960 where a band (rows 300-400) is person over `box_cover`
    of its width."""
    pat = str(tmp_path / "m%05d.png")
    for i in range(n):
        m = np.zeros((960, 540), np.uint8)
        m[300:400, : int(540 * box_cover)] = 255
        Image.fromarray(m, "L").save(pat % i)
    return pat


def test_guard_keeps_covered_text_on_top(tmp_path):
    pat = _masks(tmp_path, 10, 0.7)
    box = (0.0, 0.3, 0.0, 600.0, 1080.0, 800.0)          # rows 300-400 at half size
    (b, cover, is_behind), = behind.guard(pat, 10, 30, [box])
    assert cover > 0.6 and not is_behind
    assert np.asarray(Image.open(pat % 3))[300:400].max() == 0   # cleared: text stays on top


def test_guard_allows_text_that_peeks_around(tmp_path):
    pat = _masks(tmp_path, 10, 0.2)
    (_, cover, is_behind), = behind.guard(pat, 10, 30, [(0.0, 0.3, 0.0, 600.0, 1080.0, 800.0)])
    assert cover < 0.25 and is_behind
    assert np.asarray(Image.open(pat % 3))[300:400].max() == 255  # the person stays over the text


def test_only_where_the_brand_asks_and_faces_matter(monkeypatch):
    from reelly import brandkit
    monkeypatch.delenv("REELLY_TEXT_BEHIND", raising=False)
    monkeypatch.delenv("REELLY_PROFILE", raising=False)
    monkeypatch.setattr(brandkit, "brand", lambda name=None: {"fx": {"text_behind": True}})
    shaderfx.set_profile("talking_head")
    assert behind.enabled()
    shaderfx.set_profile("gameplay")
    assert not behind.enabled()                     # never on gameplay
    monkeypatch.setattr(brandkit, "brand", lambda name=None: {})
    shaderfx.set_profile("talking_head")
    assert not behind.enabled()                     # the brand did not ask
    shaderfx.set_profile(None)


@pytest.mark.skipif(not behind.find_model(), reason="segmenter model not installed")
def test_masks_from_the_real_segmenter(tmp_path):
    import subprocess
    from reelly import config
    v = str(tmp_path / "v.mp4")
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=gray:s=1080x1920:r=30:d=1", "-pix_fmt", "yuv420p", v], check=True)
    pat, fps, n = behind.masks(v, str(tmp_path / "m"))
    assert n == 30 and Image.open(pat % 0).mode == "L"


def test_guided_filter_snaps_a_soft_mask_to_the_image_edge():
    """A blurry mask over a sharp boundary comes out sharp at that boundary."""
    rgb = np.zeros((64, 64, 3), np.uint8)
    rgb[:, 32:] = 220                                   # a hard edge at x = 32
    soft = np.tile(np.clip((np.arange(64) - 20) / 24.0, 0, 1), (64, 1)).astype(np.float32)
    out = behind.guided_filter(rgb, soft, r=4, eps=1e-4)
    row, before = out[32], soft[32]
    # the step between the two pixels either side of the image edge
    assert row[32] - row[31] > 3 * (before[32] - before[31])
    assert row[28] < before[28] and row[36] > before[36]      # pushed apart around it
