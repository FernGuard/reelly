"""ONE layout for TikTok, Reels and Shorts: the platform chrome is modelled
as regions (layout.UI_ZONES) and no text of ours may enter them."""
import pytest

from reelly import captions, judge, layout, motion, overlays, placement


def test_every_module_reads_the_one_safe_area():
    assert placement.SAFE == {"top": layout.SAFE_TOP, "bottom": layout.SAFE_BOTTOM,
                              "left": layout.SAFE_LEFT, "right": layout.SAFE_RIGHT}
    assert (overlays.SAFE_TOP, overlays.SAFE_BOTTOM_Y, overlays.SAFE_RIGHT_X,
            overlays.SAFE_LEFT_X) == (layout.SAFE_TOP, layout.SAFE_BOTTOM,
                                      layout.SAFE_RIGHT, layout.SAFE_LEFT)
    assert motion.SAFE_TOP == layout.SAFE_TOP
    assert layout.FRAME_H - motion.SAFE_BOTTOM == layout.SAFE_BOTTOM
    assert layout.FRAME_W - motion.SAFE_RIGHT == layout.RAIL_X
    assert placement.CAPTION_BAND == tuple(layout.FULL_BOTTOM)


def test_text_bands_sit_outside_the_chrome():
    assert layout.FULL_CENTER[0] >= layout.SAFE_TOP
    assert layout.FULL_BOTTOM[1] <= layout.SAFE_BOTTOM
    z = layout.zones(None, None)
    for band in z.values():
        assert band[0] >= layout.SAFE_TOP and band[1] <= layout.SAFE_BOTTOM


def test_split_bottom_band_never_enters_the_caption_block():
    z = layout.zones({"cam_h": 600}, "split")
    if z["bottom"]:
        assert z["bottom"][1] <= layout.SAFE_BOTTOM


def test_centered_width_respects_the_rail_lower_down():
    assert layout.centered_width(300, 400) == 2 * (layout.SAFE_RIGHT - 540)
    assert layout.centered_width(900, 1000) == 2 * (layout.RAIL_X - 540)
    assert captions.HOOK_WIDTH == layout.centered_width(*layout.FULL_CENTER)
    assert captions.CUE_WIDTH == layout.centered_width(*layout.FULL_BOTTOM)


@pytest.mark.parametrize("box, hit", [
    ((140, 300, 940, 400), []),
    ((140, 200, 940, 300), ["header"]),
    ((100, 1200, 880, 1300), ["bottom_ui"]),
    ((150, 900, 930, 960), ["right_rail"]),
    ((20, 500, 300, 560), ["left_edge"]),
])
def test_ui_hits(box, hit):
    assert layout.ui_hits(*box) == hit


def _plan(**kw):
    p = {"id": "cut_01", "segments": [[0.0, 8.0]], "duration_s": 10.8,
         "content_s": 8.0, "captions": "none", "game_name": "Game Name",
         "hook": {"text": "The door opens.", "show_s": 3.6},
         "overlay_lines": [{"t": 4.4, "text": "Not this one again.", "show_s": 3.0}],
         "cta": "Follow for more", "outro": {"len_s": 2.8, "style": "final_frame"}}
    p.update(kw)
    return p


def test_judge_passes_the_default_layout(monkeypatch):
    monkeypatch.delenv("REELLY_BRAND", raising=False)
    gate, status, detail = judge.ui_zones(_plan(), [])
    assert gate == "ui_zones" and status == "PASS", detail


def test_judge_fails_text_under_the_chrome(monkeypatch):
    monkeypatch.setattr(layout, "FULL_CENTER", (210, 430))
    gate, status, detail = judge.ui_zones(_plan(), [])
    assert status == "FAIL" and "header" in detail


def test_judge_reads_the_layout_positions():
    rows = judge._text_layers(_plan(), [])
    hook = next(r for r in rows if r[4] == "hook")
    assert hook[2] == layout.plan_text(_plan(), None)["hook"]["y"]
    assert any(r[4] == "game_name" for r in rows)


def test_text_sits_just_above_a_full_width_picture():
    z = layout._zones_full(None, 1.0)
    top = layout.picture_top(1.0)
    assert top == 656
    assert z["game_label"][1] == top - layout.PICTURE_GAP
    assert z["center"][1] == z["game_label"][0]
    assert z["center"][0] >= layout.SAFE_TOP


def test_zoomed_pictures_keep_the_default_bands():
    assert layout._zones_full(None, 2.0) == layout._zones_full(None, None)
    assert layout._zones_full(None, 1.5)["center"][0] >= layout.SAFE_TOP


def test_plan_text_follows_the_plan_reframe():
    full = layout.plan_text({"hook": {"text": "x"}, "duration_s": 10, "reframe": 1.0}, None)
    zoom = layout.plan_text({"hook": {"text": "x"}, "duration_s": 10, "reframe": 1.5}, None)
    assert full["hook"]["y"] > zoom["hook"]["y"]
