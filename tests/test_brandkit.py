"""Brand kit + copy linter tests.

The kit lives OUTSIDE the repo (~/.reelly/brandkit, env REELLY_BRANDKIT
overrides); every test here points REELLY_BRANDKIT at a tmp dir so nothing
touches the real machine kit. Sources (the registered wordmark PNGs) are
stubbed via _studio_logos so tests do not depend on this machine's
~/.reelly/config.json.
"""
import json
import os
import re

import pytest
from PIL import Image

from reelly import brandkit


@pytest.fixture
def kit(tmp_path, monkeypatch):
    """Isolated kit dir + one fake studio logo as the source asset."""
    monkeypatch.setenv("REELLY_BRANDKIT", str(tmp_path / "kit"))
    logo = tmp_path / "video-studio-logo.png"
    img = Image.new("RGBA", (400, 160), (0, 0, 0, 0))
    for x in range(400):
        for y in range(60, 100):
            img.putpixel((x, y), (23, 205, 255, 255))   # a saturated cyan mark
    img.save(logo)
    monkeypatch.setattr(brandkit, "_studio_logos",
                        lambda: {"video": str(logo)})
    return tmp_path / "kit", logo


# --- kit dir + graceful degradation -----------------------------------------

def test_env_var_overrides_kit_dir(monkeypatch):
    monkeypatch.setenv("REELLY_BRANDKIT", "/somewhere/else")
    assert brandkit.kit_dir() == "/somewhere/else"
    monkeypatch.delenv("REELLY_BRANDKIT")
    assert brandkit.kit_dir() == os.path.expanduser("~/.reelly/brandkit")


def test_everything_degrades_gracefully_with_no_kit(tmp_path, monkeypatch):
    """A missing kit returns None/{}/fallbacks: pipeline behavior identical
    to the pre-kit world. Nothing raises."""
    monkeypatch.setenv("REELLY_BRANDKIT", str(tmp_path / "nowhere"))
    assert brandkit.endcard("video") is None
    assert brandkit.outro("video") is None
    assert brandkit.font("caption") is None
    assert brandkit.music_manifest() == {}
    assert brandkit.accent() == brandkit.FALLBACK_ACCENT


def test_copy_bank_generates_its_default_on_first_use(kit):
    """The copy bank ships as code: first read writes the generated default
    into the kit dir (repo carries schema+code only, never data files)."""
    kit_dir, _ = kit
    bank = brandkit.copy_bank()
    assert (kit_dir / "copy_bank.yaml").exists()
    assert bank["limits"] == {"hook": 7, "payoff": 6, "cta": 4}
    assert bank["one_cta"] is True
    assert bank["banned"] == []
    # per-studio CTAs come from products end_tags, the single source of truth
    assert bank["ctas"]["video"] == "Edited with Reelly"
    assert bank["ctas"]["adventure"] == "Edited with Reelly"


# --- build_defaults ----------------------------------------------------------

def test_build_defaults_creates_tree_endcards_and_kit_json(kit):
    kit_dir, logo = kit
    summary = brandkit.build_defaults()
    for d in ("endcards", "outros", "fonts", "music"):
        assert (kit_dir / d).is_dir()
    assert summary["endcards_built"] == ["video"]
    ec = brandkit.endcard("video")
    assert ec and ec.endswith("video.png")
    im = Image.open(ec)
    assert im.size == (1080, 1920)
    meta = json.loads((kit_dir / "kit.json").read_text())
    assert meta["version"] == brandkit.KIT_VERSION
    assert meta["sources"]["video"] == brandkit._sha256(str(logo))
    assert re.fullmatch(r"#[0-9A-Fa-f]{6}", meta["accent"])
    # the accent was sampled from the mark's dominant (cyan) hue
    assert brandkit.accent() == meta["accent"]


def test_source_hash_change_marks_assets_stale_and_rebuild_rerenders(kit):
    kit_dir, logo = kit
    brandkit.build_defaults()
    assert brandkit.stale_studios() == []
    # the source wordmark changes -> derived assets are stale
    Image.new("RGBA", (400, 160), (255, 0, 0, 255)).save(logo)
    assert brandkit.stale_studios() == ["video"]
    summary = brandkit.build_defaults()
    assert summary["endcards_built"] == ["video"]
    assert brandkit.stale_studios() == []


