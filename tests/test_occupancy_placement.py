"""Regressions for the multi-region occupancy fix.

The bug: design
modelled content as ONE subject_box. On a six-character lineup filling the
frame every candidate row overlapped that single box, least-overlap
degenerated (all rows scored the same, ties fell to the top), and the payoff
landed across a character's face. Baked titles/logos (SampleTitle, ExampleBrand) were
flagged D2 by the critic but never MODELLED, so placement could not clear them.

The fix: design.occupancy() returns THREE lists in one vision call -- faces
(each character face), subjects (bodies), text_regions (baked titles/logos) --
and placement scores rows by WEIGHTED overlap (faces heaviest, then text, then
bodies), choosing the minimum-occupancy row anywhere in the safe band. A
measured D1 gate fails on residual face overlap with the fraction.

Each test is named for the failure it prevents. No test makes a live API call:
the vision client is mocked exactly where design.occupancy calls it.
"""
from PIL import Image

from reelly import design, motion

FRAME = Image.new("RGB", (1080, 1920))


def _reply(monkeypatch, resp):
    """Make design.occupancy see exactly this vision reply."""
    monkeypatch.setattr(design, "_gemini", lambda *a, **k: resp)


class _FakeFrame:
    size = (1080, 1920)


# --- design.occupancy parsing ----------------------------------------------

def test_occupancy_returns_three_lists_of_pixel_boxes(monkeypatch):
    """The core contract: three separate lists, each a pixel (x, y, w, h)."""
    _reply(monkeypatch, {
        "faces": [[0.1, 0.05, 0.3, 0.2], [0.6, 0.05, 0.8, 0.2]],
        "subjects": [[0.1, 0.2, 0.8, 0.9]],
        "text_regions": [[0.2, 0.85, 0.8, 0.95]]})
    occ = design.occupancy(FRAME)
    assert len(occ["faces"]) == 2 and len(occ["subjects"]) == 1
    assert len(occ["text_regions"]) == 1
    for key in occ:
        for box in occ[key]:
            x, y, w, h = box
            assert w > 0 and h > 0 and 0 <= x < 1080 and 0 <= y < 1920


def test_occupancy_accepts_permille_coords_like_subject_box(monkeypatch):
    """gemini returns 0..1000 per-mille integers; occupancy reuses _normalize_box
    so faces are parsed, not discarded on every frame (the subject_box bug)."""
    _reply(monkeypatch, {"faces": [[345, 50, 548, 300]], "subjects": [],
                         "text_regions": []})
    occ = design.occupancy(FRAME)
    assert len(occ["faces"]) == 1
    x, y, w, h = occ["faces"][0]
    assert 200 < x < 700 and w > 0 and h > 0


# --- (4) partial / malformed region lists degrade gracefully ---------------

def test_partial_and_malformed_region_lists_degrade_gracefully(monkeypatch):
    """A reply with one good face, one junk box, a missing list and a nested
    box must yield the parseable regions and never raise."""
    _reply(monkeypatch, {
        "faces": [[0.1, 0.05, 0.3, 0.2], [0.5], "garbage", None],  # 1 good, 3 junk
        "subjects": [[[0.1, 0.2, 0.8, 0.9]]],                      # nested [[...]]
        # text_regions key absent entirely
    })
    occ = design.occupancy(FRAME)
    assert len(occ["faces"]) == 1, "the one valid face survives the junk"
    assert len(occ["subjects"]) == 1, "a nested box is unwrapped, not dropped"
    assert occ["text_regions"] == []


def test_occupancy_totally_unusable_reply_is_empty_not_a_crash(monkeypatch):
    """A non-dict/non-list reply returns three empty lists (read as
    content-blind upstream), never an exception."""
    for resp in ("not json", None, 42):
        _reply(monkeypatch, resp)
        occ = design.occupancy(FRAME)
        assert occ == {"faces": [], "subjects": [], "text_regions": []}, resp


