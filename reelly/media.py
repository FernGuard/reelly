"""Thin ffmpeg/ffprobe helpers used by every stage."""
import functools
import json
import os
import subprocess

from . import config


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True)


def _probe_run(path):
    r = sh(config.FFPROBE, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path)
    return r.stdout or "{}"


@functools.lru_cache(maxsize=64)
def _probe_raw(abspath, mtime_ns, size):
    # keyed by (path, mtime, size) so an overwritten file re-probes; the raw
    # JSON text is cached (not the parsed dict) so callers can never mutate
    # each other's copy
    return _probe_run(abspath)


def probe(path):
    try:
        st = os.stat(path)
    except OSError:  # missing file: same empty-probe behavior as before
        return json.loads(_probe_run(path))
    return json.loads(_probe_raw(os.path.abspath(path), st.st_mtime_ns, st.st_size))


def duration(path):
    return float(probe(path).get("format", {}).get("duration", 0) or 0)


def fmt(sec):
    return f"{int(sec) // 60:02d}:{int(sec) % 60:02d}"


# HDR sources (iPhone HLG, mirrorless PQ) that only get bit-depth downconverted
# keep their HDR transfer metadata: platform re-encodes then read 8-bit values
# in an HDR container and the result looks blown out, while QuickTime hides the
# problem locally. Detect and tone-map to clean Rec.709 SDR instead.
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) and HLG

TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
           "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
           "format=yuv420p")


def color_transfer(path):
    r = sh(config.FFPROBE, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=color_transfer",
           "-of", "default=noprint_wrappers=1:nokey=1", path)
    return (r.stdout or "").strip()


def is_hdr(path):
    return color_transfer(path) in HDR_TRANSFERS


_FILTERS = None


def has_filter(name):
    global _FILTERS
    if _FILTERS is None:
        r = sh(config.FFMPEG, "-hide_banner", "-filters")
        _FILTERS = r.stdout or ""
    return f" {name} " in _FILTERS


def sdr_chain(path):
    """Tonemap chain for an HDR source, or '' when nothing is needed OR the
    build cannot do it. The zscale steps need libzimg, which lean homebrew
    builds omit (same story as libass): in that case warn loudly and ship
    untonemapped — the judge's sdr_transfer gate FAILs the deliverable, so
    the problem stays visible instead of erroring mid-render."""
    if not is_hdr(path):
        return ""
    if has_filter("zscale"):
        return TONEMAP
    import os
    print(f"[warn ] {os.path.basename(path)} is HDR but this ffmpeg lacks zscale "
          "(libzimg); rendering untonemapped and judge will FAIL sdr_transfer. "
          "Fix: install an ffmpeg build with libzimg.")
    return ""


def extract_wav(video, wav, rate=16000):
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", video,
                    "-ac", "1", "-ar", str(rate), wav], check=True)


BLACK_LUMA = 20.0  # YAVG at or below this reads as video black (16 = pure)


def frame_luma(video, t):
    """Average luma (YAVG) of the frame at t seconds, or None if unreadable."""
    r = subprocess.run([config.FFMPEG, "-ss", str(t), "-i", video, "-frames:v", "1",
                        "-vf", "signalstats,metadata=print", "-f", "null", "-"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "YAVG" in line:
            try:
                return float(line.split("=")[-1])
            except ValueError:
                return None
    return None


def assert_not_black(video, samples=5):
    """Refuse a source whose picture never turned on (dead OBS display source:
    audio records, every frame is black). Five spread probes cost ~a second;
    letting the pipeline run costs ~30 min and ~$2.50 of AI spend, and QC only
    catches it after the render. All-black on purpose (intentional black-screen
    content) opts out with REELLY_ALLOW_BLACK_SOURCE=1."""
    import os
    if os.environ.get("REELLY_ALLOW_BLACK_SOURCE") == "1":
        return
    d = duration(video)
    lumas = [frame_luma(video, d * f) for f in (0.05, 0.25, 0.5, 0.75, 0.95)][:samples]
    seen = [v for v in lumas if v is not None]
    if seen and all(v <= BLACK_LUMA for v in seen):
        raise SystemExit(
            f"[ingest] SOURCE IS BLACK: all {len(seen)} probe frames across "
            f"{os.path.basename(video)} read as video black (luma <= {BLACK_LUMA}). "
            "This is almost always a dead screen-capture source (OBS recorded "
            "audio only). Fix the capture and re-record; to analyze intentional "
            "black-screen footage set REELLY_ALLOW_BLACK_SOURCE=1.")
