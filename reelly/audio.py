"""UNDERSTAND: loudness map (EBU R128) and audio-energy peaks (hook candidates)."""
import numpy as np

from . import config, media


def loudness(video):
    """1 Hz momentary-loudness curve, integrated LUFS, true peak, energy peaks."""
    r = media.sh(config.FFMPEG, "-v", "info", "-i", video,
                 "-filter_complex", "ebur128=peak=true", "-f", "null", "-")
    raw = []
    integrated = None
    true_peak = None
    lines = r.stderr.splitlines()
    for line in lines:
        if " t: " in line and " M:" in line:
            try:
                t = float(line.split(" t: ")[1].split()[0])
                m = float(line.split(" M:")[1].split()[0])
                raw.append((t, m))
            except (ValueError, IndexError):
                pass
    # summary block at the end holds the final integrated + true peak
    for i, line in enumerate(lines):
        if "Integrated loudness:" in line:
            for l2 in lines[i:i + 4]:
                if "I:" in l2:
                    try:
                        integrated = float(l2.split("I:")[1].split()[0])
                    except (ValueError, IndexError):
                        pass
        if "True peak:" in line:
            for l2 in lines[i:i + 4]:
                if "Peak:" in l2:
                    try:
                        true_peak = float(l2.split("Peak:")[1].split()[0])
                    except (ValueError, IndexError):
                        pass

    # downsample the ~10 Hz curve to 1 Hz
    curve = {}
    for t, m in raw:
        if m > -70:  # ignore digital-silence readings
            sec = int(t)
            curve[sec] = max(curve.get(sec, -70.0), m)
    curve = sorted(curve.items())

    peaks = _energy_peaks(curve)
    return {
        "integrated_lufs": integrated,
        "true_peak_dbtp": true_peak,
        "curve_1hz": [(t, round(m, 1)) for t, m in curve],
        "energy_peaks": peaks,
    }


def _energy_peaks(curve, boost=6.0, min_gap=10, top=15):
    """Seconds noticeably louder than their neighborhood: reactions, reveals, hits."""
    if len(curve) < 30:
        return []
    ts = np.array([t for t, _ in curve])
    ms = np.array([m for _, m in curve])
    med = np.median(ms)
    idx = np.argsort(-ms)
    peaks = []
    for i in idx:
        if ms[i] < med + boost:
            break
        if all(abs(int(ts[i]) - p["t"]) >= min_gap for p in peaks):
            peaks.append({"t": int(ts[i]), "lufs": round(float(ms[i]), 1),
                          "above_median": round(float(ms[i] - med), 1)})
        if len(peaks) >= top:
            break
    return sorted(peaks, key=lambda p: p["t"])
