"""Local speaker diarization: WHICH speaker is talking WHEN.

WHY PYANNOTE AND NOT WHISPERX (decision 2026-08-01, reviewer: local over
cloud). These are hour-long weekly sessions; a per-minute API charge recurs
forever, and Reelly is Mac-first ("cloud only for AI calls"). Between the two
local options, whisperx bundles faster-whisper plus its own alignment stack
and uses pyannote for the diarization step anyway -- we already have word
timings from mlx-whisper, so all of that would be dead weight. pyannote.audio
supplies exactly the missing piece: speaker turns, on-device, no per-run cost.

SETUP (one-time, documented in README):
  1. uv sync --extra diarize          (installs pyannote.audio)
  2. a free HuggingFace token in HUGGINGFACE_TOKEN / HF_TOKEN, or
     ~/.reelly/config.json {"huggingface_token": "hf_..."}
  3. accept the (free) model terms on the HuggingFace pages for
     pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0
The model downloads once into the HF cache; every run after that is offline.

DIVISION OF LABOUR (gap 8): this module answers WHEN each voice speaks and
writes analysis/speaker_turns.json. The declared clearance map
(analysis/voices.json) stays the source of truth for WHO is allowed -- a
human marks each diarized speaker id cleared or not, and `clearance` resolves
an uncleared id to its diarized ranges so cuts are filterable by speaker.

FAILURE IS LOUD, NEVER SILENT (the silent-skip class): a missing dependency,
token or model raises with the exact fix; `analyze` records the failure in
the artifact marked unverified and keeps the transcript-cue heuristic as the
only (clearly labelled) guess. It never pretends a session is single-speaker.
"""
import json
import os
import subprocess
import tempfile

from . import config, media

MODEL = "pyannote/speaker-diarization-3.1"

SETUP_HELP = (
    "Local diarization needs a one-time setup:\n"
    "  1. install the extra:  uv sync --extra diarize   "
    "(or: pip install 'reelly[diarize]')\n"
    "  2. put a free HuggingFace token in HUGGINGFACE_TOKEN or HF_TOKEN, or in "
    "~/.reelly/config.json as {\"huggingface_token\": \"hf_...\"}\n"
    f"  3. accept the free model terms on huggingface.co for {MODEL}, "
    "pyannote/segmentation-3.0, and pyannote/speaker-diarization-community-1\n"
    "The model downloads once; every run after that is offline and free.")


def token():
    """HuggingFace token from env or ~/.reelly/config.json, else None."""
    return config.get_key("huggingface")


def _import_pipeline():
    from pyannote.audio import Pipeline
    return Pipeline


def _pyannote_version():
    try:
        import pyannote.audio
        return getattr(pyannote.audio, "__version__", "unknown")
    except Exception:
        return "unknown"


def _pipeline():
    """The diarization pipeline, or a RuntimeError that says exactly what to fix."""
    try:
        Pipeline = _import_pipeline()
    except ImportError as e:
        raise RuntimeError(
            f"pyannote.audio is not installed ({e}).\n{SETUP_HELP}") from e
    tok = token()
    if not tok:
        raise RuntimeError(
            f"no HuggingFace token found for the {MODEL} download.\n{SETUP_HELP}")
    # pyannote.audio renamed the kwarg (use_auth_token -> token) in v4. Support
    # both so the loader is not pinned to one release line.
    try:
        pipe = Pipeline.from_pretrained(MODEL, token=tok)
    except TypeError:
        try:
            pipe = Pipeline.from_pretrained(MODEL, use_auth_token=tok)
        except TypeError as e:
            # A signature mismatch is OUR bug, not the operator's setup. Say so
            # instead of sending them back to the terms page for nothing.
            raise RuntimeError(
                f"pyannote.audio {_pyannote_version()} does not accept either "
                f"`token` or `use_auth_token` on Pipeline.from_pretrained "
                f"({e}). This is a reelly compatibility bug, not a setup "
                f"problem — the token and model terms are fine.") from e
        except Exception as e:
            raise RuntimeError(
                f"could not load {MODEL} ({e}). Most often the model terms have "
                f"not been accepted for this token.\n{SETUP_HELP}") from e
    except Exception as e:
        raise RuntimeError(
            f"could not load {MODEL} ({e}). Most often the model terms have "
            f"not been accepted for this token.\n{SETUP_HELP}") from e
    if pipe is None:
        raise RuntimeError(
            f"HuggingFace returned no pipeline for {MODEL}: the model terms "
            f"have not been accepted for this token.\n{SETUP_HELP}")
    return pipe


