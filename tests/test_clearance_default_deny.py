"""Voice clearance is DEFAULT-DENY.

Guest speech shipped in 3 cuts because blocking was opt-in: a diarized
speaker nobody had listed in voices.json was allowed by omission. Now every
diarized speaker blocks planning and QC until a human clears it; diarize
writes the cleared:false defaults and `reelly clear` flips one. The
UNVERIFIED degrade path (no/failed diarization) is unchanged.
"""
import json
import os

import pytest

from reelly import clearance


def _proj(tmp_path, turns=None, voices=None):
    root = tmp_path / "proj"
    (root / "analysis").mkdir(parents=True, exist_ok=True)
    if turns is not None:
        json.dump(turns, open(root / "analysis" / "speaker_turns.json", "w"))
    if voices is not None:
        json.dump(voices, open(root / "analysis" / "voices.json", "w"))
    return str(root)


def _ok_turns(**speakers):
    return {"engine": "test", "status": "ok", "turns": [],
            "speakers": {k: {"ranges": v,
                             "talk_s": round(sum(e - s for s, e in v), 1)}
                         for k, v in speakers.items()}}


def test_unlisted_diarized_speakers_are_blocked_by_default(tmp_path):
    """No voices.json at all: EVERY diarized turn is a blocked range."""
    root = _proj(tmp_path, _ok_turns(SPEAKER_00=[[0.0, 10.0]],
                                     SPEAKER_01=[[10.0, 20.0], [30.0, 35.0]]))
    blocked = clearance.blocked_ranges(root)
    assert [(s, e) for s, e, _ in blocked] == [(0.0, 10.0), (10.0, 20.0),
                                               (30.0, 35.0)]
    assert all("not cleared" in why for _, _, why in blocked)


def test_cleared_speaker_is_allowed_uncleared_and_unlisted_stay_blocked(tmp_path):
    root = _proj(
        tmp_path,
        _ok_turns(SPEAKER_00=[[0.0, 10.0]], SPEAKER_01=[[10.0, 20.0]],
                  SPEAKER_02=[[20.0, 25.0]]),
        {"speakers": [
            {"id": "SPEAKER_00", "cleared": True, "by": "reviewer",
             "on": "2026-08-02"},
            {"id": "SPEAKER_01", "cleared": False}]})
    blocked = clearance.blocked_ranges(root)
    spans = sorted((s, e) for s, e, _ in blocked)
    assert (0.0, 10.0) not in spans          # cleared host is allowed
    assert (10.0, 20.0) in spans             # recorded uncleared
    assert (20.0, 25.0) in spans             # unlisted: default-deny


def test_planning_gate_fails_a_cut_touching_an_unlisted_speaker(tmp_path):
    root = _proj(tmp_path, _ok_turns(GUEST=[[100.0, 150.0]]))
    blocked = clearance.blocked_ranges(root)
    gate, status, detail = clearance.verdict(
        {"segments": [[120.0, 130.0]]}, blocked)
    assert status == "FAIL" and "GUEST" in detail


def test_project_predating_diarization_keeps_working_ungated(tmp_path):
    """No speaker_turns.json: the old UNVERIFIED path, no blocks invented."""
    root = _proj(tmp_path)
    assert clearance.blocked_ranges(root) == []


def test_unavailable_diarization_keeps_the_unverified_degrade(tmp_path):
    """A failed diarize run must not default-deny ranges it never produced."""
    root = _proj(tmp_path, {"engine": "test", "status": "unavailable",
                            "error": "no token", "unverified": True,
                            "turns": [], "speakers": {}})
    assert clearance.blocked_ranges(root) == []


def test_single_speaker_session_gated_until_cleared_then_ungated(tmp_path):
    root = _proj(tmp_path, _ok_turns(SPEAKER_00=[[0.0, 60.0]]))
    assert clearance.blocked_ranges(root)          # gated: nobody cleared
    clearance.mark_cleared(root, "SPEAKER_00", cleared=True, by="reviewer")
    assert clearance.blocked_ranges(root) == []    # ungated as today


def test_sync_voices_writes_defaults_and_preserves_human_decisions(tmp_path):
    root = _proj(tmp_path)
    an = os.path.join(root, "analysis")
    art = _ok_turns(SPEAKER_00=[[0.0, 5.0]], SPEAKER_01=[[5.0, 9.0]])
    p = clearance.sync_voices(an, art)
    data = json.load(open(p))
    assert [sp["id"] for sp in data["speakers"]] == ["SPEAKER_00", "SPEAKER_01"]
    assert all(sp["cleared"] is False for sp in data["speakers"])
    # a human clears one; a re-diarize adds a new id without losing the call
    clearance.mark_cleared(root, "SPEAKER_00", cleared=True, by="reviewer")
    clearance.sync_voices(an, _ok_turns(SPEAKER_00=[[0.0, 5.0]],
                                        SPEAKER_01=[[5.0, 9.0]],
                                        SPEAKER_02=[[9.0, 12.0]]))
    by_id = {sp["id"]: sp for sp in json.load(open(p))["speakers"]}
    assert by_id["SPEAKER_00"]["cleared"] is True
    assert by_id["SPEAKER_00"]["by"] == "reviewer"
    assert by_id["SPEAKER_02"]["cleared"] is False


def test_sync_voices_writes_nothing_for_unavailable_diarization(tmp_path):
    root = _proj(tmp_path)
    an = os.path.join(root, "analysis")
    assert clearance.sync_voices(an, {"status": "unavailable",
                                      "speakers": {}}) is None
    assert not os.path.exists(os.path.join(an, "voices.json"))


def test_mark_cleared_requires_a_name_and_a_real_speaker_id(tmp_path):
    root = _proj(tmp_path, _ok_turns(SPEAKER_00=[[0.0, 5.0]]))
    with pytest.raises(SystemExit, match="--by"):
        clearance.mark_cleared(root, "SPEAKER_00", cleared=True)
    with pytest.raises(SystemExit, match="unknown speaker"):
        clearance.mark_cleared(root, "SPEAKER_99", cleared=True, by="F")


def test_mark_uncleared_records_the_block(tmp_path):
    root = _proj(tmp_path, _ok_turns(SPEAKER_00=[[0.0, 5.0]],
                                     SPEAKER_01=[[5.0, 9.0]]))
    entry = clearance.mark_cleared(root, "SPEAKER_01", cleared=False,
                                   note="guest, no release")
    assert entry["cleared"] is False and entry["note"] == "guest, no release"
    spans = [(s, e) for s, e, _ in clearance.blocked_ranges(root)]
    assert (5.0, 9.0) in spans
