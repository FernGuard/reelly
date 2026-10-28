"""Per-brand type system: brands/<name>/brand.json in the kit gives a run its
fonts per role, accent, ink and surface. No active brand = the old behaviour."""
import json
import os
import shutil

import pytest
from PIL import Image

from reelly import brandkit, captions, overlays

_SYSTEM_FONTS = [p for p in captions.FONTS if os.path.exists(p)]


def _two_fonts():
    """Two different font files available on this machine (skip otherwise)."""
    extra = ["/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
             "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"]
    found = [p for p in _SYSTEM_FONTS + extra
             if os.path.exists(p) and p.endswith(".ttf")]
    if len(found) < 2:
        pytest.skip("needs two .ttf fonts on this machine")
    return found[0], found[1]


@pytest.fixture
def kit(tmp_path, monkeypatch):
    root = tmp_path / "kit"
    monkeypatch.setenv("REELLY_BRANDKIT", str(root))
    monkeypatch.delenv("REELLY_BRAND", raising=False)
    monkeypatch.delenv("REELLY_KARAOKE_COLOR", raising=False)
    brandkit._WARNED.clear()
    return root


def _make_brand(root, name, fonts=None, **colors):
    d = root / "brands" / name
    d.mkdir(parents=True)
    spec = {"name": name, "fonts": {}}
    for role, src in (fonts or {}).items():
        fn = f"{role}{os.path.splitext(src)[1]}"
        shutil.copyfile(src, d / fn)
        spec["fonts"][role] = fn
    spec.update(colors)
    (d / "brand.json").write_text(json.dumps(spec))
    return d


def test_no_brand_is_the_old_behaviour(kit):
    assert brandkit.brand_name() is None
    assert brandkit.brand() == {}
    assert brandkit.accent() == brandkit.FALLBACK_ACCENT
    assert brandkit.ink() is None and brandkit.surface() is None
    assert brandkit.font("headline") is None
    assert captions.karaoke_highlight() == captions.KARAOKE_HIGHLIGHT
    assert brandkit.font_css() == ""


def test_active_brand_gives_fonts_per_role(kit, monkeypatch):
    head, cap = _two_fonts()
    d = _make_brand(kit, "curses", {"headline": head, "caption": cap},
                    accent="#a87cf0", ink="#ebeae7", surface="#0b0b0c")
    monkeypatch.setenv("REELLY_BRAND", "curses")
    assert brandkit.font("headline") == str(d / f"headline{os.path.splitext(head)[1]}")
    assert brandkit.font("caption") == str(d / f"caption{os.path.splitext(cap)[1]}")
    assert brandkit.accent() == "#a87cf0"
    assert brandkit.ink() == "#ebeae7"
    assert brandkit.surface() == "#0b0b0c"


def test_headline_borrows_caption_when_the_brand_has_none(kit, monkeypatch):
    _, cap = _two_fonts()
    d = _make_brand(kit, "toxtli", {"caption": cap}, accent="#ffa322")
    monkeypatch.setenv("REELLY_BRAND", "toxtli")
    assert brandkit.font("headline") == brandkit.font("caption")
    assert brandkit.font("headline").startswith(str(d))


def test_default_brand_from_kit_json(kit):
    _make_brand(kit, "studio", accent="#ebeae7")
    (kit / "kit.json").write_text(json.dumps({"default_brand": "studio"}))
    assert brandkit.brand_name() == "studio"
    assert brandkit.accent() == "#ebeae7"


def test_env_brand_beats_default_brand(kit, monkeypatch):
    _make_brand(kit, "studio", accent="#ebeae7")
    _make_brand(kit, "curses", accent="#a87cf0")
    (kit / "kit.json").write_text(json.dumps({"default_brand": "studio"}))
    monkeypatch.setenv("REELLY_BRAND", "curses")
    assert brandkit.accent() == "#a87cf0"


def test_missing_brand_warns_once_and_falls_back(kit, monkeypatch, capsys):
    kit.mkdir()
    monkeypatch.setenv("REELLY_BRAND", "nope")
    assert brandkit.accent() == brandkit.FALLBACK_ACCENT
    brandkit.accent()
    assert capsys.readouterr().out.count("WARNING: brand 'nope'") == 1


