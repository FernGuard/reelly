"""UNDERSTAND: Gemini visual review in chunks (ported from the proven seed).

Every call is budget-checked and logged to the cost ledger.

THE SILENT-SKIP LESSON
A failed chunk upload used to print one line and `continue`; the run exited 0
and the artifact looked complete while missing ten minutes of footage. Three
rules now hold, as a class, not as patches:

  1. A failed chunk is retried with backoff; when it still fails, the missing
     range is recorded IN the artifact and the stage exits non-zero. A partial
     bundle can never look like a complete one.
  2. Results are cached PER CHUNK, keyed on (video, range, model), so repairing
     one chunk re-bills one chunk and every other chunk keeps its original
     analysis. Gemini is nondeterministic even at temperature 0.3: a full
     reroll returns different moments, and cut plans are keyed to the roll
     they were made from.
  3. The ledger is reconciled at stage end: what was charged is compared to
     what the budget check priced, and the difference is stated with the
     reason (cached vs failed). Cheaper than expected is never silent.
"""
import json
import os
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, ledger, media, timing

# chunk workers run concurrently; the ledger is a read-modify-write JSON file
_LEDGER_LOCK = threading.Lock()

PROMPT = """This is a {mins}-minute SEGMENT of a longer screen recording of someone building content in a creative tool.
Find the strongest cuttable sequences in THIS segment for two goals:
(a) a GAME/PLATFORM TRAILER (the most visually impressive results, reveals, and satisfying on-screen moments), and
(b) SHORT-FORM vertical clips for TikTok/Reels/Shorts (a strong hook, a satisfying or surprising on-screen beat).
Focus on what is happening ON SCREEN (generated art, reveals, results, the build), NOT the small webcam face.
Be selective. Skip menus, typing, waiting, dead air, and repetition.

Return ONLY a JSON array. Each object:
- "label": short name
- "start": "MM:SS"   (relative to THIS segment; 00:00 is the segment start; max {mins}:00)
- "end": "MM:SS"
- "trailer_score": 1-10 integer
- "short_score": 1-10 integer
- "what_happens": one line (what is on screen)
- "why": one line
- "strong_hook": true/false
If nothing in this segment is worth cutting, return []."""

# gemini-3.5-flash economics for the ledger (estimates, refreshed as pricing
# moves). Source: https://ai.google.dev/gemini-api/docs/pricing (checked
# 2026-08-02): $1.50 per 1M input tokens, $9.00 per 1M output tokens. The old
# 0.30/2.50 constants were gemini-2.5-flash-era and under-counted spend ~5x.
VIDEO_TOKENS_PER_S = 300
PRICE_IN_PER_M = 1.50
PRICE_OUT_PER_M = 9.00
EST_OUT_TOKENS = 2000

# retry ladder for a failed chunk (upload or generate); backoff is the wait
# BEFORE each retry, so 3 attempts total per chunk
RETRIES = 3
BACKOFF_S = (5, 20)
# a Files-API upload that sits in PROCESSING past this is treated as failed
# (the poll used to be unbounded: one wedged upload hung the whole stage
# forever). Raising here hands the chunk to the retry ladder instead.
UPLOAD_DEADLINE_S = 300
_sleep = time.sleep  # injectable for tests


def _client():
    from google import genai
    return genai.Client(api_key=config.provider_key("google-genai"))


def _local_sec(t, cap):
    nums = [int(x) for x in str(t).replace(";", ":").split(":") if x.strip().isdigit()]
    v = nums[0] * 60 + nums[1] if len(nums) >= 2 else (nums[0] if nums else 0)
    return max(0, min(v, cap))


def _compress_segment(src, start, length, dst, crop=None):
    vf = "scale=-2:600,fps=5"
    if crop:
        vf = f"crop={crop},{vf}"
    subprocess.run([config.FFMPEG, "-y", "-v", "error", *config.hwdecode_args(),
                    "-ss", str(start), "-i", src,
                    "-t", str(length), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "30", "-c:a", "aac", "-b:a", "48k",
                    "-movflags", "+faststart", dst], check=True)


def chunk_cost(chunk_s):
    tokens_in = chunk_s * VIDEO_TOKENS_PER_S
    return tokens_in / 1e6 * PRICE_IN_PER_M + EST_OUT_TOKENS / 1e6 * PRICE_OUT_PER_M


def sequences(artifact):
    """Sequences from either artifact shape.

    The artifact grew from a bare list to a dict carrying coverage metadata
    (missing ranges, model). Consumers go through this so old bundles keep
    working and nobody has to remember which shape a project has on disk.
    """
    if artifact is None:
        return []
    if isinstance(artifact, dict):
        return artifact.get("sequences", [])
    return artifact


