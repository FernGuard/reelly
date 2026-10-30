"""Per-frame control track for effects: what the picture should answer.

One track for every effect and every content type, computed once per cut in
Python and handed to the shader page:
- beats: (first beat, period) from the soundtrack's stated tempo (music)
- pops: instants the hook's and lines' hot words pop (beat-timed accents)
- onsets: strong attacks in the cut's own audio (game SFX hits, a slammed
  door, a laugh, an emphatic word), spectral-flux peaks with a refractory gap
- energy: the cut audio's loudness per output frame, 0..1 (speech emphasis,
  game intensity), shipped to WebGL as a 1-row greyscale texture

Effects read it through effects/lib/control.glsl (beatPulse, energy,
sinceOnset); nothing here knows which effect will listen.
"""
import base64
import io
import subprocess

from . import config

FPS = 60                 # control rate: one value per output frame at 60
MAX_ONSETS = 16
REFRACTORY_S = 0.25      # onsets closer than this are one event


def _mono(path, sr=22050):
    import numpy as np
    r = subprocess.run([config.FFMPEG, "-v", "error", "-i", path, "-vn", "-ac", "1",
                        "-ar", str(sr), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32), sr


def energy(path, dur, fps=FPS):
    """Loudness per output frame, smoothed, robustly scaled to 0..1 (95th
    percentile = 1). Empty list for silent or missing audio."""
    import numpy as np
    x, sr = _mono(path)
    n = int(round(dur * fps))
    if len(x) < sr // 4 or n < 1:
        return []
    hop = sr / fps
    rms = np.array([np.sqrt(np.mean(x[int(i * hop):int((i + 1) * hop)] ** 2) + 1e-12)
                    for i in range(n)])
    rms = np.convolve(rms, np.ones(5) / 5, mode="same")          # ~80 ms smoothing
    hi = np.percentile(rms, 95)
    if hi <= 1e-6:
        return []
    return [float(v) for v in np.clip(rms / hi, 0.0, 1.0)]


def onsets(path, dur, limit=MAX_ONSETS):
    """Strongest attacks (seconds), spectral flux peaks, at least
    REFRACTORY_S apart, strongest first then sorted by time."""
    import numpy as np
    from scipy import signal
    x, sr = _mono(path)
    if len(x) < sr // 2:
        return []
    _, t, Z = signal.stft(x, sr, nperseg=1024, noverlap=768)
    mag = np.log1p(np.abs(Z))
    flux = np.maximum(mag[:, 1:] - mag[:, :-1], 0).sum(axis=0)
    t = t[1:]
    if not len(flux) or flux.max() <= 0:
        return []
    flux = flux / flux.max()
    thr = max(0.35, float(np.median(flux) + 2.5 * np.std(flux)))
    peaks, _ = signal.find_peaks(flux, height=thr,
                                 distance=max(1, int(REFRACTORY_S / (t[1] - t[0]))))
    best = sorted(peaks, key=lambda i: -flux[i])[:limit]
    return sorted(round(float(t[i]), 3) for i in best if t[i] <= dur)


def track(plan, audio_path, dur):
    """The control track for one cut (content time 0..dur)."""
    from . import finalize
    beats = plan.get("_beats")
    return {"beats": [float(beats[0]), float(beats[1])] if beats else [0.0, 0.0],
            "pops": finalize._pop_times(plan),
            "onsets": onsets(audio_path, dur) if audio_path else [],
            "energy": energy(audio_path, dur) if audio_path else [],
            "fps": FPS}


def energy_png(values):
    """data: URI of a W x 1 greyscale PNG (W = frames) for u_ctrl, or None."""
    if not values:
        return None
    from PIL import Image
    im = Image.new("L", (len(values), 1))
    im.putdata([int(round(255 * max(0.0, min(1.0, v)))) for v in values])
    b = io.BytesIO()
    im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


def host_uniforms(ctrl, t_offset=0.0):
    """Shader uniforms for a control track as seen from a layer that starts
    at t_offset in content time (an end card after the content: dur)."""
    if not ctrl:
        return {"u_beat": [0.0, 0.0], "u_onsets": [-1.0] * MAX_ONSETS, "u_ctrl_n": 0.0,
                "u_events": [-1.0, 0.0, 0.0, 0.0] * MAX_EVENTS}
    b0, per = ctrl["beats"]
    on = [o - t_offset for o in ctrl["onsets"] if o - t_offset >= 0][:MAX_ONSETS]
    ev = []
    for e in (ctrl.get("events") or [])[:MAX_EVENTS]:
        if e["t"] - t_offset >= 0:
            ev += [e["t"] - t_offset, e["x"], e["y"], e["s"]]
    ev += [-1.0, 0.0, 0.0, 0.0] * (MAX_EVENTS - len(ev) // 4)
    return {"u_beat": [b0 - t_offset, per],
            "u_onsets": on + [-1.0] * (MAX_ONSETS - len(on)),
            "u_ctrl_n": float(len(ctrl["energy"])) if t_offset == 0 else 0.0,
            "u_events": ev}


MAX_EVENTS = 8
EVENT_GAP_S = 0.9        # one mark per moment, never a machine-gun


def _picture_motion(video, pic, dur, fps=FPS, w=96, h=54):
    """Per-frame picture NOVELTY inside the protected picture rect (uv, y
    up), and where it is: (novelty[n], cx[n], cy[n]) in picture uv.

    Novelty, not raw change: each block of a 12 x 6 grid is compared with
    its own median over the previous 0.5 s. A region that is always moving
    (a swirling background, an idle character) stops counting; a sudden
    burst where nothing was happening (an explosion, a hit flash) stands
    out. Raw change put Curses impacts on the ever-moving vortex."""
    import numpy as np
    H = 1920
    y0 = int(round((1.0 - pic[3]) * H))
    ph = max(2, int(round((pic[3] - pic[1]) * H)) // 2 * 2)
    r = subprocess.run([config.FFMPEG, "-v", "error", "-i", video, "-vf",
                        f"crop=1080:{ph}:0:{y0},fps={fps},scale={w}:{h}",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
    n = len(r.stdout) // (w * h)
    if n < 3:
        return np.zeros(max(n, 1)), np.full(max(n, 1), 0.5), np.full(max(n, 1), 0.5)
    f = np.frombuffer(r.stdout[: n * w * h], np.uint8).reshape(n, h, w).astype(np.float32)
    d = np.abs(np.diff(f, axis=0))
    d = np.concatenate([d[:1] * 0, d])
    by, bx = 6, 12
    hb, wb = h // by, w // bx
    blocks = d[:, : hb * by, : wb * bx].reshape(n, by, hb, bx, wb).mean(axis=(2, 4))
    hist = int(0.5 * fps)
    nov = np.zeros_like(blocks)
    for i in range(n):
        past = blocks[max(0, i - hist):max(1, i - 2)]
        base = np.median(past, axis=0) if len(past) else blocks[i] * 0
        nov[i] = blocks[i] / (base + 2.0)          # +2: grey-level floor, kills noise
    flat = nov.reshape(n, -1)
    k = flat.argmax(axis=1)
    novelty = flat.max(axis=1)
    cx = ((k % bx) + 0.5) / bx
    cy = 1.0 - ((k // bx) + 0.5) / by
    return novelty, cx, cy


def _flux_per_frame(path, n, fps=FPS):
    import numpy as np
    from scipy import signal
    x, sr = _mono(path)
    if len(x) < sr // 4:
        return np.zeros(n)
    hop = int(sr / fps)
    _, _, Z = signal.stft(x, sr, nperseg=hop * 4, noverlap=hop * 3)
    mag = np.log1p(np.abs(Z))
    flux = np.maximum(mag[:, 1:] - mag[:, :-1], 0).sum(axis=0)
    flux = np.concatenate([[0.0], flux])[:n]
    return np.pad(flux, (0, max(0, n - len(flux))))


def events(video, audio_path, pic, dur, fps=FPS):
    """Real impacts: frames where the SOUND and the PICTURE both jump (a UI
    blip or a dialogue tick moves only the sound, a camera pan only the
    picture). [{t, x, y, s}] with x, y = where in the picture it happened
    (picture uv, y up) and s = strength 0..1, strongest first then by time,
    at least EVENT_GAP_S apart, at most MAX_EVENTS."""
    import numpy as np
    from scipy import signal
    energy, cx, cy = _picture_motion(video, pic, dur, fps)
    n = len(energy)
    flux = _flux_per_frame(audio_path, n, fps) if audio_path else np.zeros(n)

    def z(a):
        a = np.asarray(a, dtype=np.float64)
        s = a.std()
        return (a - np.median(a)) / s if s > 1e-9 else a * 0

    # picture reaction may trail the sound by a frame or two (and vice versa)
    vis = z(energy)
    vis_near = np.array([vis[max(0, i - 2):i + 3].max() for i in range(n)])
    aud = z(flux)
    score = np.clip(aud, 0, None) * np.clip(vis_near, 0, None)
    if score.max() <= 0:
        return []
    peaks, props = signal.find_peaks(score, height=max(4.0, float(np.percentile(score, 99))),
                                     distance=max(1, int(EVENT_GAP_S * fps)))
    strongest = sorted(peaks, key=lambda i: -score[i])[:MAX_EVENTS]
    top = score[strongest[0]] if strongest else 1.0
    out = []
    for i in sorted(strongest):
        j = max(range(max(0, i - 2), min(n, i + 3)), key=lambda k: energy[k])  # the picture's frame
        out.append({"t": round(float(i) / fps, 3), "x": round(float(cx[j]), 3),
                    "y": round(float(cy[j]), 3), "s": round(float(score[i] / top), 3)})
    return out
