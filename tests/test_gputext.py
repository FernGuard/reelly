"""GPU kinetic text: atlas, layout and page match the DOM kinetic contract."""
import json
import re

import pytest

from reelly import gputext, kinetic


def test_atlas_has_one_distance_tile_per_glyph():
    sheet, place = gputext.atlas("Wishlist on Steam", 72, "headline")
    assert set(place) == set("Wishlist on Steam") - {" "}
    x, y, w, h, ox, oy = place["W"]
    px = sheet.getpixel((x + w // 2, y + h // 2))
    assert sheet.getpixel((x, y)) == 0                       # far outside: 0
    assert oy < 0                                            # tile starts above the baseline
    lo, hi = sheet.crop((x, y, x + w, y + h)).getextrema()
    assert lo == 0 and hi > 150                              # a real distance field


def test_layout_centres_lines_and_matches_the_static_wrap():
    text = "Death threats delivered to your door."
    lines = kinetic.wrap_lines(text, 58, 800, "headline")
    glyphs, anchors, words = gputext.layout(text, 58, 800, "headline", lines, kinetic.PAD)
    assert words == text.split()
    assert len(glyphs) == len(text.replace(" ", ""))
    for li in range(len(lines)):
        xs = [g[1] for g in glyphs if g[4] == li]
        left, right = min(xs) - kinetic.PAD, 800 - (max(xs) - kinetic.PAD)
        assert left > 0 and abs(left - right) < 80          # centred (last glyph's width aside)
    assert len({round(g[2]) for g in glyphs}) == len(lines)  # one baseline per line


def test_vertex_data_and_page():
    text = "Wishlist on Steam"
    sheet, place = gputext.atlas(text, 72, "headline")
    lines = [text.split()]
    glyphs, anchors, _ = gputext.layout(text, 72, 800, "headline", lines, kinetic.PAD)
    v = gputext.vertex_data(glyphs, anchors, place, hot=0)
    assert len(v) == len(glyphs) * 6 * 8
    assert v[7] == 1.0 and v[-1] == 0.0                     # first word hot, last not
    html, settle, exit_t = gputext.page(text, 72, 856, 200, 3.0, "headline", 0, lines,
                                        True, False, 1.0, "#FCFCFB", "#a87cf0")
    params = json.loads(re.search(r"var P = (\{.*?\});\n", html, re.S).group(1))
    assert params["ease_type"] == [0.16, 0.84, 0.28, 1.0]
    assert params["ease_pop"] == [0.3, 1.6, 0.5, 1.0] and params["hot"] == 0
    assert params["pop"] == 1.0 and params["leave"] is False and exit_t == 3.0
    assert "requestAnimationFrame" not in html and "window.__reellyDraw = function(ms)" in html


def test_off_switch_and_word_limit(monkeypatch):
    monkeypatch.setenv("REELLY_GPU_TEXT", "off")
    assert not gputext.enabled()
    monkeypatch.delenv("REELLY_GPU_TEXT")
    assert gputext.enabled()
    with pytest.raises(ValueError):
        gputext.page(" ".join(["w"] * 41), 40, 800, 200, 3.0, "headline", None,
                     [["w"] * 41], True, True, None, "#ffffff", "#a87cf0")