def test_occupancy_retries_an_unparseable_reply_then_gives_up_clean(monkeypatch):
    """The Zombie-card failure: vision returns garbage. It is retried twice
    (three calls total) before the window is declared content-blind -- never a
    crash, never a fifth silent blind render."""
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        return "not json"

    monkeypatch.setattr(design, "_gemini", flaky)
    occ = design.occupancy(FRAME)
    assert calls["n"] == 3
    assert occ == {"faces": [], "subjects": [], "text_regions": []}


def test_occupancy_retry_recovers_when_a_later_try_parses(monkeypatch):
    """A transient garbage reply must not cost the regions: the retry that comes
    back well-formed is used, not discarded for the first bad one."""
    replies = iter(["garbage",
                    {"faces": [[0.1, 0.05, 0.3, 0.2]], "subjects": [],
                     "text_regions": []}])
    monkeypatch.setattr(design, "_gemini", lambda *a, **k: next(replies))
    occ = design.occupancy(FRAME)
    assert len(occ["faces"]) == 1


def test_occupancy_does_not_retry_a_wellformed_empty_reply(monkeypatch):
    """An empty-but-valid reply is a genuinely blank frame, not garbage: it must
    be taken at face value in ONE call, never re-asked (that burned money on the
    same empty answer)."""
    calls = {"n": 0}

    def empty(*a, **k):
        calls["n"] += 1
        return {"faces": [], "subjects": [], "text_regions": []}

    monkeypatch.setattr(design, "_gemini", empty)
    occ = design.occupancy(FRAME)
    assert calls["n"] == 1
    assert occ == {"faces": [], "subjects": [], "text_regions": []}


def test_occupancy_tolerates_flat_kinded_regions_list(monkeypatch):
    """Some models answer the 'JSON list' contract with one flat list of tagged
    regions; they must still be routed into the right buckets."""
    _reply(monkeypatch, [
        {"kind": "face", "box": [0.1, 0.05, 0.3, 0.2]},
        {"kind": "logo", "box": [0.2, 0.85, 0.8, 0.95]},
        {"type": "body", "box": [0.1, 0.2, 0.8, 0.9]}])
    occ = design.occupancy(FRAME)
    assert len(occ["faces"]) == 1 and len(occ["text_regions"]) == 1
    assert len(occ["subjects"]) == 1


# --- (1) multi-character lineup places in the row with no faces ------------

def test_multi_character_lineup_places_in_the_row_with_no_faces():
    """Six faces stretched across the upper frame (a lineup). Every upper row
    overlaps a face; only a lower row is clear. Weighted ranking must pick it --
    the exact case where single-subject least-overlap degenerated onto a face."""
    x, width, box_h = (1080 - 920) // 2, 920, 260
    faces = [(c, 140, 150, 200) for c in range(30, 1000, 170)]  # six faces, y 140..340
    occ = {"faces": faces, "subjects": [(0, 120, 1080, 700)], "text_regions": []}
    rows = motion._candidate_rows(box_h)
    ranked = motion._rank_rows(rows, x, width, box_h, occ)
    winner = ranked[0]
    # the winning row clears every face entirely
    assert motion._box_overlap((x, winner, width, box_h), faces) == 0
    assert winner + box_h <= motion.FRAME_H - motion.SAFE_BOTTOM
    # and an upper row that sits on the faces is genuinely worse, not tied
    assert (motion._weighted_overlap((x, 140, width, box_h), occ)
            > motion._weighted_overlap((x, winner, width, box_h), occ))


def test_faces_weigh_heavier_than_bodies_in_ranking():
    """A row grazing a face must rank worse than a row grazing an equal-area
    body: faces are the cardinal D1 sin."""
    box = (80, 500, 900, 200)
    face_only = {"faces": [(80, 500, 900, 200)], "subjects": [], "text_regions": []}
    body_only = {"faces": [], "subjects": [(80, 500, 900, 200)], "text_regions": []}
    assert (motion._weighted_overlap(box, face_only)
            > motion._weighted_overlap(box, body_only))


# --- (2) baked-title frame avoids the title zone ---------------------------

