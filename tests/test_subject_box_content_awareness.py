"""Regressions for the content-awareness pass that silently no-opped.

The bug (2026-07-31): design.subject_box parsed the vision reply as 0..1
fractions, but gemini-3.5-flash returns 0..1000 per-mille integers (observed
live: [345, 50, 548, 949]). Every box was rejected as out-of-bounds, so
subject_box returned None on every frame, placement went content-blind, the
D1/D3 re-place loop had nothing to avoid, and lettering landed on faces. An
earlier shape ([[...]]) crashed outright with 'expected 4, got 1'.

Each test is named for the failure it prevents. No test makes a live API call:
the vision client is mocked exactly where design.subject_box calls it.
"""
from PIL import Image

from reelly import design, motion


def _reply(monkeypatch, resp):
    """Make design.subject_box see exactly this vision reply."""
    monkeypatch.setattr(design, "_gemini", lambda *a, **k: resp)


FRAME = Image.new("RGB", (1080, 1920))


# --- (1) the real cause: coordinate-scale parsing --------------------------

def test_subject_box_accepts_gemini_permille_coords_not_just_fractions(monkeypatch):
    """The core regression: gemini returns 0..1000 integers, not 0..1 floats.
    The old `0 <= n <= 1` guard rejected the LIVE reply on every frame."""
    _reply(monkeypatch, {"found": True, "box": [345, 50, 548, 949]})
    box = design.subject_box(FRAME)
    assert box is not None, "per-mille box must not be discarded as out-of-bounds"
    x, y, w, h = box
    assert 0 <= x < 1080 and 0 <= y < 1920 and w > 0 and h > 0
    # a tall centred subject: box spans most of the height, sits mid-width
    assert h > w
    assert 200 < x < 700


def test_subject_box_survives_nested_list_that_crashed_unpack(monkeypatch):
    """[[x0,y0,x1,y1]] is the shape that raised 'expected 4, got 1'. It must
    now be unwrapped to a real box, never crash."""
    _reply(monkeypatch, {"found": True, "box": [[0.1, 0.2, 0.3, 0.8]]})
    box = design.subject_box(FRAME)
    assert box is not None and len(box) == 4


def test_subject_box_parses_string_and_corner_dict_shapes(monkeypatch):
    """Models also return the box as a comma string or a corner dict."""
    for resp in ({"found": True, "box": "0.1, 0.2, 0.3, 0.8"},
                 {"found": True, "box": {"x0": 0.1, "y0": 0.2, "x1": 0.3, "y1": 0.8}},
                 {"found": True, "box": {"x": 108, "y": 384, "w": 216, "h": 1152}},
                 {"found": True, "box_2d": [50, 345, 949, 548]}):
        _reply(monkeypatch, resp)
        assert design.subject_box(FRAME) is not None, resp


def test_subject_box_recovers_swapped_corners(monkeypatch):
    """A model that emits x1,y1,x0,y0 should not be failed for it."""
    _reply(monkeypatch, {"found": True, "box": [548, 949, 345, 50]})
    assert design.subject_box(FRAME) is not None


def test_subject_box_rejects_truly_malformed_shapes_as_none(monkeypatch):
    """Genuinely unusable replies still return None instead of a bad box."""
    for resp in ({"found": True, "box": [0.5]},            # too short
                 {"found": True, "box": [True, 0.2, 0.3, 0.8]},  # bool poison
                 {"found": True, "box": [0.5, 0.5, 0.5, 0.5]},   # zero area
                 "not even json"):
        _reply(monkeypatch, resp)
        assert design.subject_box(FRAME) is None, resp


def test_subject_box_respects_found_false(monkeypatch):
    """found:false is a legitimate 'no subject', not a parse failure."""
    _reply(monkeypatch, {"found": False})
    assert design.subject_box(FRAME) is None


# --- (2) content-blind fallback must be LOUD, recorded, not a bare print ----

class _FakeFrame:
    size = (1080, 1920)


def test_content_blind_fallback_is_recorded_in_diag_not_just_printed(monkeypatch):
    """When no subject box survives, the plan/report must be able to say
    'content-blind fallback'. The old code only printed and moved on."""
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: _FakeFrame())
    monkeypatch.setattr(design, "subject_box", lambda frame, project="": None)
    diag = {"placement": "subject-aware"}
    boxes = motion._sample_subjects("v.mp4", 0.0, 4.0, "proj", diag=diag)
    assert boxes == []
    assert diag["placement"] == "content-blind fallback"
    assert diag["windows"] == [{"window": [0.0, 4.0], "boxes": 0}]


