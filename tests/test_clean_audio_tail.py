"""Tail hygiene: the content end must never sit
on a rising sound onset.

The source went silent at ~19.4s and a NEW loud sound began at ~19.96s; the
20.0s content end shipped its first ~60ms, loudness gain amplified it ~10x,
and the appended outro's silence followed -- a clipped blast at the
content->outro boundary. The vision ending check passed it (video only).

Covered here: the detector on synthetic envelopes, the plan-time and
render-time trims (payoff floor + cap), finalize's accounting consistency
after a trim, and the clean_audio_tail QC gate's PASS/FAIL/WARN paths.
"""
import json
import os
import subprocess
from contextlib import redirect_stdout
from io import StringIO

import pytest

from reelly import audiotail, config, finalize, judge, outro


def _env(rms_values, end_t, win=audiotail.WIN_S):
    """Synthetic tail envelope ending exactly at end_t: [(t_start, rms)]."""
    n = len(rms_values)
    return [(end_t - (n - i) * win, float(v)) for i, v in enumerate(rms_values)]


# the measured cut_04 shape: sound, real silence, then a chopped loud onset
CHOP = [900, 500, 60, 10, 4, 2, 1, 0, 0, 0, 0, 7, 634, 840]


# --- detector on synthetic envelopes -------------------------------------------

def test_detector_fires_on_silence_then_chopped_onset():
    hit = audiotail.clipped_onset(_env(CHOP, 20.0), 20.0)
    assert hit is not None
    # the onset is the last two windows (0.06s); new end = onset - breath
    assert hit["onset_start"] == pytest.approx(20.0 - 2 * 0.03, abs=1e-6)
    assert hit["new_end"] == pytest.approx(20.0 - 0.06 - 0.05, abs=1e-6)
    assert hit["silence_s"] >= audiotail.SILENCE_MIN_S
    assert hit["onset_rms"] == 634 and hit["last_rms"] == 840


def test_detector_ignores_sustained_sound_to_the_end():
    # music playing through the cut: loud all the way, nothing is chopped
    assert audiotail.clipped_onset(_env([800] * 14, 20.0), 20.0) is None


def test_detector_ignores_already_clean_tail():
    # delivery lands, silence to the end: exactly the designed ending
    clean = [900, 700, 300, 80, 20, 5, 2, 1, 0, 0, 0, 0, 0, 0]
    assert audiotail.clipped_onset(_env(clean, 20.0), 20.0) is None


def test_detector_ignores_burst_that_ends_before_the_boundary():
    # a burst that finishes and leaves real trailing silence (>= END_SLACK_S)
    # is contained content, not a chop
    contained = [900, 500, 60, 5, 1, 0, 700, 650, 300, 80, 60, 50]
    assert audiotail.clipped_onset(_env(contained, 20.0), 20.0) is None


def test_detector_fires_on_chopped_burst_despite_codec_padding():
    # the REAL cut_04 raw shape: AAC padding leaves quiet windows past the
    # last content sample (376 -> 106) while the chopped burst peaked at 800;
    # trailing quiet shorter than END_SLACK_S must not read as a clean ending
    padded = [5, 4, 3, 2, 1, 0, 0, 0, 0, 0, 125, 800, 778, 376, 106]
    hit = audiotail.clipped_onset(_env(padded, 20.06), 20.06)
    assert hit is not None
    assert hit["onset_start"] == pytest.approx(20.06 - 5 * 0.03, abs=1e-6)
    assert hit["last_rms"] == 800   # boundary loudness: max of the slack span


def test_detector_ignores_soft_blip_at_the_boundary():
    # rises out of silence but never peaks above ONSET_RMS: not a blast
    blip = [900, 500, 60, 10, 4, 2, 1, 0, 0, 0, 0, 150, 300, 380]
    assert audiotail.clipped_onset(_env(blip, 20.0), 20.0) is None


def test_detector_ignores_word_gap_shorter_than_silence_min():
    # a 0.09s dip between words is a gap, not a silence stretch
    gap = [900, 800, 700, 600, 500, 400, 300, 200, 90, 80, 70, 500, 700, 900]
    assert audiotail.clipped_onset(_env(gap, 20.0), 20.0) is None


def test_detector_caps_the_trim():
    # onset starting so early the trim would exceed MAX_TRIM_S: not hygiene's
    # move (only reachable with a wider scan window; the detector still guards)
    long_tail = [0] * 6 + [500] * 24
    env = _env(long_tail, 20.0)
    assert env[6][0] < 20.0 - audiotail.MAX_TRIM_S
    assert audiotail.clipped_onset(env, 20.0) is None


