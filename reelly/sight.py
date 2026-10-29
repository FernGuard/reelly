"""UNDERSTAND, zero-paid: look at the picture with a subscription or local
vision model instead of the per-token Gemini pass.

WHY
With paid calls off, analyze used to skip the visual pass entirely, and the
editor brain then had nothing to plan from on picture-only footage (silent
gameplay): 0 cut plans. This pass samples frames into CONTACT SHEETS (3x3
grids, read left to right, top to bottom), asks a vision model what happens
in each sheet, and writes the SAME visual_review.json the Gemini pass writes,
so direct plans from it unchanged.

BACKENDS (resolved per run, never billed per call)
- codex : the Codex CLI signed in to a ChatGPT plan (`codex exec -i sheet`).
          The default: on the reference footage it read every frame right in
          ~9 s per sheet.
- local : an OpenAI-compatible endpoint (REELLY_VISION_URL, model
          REELLY_VISION_MODEL), e.g. a vision model on a home GPU. Slower and
          less exact in the same test, but fully offline.
REELLY_SIGHT=off disables the pass; REELLY_SIGHT=codex|local forces one.

It also writes analysis/frames.json: per sampled frame what happens, the
on-screen text and a 1-10 "would this stop a scroll as frame one" score,
for choosing an opening frame together with the hook.

Each sheet's answer is cached under analysis/visual_chunks/ keyed on the
video's identity, the sheet's range and the backend, so a re-run only asks
about sheets it has not seen.
"""
import base64
import json
import os
import subprocess
import tempfile
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config, ledger, media

COLS, ROWS = 3, 3
PER_SHEET = COLS * ROWS
THUMB_W = 480
_LEDGER_LOCK = threading.Lock()
DEFAULT_LOCAL_MODEL = "qwen3.6:35b-a3b"

PROMPT = """This image is a contact sheet of {n} frames from a video game recording, read left to right, top to bottom.
Frame i (0-based) is at t = {t0:.1f} + {step:g} * i seconds of the recording: {times}.
Judge it for two goals: (a) a game trailer (striking, readable, satisfying moments) and (b) a vertical short for TikTok/Reels/Shorts (a moment that stops a scroll and pays off fast).
Look at the game itself: characters, events, reveals, UI that tells a story. Ignore black frames.

Return ONLY one JSON object:
{{"frames": [{{"t": <seconds>, "what_happens": "<one short line>", "on_screen_text": "<text visible in the frame, or empty>", "opening_score": <1-10, how hard this frame would stop a scroll as the FIRST frame of a short>}}],
 "sequences": [{{"label": "<short name>", "start_t": <seconds>, "end_t": <seconds>, "trailer_score": <1-10>, "short_score": <1-10>, "what_happens": "<one line>", "why": "<one line>", "strong_hook": <true|false>}}]}}
One entry in "frames" per frame. "sequences" are the cuttable stretches inside this sheet (they may continue past its last frame); return [] when nothing is worth cutting."""


def step_for(duration):
    """Seconds between sampled frames: dense on short clips, sparse on long
    recordings so a 30-minute session stays a few dozen sheets."""
    if duration <= 300:
        return 2.0
    if duration <= 1200:
        return 3.0
    return 5.0


def resolve_backend():
    """'codex' | 'local' | None for this run. auto: codex when signed in,
    else the local endpoint when configured."""
    mode = os.environ.get("REELLY_SIGHT", "").strip().lower() or "auto"
    if mode in ("off", "0", "no"):
        return None
    if mode == "codex":
        return "codex" if _codex_ready() else None
    if mode == "local":
        return "local" if _local_url() else None
    if _codex_ready():
        return "codex"
    return "local" if _local_url() else None


def _codex_ready():
    from . import direct
    return direct.cli_ready("codex")


def _local_url():
    return (os.environ.get("REELLY_VISION_URL") or "").rstrip("/")


def _sheet(video, t0, step, n, out, crop=None):
    """One COLS x ROWS contact sheet of n frames from t0, `step` apart."""
    vf = f"fps=1/{step:g}"
    if crop:
        w, h, x, y = crop.split(":")
        vf = f"crop={w}:{h}:{x}:{y}," + vf
    vf += f",scale={THUMB_W}:-2,tile={COLS}x{ROWS}"
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-ss", f"{t0:.3f}",
                    "-t", f"{step * n:.3f}", "-i", video, "-vf", vf,
                    "-frames:v", "1", "-q:v", "3", out], check=True)
    return out


