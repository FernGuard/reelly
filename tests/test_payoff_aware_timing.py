"""Payoff-aware timing (screening fixes, 2026-08-03).

the reviewer's two screening symptoms share one cause: overlay/endcard timing
was scheduled against PLAN ESTIMATES, not measured footage. These tests pin
the fix: beats.py measures the payoff anchor (speech / picture / both) from
the analysis artifacts, the planner's tail accounting consumes the same
anchor, the endcard enters after it, reveal-role overlay lines never
precede the moment they describe, and two QC gates catch regressions --
degrading to WARN, never blocking, when the artifacts are absent.
"""
import json
import os
from contextlib import redirect_stdout
from io import StringIO

import pytest

from reelly import beats, direct, judge, overlays, products


# --- fixtures -----------------------------------------------------------------

def _words_json(path, words):
    """analysis/words.json in the whisper shape speech.words_from reads."""
    json.dump({"segments": [{"words": [
        {"word": t, "start": s, "end": e} for t, s, e in words]}]},
        open(path, "w"))


def _analysis_dir(tmp_path, words=None, scenes=None):
    an = tmp_path / "analysis"
    an.mkdir(exist_ok=True)
    if words is not None:
        _words_json(str(an / "words.json"), words)
    if scenes is not None:
        json.dump([{"t": t, "score": 0.2} for t in scenes],
                  open(an / "scenes.json", "w"))
    return str(an)


# ten words, 100.0-104.5s source: "so the whole level just builds itself
# in one click"
WORDS = [("so", 100.0, 100.3), ("the", 100.35, 100.5), ("whole", 100.55, 100.9),
         ("level", 100.95, 101.4), ("just", 101.5, 101.8),
         ("builds", 101.9, 102.4), ("itself", 102.5, 103.0),
         ("in", 103.4, 103.5), ("one", 103.6, 103.9), ("click.", 104.0, 104.5)]


# --- word matching: exact, drifted, absent --------------------------------------

def test_match_phrase_exact_quote_lands_on_the_words(tmp_path):
    an = _analysis_dir(tmp_path, words=WORDS)
    words = beats._load_words(an)
    m = beats.match_phrase("builds itself in one click", words)
    assert m and m[0] == pytest.approx(101.9) and m[1] == pytest.approx(104.5)
    assert m[2] >= 0.99


def test_match_phrase_tolerates_transcription_drift(tmp_path):
    an = _analysis_dir(tmp_path, words=WORDS)
    words = beats._load_words(an)
    # near-quote: one word wrong, one missing -- still the same landing
    m = beats.match_phrase("build itself in a click", words)
    assert m is not None
    assert m[1] == pytest.approx(104.5, abs=0.6)


def test_match_phrase_absent_text_returns_none(tmp_path):
    an = _analysis_dir(tmp_path, words=WORDS)
    words = beats._load_words(an)
    assert beats.match_phrase("completely unrelated sentence here", words) is None
    assert beats.match_phrase("", words) is None
    assert beats.match_phrase("builds itself", []) is None


def test_match_phrase_prefers_the_last_repetition():
    words = [{"t": t, "s": s, "e": e} for t, s, e in
             [("one", 10.0, 10.2), ("click.", 10.3, 10.6),
              ("later", 20.0, 20.4),
              ("one", 30.0, 30.2), ("click.", 30.3, 30.6)]]
    m = beats.match_phrase("one click", words)
    assert m[0] == pytest.approx(30.0)  # the landing, not the earlier mention


# --- source <-> local mapping ---------------------------------------------------

def test_time_mapping_across_jump_segments_and_speed():
    segs = [[100.0, 110.0], [200.0, 208.0, 2.0]]  # second segment at 2x
    assert beats.local_time(105.0, segs) == pytest.approx(5.0)
    assert beats.local_time(204.0, segs) == pytest.approx(12.0)
    assert beats.local_time(150.0, segs) is None  # cut out
    assert beats.source_time(5.0, segs) == pytest.approx(105.0)
    assert beats.source_time(12.0, segs) == pytest.approx(204.0)
    assert beats.source_time(99.0, segs) is None  # past the clip end


