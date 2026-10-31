"""EA IRIS photosensitivity analysis as a judge gate (where installed).

WHY
flashqc implements the WCAG general-flash rule itself, simplified. EA's
IRIS (BSD-3-Clause, github.com/electronicarts/IRIS) is the reference tool
games use: luminance flashes, SATURATED-RED flashes, the extended rule
(2-3 flashes a second sustained over 5 s) and harmful spatial patterns.
Both run: flashqc everywhere, IRIS where its app is installed.

HOW
IrisApp.exe -v <video> -j 1 -p 1, run from its own folder (it reads
appsettings.json there and writes Results/<video name>/result.json). The
video is hard-linked under a unique name so parallel checks never share a
results folder, and the folder is removed after reading. IRIS states it
does not certify compliance: this is a detector, the owner signs off.

REELLY_IRIS = path to IrisApp (else <REELLY_HOME>/tools/iris/, else PATH).
"""
import json
import os
import shutil
import subprocess
import uuid

def find():
    """IrisApp: REELLY_IRIS, then <REELLY_HOME>/tools/iris/, then PATH."""
    from . import config
    p = os.environ.get("REELLY_IRIS", "").strip()
    if p and os.path.exists(p):
        return p
    exe = "IrisApp.exe" if os.name == "nt" else "IrisApp"
    c = os.path.join(config.HOME, "tools", "iris", exe)
    if os.path.exists(c):
        return c
    return shutil.which("IrisApp") or None


def analyse(video, app=None, patterns=True, timeout=900):
    """IRIS's result.json for `video` as a dict, or None when IRIS is not
    installed."""
    app = app or find()
    if not app:
        return None
    home = os.path.dirname(app)
    name = f"reelly-{uuid.uuid4().hex[:10]}{os.path.splitext(video)[1] or '.mp4'}"
    staged = os.path.join(home, "TestVideos", name)
    os.makedirs(os.path.dirname(staged), exist_ok=True)
    try:
        os.link(video, staged)
    except OSError:
        shutil.copyfile(video, staged)
    out = os.path.join(home, "Results", name)
    try:
        subprocess.run([app, "-v", staged, "-j", "1", "-p", "1" if patterns else "0"],
                       cwd=home, capture_output=True, timeout=timeout)
        with open(os.path.join(out, "result.json"), encoding="utf-8") as f:
            return json.load(f)
    finally:
        shutil.rmtree(out, ignore_errors=True)
        try:
            os.remove(staged)
        except OSError:
            pass


def gate(path):
    """("iris", status, detail): FAIL on any flash, extended or pattern
    failure, WARN on warnings only, SKIP when IRIS is not installed."""
    r = analyse(path)
    if r is None:
        return ("iris", "SKIP", "IRIS not installed (flashqc still applies)")
    lum, red = r.get("TotalLuminanceIncidents") or {}, r.get("TotalRedIncidents") or {}
    fails = {"luminance": lum.get("TotalFailedFrames", 0), "red": red.get("TotalFailedFrames", 0),
             "pattern": r.get("PatternFailFrames", 0)}
    warns = lum.get("PassWithWarningFrames", 0) + red.get("PassWithWarningFrames", 0)
    overall = str(r.get("OverallResult", ""))
    detail = (f"{overall}: failed frames lum {fails['luminance']} / red {fails['red']} / "
              f"pattern {fails['pattern']}, warnings {warns} of {r.get('TotalFrame')}")
    if overall.lower().startswith("fail") or any(fails.values()):
        return ("iris", "FAIL", detail)
    if warns or "warning" in overall.lower():
        return ("iris", "WARN", detail)
    return ("iris", "PASS", detail)