def test_baked_title_frame_avoids_the_title_zone():
    """A baked title band across the top (text_region) with no faces: placement
    must route the payoff below the title, which single-subject modelling could
    not even see (it was flagged D2 but never avoided)."""
    x, width, box_h = (1080 - 880) // 2, 880, 240
    title = [(140, 120, 800, 220)]  # SampleTitle wordmark band, y 120..340
    occ = {"faces": [], "subjects": [], "text_regions": title}
    rows = motion._candidate_rows(box_h)
    ranked = motion._rank_rows(rows, x, width, box_h, occ)
    assert motion._box_overlap((x, ranked[0], width, box_h), title) == 0
    assert motion._weighted_overlap((x, 120, width, box_h), occ) > 0


# --- (3) single-subject art still works ------------------------------------

def test_single_subject_art_still_works():
    """One centred subject with a face up top: the fix must still find the clear
    lower band, exactly as the single-subject path did (no regression)."""
    x, width, box_h = (1080 - 920) // 2, 920, 300
    occ = {"faces": [(360, 120, 360, 360)],
           "subjects": [(280, 120, 520, 900)], "text_regions": []}
    rows = motion._candidate_rows(box_h)
    ranked = motion._rank_rows(rows, x, width, box_h, occ)
    assert motion._box_overlap((x, ranked[0], width, box_h), occ["faces"]) == 0


# --- content-blind fallback stays LOUD when occupancy is empty -------------

def _hybrid(monkeypatch, local, text):
    """Stub the hybrid sampler's three seams: the local detectors, the single
    Gemini text call, and the illustrated-face midpoint fallback that fires
    whenever the local detectors report zero faces (game key art / anime stills
    sample as zero photographic faces). The fallback returning empty here is
    what keeps a genuinely content-blind window content-blind."""
    monkeypatch.delenv("REELLY_OCCUPANCY", raising=False)
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: _FakeFrame())
    monkeypatch.setattr(design, "occupancy_local",
                        lambda frame, project="": dict(local))
    monkeypatch.setattr(design, "occupancy_text",
                        lambda frame, project="": list(text))
    monkeypatch.setattr(design, "occupancy",
                        lambda *a, **k: {"faces": [], "subjects": [],
                                         "text_regions": []})


def test_content_blind_fallback_when_no_regions_survive(monkeypatch, capsys):
    """No faces/subjects/text of any kind -> content-blind, recorded loud in
    diag and printed. The whole fallback contract, on the hybrid sampler."""
    _hybrid(monkeypatch,
            {"faces": [], "subjects": [], "text_regions": []}, [])
    diag = {"placement": "subject-aware"}
    occ = motion._sample_occupancy("v.mp4", 0.0, 4.0, "proj", diag=diag)
    assert occ == {"faces": [], "subjects": [], "text_regions": []}
    assert diag["placement"] == "content-blind fallback"
    assert diag["windows"] == [{"window": [0.0, 4.0], "faces": 0,
                                "subjects": 0, "text_regions": 0}]
    assert "content-blind fallback" in capsys.readouterr().out


def test_real_regions_keep_placement_subject_aware(monkeypatch):
    """With real occupancy the sampler stays subject-aware and unions boxes
    across the sampled frames -- the point of the fix."""
    _hybrid(monkeypatch,
            {"faces": [(300, 100, 200, 200)],
             "subjects": [(280, 100, 500, 900)], "text_regions": []},
            [(140, 120, 800, 200)])
    diag = {"placement": "subject-aware"}
    occ = motion._sample_occupancy("v.mp4", 0.0, 4.0, "proj", samples=3, diag=diag)
    assert diag["placement"] == "subject-aware"
    assert len(occ["faces"]) == 3 and len(occ["subjects"]) == 3
    assert len(occ["text_regions"]) == 1        # midpoint call, once