# --- kind resolution -------------------------------------------------------------

def test_payoff_kind_reads_the_plan_schema():
    assert beats.payoff_kind({"planned_from": "visual"}) == "picture"
    assert beats.payoff_kind({"captions": "none"}) == "picture"
    assert beats.payoff_kind({"planned_from": "speech",
                              "transcript": "we did it."}) == "speech"
    # payoff jump-cut dict flags a picture landing; spoken payoff text -> both
    assert beats.payoff_kind({"planned_from": "speech",
                              "transcript": "and it worked.",
                              "payoff": {"local_t": 18.0}}) == "both"
    assert beats.payoff_kind({"planned_from": "speech", "transcript": "",
                              "payoff": {"local_t": 18.0}}) == "picture"


# --- the anchor: speech / picture / both / fallback ------------------------------

def _speech_plan():
    return {"id": "cut_01", "planned_from": "speech",
            "segments": [[95.0, 106.0]], "duration_s": 11.0,
            "transcript": "here we go. the whole level just builds itself "
                          "in one click.",
            "payoff": None}


def test_speech_anchor_is_the_last_payoff_word_plus_settle(tmp_path):
    an = _analysis_dir(tmp_path, words=WORDS)
    a = beats.payoff_anchor(_speech_plan(), an)
    assert a["kind"] == "speech" and a["resolved"] and a["basis"] == "measured"
    # last word "click." ends 104.5 source = 9.5 local; + small settle
    assert a["t_speech_end"] == pytest.approx(9.5, abs=0.05)
    assert a["t_anchor"] == pytest.approx(9.5 + beats.SPEECH_SETTLE_S, abs=0.05)


def test_picture_anchor_takes_first_scene_boundary_after_planned_moment(tmp_path):
    plan = {"id": "cut_02", "planned_from": "visual", "captions": "none",
            "segments": [[200.0, 224.0]], "duration_s": 24.0}
    # planned moment = dur - LEGACY_TAIL_S = 20.6 local = 220.6 source
    an = _analysis_dir(tmp_path, scenes=[210.0, 221.4, 223.0])
    a = beats.payoff_anchor(plan, an)
    assert a["kind"] == "picture" and a["resolved"]
    assert a["t_visual_settle"] == pytest.approx(21.4, abs=0.05)
    assert a["t_anchor"] == pytest.approx(21.4, abs=0.05)


def test_picture_settle_is_capped_at_planned_plus_2_5s(tmp_path, capsys):
    plan = {"id": "cut_03", "planned_from": "visual", "captions": "none",
            "segments": [[200.0, 230.0]], "duration_s": 30.0}
    # planned = 26.6 local; only scene boundary is past the cap -> no scene
    # hit, no video -> unresolved fallback, loudly
    an = _analysis_dir(tmp_path, scenes=[229.9])
    a = beats.payoff_anchor(plan, an)
    assert not a["resolved"] and a["basis"] == "plan"
    assert a["t_anchor"] == pytest.approx(30.0 - beats.LEGACY_TAIL_S, abs=0.05)
    assert "UNRESOLVED" in capsys.readouterr().out


def test_both_kind_takes_the_later_of_speech_and_picture(tmp_path):
    plan = {"id": "cut_04", "planned_from": "speech",
            "segments": [[95.0, 106.0]], "duration_s": 11.0,
            "transcript": "the whole level just builds itself in one click.",
            "payoff": {"jump": False, "local_t": 8.0, "local_e": 9.0}}
    # speech end 9.5+0.15; scene boundary right after planned local_e=9.0:
    # source 104.2 = local 9.2 -> speech is later and wins
    an = _analysis_dir(tmp_path, words=WORDS, scenes=[104.2])
    a = beats.payoff_anchor(plan, an)
    assert a["kind"] == "both" and a["resolved"]
    assert a["t_anchor"] == pytest.approx(9.5 + beats.SPEECH_SETTLE_S, abs=0.05)
    assert a["t_visual_settle"] == pytest.approx(9.2, abs=0.05)


