"""Shader end card: opt-in per brand, deterministic page, wired into the outro."""
import pytest

from reelly import card, outro, shaderfx


def test_off_unless_the_brand_or_the_run_asks(monkeypatch):
    from reelly import brandkit
    monkeypatch.delenv("REELLY_OUTRO_FX", raising=False)
    monkeypatch.setattr(brandkit, "brand", lambda name=None: {})
    assert shaderfx.active() is None
    monkeypatch.setattr(brandkit, "brand", lambda name=None: {"outro_fx": "smoke"})
    assert shaderfx.active() == "smoke"
    monkeypatch.setenv("REELLY_OUTRO_FX", "off")
    assert shaderfx.active() is None                  # the run overrides the kit
    monkeypatch.setenv("REELLY_OUTRO_FX", "lasers")
    assert shaderfx.active() is None                  # unknown effects are refused


def test_page_draws_by_seek_not_by_clock(tmp_path):
    from PIL import Image
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1920), (20, 10, 30)).save(bg)
    html = shaderfx.page_html("smoke", str(bg), 540, 960, "#a87cf0", pulse_t=1.25)
    assert "window.__reellyDraw = function(ms)" in html
    assert "requestAnimationFrame" not in html and "performance.now" not in html
    assert "data:image/jpeg;base64," in html          # no file-origin texture taint
    assert "gl.uniform1f(u('u_pulse'), 1.2500)" in html


def test_seek_calls_the_draw_hook_and_counts_it():
    assert "window.__reellyDraw(ms)" in card._SEEK
    assert "window.__reellyDraw ? 1 : 0" in card._SEEK


def test_only_webgl_pages_get_the_software_gpu(tmp_path):
    assert "--disable-gpu" in card._Browser(str(tmp_path)).gpu_flags
    flags = card._Browser(str(tmp_path), webgl=True).gpu_flags
    assert "--use-angle=swiftshader" in flags and "--disable-gpu" not in flags


def test_outro_uses_the_shader_sequence_as_backdrop(monkeypatch):
    seen = {}
    monkeypatch.setattr(outro.subprocess, "run", lambda cmd, **k: seen.setdefault("cmd", cmd))
    outro.build_segment("b.png", "c.png", 2.8, "o.mp4", fps=60,
                        seq=("/x/k%05d.png", 870), backdrop_seq="/fx/f%05d.png")
    cmd = seen["cmd"]
    i = cmd.index("/fx/f%05d.png")
    assert cmd[i - 1] == "-i" and cmd[i - 3:i - 1] == ["-framerate", "60"]
    assert "b.png" not in cmd and "-loop" in cmd           # the card still loops


def test_shader_failure_falls_back_to_the_still(monkeypatch, tmp_path):
    monkeypatch.setattr(shaderfx, "active", lambda: "smoke")
    def boom(*a, **k):
        raise RuntimeError("no WebGL")
    monkeypatch.setattr(shaderfx, "render", boom)
    plan = {"id": "cut_01", "cta": "Wishlist on Steam", "cta_hot": "Wishlist"}
    assert outro._shader_backdrop("b.png", plan, str(tmp_path), 2.8, 12.0) is None