def missing_ranges(artifact):
    """Coverage holes recorded in the artifact (empty for legacy list shape)."""
    if isinstance(artifact, dict):
        return artifact.get("missing", [])
    return []


def needs_rerun(out_json):
    """True when an artifact on disk records incomplete coverage.

    The per-artifact cache in `analyze` must not skip a stage whose artifact
    says it is missing footage; the per-chunk cache below makes the re-run
    cost only the holes.
    """
    if not os.path.exists(out_json):
        return False
    try:
        return bool(missing_ranges(json.load(open(out_json))))
    except (ValueError, OSError):
        return False


def chunk_cache_path(cache_dir, video, start_s, end_s, model):
    """Per-chunk cache key: (video identity, range, model).

    Identity is basename + size so a moved file still hits and a re-exported
    file (different bytes) misses.
    """
    try:
        size = os.path.getsize(video)
    except OSError:
        size = 0
    base = os.path.splitext(os.path.basename(video))[0]
    safe_model = str(model).replace("/", "_")
    return os.path.join(cache_dir,
                        f"{base}.{size}.{int(start_s)}-{int(end_s)}.{safe_model}.json")


def _analyze_chunk(client, model, prox_path, mins):
    """Upload one compressed chunk and get its sequence list. Raises on failure."""
    from google.genai import types
    f = client.files.upload(file=prox_path)
    t0 = time.monotonic()
    while str(getattr(f.state, "name", f.state)) not in ("ACTIVE", "FAILED"):
        if time.monotonic() - t0 > UPLOAD_DEADLINE_S:
            raise RuntimeError(
                f"Gemini upload stuck in state "
                f"{getattr(f.state, 'name', f.state)} for {UPLOAD_DEADLINE_S}s")
        _sleep(2)
        f = client.files.get(name=f.name)
    if str(getattr(f.state, "name", f.state)) == "FAILED":
        raise RuntimeError("Gemini file upload entered FAILED state")
    resp = client.models.generate_content(
        model=model,
        contents=[f, PROMPT.format(mins=mins)],
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           temperature=0.3))
    try:
        seqs = json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        seqs = []
    return seqs if isinstance(seqs, list) else []


def _analyze_with_retry(client, model, prox_path, mins, label):
    last = None
    for attempt in range(RETRIES):
        if attempt:
            wait = BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)]
            print(f"  retry {attempt + 1}/{RETRIES} in {wait}s ({last})")
            _sleep(wait)
        try:
            return _analyze_chunk(client, model, prox_path, mins)
        except Exception as e:  # network, upload state, API: all retriable here
            last = e
    raise RuntimeError(f"{label}: all {RETRIES} attempts failed: {last}")


def _review_chunk(video, model, ci, n, cs, clen, crop, project, cache_dir):
    """One chunk end to end: cache check, compress, upload (with retries),
    cache write. Independent of every other chunk, so chunks run in a small
    worker pool. Each worker task builds its own Gemini client: nothing is
    shared across threads. Returns (kind, seqs_or_missing, cost) where kind
    is "cached" | "fresh" | "failed"."""
    tag = f"[visual {ci + 1}/{n}]"
    rng = f"{media.fmt(cs)}-{media.fmt(cs + clen)}"
    cache_p = chunk_cache_path(cache_dir, video, cs, cs + clen, model)
    if os.path.exists(cache_p):
        seqs = json.load(open(cache_p)).get("sequences", [])
        print(f"{tag} {rng} cached ({len(seqs)} sequences, $0)")
        kind, cost = "cached", 0.0
    else:
        with tempfile.TemporaryDirectory() as td:
            prox = os.path.join(td, "seg.mp4")
            print(f"{tag} {rng} compressing + uploading ...")
            _compress_segment(video, cs, clen, prox, crop)
            try:
                seqs = _analyze_with_retry(_client(), model, prox,
                                           int(round(clen / 60)) or 1,
                                           f"chunk {ci + 1}/{n} {rng}")
            except RuntimeError as e:
                print(f"{tag} CHUNK FAILED after {RETRIES} attempts: {e}")
                return ("failed", {"start_s": cs, "end_s": cs + clen,
                                   "start_abs": media.fmt(cs),
                                   "end_abs": media.fmt(cs + clen),
                                   "error": str(e)}, 0.0)
        cost = chunk_cost(clen)
        with _LEDGER_LOCK:
            ledger.add("gemini-visual", f"{os.path.basename(video)} chunk {ci + 1}/{n}",
                       cost, project)
        json.dump({"video": os.path.basename(video), "start_s": cs,
                   "end_s": cs + clen, "model": model, "sequences": seqs},
                  open(cache_p, "w"), indent=1)
        print(f"{tag} found {len(seqs)}")
        kind = "fresh"
    for s in seqs:
        st = cs + _local_sec(s.get("start", 0), clen)
        en = cs + _local_sec(s.get("end", 0), clen)
        if en <= st:
            en = st + 4
        s["start_s"], s["end_s"] = st, en
        s["start_abs"], s["end_abs"] = media.fmt(st), media.fmt(en)
    return (kind, seqs, cost)