def test_both_kind_floors_at_the_plan_payoff_end_when_picture_unmeasured(tmp_path):
    """Found on real as-* plans: a 'both' cut whose scenes gave no settle
    anchored on speech alone at 20s while the payoff PICTURE ran to 28s --
    which would put the card back on the payoff. The plan's own payoff end
    floors the anchor when the picture side could not be measured."""
    plan = {"id": "cut_06", "planned_from": "speech",
            "segments": [[95.0, 106.0], [200.0, 219.0]], "duration_s": 30.0,
            "transcript": "the whole level just builds itself in one click.",
            "payoff": {"jump": True, "local_t": 11.0, "local_e": 28.0}}
    an = _analysis_dir(tmp_path, words=WORDS)  # no scenes.json, no video
    a = beats.payoff_anchor(plan, an)
    assert a["kind"] == "both" and a["resolved"]
    assert a["t_speech_end"] == pytest.approx(9.5, abs=0.05)
    assert a["t_anchor"] == pytest.approx(28.0)  # floored, not 9.65


def test_missing_artifacts_fall_back_loudly_never_crash(tmp_path, capsys):
    an = str(tmp_path / "nonexistent-analysis")
    a = beats.payoff_anchor(_speech_plan(), an)
    assert not a["resolved"] and a["basis"] == "plan"
    assert a["t_anchor"] is not None
    out = capsys.readouterr().out
    assert "falls back" in out and "UNRESOLVED" in out


# --- motion settle: pure drop-point logic + cap ----------------------------------

def test_settle_point_finds_the_drop_after_the_peak():
    samples = [(20.0, 2.0), (20.5, 9.0), (21.0, 6.0), (21.5, 3.0), (22.0, 1.0)]
    assert beats.settle_point(samples, cap=23.0) == pytest.approx(21.5)


def test_settle_point_caps_when_motion_never_calms():
    samples = [(20.0, 8.0), (20.5, 9.0), (21.0, 8.5), (21.5, 8.8)]
    assert beats.settle_point(samples, cap=21.2) == pytest.approx(21.2)
    assert beats.settle_point([], cap=21.2) is None


# --- planner threading: tail accounting consumes the same anchor ------------------

def _plan_via_direct(tmp_path, anchor):
    """Run _plan_from with a monkeypatch-free fake anchor via anchor_ctx."""
    sents = [{"s": 95.0, "e": 100.0, "text": "here we go."},
             {"s": 100.0, "e": 104.5,
              "text": "the whole level just builds itself in one click."},
             {"s": 104.5, "e": 118.0, "text": "and that is the whole trick "
              "really, twenty five seconds of it in fact honestly."}]
    ref = {"landing_ok": True, "ranges": [[0, 2]], "title": "t",
           "hook": "watch this", "cta": "save this trick",
           "handles": ["readable_detail", "narrative_turn"],
           "overlay_lines": []}
    return direct._plan_from({"s": 95.0, "e": 118.0, "signals": [], "why": "w"},
                             ref, sents, [], 1, vr=(), anchor_ctx=anchor)


def test_plan_duration_accounts_for_the_measured_anchor(tmp_path, monkeypatch):
    fake = {"kind": "speech", "t_speech_end": 22.1, "t_visual_settle": None,
            "t_anchor": 22.25, "resolved": True, "basis": "measured"}
    monkeypatch.setattr(direct, "_measured_anchor", lambda pl, ctx: dict(fake))
    with redirect_stdout(StringIO()):
        plan = _plan_via_direct(tmp_path, anchor={"analysis_dir": "x"})
    assert plan and not plan.get("_rejected")
    assert plan["payoff_anchor"]["t_anchor"] == 22.25
    # the tail accounting guarantees breath + minimum card room after the
    # SAME anchor the render will use, so plan duration == rendered duration
    assert plan["duration_s"] >= (plan["delivery_end_s"]
                                  + overlays.ENDCARD_BREATH_S
                                  + overlays.MIN_CARD_AFTER_PAYOFF_S - 0.05)
    t0, t1 = overlays.endcard_window(plan)
    assert t0 >= 22.25 + overlays.ENDCARD_BREATH_S - 0.01
    assert t1 == plan["duration_s"]


