"""EA IRIS gate: real analysis where installed, a clean skip elsewhere."""
import subprocess

import pytest

from reelly import config, iris


def test_skip_without_iris(monkeypatch):
    monkeypatch.setattr(iris, "find", lambda: None)
    assert iris.gate("x.mp4")[1] == "SKIP"


def test_verdict_mapping(monkeypatch):
    base = {"OverallResult": "Pass", "TotalFrame": 100, "PatternFailFrames": 0,
            "TotalLuminanceIncidents": {"TotalFailedFrames": 0, "PassWithWarningFrames": 0},
            "TotalRedIncidents": {"TotalFailedFrames": 0, "PassWithWarningFrames": 0}}
    monkeypatch.setattr(iris, "analyse", lambda p: base)
    assert iris.gate("x")[1] == "PASS"
    monkeypatch.setattr(iris, "analyse", lambda p: {**base, "TotalRedIncidents":
                                                    {"TotalFailedFrames": 3, "PassWithWarningFrames": 0}})
    assert iris.gate("x")[1] == "FAIL"
    monkeypatch.setattr(iris, "analyse", lambda p: {**base, "TotalLuminanceIncidents":
                                                    {"TotalFailedFrames": 0, "PassWithWarningFrames": 5}})
    assert iris.gate("x")[1] == "WARN"


@pytest.mark.skipif(not iris.find(), reason="IRIS not installed")
def test_iris_catches_a_strobe_and_passes_a_steady_clip(tmp_path):
    def clip(name, lum):
        p = str(tmp_path / name)
        subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=black:s=320x568:r=30:d=3", "-vf",
                        f"geq=lum='{lum}':cb=128:cr=128", "-pix_fmt", "yuv420p", p], check=True)
        return p
    assert iris.gate(clip("strobe.mp4", "if(lt(mod(T,0.2),0.1),235,16)"))[1] == "FAIL"
    assert iris.gate(clip("flat.mp4", "120"))[1] == "PASS"