def review(video, out_json, out_md, model=None, chunk_min=10, crop=None, project=""):
    model = model or config.GEMINI_MODEL
    total = media.duration(video)
    chunk = chunk_min * 60
    n = int(total // chunk) + 1
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(out_json)), "visual_chunks")
    os.makedirs(cache_dir, exist_ok=True)

    chunks = [(ci, ci * chunk, min(chunk, total - ci * chunk))
              for ci in range(n) if ci * chunk < total]

    # budget gate for the whole run before spending anything; cached chunks
    # are excluded because they will not be billed again
    fresh = [(ci, cs, clen) for ci, cs, clen in chunks
             if not os.path.exists(chunk_cache_path(cache_dir, video, cs, cs + clen, model))]
    checked = sum(chunk_cost(clen) for _, _, clen in fresh)
    ledger.check(checked)

    tpath = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(out_json)), "..", "timings.json"))
    # Chunk work is network-bound (upload + Gemini server time); the local
    # compress is a small fraction of each chunk. A pool of 3 made an hour-long
    # session (7 chunks) run in 3 serial waves -- the measured p90 of this
    # stage was 884s. Default 6 collapses most sessions into 1-2 waves;
    # REELLY_VISUAL_WORKERS tunes it per machine/network.
    workers = max(1, int(os.environ.get("REELLY_VISUAL_WORKERS", "6")))
    with timing.stage("visual review", tpath):
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(
                lambda a: _review_chunk(video, model, a[0], len(chunks), a[1], a[2],
                                        crop, project, cache_dir),
                chunks))

    # reassemble in chunk order
    allseq, missing, charged = [], [], 0.0
    for kind, payload, cost in results:
        if kind == "failed":
            missing.append(payload)
        else:
            allseq.extend(payload)
            charged += cost

    # ledger reconciliation (gap: under-spend was the only trace of a hole).
    # charged < checked has exactly one cause once caching is priced in: a
    # failed chunk. Say so with the numbers.
    cached_n = len(chunks) - len(fresh)
    print(f"[visual] ledger: charged ${charged:.2f} of ${checked:.2f} checked "
          f"({cached_n} chunk(s) cached, {len(missing)} failed)")
    if charged + 1e-9 < checked and missing:
        print(f"[visual] WARNING: ${checked - charged:.2f} under the budget check "
              f"because {len(missing)} chunk(s) produced nothing")

    allseq.sort(key=lambda s: max(s.get("trailer_score", 0), s.get("short_score", 0)), reverse=True)
    artifact = {"video": os.path.basename(video), "model": model,
                "chunk_min": chunk_min, "duration_s": round(total, 2),
                "complete": not missing, "missing": missing, "sequences": allseq}
    json.dump(artifact, open(out_json, "w"), indent=1)

    lines = [f"# Visual review: {os.path.basename(video)}", "",
             f"{len(allseq)} sequences across {media.fmt(total)}. T=trailer score, S=short-form score.", ""]
    if missing:
        lines += ["## INCOMPLETE COVERAGE", ""]
        lines += [f"- MISSING [{m['start_abs']}-{m['end_abs']}]: {m['error']}" for m in missing]
        lines += ["", "Re-run `reelly analyze` to retry only the missing ranges "
                  "(everything above is cached per chunk).", ""]
    for i, s in enumerate(allseq, 1):
        hook = "HOOK" if s.get("strong_hook") else "    "
        lines.append(f"{i:2}. [{s['start_abs']}-{s['end_abs']}]  "
                     f"T{s.get('trailer_score', '?')}/S{s.get('short_score', '?')}  {hook}  {s.get('label', '')}")
        lines.append(f"      {s.get('what_happens', '')}  |  {s.get('why', '')}")
    open(out_md, "w").write("\n".join(lines) + "\n")

    if missing:
        holes = ", ".join(f"[{m['start_abs']}-{m['end_abs']}]" for m in missing)
        raise RuntimeError(
            f"visual review INCOMPLETE: {len(missing)} chunk(s) failed after "
            f"{RETRIES} attempts each: {holes}. The holes are recorded in "
            f"{os.path.basename(out_json)}; re-run to retry only the missing "
            f"ranges (successful chunks are cached).")
    return allseq