def test_unresolved_anchor_keeps_plan_estimate(monkeypatch, tmp_path):
    monkeypatch.setattr(direct, "_measured_anchor",
                        lambda pl, ctx: {"kind": "speech", "t_speech_end": None,
                                         "t_visual_settle": None, "t_anchor": 19.0,
                                         "resolved": False, "basis": "plan"})
    with redirect_stdout(StringIO()):
        plan = _plan_via_direct(tmp_path, anchor={"analysis_dir": "x"})
    # unresolved: delivery end stays the plan's own estimate (clip end)
    assert plan["delivery_end_s"] >= 19.0
    assert plan["payoff_anchor"]["resolved"] is False


# --- endcard window: measured anchor beats plan estimate --------------------------

def test_endcard_window_uses_the_resolved_measured_anchor():
    plan = {"id": "c", "duration_s": 26.0,
            "payoff": {"jump": True, "local_t": 17.0, "local_e": 20.0},
            "payoff_anchor": {"kind": "both", "t_anchor": 23.4,
                              "resolved": True, "basis": "measured"}}
    t0, t1 = overlays.endcard_window(plan)
    assert t0 == pytest.approx(23.4 + overlays.ENDCARD_BREATH_S)
    assert t1 == 26.0


def test_endcard_window_ignores_unresolved_anchor():
    plan = {"id": "c", "duration_s": 26.0,
            "payoff": {"jump": True, "local_t": 18.0, "local_e": 23.0},
            "payoff_anchor": {"kind": "picture", "t_anchor": 20.0,
                              "resolved": False, "basis": "plan"}}
    t0, _ = overlays.endcard_window(plan)
    assert t0 == pytest.approx(23.0 + overlays.ENDCARD_BREATH_S)


def test_endcard_window_warns_when_card_room_is_squeezed(capsys):
    # 1.0s of room: usable but under MIN_CARD_AFTER_PAYOFF_S -> warn, keep card
    plan = {"id": "cut_09", "duration_s": 21.0,
            "payoff_anchor": {"kind": "picture", "t_anchor": 19.75,
                              "resolved": True, "basis": "measured"}}
    t0, t1 = overlays.endcard_window(plan)
    assert t0 == pytest.approx(20.0)  # never pulled back onto the payoff
    assert "card room" in capsys.readouterr().out


def test_endcard_window_skips_card_when_no_usable_room(capsys):
    # payoff runs to 20.5 of a 21.0 cut: 0.25s is a glitch-flash, not a card.
    # The cut ships clean (None) instead (reviewer, 2026-08-03).
    plan = {"id": "cut_09", "duration_s": 21.0,
            "payoff_anchor": {"kind": "picture", "t_anchor": 20.5,
                              "resolved": True, "basis": "measured"}}
    assert overlays.endcard_window(plan) is None
    assert "NO ROOM" in capsys.readouterr().out


# --- reveal vs tease --------------------------------------------------------------

def test_overlay_lines_carry_roles_defaulting_to_tease():
    ref = {"overlay_lines": [
        {"t": 6.0, "text": "what happens next", "role": "tease"},
        {"t": 12.0, "text": "the door opens", "role": "reveal"},
        {"t": 16.0, "text": "no role given"},
        {"t": 18.0, "text": "bad role", "role": "spoiler"}]}
    out = direct._overlay_lines(ref, 30.0)
    assert [o["role"] for o in out[:2]] == ["tease", "reveal"]
    # only 3 lines kept, and unknown/absent roles read as tease
    assert len(out) == 3 and out[2]["role"] == "tease"


def test_reveal_lines_are_held_back_to_their_moment(monkeypatch):
    monkeypatch.setattr("reelly.beats.resolve_reveal",
                        lambda plan, text, t, an: 14.0)
    lines = [{"t": 8.0, "text": "the door opens", "show_s": 3.0, "role": "reveal"},
             {"t": 6.0, "text": "wait for it", "show_s": 3.0, "role": "tease"}]
    with redirect_stdout(StringIO()):
        out = direct._resolve_reveals(lines, {"segments": [[0, 30]]},
                                      {"analysis_dir": "x"}, 30.0)
    assert out[0]["t"] == 14.0 and out[0]["anchor"] == 14.0  # constrained
    assert out[1]["t"] == 6.0 and "anchor" not in out[1]     # tease exempt