def _ask_local(prompt, image, label, project):
    with open(image, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = {"model": os.environ.get("REELLY_VISION_MODEL") or DEFAULT_LOCAL_MODEL,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + b64}}]}]}
    req = urllib.request.Request(_local_url() + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    # a home endpoint: never through a proxy from the environment
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=600) as r:
        text = json.load(r)["choices"][0]["message"]["content"]
    with _LEDGER_LOCK:
        ledger.add("local-vision", label, 0.0, project)
    from . import direct
    return direct._json_from_text(text)


def _ask(backend, prompt, image, label, project):
    from . import direct
    if backend == "codex":
        return direct._ask_cli("codex", prompt, project, label, images=[image])
    return _ask_local(prompt, image, label, project)


def _cache_path(cache_dir, video, t0, t1, backend):
    try:
        size = os.path.getsize(video)
    except OSError:
        size = 0
    base = os.path.splitext(os.path.basename(video))[0]
    return os.path.join(cache_dir, f"sight.{base}.{size}.{t0:.1f}-{t1:.1f}.{backend}.json")


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _score(v):
    return int(max(1, min(10, round(_num(v, 1)))))


def _one_sheet(video, backend, i, n_sheets, t0, step, n, total, crop, project,
               cache_dir):
    """('ok', frames, sequences) | ('failed', hole, None) for one sheet."""
    t1 = min(total, t0 + step * n)
    cp = _cache_path(cache_dir, video, t0, t1, backend)
    tag = f"[sight] sheet {i + 1}/{n_sheets} [{media.fmt(t0)}-{media.fmt(t1)}]"
    if os.path.exists(cp):
        data = json.load(open(cp))
        print(f"{tag} cached")
        return "ok", data.get("frames", []), data.get("sequences", [])
    times = [round(t0 + step * k, 1) for k in range(n)]
    prompt = PROMPT.format(n=n, t0=t0, step=step,
                           times=", ".join(f"{t:g}" for t in times))
    with tempfile.TemporaryDirectory() as td:
        try:
            img = _sheet(video, t0, step, n, os.path.join(td, "sheet.jpg"), crop)
            reply = _ask(backend, prompt, img, f"sight {media.fmt(t0)}", project)
        except Exception as e:  # noqa: BLE001 -- recorded as a hole, never silent
            reply, err = None, str(e)[:200]
        else:
            err = "no JSON in the reply"
    if not isinstance(reply, dict):
        print(f"{tag} FAILED ({err})")
        return "failed", {"start_s": t0, "end_s": t1, "start_abs": media.fmt(t0),
                          "end_abs": media.fmt(t1), "error": err}, None
    frames = []
    for f in reply.get("frames") or []:
        if isinstance(f, dict):
            t = _num(f.get("t"), None)
            if t is None or not (t0 - 0.5 <= t <= t1 + 0.5):
                continue
            frames.append({"t": round(t, 2),
                           "what_happens": str(f.get("what_happens", ""))[:200],
                           "on_screen_text": str(f.get("on_screen_text", ""))[:300],
                           "opening_score": _score(f.get("opening_score"))})
    seqs = []
    for s in reply.get("sequences") or []:
        if not isinstance(s, dict):
            continue
        st = max(0.0, _num(s.get("start_t"), t0))
        en = min(total, max(st + 1.0, _num(s.get("end_t"), t1)))
        seqs.append({"label": str(s.get("label", ""))[:60],
                     "start_s": round(st, 2), "end_s": round(en, 2),
                     "start_abs": media.fmt(st), "end_abs": media.fmt(en),
                     "trailer_score": _score(s.get("trailer_score")),
                     "short_score": _score(s.get("short_score")),
                     "what_happens": str(s.get("what_happens", ""))[:200],
                     "why": str(s.get("why", ""))[:200],
                     "strong_hook": bool(s.get("strong_hook"))})
    json.dump({"video": os.path.basename(video), "start_s": t0, "end_s": t1,
               "backend": backend, "frames": frames, "sequences": seqs},
              open(cp, "w"), indent=1)
    print(f"{tag} {len(frames)} frames, {len(seqs)} sequence(s), $0")
    return "ok", frames, seqs


