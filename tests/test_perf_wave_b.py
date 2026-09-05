"""Perf wave B: parallel analyze DAG, MPS diarizer, parakeet ASR option,
hardware decode, ledger price fix, window-scoped diarization."""
import json
import os
import sys
import threading
import time
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reelly import analyze, config, diarize, judge, scenes, transcribe, visual  # noqa: E402


# --- 1. parallel analyze DAG -------------------------------------------------

def _fake_words():
    return {"segments": [{"words": [
        {"word": " hello", "start": 0.0, "end": 0.4},
        {"word": " world.", "start": 0.5, "end": 0.9}]}]}


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """analyze.run with every heavy stage stubbed; records (event, t) pairs."""
    events, lock = [], threading.Lock()

    def mark(name, dur=0.0):
        t0 = time.monotonic()
        if dur:
            time.sleep(dur)
        with lock:
            events.append((name, t0, time.monotonic()))

    video = tmp_path / "session.mp4"
    video.write_bytes(b"fake")

    monkeypatch.setattr(analyze.media, "probe", lambda f: {"streams": []})

    def fake_transcribe(v, out, model=None):
        mark("transcribe", 0.15)
        json.dump(_fake_words(), open(out, "w"))
    monkeypatch.setattr(analyze.transcribe, "transcribe", fake_transcribe)
    monkeypatch.setattr(analyze.speech, "clean_srt",
                        lambda words, p: (mark("srt"), open(p, "w").write("1\n")))
    monkeypatch.setattr(analyze.speech, "speech_map",
                        lambda v, words: (mark("speech_map"), {"duration_s": 1})[1])
    monkeypatch.setattr(analyze.scenes, "scene_cuts",
                        lambda v: (mark("scenes"), [])[1])
    monkeypatch.setattr(analyze.audio_mod, "loudness",
                        lambda v: (mark("loudness"), {})[1])
    monkeypatch.setattr(analyze.topics, "topic_clips",
                        lambda s: (mark("topics"), [])[1])
    monkeypatch.setattr("reelly.clearance.write_guest_blocks",
                        lambda root, sents: (mark("guests"), json.dump(
                            [], open(os.path.join(root, "analysis", "guest_blocks.json"), "w")))[0])

    def fake_diarize(v, out):
        mark("diarize")
        art = {"engine": "fake", "status": "ok", "turns": [], "speakers": {}}
        json.dump(art, open(out, "w"))
        return art
    monkeypatch.setattr("reelly.diarize.run", fake_diarize)

    def fake_visual(v, oj, om, crop=None, project=""):
        mark("visual", 0.15)
        json.dump({"complete": True, "missing": [], "sequences": []}, open(oj, "w"))
        open(om, "w").write("# vr\n")
    monkeypatch.setattr("reelly.visual.review", fake_visual)
    return str(video), str(tmp_path / "projects"), events


ARTIFACTS = ["probe.json", "words.json", "session.srt", "speech_map.json",
             "scenes.json", "loudness.json", "topics.json",
             "guest_blocks.json", "speaker_turns.json", "visual_review.json",
             "ANALYSIS.md"]


def test_analyze_dag_produces_the_serial_artifact_set(wired):
    video, out_root, events = wired
    root = analyze.run(video, out_root=out_root)
    an = os.path.join(root, "analysis")
    for f in ARTIFACTS:
        assert os.path.exists(os.path.join(an, f)), f"missing {f}"


def test_analyze_dag_respects_word_dependencies_but_runs_chains_concurrently(wired):
    video, out_root, events = wired
    analyze.run(video, out_root=out_root)
    ev = {name: (s, e) for name, s, e in events}
    # chain A order: transcribe before every word-consumer
    for dep in ("srt", "speech_map", "topics", "guests"):
        assert ev[dep][0] >= ev["transcribe"][1], f"{dep} ran before transcribe finished"
    # independent chains overlap the (slow) transcribe stage instead of
    # queueing behind it — the whole point of the DAG
    overlapped = [n for n in ("scenes", "loudness", "diarize", "visual")
                  if ev[n][0] < ev["transcribe"][1]]
    assert overlapped, "no independent chain started while transcribe was running"