def test_unresolvable_reveal_keeps_time_and_records_none_anchor(monkeypatch):
    monkeypatch.setattr("reelly.beats.resolve_reveal",
                        lambda plan, text, t, an: None)
    lines = [{"t": 8.0, "text": "the door opens", "show_s": 3.0, "role": "reveal"}]
    out = direct._resolve_reveals(lines, {"segments": [[0, 30]]},
                                  {"analysis_dir": "x"}, 30.0)
    assert out[0]["t"] == 8.0 and out[0]["anchor"] is None


def test_reveal_whose_moment_is_at_clip_end_is_dropped_loudly(monkeypatch, capsys):
    monkeypatch.setattr("reelly.beats.resolve_reveal",
                        lambda plan, text, t, an: 29.7)
    lines = [{"t": 8.0, "text": "too late", "show_s": 3.0, "role": "reveal"}]
    out = direct._resolve_reveals(lines, {"segments": [[0, 30]]},
                                  {"analysis_dir": "x"}, 30.0)
    assert out == [] and "dropped reveal" in capsys.readouterr().out


def test_resolve_reveal_matches_speech_and_payoff_beat(tmp_path):
    an = _analysis_dir(tmp_path, words=WORDS)
    plan = {"planned_from": "speech", "segments": [[95.0, 106.0]]}
    # spoken moment: line quotes the transcript -> matched phrase START
    t = beats.resolve_reveal(plan, "builds itself in one click", 3.0, an)
    assert t == pytest.approx(101.9 - 95.0, abs=0.1)
    # visual moment: picture plan, line planned near the payoff beat
    vplan = {"planned_from": "visual", "captions": "none",
             "segments": [[0, 24]], "payoff": {"local_t": 18.0, "local_e": 21.0}}
    assert beats.resolve_reveal(vplan, "the reveal", 16.0, an) == 18.0
    # nothing to resolve against
    assert beats.resolve_reveal({"planned_from": "visual", "captions": "none",
                                 "segments": [[0, 24]]}, "x", 5.0, an) is None


# --- QC gates ---------------------------------------------------------------------

def _root_with_specs(tmp_path, events, meta=None):
    root = tmp_path / "proj"
    (root / "edl").mkdir(parents=True)
    specs = {"cut_01": events}
    if meta is not None:
        specs["_meta"] = {"cut_01": meta}
    (root / "edl" / "overlay_specs.json").write_text(json.dumps(specs))
    return str(root)


ANCHOR = {"kind": "both", "t_anchor": 21.0, "t_speech_end": 20.4,
          "t_visual_settle": 21.0, "resolved": True, "basis": "measured"}


def test_endcard_timing_pass_fail_and_both_numbers_printed(tmp_path):
    plan = {"id": "cut_01", "duration_s": 25.0}
    ok = _root_with_specs(tmp_path, [{"template": "kitcard", "args": ["x"],
                                      "t": [21.3, 25.0], "role": "endcard",
                                      "anchor": ANCHOR}])
    assert judge.endcard_timing(ok, plan)[1] == "PASS"
    bad = _root_with_specs(tmp_path / "b",
                           [{"template": "kitcard", "args": ["x"],
                             "t": [20.5, 25.0], "role": "endcard",
                             "anchor": ANCHOR}])
    name, status, detail = judge.endcard_timing(bad, plan)
    assert status == "FAIL" and "20.50" in detail and "21.00" in detail


def test_endcard_timing_warns_on_unresolved_anchor_and_skips_without_card(tmp_path):
    plan = {"id": "cut_01", "duration_s": 25.0}
    root = _root_with_specs(tmp_path, [
        {"template": "kitcard", "args": ["x"], "t": [20.0, 25.0],
         "role": "endcard",
         "anchor": {"kind": "picture", "t_anchor": 21.6, "resolved": False}}])
    assert judge.endcard_timing(root, plan)[1] == "WARN"
    empty = _root_with_specs(tmp_path / "e", [])
    assert judge.endcard_timing(empty, plan)[1] == "SKIP"
    assert judge.endcard_timing(str(tmp_path / "nowhere"), plan)[1] == "SKIP"


