"""Step 3: the reserved text zones must actually be clear, and a render that
fails the style/band gate must re-render once, then be flagged for review rather
than silently shipped over the subject (reviewer 2026-08-13).
"""
from reelly import motion


# --- zone geometry ------------------------------------------------------------

def test_zone_overlap_fraction_measures_coverage():
    # a full-width band filling the whole top zone -> ~100% coverage
    box = (0, 0, motion.FRAME_W, motion.TEXT_ZONE_TOP_Y)
    assert motion._zone_overlap_frac([box], 0, motion.TEXT_ZONE_TOP_Y) > 0.95
    # a box entirely in the middle -> 0 coverage of the top zone
    mid = (400, motion.TEXT_ZONE_TOP_Y + 100, 200, 200)
    assert motion._zone_overlap_frac([mid], 0, motion.TEXT_ZONE_TOP_Y) == 0.0


# --- band gate ----------------------------------------------------------------

def _occ(faces=(), subjects=()):
    return {"faces": list(faces), "subjects": list(subjects), "text_regions": []}


def test_band_gate_ignores_scenery_detail_only_faces_matter(monkeypatch, tmp_path):
    # a busy scene fills the zones with 'subjects' (edge detail) but NO faces --
    # this must PASS: the 40% plate handles scenery legibility.
    busy = [(0, 0, motion.FRAME_W, motion.TEXT_ZONE_TOP_Y),
            (0, motion.TEXT_ZONE_BOTTOM_Y, motion.FRAME_W, 400)]
    subj_mid = (340, motion.TEXT_ZONE_TOP_Y + 50, 400, 300)
    monkeypatch.setattr(motion, "_sample_occupancy",
                        lambda *a, **k: _occ(subjects=busy + [subj_mid]))
    g = motion._band_clear_gate("v.mp4", 4, 8, "p", str(tmp_path))
    assert g["pass"] and g["top"] and g["bottom"]
    assert (tmp_path / "qc" / "band_report.md").read_text().count("CLEAR") == 2


def test_band_gate_blocks_when_a_face_enters_a_text_zone(monkeypatch, tmp_path):
    face_top = (300, 120, 480, 300)          # a face centred in the top text zone
    monkeypatch.setattr(motion, "_sample_occupancy", lambda *a, **k: _occ(faces=[face_top]))
    g = motion._band_clear_gate("v.mp4", 4, 8, "p", str(tmp_path))
    assert g["pass"] is False and g["top"] is False and g["bottom"] is True
    assert "BLOCKED" in (tmp_path / "qc" / "band_report.md").read_text()


def test_band_gate_allows_a_face_that_only_grazes_the_zone_edge(monkeypatch, tmp_path):
    # the real test4 case: the running cat's head-top clips ~97px into the top
    # zone (y 575..672) but the face CENTRE (~y845) is in the middle band -> PASS.
    graze = (306, 575, 535, 541)
    assert graze[1] < motion.TEXT_ZONE_TOP_Y < graze[1] + graze[3] / 2.0
    monkeypatch.setattr(motion, "_sample_occupancy", lambda *a, **k: _occ(faces=[graze]))
    g = motion._band_clear_gate("v.mp4", 4, 8, "p", str(tmp_path))
    assert g["pass"] and g["top"] and g["bottom"]


# --- escalation ---------------------------------------------------------------

def test_escalation_note_names_which_zone_a_face_invaded():
    note = motion._escalation_note({"top": False, "bottom": True})
    assert "top" in note.lower() and "clear" in note.lower()
    assert motion._escalation_note({"top": True, "bottom": True}) == ""


# --- the checked-generation re-render loop ------------------------------------

def _wire(monkeypatch, style_seq, band_seq):
    calls = {"gen": 0}
    monkeypatch.setattr(motion, "_character_frame", lambda *a, **k: "c.png")
    monkeypatch.setattr(motion, "_background_frame", lambda *a, **k: "b.png")
    monkeypatch.setattr(motion, "_dur", lambda v: 8)
    monkeypatch.setattr(motion, "os", motion.os)  # keep os
    monkeypatch.setattr(motion, "_write_review_preview", lambda *a, **k: None)

    def gen(*a, **k):
        calls["gen"] += 1
        return ("base.mp4", 4, 8)
    monkeypatch.setattr(motion, "_generate", gen)
    styles = iter(style_seq)
    bands = iter(band_seq)
    monkeypatch.setattr(motion, "_style_gate", lambda *a, **k: next(styles))
    monkeypatch.setattr(motion, "_band_clear_gate", lambda *a, **k: next(bands))
    return calls


AI = {"character": "cat", "shots": [{"prompt": "run", "seconds": 4},
                                    {"prompt": "sit", "seconds": 4}]}


def test_checked_passes_first_try_no_rerender(monkeypatch, tmp_path):
    calls = _wire(monkeypatch, [{"pass": True, "frames": []}],
                  [{"pass": True, "top": True, "bottom": True}])
    _, _, _, rep = motion._generate_checked(str(tmp_path), "s.png", AI, "draft", "p",
                                            "base.mp4", "seedance")
    assert calls["gen"] == 1 and rep["attempts"] == 1 and rep["blocked"] is False


def test_checked_rerenders_once_then_passes(monkeypatch, tmp_path):
    calls = _wire(monkeypatch,
                  [{"pass": False, "frames": []}, {"pass": True, "frames": []}],
                  [{"pass": False, "top": False, "bottom": True},
                   {"pass": True, "top": True, "bottom": True}])
    _, _, _, rep = motion._generate_checked(str(tmp_path), "s.png", AI, "draft", "p",
                                            "base.mp4", "seedance")
    assert calls["gen"] == 2 and rep["attempts"] == 2 and rep["blocked"] is False


def test_checked_blocks_after_two_failures(monkeypatch, tmp_path):
    review = {"n": 0}
    calls = _wire(monkeypatch,
                  [{"pass": False, "frames": []}, {"pass": False, "frames": []}],
                  [{"pass": False, "top": False, "bottom": True},
                   {"pass": False, "top": False, "bottom": True}])
    monkeypatch.setattr(motion, "_write_review_preview",
                        lambda *a, **k: review.__setitem__("n", 1))
    _, _, _, rep = motion._generate_checked(str(tmp_path), "s.png", AI, "draft", "p",
                                            "base.mp4", "seedance")
    assert calls["gen"] == 2 and rep["blocked"] is True and review["n"] == 1