def test_real_envelope_and_detector_end_to_end(tmp_path):
    """A real file: tone, real silence, then a loud onset cut at the end."""
    wav = str(tmp_path / "tail.wav")
    subprocess.run(
        [config.FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
         "aevalsrc=if(lt(t\\,0.5)\\,0.5*sin(880*2*PI*t)\\,"
         "if(lt(t\\,0.9)\\,0\\,0.8*sin(440*2*PI*t))):s=16000:d=0.98",
         wav], check=True, capture_output=True)
    env = audiotail.envelope(wav, 0.58, 0.98)
    assert env and env[-1][0] == pytest.approx(0.95, abs=0.01)
    hit = audiotail.clipped_onset(env, 0.98)
    assert hit is not None
    assert hit["new_end"] == pytest.approx(0.85, abs=0.05)
    assert hit["last_rms"] > audiotail.ONSET_RMS


# --- plan-time trim --------------------------------------------------------------

def test_plan_tail_trim_trims_segment_and_total(monkeypatch, tmp_path):
    video = str(tmp_path / "src.mp4")
    open(video, "w").write("x")
    monkeypatch.setattr(audiotail, "envelope",
                        lambda p, t0, t1, **kw: _env(CHOP, t1))
    merged = [[2565.37, 2579.72], [2612.48, 2618.17]]
    out = StringIO()
    with redirect_stdout(out):
        merged, total, note = audiotail.plan_tail_trim(
            video, merged, 20.04, 18.0, "cut_04")
    trim = 0.06 + audiotail.BREATH_S
    assert merged[-1][1] == pytest.approx(2618.17 - trim, abs=0.01)
    assert total == pytest.approx(20.04 - trim, abs=0.01)
    assert note and "TAIL-hygiene" in note and "634" in note and "840" in note
    assert "trimmed 0.11s clipped onset off the content tail" in out.getvalue()


def test_plan_tail_trim_never_moves_end_before_payoff(monkeypatch, tmp_path):
    """Onset before payoff_complete_by = no trim + a loud print: bigger
    moves belong to the planner/ending check."""
    video = str(tmp_path / "src.mp4")
    open(video, "w").write("x")
    monkeypatch.setattr(audiotail, "envelope",
                        lambda p, t0, t1, **kw: _env(CHOP, t1))
    merged = [[2612.48, 2618.17]]
    out = StringIO()
    with redirect_stdout(out):
        m2, total, note = audiotail.plan_tail_trim(
            video, merged, 5.69, 5.69, "cut_04")   # floor == content end
    assert m2[-1][1] == 2618.17 and total == 5.69 and note is None
    assert "payoff_complete_by" in out.getvalue()
    assert "trimmed" not in out.getvalue()


def test_plan_tail_trim_no_video_or_clean_tail_is_a_noop(monkeypatch, tmp_path):
    merged = [[0.0, 20.0]]
    assert audiotail.plan_tail_trim(None, merged, 20.0, None, "c")[2] is None
    video = str(tmp_path / "src.mp4")
    open(video, "w").write("x")
    monkeypatch.setattr(audiotail, "envelope",
                        lambda p, t0, t1, **kw: _env([800] * 14, t1))
    m2, total, note = audiotail.plan_tail_trim(video, merged, 20.0, None, "c")
    assert m2[-1][1] == 20.0 and total == 20.0 and note is None


# --- render-time trim + finalize accounting ---------------------------------------

def test_raw_tail_trim_respects_floor_and_reports_trim(monkeypatch):
    monkeypatch.setattr(audiotail, "envelope",
                        lambda p, t0, t1, **kw: _env(CHOP, t1))
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: {"format": {"duration": "20.04"}})
    trim = audiotail.raw_tail_trim("raw.mp4", 18.0, "cut_04")
    assert trim == pytest.approx(0.11, abs=0.01)
    # floor past the trimmed end: print and leave
    out = StringIO()
    with redirect_stdout(out):
        assert audiotail.raw_tail_trim("raw.mp4", 20.0, "cut_04") == 0.0
    assert "payoff_complete_by" in out.getvalue()
    # legacy plans carry no payoff_complete_by: no floor, trim applies
    assert audiotail.raw_tail_trim("raw.mp4", None, "cut_04") > 0


def test_raw_tail_trim_probe_failure_never_kills_the_render(monkeypatch):
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: (_ for _ in ()).throw(OSError("no file")))
    with redirect_stdout(StringIO()):
        assert audiotail.raw_tail_trim("missing.mp4", None, "cut_01") == 0.0


def test_apply_tail_trim_keeps_all_accounting_consistent(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "edl"))
    plan = {"id": "cut_04", "segments": [[2565.37, 2579.72], [2612.48, 2618.17]],
            "content_s": 20.04, "duration_s": 22.8,
            "outro": {"len_s": 2.8, "style": "final_frame"}}
    other = {"id": "cut_05", "segments": [[1.0, 2.0]], "duration_s": 1.0}
    pp = os.path.join(root, "edl", "cut_plans.json")
    json.dump([plan, other], open(pp, "w"))
    finalize._apply_tail_trim(root, "", plan, 0.16)
    assert plan["segments"][-1][1] == pytest.approx(2618.01)
    assert plan["content_s"] == pytest.approx(19.88)
    assert plan["duration_s"] == pytest.approx(22.7)
    # accounting identities the rest of the system relies on
    assert outro.content_len(plan) == pytest.approx(plan["content_s"])
    assert outro.expected_duration(plan) == pytest.approx(plan["duration_s"])
    seg_total = sum(e - s for s, e in plan["segments"])
    assert seg_total == pytest.approx(plan["content_s"], abs=0.01)
    # persisted back for re-runs and the judge; siblings untouched
    saved = json.load(open(pp))
    assert saved[0]["content_s"] == pytest.approx(19.88)
    assert saved[0]["segments"][-1][1] == pytest.approx(2618.01)
    assert saved[1] == other


