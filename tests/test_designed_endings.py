"""Designed endings (2026-08-03): endings are a designed, verified part of
every cut.

The end card is no longer an overlay on content: plans carry an appended
outro segment (outro.py), the planner owns the ending beat
(payoff_complete_by + the ENDING prompt requirement, _breathe_tail reduced
to the breath), a vision model verifies the payoff completes on screen
(ending_check.py: adjust once, re-render once, then a loud FAIL), and the
QC gates measure the result (outro_present, ending_complete).
"""
import json
import os
import subprocess
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

import pytest

from reelly import config, direct, ending_check, judge, outro, overlays


# --- fixtures -----------------------------------------------------------------

def _tiny_video(path, secs=3.0, color="red", audio=False, fps=30):
    """A real 1080x1920 clip encoded with the burn-pass params."""
    cmd = [config.FFMPEG, "-y", "-v", "error",
           "-f", "lavfi", "-i", f"color=c={color}:size=1080x1920:rate={fps}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}"]
    cmd += ["-t", f"{secs:.3f}", "-r", str(fps), "-pix_fmt", "yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += [path]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _plan(**kw):
    p = {"id": "cut_01", "title": "t", "segments": [[100.0, 122.0]],
         "content_s": 22.0, "duration_s": 24.8,
         "outro": {"len_s": 2.8, "style": "final_frame"},
         "hook": {"text": "watch this build itself"},
         "cta": "save this trick", "captions": "none",
         "payoff_complete_by": 21.0, "because": []}
    p.update(kw)
    return p


REF = {"landing_ok": True, "title": "t", "hook": "h",
       "cta": "save this trick",
       "handles": ["readable_detail", "narrative_turn"]}


# --- plan accounting: content + outro -----------------------------------------

def test_plan_block_and_content_len_roundtrip():
    ob = outro.plan_block()
    assert 2.5 <= ob["len_s"] <= 3.0
    assert ob["style"] in ("final_frame", "gradient")
    p = _plan()
    assert outro.content_len(p) == pytest.approx(22.0)
    assert outro.expected_duration(p) == pytest.approx(24.8)
    # legacy plan: duration IS the content
    assert outro.content_len({"duration_s": 21.0}) == 21.0


def test_reelly_outro_off_expects_content_only(monkeypatch):
    monkeypatch.setenv("REELLY_OUTRO", "off")
    assert not outro.enabled()
    assert outro.expected_duration(_plan()) == pytest.approx(22.0)


def test_with_outro_stamps_the_plan_accounting():
    plan = {"because": []}
    out = direct._with_outro(plan, {"cta": "save it"}, 22.0)
    assert out["content_s"] == 22.0
    assert out["outro"]["len_s"] == outro.OUTRO_S
    assert out["duration_s"] == pytest.approx(22.0 + outro.OUTRO_S, abs=0.06)
    assert any("outro appended" in b for b in out["because"])
    # no ask -> no card moment -> no outro block
    out2 = direct._with_outro({"because": []}, {"cta": ""}, 22.0)
    assert out2["outro"] is None and out2["duration_s"] == 22.0


def test_with_outro_respects_env_off(monkeypatch):
    monkeypatch.setenv("REELLY_OUTRO", "off")
    out = direct._with_outro({"because": []}, {"cta": "save it"}, 22.0)
    assert out["outro"] is None and out["duration_s"] == 22.0


def test_visual_plan_carries_outro_and_payoff_complete_by():
    cand = {"s": 0, "e": 60, "why": "test", "signals": []}
    ref = {"segments": [[10, 34]], "handles": ["persistent_character",
                                               "readable_detail"],
           "hook": "h", "title": "t", "cta": "save this trick",
           "payoff_complete_by": 21.5}
    p = direct._visual_plan_from(cand, ref, 1, scenes=[], duration=600,
                                 reframe=1.0)
    assert p["outro"]["len_s"] == outro.OUTRO_S
    assert p["content_s"] == pytest.approx(24.0)
    assert p["duration_s"] == pytest.approx(24.0 + outro.OUTRO_S, abs=0.06)
    assert p["payoff_complete_by"] == 21.5


def test_payoff_complete_by_is_clamped_and_defaulted():
    # garbage falls back to the delivery end; a claim past the content end
    # clamps to it (the planner cannot promise a payoff after the cut)
    assert direct._payoff_complete_by({"payoff_complete_by": "x"}, 22.0, 20.0) == 20.0
    assert direct._payoff_complete_by({}, 22.0, None) == 22.0
    assert direct._payoff_complete_by({"payoff_complete_by": 22.6}, 22.0) == 22.0
    assert direct._payoff_complete_by({"payoff_complete_by": -3}, 22.0, 19.0) == 19.0


def test_prompts_carry_the_ending_requirement():
    for prompt in (direct.REFINE_PROMPT, direct.VISUAL_REFINE_PROMPT):
        assert "ENDING" in prompt
        assert "payoff_complete_by" in prompt
        assert "never end mid" in prompt.lower()
        # the outro is appended, not carved out: the brain must not
        # reserve card room
        assert "do not reserve room" in prompt


# --- outro construction ---------------------------------------------------------

def test_outro_append_concat_is_codec_matched_and_duration_true(tmp_path):
    content = _tiny_video(str(tmp_path / "content.mp4"), secs=3.0)
    plan = _plan(content_s=3.0, duration_s=5.8)
    card = str(tmp_path / "card.png")
    from PIL import Image
    Image.new("RGBA", (1080, 1920), (9, 12, 10, 153)).save(card)
    with mock.patch.object(outro, "card_png", return_value=card), \
            redirect_stdout(StringIO()):
        out = outro.append(content, content, plan, "video", str(tmp_path))
    from reelly import media
    d = float(media.probe(out)["format"]["duration"])
    assert d == pytest.approx(3.0 + 2.8, abs=0.35)
    v = next(s for s in media.probe(out)["streams"]
             if s["codec_type"] == "video")
    assert v["codec_name"] == "h264"
    assert (int(v["width"]), int(v["height"])) == (1080, 1920)


def test_outro_backdrop_gradient_fallback(tmp_path):
    p, style = outro.backdrop_png("/nonexistent.mp4", "final_frame",
                                  str(tmp_path / "b.png"))
    assert style == "gradient" and os.path.exists(p)
    from PIL import Image
    assert Image.open(p).size == (1080, 1920)


def test_outro_last_frame_backdrop_is_dark(tmp_path):
    vid = _tiny_video(str(tmp_path / "bright.mp4"), secs=2.0, color="white")
    p, style = outro.backdrop_png(vid, "final_frame", str(tmp_path / "b.png"))
    assert style == "final_frame"
    from PIL import Image
    im = Image.open(p).convert("L")
    hist = im.histogram()
    mean = sum(i * n for i, n in enumerate(hist)) / (sum(hist) or 1)
    assert mean < 200      # -0.28 brightness on white: visibly darkened


# --- autoplan: no card events on outro plans -------------------------------------

def test_autoplan_emits_no_endcard_events_for_outro_plans(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "edl").mkdir(parents=True)
    fin = root / "deliverables" / "final"
    fin.mkdir(parents=True)
    plan = _plan(caption="one bad hand move. one tiny crop. seamless.")
    json.dump([plan], open(root / "edl" / "cut_plans.json", "w"))
    (fin / "cut_01.mp4").write_bytes(b"v")
    import reelly.placement as placement
    monkeypatch.setattr(placement, "plan_mark", lambda *a, **k: {
        "x": 60, "y": 900, "size": 48, "color": "#ffd60a", "w": 700,
        "h": 60, "stroke": 5, "scrim": 0.0,
        "backdrop_detail": 1, "backdrop_luma": 100})
    with redirect_stdout(StringIO()):
        specs = overlays.autoplan(str(root), product="video")
    evs = specs.get("cut_01", [])
    assert not [e for e in evs if e.get("role") == "endcard"
                or e.get("template") == overlays.KITCARD]
    # nothing may be scheduled onto the outro (past the content end)
    assert all(e["t"][1] <= outro.content_len(plan) + 1e-6 for e in evs)
    meta = specs["_meta"]["cut_01"]
    assert meta["outro"]["len_s"] == 2.8
    assert meta["endcard_t0"] is None


# --- ending_check: parse / adjust / re-render once / fail ------------------------

def test_parse_answer_shapes():
    v = ending_check.parse_answer('{"complete": true, "completes_at_s": 6.5,'
                                  ' "reason": "playback finished"}')
    assert v["complete"] is True and v["completes_at_s"] == 6.5
    v2 = ending_check.parse_answer("not json")
    assert v2["complete"] is None and "unparseable" in v2["reason"]
    v3 = ending_check.parse_answer('{"complete": "yes"}')
    assert v3["complete"] is None
    v4 = ending_check.parse_answer('{"complete": false, "completes_at_s": null,'
                                   ' "reason": "still generating"}')
    assert v4["complete"] is False and v4["completes_at_s"] is None


def _check_root(tmp_path, src_dur=200.0, blocked=()):
    root = tmp_path / "proj"
    (root / "edl").mkdir(parents=True)
    (root / "analysis").mkdir()
    (root / "source").mkdir()
    fin = root / "deliverables" / "final"
    fin.mkdir(parents=True)
    (fin / "cut_01_gfx.mp4").write_bytes(b"v")
    return str(root)


def test_extend_plan_extends_along_source_capped_at_4s(tmp_path, monkeypatch):
    root = _check_root(tmp_path)
    plan = _plan()
    monkeypatch.setattr("reelly.clearance.blocked_ranges", lambda r: [])
    monkeypatch.setattr("reelly.direct._source_video", lambda r: "src.mp4")
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: {"format": {"duration": "500.0"}})
    out = ending_check.extend_plan(plan, root)
    assert out["segments"][-1][1] == pytest.approx(126.0)   # +4.0 cap
    assert out["content_s"] == pytest.approx(26.0)
    assert out["duration_s"] == pytest.approx(28.8, abs=0.06)
    assert any("ENDING-check" in b for b in out["because"])


