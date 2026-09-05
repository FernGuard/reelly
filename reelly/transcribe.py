"""UNDERSTAND: word-level transcription.

Default engine is mlx-whisper (config.WHISPER_MODEL) — the proven path.
REELLY_ASR=parakeet (env, or "asr" in ~/.reelly/config.json) opts into
parakeet-mlx, a much faster English-only ASR on Apple Silicon, installed via
`uv sync --extra asr-fast`. The parakeet result is mapped onto the EXACT
words.json schema mlx-whisper writes (segments[].words[] with "word"/"start"/
"end", seconds, leading-space word text), so every consumer of words.json —
speech.words_from and everything behind it — is engine-blind. If parakeet is
requested but unavailable (not installed, or a non-English model is asked
for), we say so and fall back to whisper; default stays whisper until an A/B
on real footage says otherwise.
"""
import json
import os

from . import config, media

PARAKEET_MODEL = "mlx-community/parakeet-tdt-0.6b-v2"  # English-only


def engine():
    """Requested ASR engine: REELLY_ASR env, then ~/.reelly/config.json "asr",
    else "whisper"."""
    e = os.environ.get("REELLY_ASR", "").strip().lower()
    if e:
        return e
    cfg = os.path.join(config.HOME, "config.json")
    if os.path.exists(cfg):
        try:
            e = str(json.load(open(cfg)).get("asr", "")).strip().lower()
            if e:
                return e
        except (ValueError, OSError):
            pass
    return "whisper"


def _parakeet_available():
    try:
        import parakeet_mlx  # noqa: F401 — probing the optional extra
        return True
    except ImportError:
        return False


def _english_job(model):
    """Parakeet tdt-0.6b-v2 is English-only. The whisper model in play is the
    language declaration we have: .en models mean an English session."""
    return ".en" in (model or config.WHISPER_MODEL)


def _merge_tokens(tokens):
    """Parakeet emits sentencepiece tokens; a leading space starts a new word.
    Merge into whisper-shaped word dicts ("word" keeps whisper's leading
    space, "start"/"end" in seconds)."""
    words = []
    for tk in tokens:
        text = str(getattr(tk, "text", ""))
        if not text.strip():
            continue
        start = float(getattr(tk, "start", 0.0))
        end = float(getattr(tk, "end",
                            start + float(getattr(tk, "duration", 0.0))))
        if not words or text.startswith(" ") or text.startswith("▁"):
            words.append({"word": " " + text.strip(), "start": start, "end": end})
        else:  # continuation piece of the current word
            words[-1]["word"] += text.strip()
            words[-1]["end"] = end
    return words


def _parakeet_transcribe(wav, out_json):
    from parakeet_mlx import from_pretrained
    print(f"[asr  ] parakeet-mlx ({PARAKEET_MODEL})")
    model = from_pretrained(PARAKEET_MODEL)
    aligned = model.transcribe(wav)
    segments = []
    for i, sent in enumerate(getattr(aligned, "sentences", None) or []):
        words = _merge_tokens(getattr(sent, "tokens", None) or [])
        if not words:
            continue
        segments.append({"id": i, "text": str(getattr(sent, "text", "")).strip(),
                         "start": float(getattr(sent, "start", words[0]["start"])),
                         "end": float(getattr(sent, "end", words[-1]["end"])),
                         "words": words})
    res = {"text": str(getattr(aligned, "text", "")),
           "language": "en",
           "engine": f"parakeet-mlx:{PARAKEET_MODEL}",
           "segments": segments}
    json.dump(res, open(out_json, "w"))
    return res


def transcribe(video, out_json, model=None):
    wav = out_json + ".tmp.wav"
    media.extract_wav(video, wav)
    try:
        if engine() == "parakeet":
            if not _english_job(model):
                print("[asr  ] REELLY_ASR=parakeet but the requested model is "
                      "not English-only; using mlx-whisper")
            elif not _parakeet_available():
                print("[asr  ] REELLY_ASR=parakeet but parakeet-mlx is not "
                      "installed (uv sync --extra asr-fast); using mlx-whisper")
            else:
                return _parakeet_transcribe(wav, out_json)
        import mlx_whisper  # heavy import, keep it lazy
        from . import vocab
        res = mlx_whisper.transcribe(
            wav, path_or_hf_repo=model or config.WHISPER_MODEL,
            word_timestamps=True, initial_prompt=vocab.bias_prompt())
        json.dump(res, open(out_json, "w"))
        return res
    finally:
        if os.path.exists(wav):
            os.remove(wav)
