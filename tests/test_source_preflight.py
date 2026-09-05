"""P1 #5: screen the SOURCE before any paid render. Six of nine content faults
were visible in the source before a cent was spent; this gate refuses the bad
ones with a named reason, auto-crops a squash, and warns on the rest.

Every test drives motion._preflight_source against a temp source file with the
design signals (baked-text boxes, faces, hue) stubbed -- no live vision calls.

Burned-in text is NOT a blanket reject (reviewer 2026-08-18): a source may BE a
screen of text (a terminal, a screenplay page), and there is one no-overlap
layout authority (layout.occupied) that routes the caption clear of it. So text
boxes are registered as static avoid-bands and the source is refused ONLY when
text blankets the frame so no caption zone is left free.
"""
import pytest
from PIL import Image

from reelly import design, layout, motion


def _src(tmp_path, size=(1080, 1920), color=(30, 30, 30)):
    p = tmp_path / "source.png"
    Image.new("RGB", size, color).save(p)
    return str(p)


@pytest.fixture(autouse=True)
def _quiet_signals(monkeypatch):
    """Default: a clean source -- no text, no faces, no hue. Each test overrides
    only the signal it exercises."""
    monkeypatch.setattr(design, "occupancy_text", lambda *a, **k: [])
    monkeypatch.setattr(design, "occupancy_local",
                        lambda *a, **k: {"faces": [], "subjects": [],
                                         "text_regions": []})
    monkeypatch.setattr(design, "hue_luma", lambda *a, **k: (None, 0.0))
    monkeypatch.delenv("REELLY_SKIP_PREFLIGHT", raising=False)


# --- (fault 1) burned-in text -> avoid-band, not a reject ---------------------

def test_a_headline_is_registered_as_an_avoid_band_not_refused(tmp_path, monkeypatch):
    """A source with a title band is no longer refused: it is rendered and the
    band is returned so the caption routes around it."""
    # a headline near the top: pixel box (x, y, w, h) in the 1080x1920 source
    monkeypatch.setattr(design, "occupancy_text",
                        lambda *a, **k: [(80, 120, 900, 140)])
    bands = motion._preflight_source(_src(tmp_path), real_art=False)
    assert bands, "the text band is returned for the layout authority"
    y0, y1 = bands[0]
    assert y0 < 300 and y1 > 200, "band covers the headline's vertical extent"


def test_a_clean_source_returns_no_bands(tmp_path):
    assert motion._preflight_source(_src(tmp_path), real_art=False) == []


def test_text_blanketing_every_caption_zone_is_refused(tmp_path, monkeypatch):
    """When baked text covers the whole frame there is nowhere clean for the
    caption -- THAT is refused, with a named reason."""
    monkeypatch.setattr(design, "occupancy_text",
                        lambda *a, **k: [(0, 0, 1080, 1920)])
    with pytest.raises(motion.SourceRejected, match="blanketed with burned-in text"):
        motion._preflight_source(_src(tmp_path), real_art=False)


def test_a_full_screen_of_text_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setattr(design, "occupancy_text",
                        lambda *a, **k: [(0, 0, 1080, 1920)])
    monkeypatch.setenv("REELLY_SKIP_PREFLIGHT", "1")
    # override clears the hard fault; still returns the bands to route around
    assert motion._preflight_source(_src(tmp_path), real_art=False)


def test_text_boxes_are_scaled_from_source_space_into_render_space(tmp_path, monkeypatch):
    """A box on a half-height source maps to twice its y in the 1920-tall frame."""
    monkeypatch.setattr(design, "occupancy_text",
                        lambda *a, **k: [(0, 100, 200, 100)])  # y=100..200 of 960
    bands = motion._preflight_source(_src(tmp_path, size=(540, 960)), real_art=False)
    y0, y1 = bands[0]
    assert 180 <= y0 <= 210 and 390 <= y1 <= 420, "scaled by 1920/960 = 2x"


# --- (fault 5) character art as a real-art source -----------------------------

def test_real_art_source_with_faces_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(design, "occupancy_local",
                        lambda *a, **k: {"faces": [(10, 10, 50, 50)],
                                         "subjects": [], "text_regions": []})
    with pytest.raises(motion.SourceRejected, match="character face"):
        motion._preflight_source(_src(tmp_path), real_art=True)


def test_faces_in_a_non_real_art_source_are_fine(tmp_path, monkeypatch):
    """The invented-character path WANTS a character reference; faces only block
    the real-art (camera-on-art) treatment."""
    monkeypatch.setattr(design, "occupancy_local",
                        lambda *a, **k: {"faces": [(10, 10, 50, 50)],
                                         "subjects": [], "text_regions": []})
    assert motion._preflight_source(_src(tmp_path), real_art=False) == []


# --- (fault 2) aspect: auto-crop, do not squash -------------------------------

def test_landscape_source_is_center_cropped_to_portrait(tmp_path):
    """A 1200x630 og-image is cropped to ~9:16 in place, not refused -- a warn,
    and the saved file is now portrait."""
    src = _src(tmp_path, size=(1200, 630))
    motion._preflight_source(src, real_art=False)
    w, h = Image.open(src).size
    assert h > w, "cropped to portrait"
    assert abs(w / h - motion.TARGET_ASPECT) < 0.02


def test_on_target_portrait_is_not_cropped(tmp_path):
    src = _src(tmp_path, size=(1080, 1920))
    motion._preflight_source(src, real_art=False)
    assert Image.open(src).size == (1080, 1920)


# --- (fault 3) hue clash ------------------------------------------------------

def test_hue_within_tolerance_warns_but_does_not_refuse(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(design, "hue_luma", lambda *a, **k: (15.0, 120.0))
    # intended type hue 20deg -> 5deg apart -> warn
    motion._preflight_source(_src(tmp_path), real_art=False, spec={"type_hue": 20.0})
    assert "low-contrast" in capsys.readouterr().out


def test_no_type_hue_declared_skips_the_hue_check(tmp_path, monkeypatch):
    """Without a declared type hue the check is skipped, never guessed."""
    called = {"n": 0}
    monkeypatch.setattr(design, "hue_luma",
                        lambda *a, **k: called.__setitem__("n", 1) or (10.0, 100.0))
    motion._preflight_source(_src(tmp_path), real_art=False, spec={})
    assert called["n"] == 0


# --- the layout authority actually routes around the source bands -------------

def test_layout_occupied_includes_source_text_bands(tmp_path):
    """The bands pre-flight returns become always-active occupancy the placers
    consult, so a caption is never stacked on the burned-in text."""
    plan = {"composition": None, "duration_s": 4.0,
            "hook": {"text": "hi", "show_s": 2.0},
            "source_text_bands": [(120, 300)]}
    at_any_time = layout.occupied(plan, template=None, t=1.0)
    assert (120, 300) in at_any_time
