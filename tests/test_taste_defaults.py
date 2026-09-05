"""Taste-wave defaults (2026-08-02): first-frame hook, loop-closure nudge,
softer dead-air pad, and the refreshed ledger cost estimates.
"""
import pytest

from reelly import direct


def test_refine_prompt_requires_first_frame_hook():
    assert "FIRST frame" in direct.REFINE_PROMPT
    assert "on screen from frame 1" in direct.REFINE_PROMPT


def test_refine_prompt_prefers_loop_closure_endings():
    assert "echoes the opening" in direct.REFINE_PROMPT
    # the picture-only prompt already carried the loop rule; keep it there
    assert "returns near the opening image" in direct.VISUAL_REFINE_PROMPT


def test_hook_overlay_lands_on_frame_one(monkeypatch, tmp_path):
    """finalize's hook burn event starts at t0=0.0 — the hook is readable on
    the first frame, which is also the cover frame."""
    from reelly import captions, finalize
    monkeypatch.setattr(captions, "hook_png", lambda text, path, **k: path)
    plan = {"hook": {"text": "watch this", "show_s": 3.6}, "cta": "",
            "overlay_lines": [], "payoff": None, "captions": "none"}
    events = finalize._burn_events(plan, [], 10.0, str(tmp_path), None, None)
    assert events, "hook event missing"
    png, y, t0, t1 = events[0]
    assert t0 == 0.0
    assert t1 == pytest.approx(3.6)


def test_pacing_pad_is_one_notch_softer():
    # +20% over the old 0.35s: keep the beat after a delivery lands
    assert direct.PACING["keep_silence_pad_s"] == pytest.approx(0.42)


def test_full_edit_defaults_to_pacing_pad():
    sm = {"duration_s": 10.0, "silences": [[3.0, 7.0]]}
    fe = direct._full_edit(sm)
    assert fe["keep"] == [[0, 3.42], [6.58, 10.0]]


def test_full_edit_still_accepts_an_explicit_pad():
    sm = {"duration_s": 10.0, "silences": [[3.0, 7.0]]}
    fe = direct._full_edit(sm, pad=0.35)
    assert fe["keep"] == [[0, 3.35], [6.65, 10.0]]


def test_refine_cost_estimates_are_conservative_2x():
    """2026-08-02: Sol per-token pricing unverifiable here, so both estimates
    are a flagged, conservative 2x of the prior values (0.002 / 0.02)."""
    assert direct.EST_REFINE_COST == pytest.approx(0.004)
    assert direct.EST_REFINE_COST_GPT == pytest.approx(0.04)