@pytest.mark.parametrize("bad", ["../etc", "a/b", "..", "x y"])
def test_unsafe_brand_names_are_ignored(kit, monkeypatch, bad):
    monkeypatch.setenv("REELLY_BRAND", bad)
    assert brandkit.brand_name() is None


def test_brand_font_files_cannot_escape_the_brand_folder(kit, monkeypatch):
    d = kit / "brands" / "evil"
    d.mkdir(parents=True)
    (d / "brand.json").write_text(json.dumps({"fonts": {"caption": "../../x.ttf"}}))
    monkeypatch.setenv("REELLY_BRAND", "evil")
    assert brandkit._brand_font("caption") is None


def test_bad_colors_are_ignored(kit, monkeypatch):
    _make_brand(kit, "b", accent="purple", ink="#12")
    monkeypatch.setenv("REELLY_BRAND", "b")
    assert brandkit.accent() == brandkit.FALLBACK_ACCENT
    assert brandkit.ink() is None


def test_karaoke_highlight_is_the_brand_accent(kit, monkeypatch):
    _make_brand(kit, "curses", accent="#a87cf0")
    monkeypatch.setenv("REELLY_BRAND", "curses")
    assert captions.karaoke_highlight() == "#a87cf0"
    monkeypatch.setenv("REELLY_KARAOKE_COLOR", "#00ff00")
    assert captions.karaoke_highlight() == "#00ff00"


def test_hook_uses_the_headline_face_and_brand_ink(kit, monkeypatch, tmp_path):
    head, cap = _two_fonts()
    _make_brand(kit, "curses", {"headline": head, "caption": cap}, ink="#ebeae7")
    monkeypatch.setenv("REELLY_BRAND", "curses")
    roles = []
    real = captions._font
    monkeypatch.setattr(captions, "_font",
                        lambda size, role=None: (roles.append(role), real(size, role))[1])
    out = str(tmp_path / "hook.png")
    captions.hook_png("Someone is knocking.", out)
    assert roles and set(roles) == {"headline"}
    import numpy as np
    a = np.asarray(Image.open(out).convert("RGBA")).reshape(-1, 4).astype(int)
    # the brightest fully drawn pixel is the brand ink, not pure white
    solid = a[a[:, 3] == 255]
    px = solid[solid[:, :3].sum(axis=1).argmax()]
    assert tuple(px[:3]) == (0xeb, 0xea, 0xe7)


def test_hook_block_height_matches_the_rendered_hook(kit, monkeypatch, tmp_path):
    head, cap = _two_fonts()
    _make_brand(kit, "curses", {"headline": head, "caption": cap})
    monkeypatch.setenv("REELLY_BRAND", "curses")
    text = "Someone is knocking on the door again tonight."
    out = str(tmp_path / "h.png")
    captions.hook_png(text, out)
    assert Image.open(out).height == captions.hook_block_height(text)


def test_overlay_page_serves_brand_fonts_locally(kit, monkeypatch):
    head, cap = _two_fonts()
    _make_brand(kit, "curses", {"headline": head, "caption": cap}, accent="#a87cf0")
    monkeypatch.setenv("REELLY_BRAND", "curses")
    page = overlays._head()
    assert "fonts.googleapis.com" not in page
    assert "@font-face" in page and "file://" in page
    assert "!important" in page and "#a87cf0" in page


def test_overlay_page_unchanged_without_brand_fonts(kit):
    assert overlays._head() == overlays.HEAD


def test_hook_fits_inside_the_safe_right_edge(kit, monkeypatch, tmp_path):
    """A wide display face steps down in size instead of running past x 940."""
    import numpy as np
    head, cap = _two_fonts()
    _make_brand(kit, "curses", {"headline": head, "caption": cap})
    monkeypatch.setenv("REELLY_BRAND", "curses")
    text = "Someone is knocking at the door."
    out = str(tmp_path / "h.png")
    captions.hook_png(text, out)
    im = Image.open(out)
    assert im.width == captions.HOOK_WIDTH
    a = np.asarray(im.convert("RGBA"))[..., 3]
    cols = np.where((a > 0).any(axis=0))[0]
    left = (1080 - im.width) // 2
    assert left + cols.max() <= 940 and left + cols.min() >= 60
    assert captions.HOOK_MIN_SIZE <= captions.hook_size(text) <= captions.HOOK_SIZE


def test_short_hook_keeps_full_size(kit):
    assert captions.hook_size("Open it.") == captions.HOOK_SIZE
