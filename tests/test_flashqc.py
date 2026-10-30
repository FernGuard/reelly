"""WCAG 2.3.1 flash gate on real encoded files."""
import subprocess

from reelly import config, flashqc


def _clip(tmp_path, name, expr, dur=2.0, fps=60):
    p = str(tmp_path / name)
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=black:s=180x320:r={fps}:d={dur}", "-vf",
                    f"geq=lum='{expr}':cb=128:cr=128", "-pix_fmt", "yuv420p", p], check=True)
    return p


def test_a_strobe_fails(tmp_path):
    p = _clip(tmp_path, "strobe.mp4", "if(lt(mod(T,0.2),0.1),235,16)")    # 5 Hz full frame
    r = flashqc.check(p)
    assert r["max_flashes_per_s"] > 3
    assert flashqc.gate(p)[1] == "FAIL"


def test_steady_and_slow_changes_pass(tmp_path):
    assert flashqc.gate(_clip(tmp_path, "flat.mp4", "120"))[1] == "PASS"
    slow = _clip(tmp_path, "slow.mp4", "16+219*(0.5+0.5*sin(2*PI*T*1))", dur=3.0)   # 1 Hz
    assert flashqc.check(slow)["max_flashes_per_s"] <= 3


def test_two_beat_flashes_a_second_pass(tmp_path):
    p = _clip(tmp_path, "beat.mp4", "if(lt(mod(T,0.5),0.05),235,40)")     # 2 Hz accents
    assert flashqc.gate(p)[1] == "PASS"