def test_endcard_timing_recognises_legacy_card_events_and_meta_anchor(tmp_path):
    """Pre-role specs: the fade_out:false closing card is still found, and
    the anchor can come from the _meta record."""
    plan = {"id": "cut_01", "duration_s": 25.0}
    root = _root_with_specs(
        tmp_path,
        [{"template": "badge", "args": ["l.png"], "t": [20.2, 25.0],
          "fade_out": False}],
        meta={"payoff_anchor": ANCHOR})
    name, status, detail = judge.endcard_timing(root, plan)
    assert status == "FAIL" and "21.00" in detail


def test_reveal_spoiler_fail_names_the_line():
    plan = {"id": "cut_01", "overlay_lines": [
        {"t": 10.0, "text": "the lemon lands", "role": "reveal", "anchor": 15.0},
        {"t": 5.0, "text": "wait for it", "role": "tease"}]}
    name, status, detail = judge.reveal_spoiler(plan)
    assert status == "FAIL" and "lemon" in detail and "15.00" in detail


def test_reveal_spoiler_pass_warn_skip_paths():
    ok = {"overlay_lines": [
        {"t": 15.0, "text": "x", "role": "reveal", "anchor": 15.0}]}
    assert judge.reveal_spoiler(ok)[1] == "PASS"
    warn = {"overlay_lines": [
        {"t": 8.0, "text": "y", "role": "reveal", "anchor": None}]}
    name, status, detail = judge.reveal_spoiler(warn)
    assert status == "WARN" and "y" in detail
    assert judge.reveal_spoiler({"overlay_lines": [
        {"t": 8.0, "text": "z", "role": "tease"}]})[1] == "SKIP"
    assert judge.reveal_spoiler({})[1] == "SKIP"


# --- autoplan writes inspectable role + anchor into overlay_specs.json -------------

def test_autoplan_records_meta_and_endcard_anchor(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / "edl").mkdir(parents=True)
    fin = root / "deliverables" / "final"
    fin.mkdir(parents=True)
    (fin / "cut_01.mp4").write_bytes(b"v")
    anc = {"kind": "picture", "t_anchor": 22.6, "t_speech_end": None,
           "t_visual_settle": 22.6, "resolved": True, "basis": "measured"}
    plan = {"id": "cut_01", "title": "t", "duration_s": 26.0,
            "hook": {"text": "h", "show_s": 3.6}, "cta": "save this",
            "caption": "", "segments": [[0, 26]],
            "overlay_lines": [{"t": 23.0, "text": "the turn", "show_s": 3.0,
                               "role": "reveal", "anchor": 22.9}],
            "payoff_anchor": anc}
    (root / "edl" / "cut_plans.json").write_text(json.dumps([plan]))
    monkeypatch.setattr(overlays, "kit_endcard", lambda product: None)
    monkeypatch.setattr(overlays, "card_scrim_event",
                        lambda r, w: {"template": "kitcard", "args": ["s.png"],
                                      "t": list(w), "ent": "none"})
    monkeypatch.setattr(
        "reelly.placement.plan_mark",
        lambda vid, t, p, text, register, avoid=None:
        {"x": 60, "y": 1200, "size": 40, "color": "#fff", "w": 900, "h": 100,
         "stroke": 5, "scrim": 0.6, "backdrop_detail": 1, "backdrop_luma": 1})
    with redirect_stdout(StringIO()):
        overlays.autoplan(str(root), meme=False)
    specs = json.load(open(root / "edl" / "overlay_specs.json"))
    card = [e for e in specs["cut_01"] if e.get("role") == "endcard"][0]
    assert card["anchor"]["t_anchor"] == 22.6
    assert card["t"][0] == pytest.approx(22.6 + overlays.ENDCARD_BREATH_S)
    meta = specs["_meta"]["cut_01"]
    assert meta["payoff_anchor"]["t_anchor"] == 22.6
    assert meta["lines"][0]["role"] == "reveal"
    assert meta["lines"][0]["anchor"] == 22.9
    assert meta["endcard_t0"] == card["t"][0]