def _to_best_device(pipe):
    """Move the pipeline to the Apple GPU (MPS) when available; CPU otherwise.

    Returns the device name actually in use. ANY failure — no torch, no MPS,
    an op the MPS backend does not support at load time — falls back to CPU
    and says so; it never crashes analyze. REELLY_DIAR_DEVICE=cpu forces CPU.
    """
    if os.environ.get("REELLY_DIAR_DEVICE", "").strip().lower() == "cpu":
        print("[diar ] REELLY_DIAR_DEVICE=cpu: pipeline on CPU")
        return "cpu"
    try:
        import torch
        if not torch.backends.mps.is_available():
            print("[diar ] MPS not available: pipeline on CPU")
            return "cpu"
        pipe.to(torch.device("mps"))
        print("[diar ] pipeline on Apple GPU (MPS)")
        return "mps"
    except Exception as e:  # noqa: BLE001 — device selection must never crash
        print(f"[diar ] could not move pipeline to MPS ({e}): pipeline on CPU")
        try:
            import torch
            pipe.to(torch.device("cpu"))  # undo a half-applied move
        except Exception:  # noqa: BLE001
            pass
        return "cpu"


def _infer(pipe, wav, device):
    """Run the pipeline; an MPS runtime failure (unsupported op surfacing only
    at inference) retries once on CPU instead of killing the stage."""
    try:
        return pipe(wav)
    except Exception as e:  # noqa: BLE001 — MPS gaps surface as many types
        if device != "mps":
            raise
        print(f"[diar ] MPS inference failed ({e}); retrying on CPU")
        import torch
        pipe.to(torch.device("cpu"))
        return pipe(wav)


def _extract_audio(video, dst):
    """16 kHz mono wav, the input pyannote expects."""
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", video,
                    "-vn", "-ac", "1", "-ar", "16000", dst], check=True)


def _merge(ranges, gap=1.0):
    out = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def speaker_ranges(turns, gap=1.0):
    """{speaker: [[s, e], ...]} with close turns merged."""
    by = {}
    for t in turns:
        by.setdefault(t["speaker"], []).append([t["s"], t["e"]])
    return {k: _merge(v, gap) for k, v in by.items()}


def label_words(words, turns):
    """Copy of `words` with a "speaker" on each (midpoint containment; None
    when no diarized turn covers the word). This is the join between the
    mlx-whisper timeline and the diarizer, so plans filter by speaker."""
    out = []
    for w in words:
        mid = (w["s"] + w["e"]) / 2
        sp = next((t["speaker"] for t in turns if t["s"] <= mid <= t["e"]), None)
        out.append({**w, "speaker": sp})
    return out


def unavailable_artifact(reason):
    """The honest degrade: diarization did not run, and the artifact says so.
    Downstream treats voice identity as UNVERIFIED, never as single-speaker."""
    return {"engine": MODEL, "status": "unavailable", "error": str(reason),
            "unverified": True, "turns": [], "speakers": {}}


def needs_rerun(out_json):
    """An 'unavailable' artifact is not a cache hit: retry once setup exists.

    "Once setup exists" is checkable for the token case: with no HuggingFace
    token there is still nothing to run, so the retry would fail identically.
    Skip it (and say so) instead of re-attempting on every analyze run."""
    if not os.path.exists(out_json):
        return False
    try:
        art = json.load(open(out_json))
    except (ValueError, OSError):
        return False
    if art.get("status") == "ok":
        return False
    if art.get("status") == "unavailable" and token() is None:
        print("[diar ] diarization still unavailable (no HuggingFace token); "
              "skipping the retry -- add a token to re-enable")
        return False
    return True


def _annotation(result):
    """The pyannote Annotation, whatever the release wrapped it in.

    3.x returns an Annotation directly; 4.x returns a DiarizeOutput whose
    `.speaker_diarization` holds it. Unwrap rather than pin to one shape.
    """
    ann = getattr(result, "speaker_diarization", result)
    if not hasattr(ann, "itertracks"):
        raise RuntimeError(
            f"pyannote returned {type(result).__name__} with no usable "
            f"Annotation (looked at .speaker_diarization and the object "
            f"itself). This is a reelly compatibility bug against "
            f"pyannote.audio {_pyannote_version()}, not a setup problem.")
    return ann


def _turns_from(ann):
    turns = []
    for seg, _, label in ann.itertracks(yield_label=True):
        turns.append({"s": round(float(seg.start), 2),
                      "e": round(float(seg.end), 2), "speaker": str(label)})
    turns.sort(key=lambda t: t["s"])
    return turns


def _artifact_from(turns, scope=None):
    """The speaker_turns.json artifact shape (voices.json keys off these
    speaker ids and ranges), identical for full-session and window scope."""
    ranges = speaker_ranges(turns)
    art = {"engine": MODEL, "status": "ok", "turns": turns,
           "speakers": {sp: {"ranges": rs,
                             "talk_s": round(sum(e - s for s, e in rs), 1)}
                        for sp, rs in ranges.items()}}
    if scope:
        art["scope"] = scope
    return art