def test_build_is_idempotent_when_nothing_changed(kit):
    brandkit.build_defaults()
    summary = brandkit.build_defaults()
    assert summary["endcards_built"] == []
    assert summary["endcards_kept"] == ["video"]


def test_endcard_prefers_animated_mov_over_the_static_png(kit):
    """Animated .mov endcards are the later manual upgrade; the accessor
    already prefers them the moment one is dropped into the kit."""
    kit_dir, _ = kit
    brandkit.build_defaults()
    mov = kit_dir / "endcards" / "video.mov"
    mov.write_bytes(b"stub")
    assert brandkit.endcard("video") == str(mov)
    # legacy alias keys resolve to the canonical studio
    assert brandkit.endcard("video") == str(mov)


def test_font_resolves_role_then_default_then_any(kit):
    kit_dir, _ = kit
    fonts = kit_dir / "fonts"
    fonts.mkdir(parents=True)
    (fonts / "zz-any.otf").write_bytes(b"f")
    assert brandkit.font("caption").endswith("zz-any.otf")
    (fonts / "default.ttf").write_bytes(b"f")
    assert brandkit.font("caption").endswith("default.ttf")
    (fonts / "caption.ttf").write_bytes(b"f")
    assert brandkit.font("caption").endswith("caption.ttf")


# --- lint_copy: the CODE gate in front of the sense gate ---------------------

GOOD = [
    ("Your story plays itself", "Viewers choose every twist", "Start free"),
    ("Build worlds before lunch", "One idea becomes a full game",
     "Play on example.invalid"),
    ("made with Reelly", "The whole cut in one take", "Try Reelly"),
]

BAD = [
    # hook 9 words: limit violation
    ("This hook is far too long to ever ship today",
     "Payoff fine", "Go now", "hook is"),

    # two chained asks: one-CTA rule
    ("Fine hook", "Fine payoff", "Download and subscribe", "chains asks"),
]


def test_lint_copy_passes_clean_copy(kit):
    for hook, payoff, cta in GOOD:
        assert brandkit.lint_copy(hook, payoff, cta, "video") == [], (hook, cta)


def test_lint_copy_catches_each_violation(kit):
    for hook, payoff, cta, expect in BAD:
        v = brandkit.lint_copy(hook, payoff, cta, "video")
        assert v and any(expect in x for x in v), (hook, cta, v)


def test_lint_copy_word_limits_per_field(kit):
    v = brandkit.lint_copy("one two three four five six seven eight",
                           "a b c d e f g h i", "w x y z z", "video")
    assert len([x for x in v if "words (limit" in x]) == 3


def test_payoff_layout_cap_is_six_words(kit):
    """#10: payoff is a layout input -- capped at 6 so it fits the top text band.
    Six passes, seven is rejected at copy time."""
    assert brandkit.lint_copy("Hook fine", "one two three four five six", "Go", "video") == []
    v = brandkit.lint_copy("Hook fine", "one two three four five six seven", "Go", "video")
    assert any("payoff is 7 words (limit 6)" in x for x in v)


def test_lint_copy_flags_empty_cta(kit):
    v = brandkit.lint_copy("A useful hook", "Payoff fine", "", "story")
    assert any("cta is empty" in x for x in v)


def test_lint_copy_uses_private_banned_names(kit, monkeypatch):
    bank = brandkit.copy_bank()
    bank["banned"] = ["OldBrand"]
    monkeypatch.setattr(brandkit, "copy_bank", lambda: bank)
    v = brandkit.lint_copy("OldBrand is back", "Payoff fine", "Go now", "video")
    assert any("OldBrand" in x for x in v)


def test_lint_copy_flags_misspelled_wordmarks(kit):
    """A lowercase rendition is not the configured wordmark."""
    v = brandkit.lint_copy("made in video project", "Payoff fine",
                           "watch now", "video")
    assert any("'video project' should read 'Video Project'" in x for x in v)


def test_lint_copy_multi_sentence_cta_is_two_asks(kit):
    v = brandkit.lint_copy("Fine hook", "Fine payoff",
                           "Play now. Tell friends.", "video")
    assert any("more than one ask" in x for x in v)
