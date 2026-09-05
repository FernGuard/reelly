"""Perceptual QC: what the metric gates cannot see.

For each cut boundary in a final deliverable, composites a timeline view
(filmstrip + RMS waveform + word labels + silence shading, ±1.5s around the
join) and has Gemini review the composites for jump cuts, boundary pops,
occluded or missing captions, and flash frames. A handful of PNGs instead of
re-uploading video: pennies per cut, ledgered like every AI call.

Adapted from MIT-licensed browser-use/video-use helpers/timeline_view.py.
See THIRD_PARTY_NOTICES.md. Verdicts are WARN-level: perceptual calls advise
the human, they do not block.
"""
import json
import os
import subprocess
import tempfile
import wave

from . import config, ledger, media

WINDOW_S = 1.5     # context on each side of a join
N_FRAMES = 8
EST_COST_PER_CUT = 0.002  # a few small images + a short JSON reply

PROMPT = """These images are timeline composites around the internal cut points of one
short-form video: a filmstrip of frames spanning the join (the cut lands at the
horizontal center), an audio RMS waveform below (shaded regions are silence),
and the caption words on the output timeline.

For each image, judge the join:
- jump_cut: do the frames around the center show a jarring visual jump
  (same framing, small discontinuity) rather than an intentional cut?
- pop: is there a sharp isolated waveform spike exactly at the center line?
- captions: do the word labels stop mid-sentence at the join, or would two
  caption lines overlap?
- flash: any black or corrupted-looking frame near the center?

Reply with JSON only: a list, one object per image in order:
{"image": <1-based index>, "ok": true/false, "issues": ["<short issue>", ...]}
Empty issues list when ok. Be strict about pops and flashes, lenient about
intentional-looking hard cuts.
"""


