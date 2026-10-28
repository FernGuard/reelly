"""Item 5 fixes: analyze notices a different source under a reused project
name, mediapipe aborts are contained, noise words are dropped, the
diarization retry names the real blocker."""
import json
import os
import subprocess

import pytest

from reelly import analyze, diarize, face, speech


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def test_fingerprint_changes_with_content(tmp_path):
    a = _write(tmp_path / "a.mp4", b"x" * 1000)
    b = _write(tmp_path / "b.mp4", b"y" * 1000)
    assert analyze.fingerprint(a)["sha256_edges"] != analyze.fingerprint(b)["sha256_edges"]
    assert analyze.fingerprint(a)["size"] == 1000


def test_fingerprint_reads_both_ends_of_big_files(tmp_path, monkeypatch):
    monkeypatch.setattr(analyze, "_FP_EDGE", 16)
    a = _write(tmp_path / "a.mp4", b"A" * 16 + b"M" * 100 + b"Z" * 16)
    b = _write(tmp_path / "b.mp4", b"A" * 16 + b"M" * 100 + b"Q" * 16)
    assert analyze.fingerprint(a)["sha256_edges"] != analyze.fingerprint(b)["sha256_edges"]


def test_same_source_is_a_cache_hit(tmp_path):
    an = tmp_path / "analysis"
    v = _write(tmp_path / "v.mp4", b"v" * 500)
    assert analyze._sources_changed(str(an), v, None, None) is False   # fresh folder
    (an / "words.json").write_text("{}")
    assert analyze._sources_changed(str(an), v, None, None) is False


def test_different_video_under_the_same_name_reruns(tmp_path):
    an = tmp_path / "analysis"
    v1 = _write(tmp_path / "v1.mp4", b"1" * 500)
    v2 = _write(tmp_path / "v2.mp4", b"2" * 500)
    analyze._sources_changed(str(an), v1, None, None)
    (an / "words.json").write_text("{}")
    assert analyze._sources_changed(str(an), v2, None, None) is True
    # and the record now describes v2, so the next run is a hit again
    assert analyze._sources_changed(str(an), v2, None, None) is False


def test_crop_change_reruns(tmp_path):
    an = tmp_path / "analysis"
    v = _write(tmp_path / "v.mp4", b"v" * 500)
    analyze._sources_changed(str(an), v, None, None)
    assert analyze._sources_changed(str(an), v, None, "100:100:0:0") is True


def test_analysis_from_before_fingerprints_reruns_once(tmp_path):
    an = tmp_path / "analysis"
    an.mkdir()
    (an / "words.json").write_text("{}")
    v = _write(tmp_path / "v.mp4", b"v" * 500)
    assert analyze._sources_changed(str(an), v, None, None) is True
    assert analyze._sources_changed(str(an), v, None, None) is False


# --- mediapipe abort containment ------------------------------------------------------

@pytest.fixture
def fresh_probe():
    face.mediapipe_usable.cache_clear()
    face._landmarker.cache_clear()
    face._blaze.cache_clear()
    yield
    face.mediapipe_usable.cache_clear()
    face._landmarker.cache_clear()
    face._blaze.cache_clear()


def test_a_killed_probe_turns_face_detection_off(monkeypatch, fresh_probe, capsys):
    monkeypatch.delenv("REELLY_FACE", raising=False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a[0], -6, b"", b"F0000 graph_service.h:139] Check failed: service_"))
    assert face.mediapipe_usable() is False
    assert face._landmarker() is None and face._blaze() is None
    assert face.detector_kind() == "none"
    assert "face detection unavailable" in capsys.readouterr().out


def test_a_surviving_probe_keeps_detection(monkeypatch, fresh_probe):
    monkeypatch.delenv("REELLY_FACE", raising=False)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, b"", b""))
    assert face.mediapipe_usable() is True


def test_face_env_switch(monkeypatch, fresh_probe):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no probe"))
    monkeypatch.setenv("REELLY_FACE", "off")
    assert face.mediapipe_usable() is False
    face.mediapipe_usable.cache_clear()
    monkeypatch.setenv("REELLY_FACE", "on")
    assert face.mediapipe_usable() is True


def test_raw_cache_key_needs_no_detector_without_a_facecam(monkeypatch, tmp_path):
    from reelly import finalize
    monkeypatch.setattr(face, "detector_kind",
                        lambda: pytest.fail("no detector without a facecam"))
    v = _write(tmp_path / "v.mp4", b"v")
    assert finalize._raw_cache_key({"segments": [[0, 1]]}, v, None, False)


# --- transcript noise ---------------------------------------------------------------------

def test_noise_words_are_dropped():
    result = {"segments": [{"words": [
        {"word": "!", "start": 0.0, "end": 0.98, "probability": 0.00023},
        {"word": " hello", "start": 1.0, "end": 1.4, "probability": 0.91},
        {"word": " uh", "start": 1.5, "end": 1.6, "probability": 0.01},
        {"word": " there", "start": 1.7, "end": 2.0},          # no probability: kept
        {"word": " nan", "start": 2.1, "end": 2.3, "probability": float("nan")},
    ]}]}
    assert [w["t"] for w in speech.words_from(result)] == ["hello", "there", "nan"]


# --- diarization message ---------------------------------------------------------------------

def test_retry_message_names_the_missing_extra(monkeypatch, tmp_path, capsys):
    art = tmp_path / "speaker_turns.json"
    art.write_text(json.dumps({"status": "unavailable"}))

    def no_pyannote():
        raise ImportError("No module named 'pyannote'")

    monkeypatch.setattr(diarize, "_import_pipeline", no_pyannote)
    monkeypatch.setattr(diarize, "token", lambda: None)
    assert diarize.needs_rerun(str(art)) is False
    out = capsys.readouterr().out
    assert "not installed" in out and "token" not in out


def test_the_suite_never_touches_the_real_home():
    """tests/conftest.py points HOME at a throwaway folder before import."""
    from reelly import config
    home = os.path.expanduser("~")
    assert "reelly-test-home-" in home
    assert config.USER_CFG.startswith(home)


# --- footage with no audio track ------------------------------------------------------------

def test_no_audio_gives_an_empty_transcript_not_a_failure(monkeypatch, tmp_path):
    from reelly import media, transcribe
    monkeypatch.setattr(media, "has_audio", lambda p: False)
    monkeypatch.setattr(media, "extract_wav",
                        lambda *a, **k: pytest.fail("nothing to extract"))
    out = tmp_path / "words.json"
    res = transcribe.transcribe("silent.mp4", str(out))
    assert res["segments"] == [] and res["no_audio"] is True
    assert json.load(open(out))["segments"] == []
    assert speech.words_from(str(out)) == []


def test_no_audio_is_all_silence(monkeypatch):
    from reelly import media
    monkeypatch.setattr(media, "has_audio", lambda p: False)
    monkeypatch.setattr(media, "duration", lambda p: 14.83)
    assert speech.get_silences("silent.mp4") == [(0.0, 14.83)]


def test_has_audio_reads_the_streams(monkeypatch):
    from reelly import media
    monkeypatch.setattr(media, "probe", lambda p: {"streams": [{"codec_type": "video"}]})
    assert media.has_audio("x.mp4") is False
    monkeypatch.setattr(media, "probe", lambda p: {"streams": [
        {"codec_type": "video"}, {"codec_type": "audio"}]})
    assert media.has_audio("x.mp4") is True
