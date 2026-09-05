"""Caption preset "dynamic minimalism" (2026 taste bar).

Clean sans, white body + ONE brand accent on the highlighted word, mixed
case, color-swap highlight only. The style layer must not change any function
signature: finalize/burnin call karaoke_png/text_png/hook_png exactly as
before and get the new style for free.
"""
import os

from PIL import Image

from reelly import brandkit, captions


def _colors(path):
    return {c for _, c in Image.open(path).convert("RGB").getcolors(1 << 20)}


# --- the accent chain --------------------------------------------------------

def test_explicit_accent_still_wins(tmp_path):
    p = str(tmp_path / "k.png")
    captions.karaoke_png(["alpha", "beta"], 0, p, accent="#FF0000")
    assert (255, 0, 0) in _colors(p)


def test_default_karaoke_highlight_is_hot_pink(tmp_path, monkeypatch):
    """accent=None (what finalize/burnin pass by omission) now resolves to the
    Hot Pink caption signature, NOT the per-studio brand accent (reviewer
    2026-08-13). The brand accent must not appear in a karaoke cue."""
    monkeypatch.setattr(brandkit, "accent", lambda: "#00FF00")
    monkeypatch.setitem(captions.DEFAULT_STYLE, "accent", None)
    p = str(tmp_path / "k.png")
    captions.karaoke_png(["alpha", "beta"], 1, p)
    cols = _colors(p)
    assert (0, 255, 0) not in cols, "the brand accent must not colour karaoke"
    assert (255, 105, 180) in cols, "the highlighted word is Hot Pink"


def test_accent_falls_back_to_blue_smoke_without_a_kit(tmp_path, monkeypatch):
    """No kit, brandkit unusable -> exactly the pre-kit color, so behavior is
    identical to today when the kit is missing."""
    def boom():
        raise RuntimeError("no kit")
    monkeypatch.setattr(brandkit, "accent", boom)
    monkeypatch.setitem(captions.DEFAULT_STYLE, "accent", None)
    assert captions._style_accent() == "#17CDFF"


def test_style_accent_prefers_pinned_style_value(monkeypatch):
    monkeypatch.setitem(captions.DEFAULT_STYLE, "accent", "#ABCDEF")
    assert captions._style_accent() == "#ABCDEF"
    assert captions._style_accent("#111111") == "#111111"


# --- one accent word, white body, mixed case ---------------------------------

def test_only_the_highlighted_word_takes_the_accent(tmp_path):
    """White body + the single spoken word in the accent: two renders of the
    same cue with different hi_index differ, and both carry white + accent."""
    a, b = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    captions.karaoke_png(["one", "two", "three"], 0, a, accent="#FF3300")
    captions.karaoke_png(["one", "two", "three"], 2, b, accent="#FF3300")
    ca, cb = _colors(a), _colors(b)
    assert (255, 51, 0) in ca and (255, 255, 255) in ca
    assert (255, 51, 0) in cb and (255, 255, 255) in cb
    assert Image.open(a).tobytes() != Image.open(b).tobytes()


def test_mixed_case_contract_is_pinned():
    """Words render as spoken; no caller upper()-coerces (verified across
    captions/finalize/burnin/preview) and the style layer pins that."""
    assert captions.DEFAULT_STYLE["mixed_case"] is True
    assert captions.DEFAULT_STYLE["highlight"] == "color"   # no scale pop
    assert captions.DEFAULT_STYLE["fade"] is True


# --- signatures + fallbacks stay intact --------------------------------------

def test_call_shapes_used_by_finalize_and_burnin_still_work(tmp_path):
    """The exact positional shapes the (untouched) burn paths use."""
    k = captions.karaoke_png(["hey", "there"], 1, str(tmp_path / "k.png"))
    t = captions.text_png("made with Video Project on example.invalid",
                          str(tmp_path / "t.png"),
                          width=900, size=44, fill="#FCFCFB", stroke_w=5)
    h = captions.hook_png("Your story plays itself", str(tmp_path / "h.png"))
    for p in (k, t, h):
        assert os.path.exists(p)
    assert captions.block_height("two line hook that wraps around", width=400) > 0


def test_kit_font_is_preferred_and_system_fonts_remain_the_fallback(monkeypatch):
    real = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    monkeypatch.setattr(brandkit, "font", lambda role="caption": real)
    assert captions._font_paths()[0] == real
    monkeypatch.setattr(brandkit, "font", lambda role="caption": None)
    assert captions._font_paths() == captions.FONTS
    f = captions._font(30)      # never raises, whatever the kit state
    assert f is not None