def test_extend_plan_respects_source_end_and_blocked_ranges(tmp_path, monkeypatch):
    root = _check_root(tmp_path)
    monkeypatch.setattr("reelly.direct._source_video", lambda r: "src.mp4")
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: {"format": {"duration": "123.5"}})
    monkeypatch.setattr("reelly.clearance.blocked_ranges", lambda r: [])
    out = ending_check.extend_plan(_plan(), root)
    assert out["segments"][-1][1] == pytest.approx(123.5)   # source end wins
    # a blocked range right after the cut end leaves no legal room
    monkeypatch.setattr("reelly.clearance.blocked_ranges",
                        lambda r: [(122.1, 300.0, "voice not cleared: g1")])
    assert ending_check.extend_plan(_plan(), root) is None
    # a blocked range COVERING the cut end: no room either
    monkeypatch.setattr("reelly.clearance.blocked_ranges",
                        lambda r: [(120.0, 300.0, "voice not cleared: g1")])
    assert ending_check.extend_plan(_plan(), root) is None


def test_verify_cut_complete_records_pass_and_never_rerenders(tmp_path, monkeypatch):
    root = _check_root(tmp_path)
    plan = _plan()
    json.dump([plan], open(os.path.join(root, "edl", "cut_plans.json"), "w"))
    calls = []
    monkeypatch.setattr(ending_check, "check_cut",
                        lambda v, p, project="": {"complete": True,
                                                  "completes_at_s": 5.0,
                                                  "reason": "settled",
                                                  "window": [14, 22]})
    with redirect_stdout(StringIO()):
        e = ending_check.verify_cut(root, plan, lambda p: calls.append(p))
    assert e["final"]["complete"] is True and not calls and not e["adjusted"]
    rec = outro.load_verdicts(root)["cut_01"]
    assert rec["final"]["complete"] is True