def test_analyze_dag_skip_visual_and_cache_semantics(wired):
    video, out_root, events = wired
    root = analyze.run(video, out_root=out_root, skip_visual=True)
    assert not os.path.exists(os.path.join(root, "analysis", "visual_review.json"))
    assert "visual" not in {n for n, _, _ in events}
    # second run: everything cached, no stage function re-fires
    n_before = len(events)
    analyze.run(video, out_root=out_root, skip_visual=True)
    assert len(events) == n_before, "cached stages re-ran"
    # --force re-fires them
    analyze.run(video, out_root=out_root, skip_visual=True, force=True)
    assert len(events) > n_before


def test_analyze_dag_chain_failure_is_raised_after_all_chains_finish(wired, monkeypatch):
    video, out_root, events = wired

    def boom(v):
        raise RuntimeError("scene detector exploded")
    monkeypatch.setattr(analyze.scenes, "scene_cuts", boom)
    with pytest.raises(RuntimeError, match="scenes.*exploded"):
        analyze.run(video, out_root=out_root, skip_visual=True)
    # the other chains still completed and left their artifacts
    an = os.path.join(out_root, "session", "analysis")
    for f in ("words.json", "loudness.json", "speaker_turns.json", "ANALYSIS.md"):
        assert os.path.exists(os.path.join(an, f))


# --- 2. diarizer device selection ---------------------------------------------

class _Pipe:
    def __init__(self, fail_on=()):
        self.devices, self.fail_on = [], fail_on

    def to(self, device):
        d = str(device)
        if d in self.fail_on:
            raise RuntimeError(f"op not implemented for {d}")
        self.devices.append(d)


def _fake_torch(mps_ok):
    t = types.SimpleNamespace()
    t.backends = types.SimpleNamespace(
        mps=types.SimpleNamespace(is_available=lambda: mps_ok))
    t.device = lambda d: d
    return t


def test_diarize_moves_pipeline_to_mps_when_available(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True))
    p = _Pipe()
    assert diarize._to_best_device(p) == "mps"
    assert p.devices == ["mps"]


def test_diarize_falls_back_to_cpu_when_mps_missing_or_broken(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(False))
    assert diarize._to_best_device(_Pipe()) == "cpu"
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True))
    p = _Pipe(fail_on=("mps",))
    assert diarize._to_best_device(p) == "cpu"  # .to(mps) raised -> CPU
    monkeypatch.setenv("REELLY_DIAR_DEVICE", "cpu")
    p2 = _Pipe()
    assert diarize._to_best_device(p2) == "cpu" and p2.devices == []


def test_diarize_mps_inference_failure_retries_on_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(True))
    class _FlakyPipe(_Pipe):
        calls = 0

        def __call__(self, wav):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("aten::stft not implemented for MPS")
            return "annotation"

    p = _FlakyPipe()
    assert diarize._infer(p, "a.wav", "mps") == "annotation"
    assert "cpu" in p.devices and p.calls == 2
    with pytest.raises(RuntimeError):  # CPU failures still raise
        diarize._infer(_FlakyPipe(), "a.wav", "cpu")


# --- 3. parakeet transcription option ----------------------------------------

class _Tok:
    def __init__(self, text, start, end):
        self.text, self.start, self.end = text, start, end
        self.duration = end - start


class _Sent:
    def __init__(self, text, tokens):
        self.text, self.tokens = text, tokens
        self.start, self.end = tokens[0].start, tokens[-1].end


class _Aligned:
    text = "hello world"
    sentences = [_Sent("hello world", [
        _Tok(" hel", 0.0, 0.2), _Tok("lo", 0.2, 0.4), _Tok(" world", 0.5, 0.9)])]


def _install_fake_parakeet(monkeypatch):
    mod = types.SimpleNamespace(
        from_pretrained=lambda m: types.SimpleNamespace(
            transcribe=lambda wav: _Aligned()))
    monkeypatch.setitem(sys.modules, "parakeet_mlx", mod)


