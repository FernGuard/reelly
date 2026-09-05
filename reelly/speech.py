"""UNDERSTAND: silence map, filler words, clean DaVinci subtitles, talk stats.

Silence snapping and clean-SRT rules keep cuts in pauses and prevent orphaned cues.
"""
import json
import re

from . import config, media

FILLERS = {"um", "uh", "erm", "uhh", "umm", "hmm", "mmm", "mm", "er", "ah"}

# Below this measured gain we leave the audio untouched, so a recording already
# at (or above) Reelly's delivery level runs silence detection byte-for-byte the
# way it does today -- the gain stage is idempotent for correctly-leveled audio.
_LEVEL_HEADROOM_DB = 1.0


def _target_lufs():
    """Reelly's delivery loudness (the judge window centre) -- the level the
    voice is *supposed* to sit at. Silence detection is calibrated against this
    so the fixed -32 dB floor means "quiet relative to a shipped voice", not
    "quiet relative to whatever this raw recording happened to be captured at".
    Imported lazily so speech has no import-time dependency on judge."""
    from . import judge
    return (judge.LUFS_LO + judge.LUFS_HI) / 2.0


def _integrated_lufs(video):
    """Source integrated loudness in LUFS (EBU R128), or None if unreadable.
    Measures the raw recording only -- it never writes loudness.json, so the
    TRUE source loudness the finalize/ASSEMBLE delta relies on is untouched."""
    r = media.sh(config.FFMPEG, "-hide_banner", "-nostats", "-i", video,
                 "-af", "ebur128", "-f", "null", "-")
    vals = re.findall(r"^\s+I:\s+(-?\d+\.?\d*)\s+LUFS", r.stderr, re.M)
    return float(vals[-1]) if vals else None


def _detect_gain_db(video):
    """Flat dB gain that lifts this recording to the level Reelly requires
    BEFORE the silence floor is applied -- the reviewer's fix for quiet narration
    being discarded as dead air.

    Flat gain (not loudnorm compression) shifts the whole signal, so the
    pause/speech *contrast* the -32 dB floor keys on is preserved exactly: the
    floor stays a pause detector, it just now sits between the (shifted) room
    tone and the (shifted) voice instead of chopping into the voice itself.

    Returns 0.0 for audio already at/above target (idempotent: no change to
    existing correctly-leveled projects) and for un-measurable audio."""
    i = _integrated_lufs(video)
    if i is None:
        return 0.0
    gain = _target_lufs() - i
    return gain if gain > _LEVEL_HEADROOM_DB else 0.0


def get_silences(video, noise="-32dB", min_d=0.12):
    gain = _detect_gain_db(video)
    af = f"silencedetect=noise={noise}:d={min_d}"
    if gain:
        # gain the voice up to Reelly's level first, THEN run the existing floor
        af = f"volume={gain:.2f}dB,{af}"
    r = media.sh(config.FFMPEG, "-i", video, "-af", af, "-f", "null", "-")
    sil, cur = [], None
    for line in r.stderr.splitlines():
        if "silence_start:" in line:
            try:
                cur = float(line.split("silence_start:")[1].strip())
            except ValueError:
                cur = None
        elif "silence_end:" in line and cur is not None:
            try:
                sil.append((cur, float(line.split("silence_end:")[1].split("|")[0].strip())))
            except ValueError:
                pass
            cur = None
    return sil


def words_from(result):
    """Flatten a whisper result (dict or path) into [{t, s, e}],
    brand-vocabulary corrected (BDGO -> OldBrand and friends)."""
    from . import vocab
    if isinstance(result, str):
        result = json.load(open(result))
    out = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            t = str(w.get("word", w.get("text", ""))).strip()
            if t and w.get("start") is not None and w.get("end") is not None:
                out.append({"t": t, "s": float(w["start"]), "e": float(w["end"])})
    return vocab.correct_words(out)


def find_fillers(words):
    """Filler words and immediate stutters ("the the"), each with timestamps."""
    out, prev = [], None
    for w in words:
        t = w["t"].strip(".,!?").lower()
        if t in FILLERS:
            out.append({**w, "kind": "filler"})
        elif prev is not None and t == prev and len(t) > 1:
            out.append({**w, "kind": "stutter"})
        prev = t
    return out


def _srt_ts(s):
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    return f"{h:02d}:{m:02d}:{s % 60:06.3f}".replace(".", ",")


def group_cues(words, max_words=9, max_chars=45, max_dur=6.5, max_gap=1.0, min_words=4):
    """Group words into short readable cues: sentence-end breaks, no orphans.

    Returns [(start, end, text)]. Shared by the SRT writer and the preview
    caption burner so both always agree.
    """
    cues, cur = [], []

    def flush():
        if cur:
            cues.append((cur[0]["s"], cur[-1]["e"], " ".join(x["t"] for x in cur).strip()))

    for w in words:
        if cur:
            dur = w["e"] - cur[0]["s"]
            gap = w["s"] - cur[-1]["e"]
            chars = len(" ".join(x["t"] for x in cur)) + 1 + len(w["t"])
            over = len(cur) >= max_words or chars > max_chars or dur > max_dur or gap > max_gap
            if over and (len(cur) >= min_words or gap > max_gap):
                flush()
                cur = []
        cur.append(w)
        if w["t"][-1:] in ".?!":
            flush()
            cur = []
    flush()
    return cues