def test_verify_cut_incomplete_adjusts_rerenders_once_then_fails(tmp_path, monkeypatch):
    root = _check_root(tmp_path)
    plan = _plan()
    json.dump([plan], open(os.path.join(root, "edl", "cut_plans.json"), "w"))
    monkeypatch.setattr("reelly.clearance.blocked_ranges", lambda r: [])
    monkeypatch.setattr("reelly.direct._source_video", lambda r: "src.mp4")
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: {"format": {"duration": "500.0"}})
    verdicts = [{"complete": False, "completes_at_s": None,
                 "reason": "still playing at the end", "window": [14, 22]},
                {"complete": False, "completes_at_s": None,
                 "reason": "still not settled", "window": [18, 26]}]
    calls = []
    monkeypatch.setattr(ending_check, "check_cut",
                        lambda v, p, project="": verdicts.pop(0))
    with redirect_stdout(StringIO()):
        e = ending_check.verify_cut(root, plan, lambda p: calls.append(p["id"]))
    assert e["adjusted"] is True
    assert calls == ["cut_01"]              # exactly ONE re-render
    assert len(e["attempts"]) == 2          # exactly ONE re-check
    assert e["final"]["complete"] is False  # loud FAIL, never a loop
    # the adjusted plan was persisted with the new accounting
    saved = json.load(open(os.path.join(root, "edl", "cut_plans.json")))[0]
    assert saved["segments"][-1][1] == pytest.approx(126.0)
    assert saved["duration_s"] == pytest.approx(28.8, abs=0.06)
    # and the QC gate reads it as ending_incomplete
    name, status, detail = judge.ending_verdict(root, saved)
    assert name == "ending_complete" and status == "FAIL"
    assert "ending_incomplete" in detail and "not settled" in detail


