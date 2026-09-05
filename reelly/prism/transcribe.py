"""TRANSCRIBE: mlx-whisper off the artifact wav. Apple-Silicon-only and heavy,
so it's imported lazily and skipped (not errored) when unavailable — PRISM
must still produce a report without it.
"""
import json
import os

from .. import config

UNAVAILABLE = "transcript unavailable"


def transcribe(wav, out_json=None):
    """Returns the mlx-whisper result dict, or None if mlx_whisper isn't installed."""
    try:
        import mlx_whisper
    except ImportError:
        print("[skip] transcribe (mlx_whisper not installed)")
        return None

    res = mlx_whisper.transcribe(wav, path_or_hf_repo=config.WHISPER_MODEL, word_timestamps=True)
    if out_json:
        json.dump(res, open(out_json, "w"))
    return res


def text_of(result):
    if not result:
        return UNAVAILABLE
    return (result.get("text") or "").strip() or UNAVAILABLE


def load_cached(out_json):
    if out_json and os.path.exists(out_json):
        return json.load(open(out_json))
    return None