def review(video, out_json, out_md, crop=None, project="", backend=None):
    """The zero-paid visual pass. Writes visual_review.json (same shape as
    the Gemini pass) and frames.json next to it. Returns the sequences, or
    None when no backend is available (the caller says so and moves on)."""
    backend = backend or resolve_backend()
    if not backend:
        print("[sight] no free vision backend (sign in to codex, or set "
              "REELLY_VISION_URL); picture-only planning has nothing to read")
        return None
    total = media.duration(video)
    step = step_for(total)
    starts = []
    t = 0.0
    # a sheet needs at least one sampled frame: a tail shorter than one step
    # past the last full sheet is already covered by that sheet's last frame
    while t < total - step:
        starts.append(t)
        t += step * PER_SHEET
    if not starts and total > 0:
        starts = [0.0]
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(out_json)), "visual_chunks")
    os.makedirs(cache_dir, exist_ok=True)
    workers = max(1, int(os.environ.get("REELLY_SIGHT_WORKERS", "3")))
    print(f"[sight] {backend}: {len(starts)} contact sheet(s), a frame every "
          f"{step:g}s, {workers} at a time ($0 per call)")

    def job(a):
        i, t0 = a
        n = min(PER_SHEET, max(1, int((total - t0) // step) + 1))
        return _one_sheet(video, backend, i, len(starts), t0, step, n, total,
                          crop, project, cache_dir)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(job, enumerate(starts)))

    frames, seqs, missing = [], [], []
    for kind, a, b in results:
        if kind == "failed":
            missing.append(a)
        else:
            frames.extend(a)
            seqs.extend(b)
    seqs.sort(key=lambda s: max(s["trailer_score"], s["short_score"]), reverse=True)
    frames.sort(key=lambda f: f["t"])
    engine = ("codex-cli" if backend == "codex" else
              f"local:{os.environ.get('REELLY_VISION_MODEL') or DEFAULT_LOCAL_MODEL}")
    artifact = {"video": os.path.basename(video), "model": engine,
                "engine": "sight", "step_s": step, "duration_s": round(total, 2),
                "complete": not missing, "missing": missing, "sequences": seqs}
    json.dump(artifact, open(out_json, "w"), indent=1)
    json.dump({"video": os.path.basename(video), "engine": engine, "step_s": step,
               "frames": frames},
              open(os.path.join(os.path.dirname(out_json), "frames.json"), "w"),
              indent=1)
    lines = [f"# Visual review (zero-paid, {engine}): {os.path.basename(video)}", "",
             f"{len(seqs)} sequences across {media.fmt(total)}, a frame every "
             f"{step:g}s. T=trailer score, S=short-form score.", ""]
    if missing:
        lines += ["## INCOMPLETE COVERAGE", ""]
        lines += [f"- MISSING [{m['start_abs']}-{m['end_abs']}]: {m['error']}"
                  for m in missing]
        lines += ["", "Re-run `reelly analyze` to retry only these sheets.", ""]
    for i, s in enumerate(seqs, 1):
        hook = "HOOK" if s.get("strong_hook") else "    "
        lines.append(f"{i:2}. [{s['start_abs']}-{s['end_abs']}]  "
                     f"T{s['trailer_score']}/S{s['short_score']}  {hook}  {s['label']}")
        lines.append(f"      {s['what_happens']}  |  {s['why']}")
    best = sorted(frames, key=lambda f: -f["opening_score"])[:5]
    if best:
        lines += ["", "## Strongest opening frames", ""]
        lines += [f"- {f['t']:g}s ({f['opening_score']}/10): {f['what_happens']}"
                  + (f"  [text: {f['on_screen_text'][:60]}]" if f["on_screen_text"] else "")
                  for f in best]
    open(out_md, "w").write("\n".join(lines) + "\n")
    if missing:
        print(f"[sight] WARNING: {len(missing)} sheet(s) failed; their ranges are "
              f"recorded as missing and retried on the next analyze")
    return seqs