def test_verify_cut_incomplete_second_pass_true_passes(tmp_path, monkeypatch):
    root = _check_root(tmp_path)
    plan = _plan()
    json.dump([plan], open(os.path.join(root, "edl", "cut_plans.json"), "w"))
    monkeypatch.setattr("reelly.clearance.blocked_ranges", lambda r: [])
    monkeypatch.setattr("reelly.direct._source_video", lambda r: "src.mp4")
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: {"format": {"duration": "500.0"}})
    verdicts = [{"complete": False, "completes_at_s": None, "reason": "mid",
                 "window": [14, 22]},
                {"complete": True, "completes_at_s": 6.0, "reason": "lands",
                 "window": [18, 26]}]
    monkeypatch.setattr(ending_check, "check_cut",
                        lambda v, p, project="": verdicts.pop(0))
    with redirect_stdout(StringIO()):
        e = ending_check.verify_cut(root, plan, lambda p: None)
    assert e["final"]["complete"] is True and e["adjusted"] is True
    assert judge.ending_verdict(root, plan)[1] == "PASS"


def test_ending_check_env_off_disables(tmp_path, monkeypatch):
    monkeypatch.setenv("REELLY_ENDING_CHECK", "off")
    assert not ending_check.enabled()
    with redirect_stdout(StringIO()):
        assert ending_check.run(str(tmp_path)) == []


def test_ending_cost_is_ledger_accounted(monkeypatch, tmp_path):
    """The vision call is budget-checked BEFORE and ledgered AFTER, at the
    module's own estimate."""
    checked, added = [], []
    monkeypatch.setattr(ending_check.ledger, "check",
                        lambda c: checked.append(c))
    monkeypatch.setattr(ending_check.ledger, "add",
                        lambda s, d, c, p="": added.append((s, c)))

    class _F:
        state = type("S", (), {"name": "ACTIVE"})()
        name = "f"

    class _Client:
        class files:
            upload = staticmethod(lambda file: _F())
            get = staticmethod(lambda name: _F())

        class models:
            generate_content = staticmethod(lambda **k: type(
                "R", (), {"text": '{"complete": true, "completes_at_s": 1,'
                                  ' "reason": "ok"}'})())

    monkeypatch.setattr(ending_check.visual, "_client", lambda: _Client())
    v = ending_check._ask_model(str(tmp_path / "p.mp4"), _plan(), 8.0, "proj")
    assert v["complete"] is True
    assert checked == [ending_check.EST_ENDING_COST]
    assert added == [("gemini-ending", ending_check.EST_ENDING_COST)]
    assert 0.001 < ending_check.EST_ENDING_COST < 0.05


# --- QC gates ---------------------------------------------------------------------

def test_outro_present_gate_pass_and_fail(tmp_path):
    plan = _plan(content_s=2.0, duration_s=4.8)
    # deliverable WITH a dark outro-like tail
    good = str(tmp_path / "good.mp4")
    _tiny_video(good, secs=4.8, color="black")
    name, status, detail = judge.outro_present(plan, good)
    assert (name, status) == ("outro_present", "PASS"), detail
    # content-only file: outro missing -> FAIL on duration
    short = _tiny_video(str(tmp_path / "short.mp4"), secs=2.0)
    assert judge.outro_present(plan, short)[1] == "FAIL"
    # full duration but BRIGHT last frame: card missing -> FAIL on luma
    bright = _tiny_video(str(tmp_path / "bright.mp4"), secs=4.8, color="white")
    n, s, d = judge.outro_present(plan, bright)
    assert s == "FAIL" and "luma" in d


def test_outro_present_gate_skips_legacy_and_env_off(tmp_path, monkeypatch):
    assert judge.outro_present({"duration_s": 20.0}, "x.mp4")[1] == "SKIP"
    monkeypatch.setenv("REELLY_OUTRO", "off")
    assert judge.outro_present(_plan(), "x.mp4")[1] == "SKIP"


def test_ending_verdict_gate_skip_paths(tmp_path):
    root = str(tmp_path)
    # legacy plan: not this architecture's problem
    assert judge.ending_verdict(root, {"id": "cut_01"})[1] == "SKIP"
    # outro plan but no verdict on file yet
    assert judge.ending_verdict(root, _plan())[1] == "SKIP"
    # unparseable model answer degrades to WARN
    outro.record_verdict(root, "cut_01",
                         {"final": {"complete": None, "reason": "garbled"}})
    assert judge.ending_verdict(root, _plan())[1] == "WARN"


def test_judge_duration_gate_expects_full_content_plus_outro():
    """check_file receives outro.expected_duration: full length normally,
    content-only under REELLY_OUTRO=off (so the gate never fails a
    deliberately outro-less render)."""
    p = _plan()
    assert outro.expected_duration(p) == p["duration_s"]