def test_illustrated_face_unions_across_travel(monkeypatch):
    """MAR-67 regression: an illustrated/painted subject (local detectors find
    zero faces) that WALKS across the shot has its face TRAVEL over the caption's
    on-screen duration. The old fallback asked the vision model at ONE midpoint
    frame, clearing only that instant, so the rising face drifted back under the
    caption. The fallback must now sample several frames spanning the window and
    union the face's whole travel path -- bounded to a few vision calls."""
    monkeypatch.delenv("REELLY_OCCUPANCY", raising=False)
    monkeypatch.setenv("REELLY_ILLUS_FACE_SAMPLES", "3")
    # _frame_at returns the sample time so the fake vision can move the face.
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: float(t))
    monkeypatch.setattr(design, "occupancy_local",
                        lambda frame, project="": {"faces": [], "subjects": [],
                                                   "text_regions": []})
    monkeypatch.setattr(design, "occupancy_text", lambda frame, project="": [])
    calls = {"n": 0}

    def vision(frame, project=""):
        calls["n"] += 1
        # Illustrated head rises (y shrinks) as t grows: a moving subject.
        y = int(1200 - 120 * float(frame))
        return {"faces": [(400, y, 240, 260)], "subjects": [], "text_regions": []}

    monkeypatch.setattr(design, "occupancy", vision)
    occ = motion._sample_occupancy("v.mp4", 0.0, 4.0, "proj", diag={})
    # Bounded to 3 vision calls (head/mid/tail), not one midpoint and not one
    # per sample, and the union spans the travel rather than a single instant.
    assert calls["n"] == 3
    assert len(occ["faces"]) == 3
    ys = sorted(b[1] for b in occ["faces"])
    assert ys[0] != ys[-1], "union must span the face's travel, not one frame"


def test_illustrated_face_fallback_stays_bounded_for_still_art(monkeypatch):
    """A still illustrated subject still costs only the bounded call budget and
    the content-blind contract survives when the vision model finds nothing."""
    monkeypatch.delenv("REELLY_OCCUPANCY", raising=False)
    monkeypatch.setenv("REELLY_ILLUS_FACE_SAMPLES", "3")
    calls = {"n": 0}
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: _FakeFrame())
    monkeypatch.setattr(design, "occupancy_local",
                        lambda frame, project="": {"faces": [], "subjects": [],
                                                   "text_regions": []})
    monkeypatch.setattr(design, "occupancy_text", lambda frame, project="": [])

    def vision(frame, project=""):
        calls["n"] += 1
        return {"faces": [], "subjects": [], "text_regions": []}

    monkeypatch.setattr(design, "occupancy", vision)
    occ = motion._sample_occupancy("v.mp4", 0.0, 4.0, "proj", diag={})
    assert calls["n"] == 3          # bounded even when it finds nothing
    assert occ["faces"] == []       # genuinely content-blind stays blind


def test_sample_occupancy_drops_out_of_bounds_boxes(monkeypatch):
    """A face box that runs off the frame is skipped, not trusted -- the same
    bounds guard the single-subject sampler had."""
    _hybrid(monkeypatch,
            {"faces": [(1000, 100, 500, 200)], "subjects": [],
             "text_regions": []}, [])
    occ = motion._sample_occupancy("v.mp4", 0.0, 0.0, "proj")
    assert occ["faces"] == []


# --- the hybrid contract: 5 Gemini calls per window -> 1 --------------------

def test_hybrid_calls_gemini_once_per_window_for_text_only(monkeypatch):
    """The point of the hybrid: local detectors run on every sample, Gemini is
    asked exactly ONCE (window midpoint) and only for text_regions."""
    calls = {"local": 0, "text": 0, "gemini": 0}
    monkeypatch.delenv("REELLY_OCCUPANCY", raising=False)
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: _FakeFrame())
    monkeypatch.setattr(design, "occupancy",
                        lambda *a, **k: calls.__setitem__("gemini", calls["gemini"] + 1)
                        or {"faces": [], "subjects": [], "text_regions": []})

    def local(frame, project=""):
        calls["local"] += 1
        return {"faces": [(300, 100, 200, 200)], "subjects": [],
                "text_regions": []}

    def text(frame, project=""):
        calls["text"] += 1
        return [(140, 1600, 800, 200)]

    monkeypatch.setattr(design, "occupancy_local", local)
    monkeypatch.setattr(design, "occupancy_text", text)
    occ = motion._sample_occupancy("v.mp4", 0.0, 4.0, "proj", diag={})
    assert calls == {"local": 5, "text": 1, "gemini": 0}
    assert len(occ["faces"]) == 5 and occ["text_regions"] == [(140, 1600, 800, 200)]


