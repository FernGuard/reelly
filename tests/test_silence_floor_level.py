"""Quiet narration must not be read as dead air.

The fixed -32 dB silence floor means "quiet relative to a shipped voice". On a
recording captured well below Reelly's delivery level it was cutting into the
narration itself, and the failure was SILENT: `analyze` printed a talk ratio of
0.046 alongside 148 wpm -- mutually contradictory -- and every stage still
exited 0, emitting a 6.9s cut from a 98s demo.
"""
import pytest

from reelly import speech


# ------------------------------------------------------- level normalisation

def test_quiet_recording_is_gained_up_to_target(monkeypatch):
    monkeypatch.setattr(speech, "_target_lufs", lambda: -14.0)
    monkeypatch.setattr(speech, "_integrated_lufs", lambda v: -32.0)
    assert speech._detect_gain_db("/x/quiet.mp4") == pytest.approx(18.0)


def test_correctly_levelled_audio_is_left_alone(monkeypatch):
    """Idempotent for audio already at target: existing projects must run the
    silence detection byte-for-byte the way they do today."""
    monkeypatch.setattr(speech, "_target_lufs", lambda: -14.0)
    monkeypatch.setattr(speech, "_integrated_lufs", lambda v: -14.0)
    assert speech._detect_gain_db("/x/ok.mp4") == 0.0


def test_loud_audio_is_never_attenuated(monkeypatch):
    monkeypatch.setattr(speech, "_target_lufs", lambda: -14.0)
    monkeypatch.setattr(speech, "_integrated_lufs", lambda v: -8.0)
    assert speech._detect_gain_db("/x/loud.mp4") == 0.0


def test_unmeasurable_audio_changes_nothing(monkeypatch):
    monkeypatch.setattr(speech, "_integrated_lufs", lambda v: None)
    assert speech._detect_gain_db("/x/weird.mp4") == 0.0


# ------------------------------------------------------------ sanity gate

def test_transcript_contradicting_the_silence_map_raises():
    """148 wpm cannot coexist with a 4.6% talk ratio."""
    with pytest.raises(RuntimeError, match="contradicts the transcript"):
        speech._assert_talk_ratio_plausible(total=98.0, words=242,
                                            talk_ratio=0.046)


def test_a_genuinely_sparse_recording_still_passes():
    """A long video with little speech is legitimate and must not trip."""
    speech._assert_talk_ratio_plausible(total=600.0, words=120, talk_ratio=0.09)


def test_a_healthy_map_passes():
    speech._assert_talk_ratio_plausible(total=98.0, words=242, talk_ratio=0.62)


def test_no_transcript_means_no_opinion():
    speech._assert_talk_ratio_plausible(total=98.0, words=0, talk_ratio=0.0)
    speech._assert_talk_ratio_plausible(total=0.0, words=10, talk_ratio=0.0)