def test_apply_tail_trim_speed_segment_moves_source_end_by_speed(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "edl"))
    plan = {"id": "cut_01", "segments": [[10.0, 30.0, 2.0]],
            "content_s": 10.0, "duration_s": 12.8,
            "outro": {"len_s": 2.8, "style": "final_frame"}}
    json.dump([plan], open(os.path.join(root, "edl", "cut_plans.json"), "w"))
    finalize._apply_tail_trim(root, "", plan, 0.1)
    assert plan["segments"][-1][1] == pytest.approx(29.8)   # 0.1s * 2.0 speed
    assert plan["content_s"] == pytest.approx(9.9)


# --- the QC gate -------------------------------------------------------------------

_PLAN = {"id": "cut_04", "content_s": 20.0, "duration_s": 22.8,
         "outro": {"len_s": 2.8, "style": "final_frame"},
         "segments": [[2565.37, 2579.72], [2612.48, 2618.17]]}


def test_gate_fails_on_chopped_onset_with_both_rms_numbers(monkeypatch):
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: {"format": {"duration": "22.84"}})
    monkeypatch.setattr(audiotail, "envelope",
                        lambda p, t0, t1, **kw: _env(CHOP, t1))
    name, status, detail = judge.clean_audio_tail(_PLAN, "cut_04.mp4")
    assert (name, status) == ("clean_audio_tail", "FAIL")
    assert "634" in detail and "840" in detail        # both RMS numbers
    assert "20.00" in detail                          # the boundary judged


def test_gate_passes_on_clean_content_tail(monkeypatch):
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: {"format": {"duration": "22.84"}})
    monkeypatch.setattr(audiotail, "envelope",
                        lambda p, t0, t1, **kw: _env([500, 200, 60, 10, 2, 1,
                                                      0, 0, 0, 0, 0, 0], t1))
    name, status, detail = judge.clean_audio_tail(_PLAN, "cut_04.mp4")
    assert status == "PASS" and "20.00" in detail


def test_gate_warns_when_content_bounds_unknown(monkeypatch):
    # unreadable file
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: (_ for _ in ()).throw(OSError("gone")))
    assert judge.clean_audio_tail(_PLAN, "x.mp4")[1] == "WARN"
    # plan whose content end lies outside the file
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: {"format": {"duration": "10.0"}})
    name, status, detail = judge.clean_audio_tail(_PLAN, "x.mp4")
    assert status == "WARN" and "could not be determined" in detail
    # plan with no usable duration at all
    assert judge.clean_audio_tail({"id": "c"}, "x.mp4")[1] == "WARN"


def test_gate_warns_when_tail_audio_undecodable(monkeypatch):
    monkeypatch.setattr("reelly.media.probe",
                        lambda p: {"format": {"duration": "22.84"}})
    monkeypatch.setattr(audiotail, "envelope", lambda p, t0, t1, **kw: [])
    assert judge.clean_audio_tail(_PLAN, "x.mp4")[1] == "WARN"


def test_gate_is_wired_into_the_final_report():
    import inspect
    src = inspect.getsource(judge.run)
    assert "clean_audio_tail(plan, path)" in src


def test_loudness_sanity_remeasures_implausible_combined_reading(capsys):
    """A sub--45 LUFS combined-graph reading triggers an audio-only re-measure
    and the audio-only number wins (the -c copy concat under-read of
    2026-08-03)."""
    from unittest import mock
    from reelly import judge
    combined = ("Integrated loudness:\n  I: -66.8 LUFS\n"
                "True peak:\n  Peak: -60.0 dBFS\n")
    audio_only = ("Integrated loudness:\n  I: -14.0 LUFS\n"
                  "True peak:\n  Peak: -1.9 dBFS\n")
    with mock.patch.object(judge, "_analysis_stderr", return_value=combined), \
         mock.patch.object(judge, "_ffmpeg_stderr", return_value=audio_only), \
         mock.patch.object(judge.media, "probe", return_value={
             "streams": [{"codec_type": "video", "width": 1080, "height": 1920},
                         {"codec_type": "audio"}],
             "format": {"duration": "20.0"}}), \
         mock.patch.object(judge.media, "color_transfer", return_value=None):
        rows = judge.check_file("x.mp4", plan_dur=None)["results"]
    loud = [r for r in rows if r[0] == "loudness"][0]
    assert loud[1] == "PASS" and "-14.0" in loud[2]
    assert "implausible" in capsys.readouterr().out