def group_cue_words(words, max_words=9, max_chars=45, max_dur=6.5,
                    max_gap=1.0, min_words=4):
    """group_cues with the word objects kept: [(start, end, [word, ...])].
    Same grouping logic verbatim so captions and SRT can never disagree
    (a pointer-sync version of this dropped trailing words; verdict bug)."""
    cues, cur = [], []

    def flush():
        if cur:
            cues.append((cur[0]["s"], cur[-1]["e"], list(cur)))

    for w in words:
        if cur:
            dur = w["e"] - cur[0]["s"]
            gap = w["s"] - cur[-1]["e"]
            chars = len(" ".join(x["t"] for x in cur)) + 1 + len(w["t"])
            over = len(cur) >= max_words or chars > max_chars or dur > max_dur or gap > max_gap
            if over and (len(cur) >= min_words or gap > max_gap):
                flush()
                cur = []
        cur.append(w)
        if w["t"][-1:] in ".?!":
            flush()
            cur = []
    flush()
    return cues


def clean_srt(words, path, **kw):
    """Clean DaVinci-ready SRT from word-level data."""
    cues = group_cues(words, **kw)
    out = []
    for i, (s, e, t) in enumerate(cues, 1):
        out += [str(i), f"{_srt_ts(s)} --> {_srt_ts(max(e, s + 0.4))}", t, ""]
    open(path, "w").write("\n".join(out))
    return len(cues)


def _pick_silence(cands):
    """Silence-safety ladder: >=400ms is a clean cut home, 150-400ms is a
    fallback, <150ms is noise between words and never worth snapping into."""
    for tier in (0.40, 0.15):
        hits = [c for c in cands if c[1] >= tier]
        if hits:
            return min(hits, key=lambda c: c[0])
    return None


def snap_start(s, sil, window=0.7, back=0.10, drift_pad=0.08):
    """Move a cut start into the pause before the first word (seed-proven).
    With no usable pause, pad outward: ASR word timestamps drift 50-100ms,
    and a cut placed exactly on the reported onset clips the first phoneme."""
    cands = [(abs(se - s), se - ss, se) for ss, se in sil if abs(se - s) < window]
    best = _pick_silence(cands)
    return max(0.0, best[2] - back) if best else max(0.0, s - drift_pad)


def snap_end(e, sil, window=0.7, fwd=0.14, drift_pad=0.12):
    """Move a cut end into the pause after the last word (seed-proven).
    Same drift rule as snap_start on the way out."""
    cands = [(abs(ss - e), se - ss, ss) for ss, se in sil if abs(ss - e) < window]
    best = _pick_silence(cands)
    return (best[2] + fwd) if best else e + drift_pad


# Fast sustained speech tops out around here (words/sec). Used only to derive a
# conservative *lower* bound on how much of the video MUST be voice given the
# transcript, so the sanity gate below false-alarms on nothing real.
_MAX_WORDS_PER_S = 5.0


def _assert_talk_ratio_plausible(total, words, talk_ratio):
    """Fail loudly on the silent-failure that shipped a 6.9s file from a 98s
    demo: a transcript full of words but a silence map that says the video is
    ~all dead air. `analyze` printed `talk ratio 0.046 | 148.2 wpm` -- mutually
    contradictory -- and nothing flagged it, so every stage exited 0.

    The bound is self-calibrating: N words occupy at least N / 5 wps seconds of
    voice, so talk_ratio can't honestly fall far below that fraction. We only
    trip at half of it, so genuinely sparse recordings pass; only a floor that
    is eating the narration itself trips."""
    if not (words and total):
        return
    min_voiced_ratio = min(1.0, (words / _MAX_WORDS_PER_S) / total)
    if talk_ratio < 0.5 * min_voiced_ratio:
        raise RuntimeError(
            f"silence map contradicts the transcript: talk_ratio={talk_ratio} "
            f"but {words} words over {total:.0f}s imply at least "
            f"~{min_voiced_ratio:.2f} voiced. The silence floor is reading the "
            f"narration itself as dead air (quiet recording, or the level/gain "
            f"stage failed to measure). Refusing to emit a speech map that "
            f"would edit the video away to near-nothing.")


def speech_map(video, words):
    """Everything DIRECT needs to cut speech well, in one artifact."""
    total = media.duration(video)
    sil = get_silences(video)
    fil = find_fillers(words)
    silent = sum(e - s for s, e in sil)
    minutes = (total or 1) / 60
    talk_ratio = round(1 - silent / total, 3) if total else 0
    _assert_talk_ratio_plausible(total, len(words), talk_ratio)
    return {
        "duration_s": round(total, 2),
        "silences": [(round(s, 3), round(e, 3)) for s, e in sil],
        "silence_total_s": round(silent, 1),
        "talk_ratio": talk_ratio,
        "fillers": fil,
        "filler_count": len(fil),
        "words": len(words),
        "wpm": round(len(words) / minutes, 1),
    }