def test_apply_skips_the_meta_record(tmp_path):
    root = tmp_path / "proj"
    (root / "edl").mkdir(parents=True)
    src = root / "deliverables" / "final"
    src.mkdir(parents=True)
    (root / "edl" / "overlay_specs.json").write_text(json.dumps(
        {"_meta": {"cut_01": {"payoff_anchor": None}}, "cut_01": []}))
    with redirect_stdout(StringIO()):
        overlays.apply(str(root))  # must not treat "_meta" as a cut id


# --- DESCRIPTION.md names only variants that ship ----------------------------------

def _desc(tmp_path, variants, targets=("tiktok",)):
    plan = {"id": "cut_01", "title": "t", "duration_s": 24.0,
            "hook": {"text": "hi"}, "cta": "play it on example.invalid",
            "caption": "cap"}
    tek = {"name": "creator", "trending_audio": True}
    p = tmp_path / "d.md"
    products.description_md("video", plan, str(p), targets=list(targets),
                            account=tek, variants=variants)
    return p.read_text()


def test_description_gfx_first_default_never_names_trending(tmp_path):
    """The gfx-only regression: variants=["gfx"] builds no _trending file
    (finalize skips the clean mix), yet the posting block routed tiktok to
    cut_01_trending.mp4. It must name the file that ships and say the bed
    is baked in."""
    body = _desc(tmp_path, ["gfx"])
    assert "cut_01_gfx.mp4" in body
    assert "cut_01_trending" not in body      # no unshipped file is ever named
    assert "baked in" in body


def test_description_trending_gfx_routes_to_the_gfx_sibling(tmp_path):
    body = _desc(tmp_path, ["gfx", "trending_gfx"])
    assert "cut_01_trending_gfx.mp4" in body
    assert "trending audio in-app" in body   # clean mix really ships


def test_description_full_variant_set_and_legacy_are_unchanged(tmp_path):
    body = _desc(tmp_path, ["plain", "gfx", "trending", "trending_gfx"])
    assert "`cut_01_trending.mp4`" in body
    assert "`cut_01.mp4`" in _desc(tmp_path, ["plain", "gfx"], targets=("x",))
    assert "`cut_01_trending.mp4`" in _desc(tmp_path, None)  # legacy: no set


# --- picture-anchor hardening (reviewer, 2026-08-03: cards mid-payoff) --------

def test_visual_settle_advances_through_a_cutting_run():
    # boundaries at 20.1, 20.9, 21.8, 24.0: the first three are one cutting
    # run (gaps <= RESETTLE_S); settle is the run's END, not its first cut
    from reelly import beats
    plan = {"id": "c", "duration_s": 26.0, "segments": [[100.0, 126.0]],
            "planned_from": "visual", "captions": "none",
            "delivery_end_s": 19.5}
    import json, tempfile, os
    d = tempfile.mkdtemp()
    json.dump([{"t": 120.1}, {"t": 120.9}, {"t": 121.8}, {"t": 124.0}],
              open(os.path.join(d, "scenes.json"), "w"))
    a = beats.payoff_anchor(plan, d)
    # planned end 19.5 -> run 20.1..21.8 within cap 22.0 -> settle 21.8
    assert a["t_visual_settle"] == pytest.approx(21.8)


def test_picture_anchor_floored_by_plan_payoff_end():
    from reelly import beats
    plan = {"id": "c", "duration_s": 30.0, "segments": [[100.0, 130.0]],
            "planned_from": "visual", "captions": "none",
            "payoff": {"local_t": 20.0, "local_e": 25.0},
            "delivery_end_s": 20.0}
    import json, tempfile, os
    d = tempfile.mkdtemp()
    # an early boundary right after the planned moment: without the floor the
    # anchor would land at 20.6, ON the payoff the plan says runs to 25.0
    json.dump([{"t": 120.6}], open(os.path.join(d, "scenes.json"), "w"))
    a = beats.payoff_anchor(plan, d)
    assert a["kind"] == "picture"
    assert a["t_anchor"] == pytest.approx(25.0)