def test_working_boxes_stop_the_content_blind_fallback(monkeypatch):
    """With parsing fixed, real boxes flow through and placement stays
    subject-aware -- the whole point of the fix."""
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: _FakeFrame())
    monkeypatch.setattr(design, "subject_box",
                        lambda frame, project="": (300, 100, 200, 1200))
    diag = {"placement": "subject-aware"}
    boxes = motion._sample_subjects("v.mp4", 0.0, 4.0, "proj", diag=diag)
    assert boxes and diag["placement"] == "subject-aware"


# --- (4) the re-place loop must actually reposition given real boxes --------

def test_replace_loop_least_overlap_picks_a_clear_row():
    """The re-place loop ranks candidate rows by overlap with the subject. With
    working boxes the clear row must win -- this is what silently no-opped while
    every subject box was None (all candidates scored 0 overlap, so ties fell to
    the topmost row, straight onto the face)."""
    x, width, box_h = (1080 - 920) // 2, 920, 300
    # a real subject box: head + upper body across the top (y 120..640). Only the
    # lowest candidate (y=690..990) clears it; the fix must rank it first.
    subjects = [(280, 120, 520, 520)]
    ranked = motion._rank_rows([230, 460, 690], x, width, box_h, subjects)
    assert ranked[0] == 690, "least-overlap row must be the one clear of the subject"
    # and the overlapping rows are genuinely penalised, not tied at zero
    assert motion._box_overlap((x, 230, width, box_h), subjects) > 0
    assert motion._box_overlap((x, 690, width, box_h), subjects) == 0


# --- (3) D7 measured contrast gate -----------------------------------------

def _swatch(rgb, a=255):
    return Image.new("RGBA", (200, 80), rgb + (a,))


def test_d7_complementary_text_passes_without_a_scrim():
    """Warm gold on a cool scene is exactly what D7 wants: pass, no escalation."""
    r = design.contrast_gate(_swatch((230, 175, 40)), _swatch((40, 90, 200)))
    assert r["pass"] and not r["escalated"] and r["scrim"] == 0.0


def test_d7_warm_on_warm_escalates_the_scrim_instead_of_shipping_it():
    """Gold on a warm background must be
    detected and rescued by escalating the scrim, not shipped as-is."""
    r = design.contrast_gate(_swatch((230, 175, 40)), _swatch((200, 150, 60)))
    assert r["warm_on_warm"] and r["escalated"] and r["scrim"] > 0.0 and r["pass"]


def test_d7_unrescuable_dark_warm_text_fails_with_measured_values():
    """Dark warm text on a mid-warm backdrop cannot be saved by any scrim
    (darkening the backdrop only pushes it toward the text's own darkness). The
    gate must FAIL and hand back the numbers, not pretend a scrim fixed it."""
    r = design.contrast_gate(_swatch((110, 80, 30)), _swatch((150, 120, 70)))
    assert r["pass"] is False
    assert r["scrim"] == design.SCRIM_CAP
    for k in ("asset_luma", "backdrop_luma", "luma_gap", "asset_hue", "backdrop_hue"):
        assert k in r


def test_d7_greyscale_asset_reports_no_hue_but_still_measures_luma():
    """A near-greyscale swatch has no meaningful hue; the gate must not crash and
    must still return a luma so low-luma contrast can be judged."""
    hue, luma = design.hue_luma(_swatch((128, 128, 128)))
    assert hue is None and 100 < luma < 160


# --- (2)+(3) both failures surface in the design report, gate fails ---------

def test_design_report_records_content_blind_and_d7_fail_and_gate_fails(
        tmp_path, monkeypatch):
    """The vision critic can pass a frame while placement was content-blind and
    D7 measured a warm-on-warm it could not rescue. Neither may fail silently:
    the report must name both and the gate must FAIL."""
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: object())
    monkeypatch.setattr(design, "critique",
                        lambda *a, **k: {"pass": True, "issues": []})
    ai = {"hook": {"text": "H"}, "payoff": {"text": "P"}, "cta": "GO"}
    diag = {"placement": "content-blind fallback", "windows": [],
            "d7": [{"asset": "payoff.png", "y": 460, "pass": False,
                    "warm_on_warm": True, "low_luma": True, "escalated": True,
                    "scrim": 0.82, "asset_luma": 83, "backdrop_luma": 123,
                    "luma_gap": 40, "asset_hue": 40, "backdrop_hue": 45,
                    "hue_delta": 5}]}
    gate = motion._design_gate(str(tmp_path), "out.mp4", ai, 4, 8, "proj",
                               diag=diag)
    assert gate["pass"] is False, "a D7 measured fail must fail the whole gate"
    assert gate["placement"] == "content-blind fallback"
    report = (tmp_path / "qc" / "design_report.md").read_text()
    assert "content-blind fallback" in report
    assert "D7 measured contrast" in report
    assert "warm-on-warm" in report and "backdrop_luma=123" in report