def _envelope(video, start, dur, samples):
    """Windowed RMS of the mono audio for [start, start+dur], normalized 0..1."""
    import numpy as np
    fd, wav_p = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        r = subprocess.run(
            [config.FFMPEG, "-y", "-v", "error", "-ss", f"{start:.3f}",
             "-i", video, "-t", f"{dur:.3f}", "-vn", "-ac", "1",
             "-ar", "16000", "-c:a", "pcm_s16le", wav_p],
            capture_output=True)
        if r.returncode != 0 or not os.path.getsize(wav_p):
            return np.zeros(samples)
        with wave.open(wav_p, "rb") as w:
            pcm = np.frombuffer(w.readframes(w.getnframes()),
                                dtype=np.int16).astype(np.float32) / 32768.0
        if pcm.size == 0:
            return np.zeros(samples)
        win = max(1, pcm.size // samples)
        env = np.sqrt(np.mean(pcm[:(pcm.size // win) * win]
                              .reshape(-1, win) ** 2, axis=1))
        env = env[:samples]
        if env.size < samples:
            env = np.pad(env, (0, samples - env.size))
        return env / env.max() if env.max() > 0 else env
    finally:
        os.unlink(wav_p)


def composite(video, center, out_png, words=None, title=""):
    """Filmstrip + waveform composite for [center-WINDOW_S, center+WINDOW_S]."""
    from PIL import Image, ImageDraw, ImageFont
    total = media.duration(video)
    start = max(0.0, center - WINDOW_S)
    # keep the last sampled frame safely inside the stream: seeking at or past
    # EOF returns no frame and ffmpeg exits nonzero
    end = min(max(0.0, total - 0.1), center + WINDOW_S)
    if end - start < 0.8:
        return None

    # one batched ffmpeg spawn for the whole strip (was N_FRAMES spawns)
    from . import faceio
    step = (end - start) / (N_FRAMES - 1)
    times = [start + i * step for i in range(N_FRAMES)]
    frames = [Image.fromarray(fr) for fr, _ in
              faceio.extract_frames(video, times, max_width=180)
              if fr is not None]
    if len(frames) < 2:
        return None

    fw, fh = frames[0].size
    pad, gap = 40, 3
    strip_w = N_FRAMES * fw + (N_FRAMES - 1) * gap
    wave_h, label_h = 120, 46
    W, H = strip_w + 2 * pad, fh + wave_h + label_h + 90
    img = Image.new("RGB", (W, H), (18, 18, 22))
    d = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 13)
    except OSError:
        font = ImageFont.load_default()

    d.text((pad, 8), f"{title}  cut @ {center:.2f}s  ({start:.2f}-{end:.2f}s)",
           fill=(235, 235, 235), font=font)
    y0 = 32
    for i, f in enumerate(frames):
        img.paste(f, (pad + i * (fw + gap), y0))

    wy = y0 + fh + 12
    d.rectangle((pad, wy, pad + strip_w, wy + wave_h), fill=(28, 28, 34))

    def t2x(t):
        return int(pad + (t - start) / (end - start) * strip_w)

    ws = [w for w in (words or []) if start <= (w["s"] + w["e"]) / 2 <= end]
    prev = start
    for w in ws:  # silence shading between words
        if w["s"] - prev >= 0.25:
            d.rectangle((t2x(prev), wy, t2x(w["s"]), wy + wave_h),
                        fill=(50, 80, 120, 120))
        prev = max(prev, w["e"])
    if end - prev >= 0.25:
        d.rectangle((t2x(prev), wy, t2x(end), wy + wave_h),
                    fill=(50, 80, 120, 120))

    env = _envelope(video, start, end - start, strip_w)
    mid, amp = wy + wave_h // 2, wave_h // 2 - 6
    for i, v in enumerate(env):
        d.line((pad + i, mid - int(v * amp), pad + i, mid + int(v * amp)),
               fill=(140, 180, 255))

    cx = t2x(center)  # the join itself
    d.line((cx, y0, cx, wy + wave_h + label_h), fill=(255, 140, 60), width=2)

    ly = wy + wave_h + 8
    for w in ws:
        d.text((t2x(w["s"]), ly + (8 if ws.index(w) % 2 else 0)),
               w["t"][:14], fill=(180, 180, 190), font=font)

    img.save(out_png)
    return out_png


def _boundaries(plan):
    """Internal join times on the OUTPUT timeline (concat points)."""
    ts, acc = [], 0.0
    segs = plan["segments"]
    for seg in segs[:-1]:
        s, e = seg[0], seg[1]
        speed = seg[2] if len(seg) > 2 else 1.0
        acc += (e - s) / speed
        ts.append(acc)
    return ts


def review(project_root, tag=None, model=None):
    """Composite + Gemini-review every deliverable's joins. Returns
    {filename: [issue strings]} for joins that failed perceptual review."""
    from google.genai import types
    from PIL import Image
    from . import direct, speech
    from .finalize import _shifted_words

    sfx = f"_{tag}" if tag else ""
    plans_p = os.path.join(project_root, "edl", f"cut_plans{sfx}.json")
    if not os.path.exists(plans_p):
        print("[vqc  ] no cut plans, skipping")
        return {}
    plans = {p["id"]: p for p in json.load(open(plans_p))}
    words = speech.words_from(os.path.join(project_root, "analysis", "words.json"))
    fin = os.path.join(project_root, "deliverables", f"final{sfx}")
    if not os.path.isdir(fin):
        print("[vqc  ] no final deliverables, skipping")
        return {}

    out_dir = os.path.join(project_root, "qc", f"visual{sfx}")
    os.makedirs(out_dir, exist_ok=True)
    from .visual import _client
    client = _client()
    model = model or config.GEMINI_MODEL

    findings = {}
    for f in sorted(os.listdir(fin)):
        if not f.endswith(".mp4") or "_trending" in f:
            continue
        plan = plans.get(f[:6])
        if not plan:
            continue
        joins = _boundaries(plan)
        if not joins:
            continue
        ledger.check(EST_COST_PER_CUT)
        wtl, _ = _shifted_words(words, plan["segments"])
        path = os.path.join(fin, f)
        pngs = []
        for bi, t in enumerate(joins):
            png = composite(path, t, os.path.join(out_dir, f"{f[:6]}_j{bi}.png"),
                            words=wtl, title=f)
            if png:
                pngs.append(png)
        if not pngs:
            continue
        print(f"[vqc  ] {f}: {len(pngs)} joins -> gemini")
        resp = client.models.generate_content(
            model=model,
            contents=[Image.open(p) for p in pngs] + [PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.2))
        ledger.add("gemini-visual-qc", f"{f} {len(pngs)} joins",
                   EST_COST_PER_CUT, os.path.basename(project_root))
        try:
            verdicts = json.loads(resp.text)
        except (json.JSONDecodeError, TypeError):
            verdicts = []
        bad = []
        for v in verdicts:
            if not v.get("ok", True):
                i = int(v.get("image", 0))
                t = joins[i - 1] if 0 < i <= len(joins) else 0.0
                bad.append(f"join @{t:.2f}s: {', '.join(v.get('issues', []))}")
        if bad:
            findings[f] = bad
    return findings