def test_parakeet_maps_tokens_onto_the_whisper_words_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("REELLY_ASR", "parakeet")
    _install_fake_parakeet(monkeypatch)
    monkeypatch.setattr(transcribe.media, "extract_wav", lambda v, w, rate=16000: None)
    out = str(tmp_path / "words.json")
    res = transcribe.transcribe("session.mp4", out)
    assert res["segments"][0]["words"] == [
        {"word": " hello", "start": 0.0, "end": 0.4},
        {"word": " world", "start": 0.5, "end": 0.9}]
    # the on-disk artifact feeds the exact same consumer whisper's does
    from reelly import speech
    words = speech.words_from(out)
    assert [w["t"] for w in words] == ["hello", "world"]
    assert words[0]["s"] == 0.0 and words[1]["e"] == 0.9


def test_parakeet_unavailable_falls_back_to_whisper(monkeypatch, tmp_path):
    monkeypatch.setenv("REELLY_ASR", "parakeet")
    monkeypatch.setattr(transcribe, "_parakeet_available", lambda: False)
    monkeypatch.setattr(transcribe.media, "extract_wav", lambda v, w, rate=16000: None)
    called = {}
    fake_whisper = types.SimpleNamespace(transcribe=lambda wav, **kw: (
        called.update(kw), {"segments": []})[1])
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_whisper)
    out = str(tmp_path / "words.json")
    res = transcribe.transcribe("session.mp4", out)
    assert res == {"segments": []} and called["word_timestamps"] is True


def test_default_engine_is_whisper(monkeypatch):
    monkeypatch.delenv("REELLY_ASR", raising=False)
    monkeypatch.setattr(transcribe.config, "HOME", "/nonexistent-reelly-home")
    assert transcribe.engine() == "whisper"


def test_parakeet_refused_for_non_english_model(monkeypatch, tmp_path):
    monkeypatch.setenv("REELLY_ASR", "parakeet")
    _install_fake_parakeet(monkeypatch)
    monkeypatch.setattr(transcribe.media, "extract_wav", lambda v, w, rate=16000: None)
    fake_whisper = types.SimpleNamespace(transcribe=lambda wav, **kw: {"segments": []})
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_whisper)
    res = transcribe.transcribe("s.mp4", str(tmp_path / "w.json"),
                                model="mlx-community/whisper-large-v3")
    assert "engine" not in res  # whisper path, not parakeet


# --- 4. hardware decode --------------------------------------------------------

def test_hwdecode_args_env_gate(monkeypatch):
    monkeypatch.delenv("REELLY_NO_HWDECODE", raising=False)
    assert config.hwdecode_args() == ["-hwaccel", "videotoolbox"]
    monkeypatch.setenv("REELLY_NO_HWDECODE", "1")
    assert config.hwdecode_args() == []


def _hw_before_input(args):
    args = list(args)
    return "-hwaccel" in args and args.index("-hwaccel") < args.index("-i")


def test_scene_cuts_decode_uses_videotoolbox(monkeypatch):
    monkeypatch.delenv("REELLY_NO_HWDECODE", raising=False)
    seen = {}

    def fake_sh(*args):
        seen["args"] = args
        return types.SimpleNamespace(stderr="")
    monkeypatch.setattr(scenes.media, "sh", fake_sh)
    scenes.scene_cuts("real.mp4")
    assert _hw_before_input(seen["args"])
    monkeypatch.setenv("REELLY_NO_HWDECODE", "1")
    scenes.scene_cuts("real.mp4")
    assert "-hwaccel" not in seen["args"]


def test_judge_analysis_decode_uses_videotoolbox_for_video_only(monkeypatch):
    monkeypatch.delenv("REELLY_NO_HWDECODE", raising=False)
    seen = []
    monkeypatch.setattr(judge, "_ffmpeg_stderr", lambda args: (seen.append(args), "")[1])
    judge._analysis_stderr("f.mp4", True, True)
    judge._analysis_stderr("f.mp4", True, False)
    judge._analysis_stderr("f.wav", False, True)
    assert _hw_before_input(seen[0]) and _hw_before_input(seen[1])
    assert "-hwaccel" not in seen[2]  # audio-only: nothing to accelerate