def test_env_gemini_restores_the_all_vision_path(monkeypatch):
    """REELLY_OCCUPANCY=gemini is the escape hatch: design.occupancy on every
    sample, the local detectors never touched."""
    calls = {"local": 0, "gemini": 0}
    monkeypatch.setenv("REELLY_OCCUPANCY", "gemini")
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: _FakeFrame())

    def full(frame, project=""):
        calls["gemini"] += 1
        return {"faces": [(300, 100, 200, 200)], "subjects": [],
                "text_regions": []}

    monkeypatch.setattr(design, "occupancy", full)
    monkeypatch.setattr(design, "occupancy_local",
                        lambda *a, **k: calls.__setitem__("local", calls["local"] + 1)
                        or {"faces": [], "subjects": [], "text_regions": []})
    occ = motion._sample_occupancy("v.mp4", 0.0, 4.0, "proj", diag={})
    assert calls == {"local": 0, "gemini": 5}
    assert len(occ["faces"]) == 5


# --- design.occupancy_local / occupancy_text --------------------------------

def test_occupancy_local_flags_busy_regions_not_flat_ones(monkeypatch):
    """The edge heatmap marks a noisy band as a subject region and leaves a
    flat frame empty -- 'busy' is measured, never guessed."""
    import random
    from PIL import Image as _I
    from reelly import face
    monkeypatch.setattr(face, "detect_faces", lambda _image: [])
    flat = _I.new("RGB", (1080, 1920), (20, 22, 21))
    occ = design.occupancy_local(flat)
    assert occ["subjects"] == [] and occ["text_regions"] == []

    rng = random.Random(7)
    noisy = _I.new("RGB", (1080, 1920), (20, 22, 21))
    d = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(noisy)
    for y in range(800, 1100, 30):          # coarse contrasty clutter, the
        for x in range(0, 1080, 30):        # kind a downscale cannot erase
            d.rectangle([x, y, x + 28, y + 28],
                        fill=(rng.randrange(256), rng.randrange(256),
                              rng.randrange(256)))
    occ = design.occupancy_local(noisy)
    assert occ["subjects"], "a noisy band must register as busy"
    x, y, w, h = occ["subjects"][0]
    assert y < 1100 and y + h > 800, "the busy box covers the noisy band"


def test_occupancy_text_parses_and_degrades(monkeypatch):
    """One vision call, boxes through the same tolerant parser; a malformed
    reply is an empty list, never a crash."""
    monkeypatch.setattr(design, "_gemini",
                        lambda *a, **k: {"text_regions": [[0.2, 0.85, 0.8, 0.95]]})
    boxes = design.occupancy_text(FRAME)
    assert len(boxes) == 1 and boxes[0][2] > 0
    for bad in (None, "junk", 42):
        monkeypatch.setattr(design, "_gemini", lambda *a, **k: bad)
        assert design.occupancy_text(FRAME) == []


# --- (3) measured D1 face-overlap gate -------------------------------------

def test_measured_d1_face_overlap_fails_gate_with_fraction(tmp_path, monkeypatch):
    """The critic can pass a frame while a lettering box still lands on a face.
    The measured D1 gate must FAIL with the fraction and name it in the report,
    even when placement did its best (no face-free band exists)."""
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: object())
    monkeypatch.setattr(design, "critique",
                        lambda *a, **k: {"pass": True, "issues": []})
    ai = {"hook": {"text": "H"}, "payoff": {"text": "P"}, "cta": "GO"}
    diag = {"placement": "subject-aware", "windows": [], "d7": [],
            "d1": [{"asset": "payoff.png", "y": 300, "box": [80, 300, 880, 240],
                    "faces": 6, "face_overlap": 0.31}]}
    gate = motion._design_gate(str(tmp_path), "out.mp4", ai, 4, 8, "proj",
                               diag=diag)
    assert gate["pass"] is False, "a face overlap past tolerance fails the gate"
    report = (tmp_path / "qc" / "design_report.md").read_text()
    assert "D1 measured face overlap" in report
    assert "31.0%" in report and "faces=6" in report


