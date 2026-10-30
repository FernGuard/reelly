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
        return {"u_beat": [0.0, 0.0], "u_onsets": [-1.0] * MAX_ONSETS, "u_ctrl_n": 0.0}
    b0, per = ctrl["beats"]
    on = [o - t_offset for o in ctrl["onsets"] if o - t_offset >= 0][:MAX_ONSETS]
    return {"u_beat": [b0 - t_offset, per],
            "u_onsets": on + [-1.0] * (MAX_ONSETS - len(on)),
            "u_ctrl_n": float(len(ctrl["energy"])) if t_offset == 0 else 0.0}