def test_visual_proxy_compress_uses_videotoolbox(monkeypatch):
    monkeypatch.delenv("REELLY_NO_HWDECODE", raising=False)
    seen = {}
    monkeypatch.setattr(visual.subprocess, "run",
                        lambda args, **kw: seen.update(args=args))
    visual._compress_segment("src.mp4", 0, 600, "dst.mp4")
    assert _hw_before_input(seen["args"])


def test_preview_cut_segments_uses_videotoolbox_on_source_not_concat(monkeypatch, tmp_path):
    from reelly import preview
    monkeypatch.delenv("REELLY_NO_HWDECODE", raising=False)
    calls = []
    monkeypatch.setattr(preview.subprocess, "run",
                        lambda args, **kw: calls.append(list(args)))
    monkeypatch.setattr("reelly.media.sdr_chain", lambda v: "")
    dst = str(tmp_path / "out.mp4")
    preview._cut_segments("real.mp4", [(0.0, 1.0)], dst)
    seg, concat = calls[0], calls[-1]
    assert _hw_before_input(seg)
    assert seg.index("-hwaccel") < seg.index("-i")
    assert "-hwaccel" not in concat  # concat of intermediates: no accel


# --- 5. ledger price fix --------------------------------------------------------

def test_gemini_35_flash_ledger_prices():
    assert visual.PRICE_IN_PER_M == 1.50 and visual.PRICE_OUT_PER_M == 9.00
    # a 10-minute chunk: 180k video tokens in + 2k estimated out
    expect = 180000 / 1e6 * 1.50 + 2000 / 1e6 * 9.00
    assert visual.chunk_cost(600) == pytest.approx(expect)


# --- 6. window-only diarization ---------------------------------------------

class _Seg:
    def __init__(self, s, e):
        self.start, self.end = s, e


class _WinAnnotation:
    """Turns on the CONCATENATED-windows timeline: windows (100,110)+(200,205)
    concat to 15s; the second turn spans the join at t=10."""
    def itertracks(self, yield_label=True):
        yield _Seg(0.0, 4.0), None, "SPEAKER_00"
        yield _Seg(8.0, 12.0), None, "SPEAKER_01"


def test_run_windows_remaps_to_absolute_time_and_splits_at_joins(monkeypatch, tmp_path):
    monkeypatch.setattr(diarize, "_pipeline", lambda: (lambda wav: _WinAnnotation()))
    monkeypatch.setattr(diarize, "_to_best_device", lambda p: "cpu")
    monkeypatch.setattr(diarize, "_extract_windows_audio", lambda v, w, d: None)
    out = str(tmp_path / "speaker_turns.json")
    art = diarize.run_windows("session.mp4", [(100.0, 110.0), (200.0, 205.0)], out)
    assert art["status"] == "ok" and art["engine"] == diarize.MODEL
    assert art["turns"] == [
        {"s": 100.0, "e": 104.0, "speaker": "SPEAKER_00"},
        {"s": 108.0, "e": 110.0, "speaker": "SPEAKER_01"},
        {"s": 200.0, "e": 202.0, "speaker": "SPEAKER_01"}]
    assert art["scope"]["mode"] == "windows"
    assert art["scope"]["windows"] == [[100.0, 110.0], [200.0, 205.0]]
    # same schema as run(): clearance can key voices.json off these ranges
    assert art["speakers"]["SPEAKER_01"]["ranges"] == [[108.0, 110.0], [200.0, 202.0]]
    on_disk = json.load(open(out))
    assert on_disk["turns"] == art["turns"]
    assert not diarize.needs_rerun(out)


def test_run_windows_normalizes_and_requires_windows():
    assert diarize._norm_windows([(5, 3), (10, 20), (15, 25), (0, 2)]) == \
        [(0.0, 2.0), (10.0, 25.0)]
    with pytest.raises(RuntimeError, match="window"):
        diarize.run_windows("s.mp4", [])
