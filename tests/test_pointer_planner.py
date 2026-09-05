"""Auto pointer overlays (reviewer 2026-08-13): a transcript-driven planner that
points a circle/brackets at the thing the narrator names, located by vision,
rate-limited so it stays tasteful. Vision only fires on a real referent.
"""
from PIL import Image

from reelly import design, finalize, overlays


# --- design.locate_referent ---------------------------------------------------

def _frame():
    return Image.new("RGB", (1080, 1920))


def test_locate_referent_returns_pixel_box_and_shape(monkeypatch):
    monkeypatch.setattr(design, "_gemini",
                        lambda *a, **k: {"found": True, "box": [0.1, 0.2, 0.4, 0.5],
                                         "shape": "brackets"})
    loc = design.locate_referent(_frame(), "the blob shadow")
    assert loc["shape"] == "brackets"
    x, y, w, h = loc["box"]
    assert w > 0 and h > 0 and 0 <= x < 1080 and 0 <= y < 1920


def test_locate_referent_none_when_no_referent(monkeypatch):
    for reply in ({"found": False}, "junk", None):
        monkeypatch.setattr(design, "_gemini", lambda *a, **k: reply)
        assert design.locate_referent(_frame(), "nothing here") is None


# --- overlays.plan_pointers ---------------------------------------------------

WORDS = [
    {"t": "look", "s": 0.0, "e": 0.3}, {"t": "at", "s": 0.3, "e": 0.5},
    {"t": "the", "s": 0.5, "e": 0.7}, {"t": "blob.", "s": 0.7, "e": 1.0},
    {"t": "now", "s": 12.0, "e": 12.3}, {"t": "the", "s": 12.3, "e": 12.5},
    {"t": "layers.", "s": 12.5, "e": 13.0},
]


def _locate_all(monkeypatch, shape="circle"):
    monkeypatch.setattr(design, "locate_referent",
                        lambda f, p, project="": {"box": (100, 200, 300, 150), "shape": shape})


def test_plan_pointers_emits_pointers_for_located_phrases(monkeypatch):
    _locate_all(monkeypatch, "circle")
    evs = overlays.plan_pointers("v.mp4", WORDS, frame_at=lambda t: _frame())
    assert len(evs) == 2                       # two narration cues, both located
    assert all(e["template"] == "circle" for e in evs)
    assert evs[0]["t"][0] == 0.0 and evs[1]["t"][0] == 12.0
    assert "blob" in evs[0]["args"][4]         # the label is the phrase


def test_plan_pointers_skips_when_no_referent(monkeypatch):
    monkeypatch.setattr(design, "locate_referent", lambda f, p, project="": None)
    assert overlays.plan_pointers("v.mp4", WORDS, frame_at=lambda t: _frame()) == []


def test_plan_pointers_rate_limits_by_gap(monkeypatch):
    _locate_all(monkeypatch)
    # a big min_gap collapses the two 12s-apart cues to a single pointer
    evs = overlays.plan_pointers("v.mp4", WORDS, frame_at=lambda t: _frame(),
                                 min_gap_s=30.0)
    assert len(evs) == 1


def test_plan_pointers_caps_at_max(monkeypatch):
    _locate_all(monkeypatch)
    evs = overlays.plan_pointers("v.mp4", WORDS, frame_at=lambda t: _frame(),
                                 max_pointers=1, min_gap_s=1.0)
    assert len(evs) == 1


def test_plan_pointers_noop_without_transcript_or_frames():
    assert overlays.plan_pointers("v.mp4", [], frame_at=lambda t: _frame()) == []
    assert overlays.plan_pointers("v.mp4", WORDS, frame_at=None) == []


def test_plan_pointers_brackets_shape_passes_region_box(monkeypatch):
    _locate_all(monkeypatch, "brackets")
    evs = overlays.plan_pointers("v.mp4", WORDS, frame_at=lambda t: _frame())
    # brackets args are the raw region [x, y, w, h, label, lx, ly]
    assert evs[0]["template"] == "brackets"
    assert evs[0]["args"][:4] == [100, 200, 300, 150]


# --- finalize integration: opt-in + guarded -----------------------------------

def test_pointer_events_off_by_default(tmp_path):
    assert finalize._pointer_events({"id": "x"}, WORDS, str(tmp_path), "v.mp4") == []


def test_pointer_events_off_without_video(tmp_path):
    assert finalize._pointer_events({"id": "x", "pointers": True}, WORDS,
                                    str(tmp_path), None) == []


def test_pointer_events_render_rows_when_opted_in(tmp_path, monkeypatch):
    monkeypatch.setattr(overlays, "plan_pointers",
                        lambda *a, **k: [{"template": "circle",
                                          "args": [1, 2, 3, 4, "lbl", 5, 6],
                                          "t": [1.0, 2.0]}])
    monkeypatch.setattr(overlays, "TEMPLATES", {"circle": lambda *a: "<svg/>"})
    monkeypatch.setattr(overlays, "_render_png",
                        lambda wd, name, body, size=None: "/tmp/ptr.png")
    rows = finalize._pointer_events({"id": "t", "pointers": True}, WORDS,
                                    str(tmp_path), "v.mp4")
    assert rows == [("/tmp/ptr.png", 0, 1.0, 2.0)]