def test_measured_d1_small_overlap_within_tolerance_passes(tmp_path, monkeypatch):
    """A hairline overlap under D1_FACE_TOL must not fail the gate -- the gate
    measures, it does not panic on a rounding pixel."""
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: object())
    monkeypatch.setattr(design, "critique",
                        lambda *a, **k: {"pass": True, "issues": []})
    ai = {"hook": {"text": "H"}, "payoff": {"text": "P"}, "cta": "GO"}
    diag = {"placement": "subject-aware", "windows": [], "d7": [],
            "d1": [{"asset": "hook.png", "y": 900, "box": [80, 900, 920, 260],
                    "faces": 3, "face_overlap": 0.0}]}
    gate = motion._design_gate(str(tmp_path), "out.mp4", ai, 4, 8, "proj",
                               diag=diag)
    assert gate["pass"] is True


def test_overlap_fraction_clamps_and_measures():
    """_overlap_fraction is area-normalised and clamped to 1.0 even when a box
    is covered by several overlapping faces."""
    box = (100, 100, 200, 200)              # 40000 px
    assert motion._overlap_fraction(box, []) == 0.0
    # half-covered
    assert abs(motion._overlap_fraction(box, [(100, 100, 200, 100)]) - 0.5) < 1e-6
    # two boxes that together exceed the area still clamp to 1.0
    covered = [(100, 100, 200, 200), (100, 100, 200, 200)]
    assert motion._overlap_fraction(box, covered) == 1.0


def test_max_letter_width_is_the_centred_rail_limit():
    """A centred line clamped to MAX_LETTER_W touches neither action rail, while
    the old FRAME_W-SAFE_LEFT-SAFE_RIGHT (=900) figure would cross the nearer
    (right) rail -- which is why the warning-that-shipped was not enough."""
    w = motion.MAX_LETTER_W
    x = (motion.FRAME_W - w) // 2
    assert x >= motion.SAFE_LEFT
    assert x + w <= motion.FRAME_W - motion.SAFE_RIGHT
    assert (motion.FRAME_W - 900) // 2 + 900 > motion.FRAME_W - motion.SAFE_RIGHT


def test_place_lettering_hard_gate_shrinks_an_overwide_line(tmp_path, monkeypatch):
    """P0 #3: an over-wide request is SHRUNK to fit, not shipped with a warning.
    The returned centred box clears both the 60px and 120px side rails."""
    from reelly import design, placement
    asset = tmp_path / "hook.png"
    Image.new("RGBA", (1000, 220)).save(asset)
    monkeypatch.setattr(motion, "_sample_occupancy",
                        lambda *a, **k: {"faces": [], "subjects": [],
                                         "text_regions": []})
    monkeypatch.setattr(placement, "grid",
                        lambda *a, **k: [[(0, 0)] * placement.COLS
                                         for _ in range(placement.ROWS)])
    monkeypatch.setattr(motion, "_frame_at",
                        lambda *a, **k: Image.new("RGB",
                                                  (motion.FRAME_W, motion.FRAME_H)))
    monkeypatch.setattr(design, "contrast_gate",
                        lambda *a, **k: {"pass": True, "scrim": 0.0})
    x, y, width, scrim = motion._place_lettering(
        "v.mp4", 0.0, 4.0, str(asset), "p", width=920)
    assert width == motion.MAX_LETTER_W
    assert x >= motion.SAFE_LEFT
    assert x + width <= motion.FRAME_W - motion.SAFE_RIGHT


def test_candidate_rows_stay_inside_the_safe_band():
    """Every candidate row keeps the box within the 120px-top / 320px-bottom
    band, so the winner is always legal regardless of which row wins."""
    box_h = 260
    rows = motion._candidate_rows(box_h)
    assert rows[0] == motion.SAFE_TOP
    assert all(r >= motion.SAFE_TOP for r in rows)
    assert all(r + box_h <= motion.FRAME_H - motion.SAFE_BOTTOM for r in rows)