def _report(artifact):
    names = ", ".join(f"{sp} ({v['talk_s']:.0f}s)"
                      for sp, v in sorted(artifact["speakers"].items()))
    print(f"[diar ] {len(artifact['speakers'])} speaker(s): {names}")
    print("[diar ] record clearance per speaker id in analysis/voices.json "
          "(cleared true/false); uncleared ids block planning and QC")


def run(video, out_json):
    """Diarize a session and write analysis/speaker_turns.json."""
    pipe = _pipeline()
    device = _to_best_device(pipe)
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "audio.wav")
        _extract_audio(video, wav)
        print(f"[diar ] {MODEL} on {os.path.basename(video)} "
              f"(local, $0, {device}) ...")
        ann = _annotation(_infer(pipe, wav, device))
    artifact = _artifact_from(_turns_from(ann))
    json.dump(artifact, open(out_json, "w"), indent=1)
    _sync_voices(out_json, artifact)
    _report(artifact)
    return artifact


def _sync_voices(out_json, artifact):
    """Default-deny needs the clearance map to exist the moment speakers do:
    every diarized id gets a cleared:false entry in voices.json (existing
    human decisions survive). See clearance.sync_voices."""
    from . import clearance
    clearance.sync_voices(os.path.dirname(os.path.abspath(out_json)), artifact)


# --- window-scoped diarization (REELLY_DIAR_SCOPE=windows) -------------------
# When a cut plan only needs voice identity inside its own segments, diarizing
# the full hour is waste. run_windows diarizes ONLY the given ranges: the
# windows are concatenated into ONE wav and diarized in ONE pass, so speaker
# labels stay consistent across windows (per-window passes would relabel
# SPEAKER_00 arbitrarily and break the voices.json clearance join). The
# artifact is the exact speaker_turns.json schema, times in absolute session
# seconds, plus a "scope" block that says what was and was not covered.
# Wiring into the cut flow lives with the cut/plan owner; this is the API.

def _extract_windows_audio(video, windows, dst):
    """One 16 kHz mono wav holding ONLY the given (start, end) ranges,
    concatenated in order."""
    sel = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in windows)
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", video, "-vn",
                    "-af", f"aselect='{sel}',asetpts=N/SR/TB",
                    "-ac", "1", "-ar", "16000", dst], check=True)


def _norm_windows(windows):
    """Sorted, positive-length, overlap-merged (start, end) floats."""
    ws = sorted((float(s), float(e)) for s, e in windows if float(e) > float(s))
    out = []
    for s, e in ws:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _remap_turns(turns, windows):
    """Turns on the concatenated-windows timeline -> absolute session time.
    A turn spanning a window join is split (the join is a fiction; the two
    halves are not adjacent in the session)."""
    spans, off = [], 0.0
    for s, e in windows:
        spans.append((off, off + (e - s), s))
        off += e - s
    out = []
    for t in turns:
        for cs, ce, abs_s in spans:
            a, b = max(t["s"], cs), min(t["e"], ce)
            if b - a > 0.05:
                out.append({"s": round(abs_s + (a - cs), 2),
                            "e": round(abs_s + (b - cs), 2),
                            "speaker": t["speaker"]})
    out.sort(key=lambda t: t["s"])
    return out


def run_windows(video, windows, out_json=None):
    """Diarize ONLY the given [(start, end), ...] ranges of a session.

    Returns (and optionally writes) the speaker_turns.json-shaped artifact
    with turns in absolute session seconds and scope metadata recording the
    covered windows. Raises RuntimeError with the setup fix when the pipeline
    is unavailable, exactly like run()."""
    windows = _norm_windows(windows)
    if not windows:
        raise RuntimeError("run_windows needs at least one (start, end) window")
    pipe = _pipeline()
    device = _to_best_device(pipe)
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "audio.wav")
        _extract_windows_audio(video, windows, wav)
        covered = sum(e - s for s, e in windows)
        print(f"[diar ] {MODEL} on {os.path.basename(video)} "
              f"({len(windows)} window(s), {covered:.0f}s, local, $0, {device}) ...")
        ann = _annotation(_infer(pipe, wav, device))
    turns = _remap_turns(_turns_from(ann), windows)
    artifact = _artifact_from(turns, scope={
        "mode": "windows", "windows": [[round(s, 2), round(e, 2)] for s, e in windows],
        "note": "voice identity is only verified INSIDE these windows"})
    if out_json:
        json.dump(artifact, open(out_json, "w"), indent=1)
        _sync_voices(out_json, artifact)
    _report(artifact)
    return artifact
