"""Shader effects platform: effect files, presets, brand choice, GPU tier, and
the three layers built on it (end card, ambient, text)."""
import json
import os

import pytest

from reelly import card, config, outro, shaderfx


def _brand(monkeypatch, data):
    from reelly import brandkit
    monkeypatch.setattr(brandkit, "brand", lambda name=None: data)


# ------------------------------------------------------------- the contract

def test_every_effect_and_preset_validates():
    for name in shaderfx.presets():
        p = shaderfx.load_preset(name)
        header, body = shaderfx.load_effect(p["effect"])
        assert header["LICENSE"] in ("MIT", "CC0", "BSD-3-Clause", "Apache-2.0")
        assert "#include" not in body                       # includes expanded
        frag = shaderfx.fragment(p["effect"])
        assert frag.startswith("precision highp float;") and "void main()" in frag
        for inp in header["INPUTS"]:                          # host declares them all
            assert f" {inp['NAME']}" in frag


def test_settings_resolve_in_order(monkeypatch):
    monkeypatch.delenv("REELLY_AMBIENT_FX", raising=False)
    from reelly import brandkit
    monkeypatch.setattr(brandkit, "accent", lambda: "#ff0000")      # brand slot
    u = shaderfx.uniforms("smoke-ambient")
    assert u["u_accent"] == [1.0, 0.0, 0.0] and u["u_level"] == 0.45   # slot, preset
    u = shaderfx.uniforms("smoke-ambient", brand_params={"u_level": 0.2})
    assert u["u_level"] == 0.2                                         # brand beats preset
    u = shaderfx.uniforms("smoke-ambient", brand_params={"u_level": 0.2},
                          overrides={"u_level": 0.3, "u_accent": "#00ff00"})
    assert u["u_level"] == 0.3 and u["u_accent"] == [0.0, 1.0, 0.0]   # caller beats brand
    u = shaderfx.uniforms("smoke-ambient", host={"u_pops": [1.0, 2.0]})
    assert u["u_pops"] == [1.0, 2.0] + [-1.0] * 6                      # padded array
    assert shaderfx.uniforms("smoke-ambient", brand_params={"u_level": 9})["u_level"] == 1.5


def test_bad_settings_are_refused_before_render():
    with pytest.raises(shaderfx.EffectError):
        shaderfx.uniforms("smoke-ambient", brand_params={"u_glitter": 1})
    with pytest.raises(shaderfx.EffectError):                 # per-frame values are Reelly's
        shaderfx.uniforms("smoke-ambient", brand_params={"u_end": 3})
    with pytest.raises(shaderfx.EffectError):
        shaderfx.load_preset("../../etc")
    with pytest.raises(shaderfx.EffectError):
        shaderfx.load_effect("nope")


def test_includes_only_from_the_effect_library(tmp_path, monkeypatch):
    (tmp_path / "lib").mkdir()
    (tmp_path / "evil.glsl").write_text('/*{"REELLY": {"kind": "overlay"}, "INPUTS": []}*/\n'
                                        '#include "../secret.txt"\nvoid main(){}')
    monkeypatch.setattr(shaderfx, "EFFECTS_DIR", str(tmp_path))
    with pytest.raises(shaderfx.EffectError):
        shaderfx.load_effect("evil")


def test_brand_picks_presets_and_params(monkeypatch):
    for k in ("OUTRO", "AMBIENT", "TEXT"):
        monkeypatch.delenv(f"REELLY_{k}_FX", raising=False)
    _brand(monkeypatch, {"fx": {"ambient": {"preset": "smoke-ambient", "params": {"u_level": 0.2}},
                                "outro": "smoke-endcard"}})
    assert shaderfx.active("ambient") == "smoke-ambient"
    assert shaderfx.brand_params("smoke-ambient") == {"u_level": 0.2}
    assert shaderfx.active("outro") == "smoke-endcard"
    assert shaderfx.active("text") is None


def test_legacy_keys_and_overrides(monkeypatch):
    for k in ("OUTRO", "AMBIENT", "TEXT"):
        monkeypatch.delenv(f"REELLY_{k}_FX", raising=False)
    _brand(monkeypatch, {"outro_fx": "smoke", "ambient_fx": "smoke", "text_fx": "smoke"})
    assert shaderfx.active("outro") == "smoke-endcard"
    assert shaderfx.active("ambient") == "smoke-ambient"
    assert shaderfx.active("text") == "smoke-aura"
    monkeypatch.setenv("REELLY_OUTRO_FX", "off")
    assert shaderfx.active("outro") is None                   # the run overrides the kit
    monkeypatch.setenv("REELLY_OUTRO_FX", "lasers")
    assert shaderfx.active("outro") is None                   # unknown preset: off
    monkeypatch.setenv("REELLY_OUTRO_FX", "smoke-ambient")
    assert shaderfx.active("outro") is None                   # wrong placement: off


def test_lite_variant(monkeypatch):
    monkeypatch.setenv("REELLY_FX_LITE", "1")
    assert shaderfx._render_opts("smoke-endcard")["scale"] == 0.25
    monkeypatch.delenv("REELLY_FX_LITE")
    assert shaderfx._render_opts("smoke-endcard")["scale"] == 0.5


def test_gpu_tier(monkeypatch):
    monkeypatch.setenv("REELLY_GPU", "software")
    assert "--use-angle=swiftshader" in config.webgl_flags()
    monkeypatch.setenv("REELLY_GPU", "gpu")
    flags = config.webgl_flags()
    assert "--use-angle=d3d11" in flags and "--force_high_performance_gpu" in flags
    assert "--disable-gpu" in card._Browser("p").gpu_flags   # DOM text keeps no GPU
    assert card._Browser("p", webgl=True).shared and not card._Browser("p").shared
    assert card._Browser("p", webgl=True).gpu_flags == flags


# ------------------------------------------------------------- the layers

def test_page_draws_by_seek_not_by_clock(tmp_path):
    from PIL import Image
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1080, 1920), (20, 10, 30)).save(bg)
    html = shaderfx.page_html("smoke-endcard", str(bg), 540, 960, "#a87cf0", pulse_t=1.25)
    assert "window.__reellyDraw = BL ? bloomed : draw;" in html
    assert "requestAnimationFrame" not in html and "performance.now" not in html
    assert "data:image/jpeg;base64," in html          # no file-origin texture taint
    assert '"u_pulse": 1.25' in html


def test_seek_calls_the_draw_hook_and_waits_for_it():
    assert "var drawn = window.__reellyDraw ? window.__reellyDraw(ms) : 1;" in card._SEEK
    assert "Promise.all([drawn].concat(" in card._SEEK
    assert "window.__reellyDraw ? 1 : 0" in card._SEEK


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
    monkeypatch.setattr(shaderfx, "active", lambda kind="outro": "smoke-endcard")
    def boom(*a, **k):
        raise RuntimeError("no WebGL")
    monkeypatch.setattr(shaderfx, "render", boom)
    plan = {"id": "cut_01", "cta": "Wishlist on Steam", "cta_hot": "Wishlist"}
    assert outro._shader_backdrop("b.png", plan, str(tmp_path), 2.8, 12.0) is None


def test_ambient_page_keeps_protected_regions_clear():
    html = shaderfx.ambient_html("smoke-ambient", 270, 480, "#a87cf0", pic=(0, 0.34, 1, 0.66),
                                 clear=(0.66, 0.8), pops=[0.55, 4.3], end=15.0)
    assert '"u_pic": [0.0, 0.34, 1.0, 0.66]' in html and '"u_clear": [0.66, 0.8]' in html
    assert '"u_pops": [0.55, 4.3, -1.0' in html
    assert "background:transparent" in html


def test_pop_times_follow_the_beat_grid():
    from reelly import finalize
    plan = {"_beats": (3.6, 0.48),
            "hook": {"text": "Death threats delivered to your door.", "show_s": 3.6,
                     "hot": "delivered"},
            "overlay_lines": [{"t": 8.0, "show_s": 3.0, "text": "Still alive."}]}
    pops = finalize._pop_times(plan)
    assert len(pops) == 2
    for p in pops:
        f = ((p - 3.6) / 0.48) % 1
        assert f == pytest.approx(0, abs=1e-6) or f == pytest.approx(1, abs=1e-6)


def test_ambient_pass_is_skipped_or_falls_back(monkeypatch, tmp_path):
    from reelly import finalize, kinetic
    monkeypatch.setattr(shaderfx, "active", lambda kind="outro": None)
    assert finalize._ambient_pass("raw.mp4", "a.mp4", {}, 10.0, str(tmp_path), None) == "raw.mp4"
    monkeypatch.setattr(shaderfx, "active", lambda kind="outro": "smoke-ambient")
    monkeypatch.setattr(kinetic, "enabled", lambda: True)
    def boom(*a, **k):
        raise RuntimeError("no WebGL")
    monkeypatch.setattr(shaderfx, "render_ambient", boom)
    assert finalize._ambient_pass("raw.mp4", "a.mp4", {"reframe": 1.0}, 10.0,
                                  str(tmp_path), None) == "raw.mp4"


def test_aura_page_reads_the_text_frames_and_a_blurred_copy():
    html = shaderfx.aura_html("smoke-aura", ["file:///w/k00000.png", "file:///w/k00001.png"],
                              900, 220, 60, "#a87cf0", pop_t=1.1)
    assert '"file:///w/k00001.png"' in html and "FPS = 60" in html
    assert "x.filter = 'blur(12px)'" in html          # soft cloud, not glyph copies
    assert '"u_tsize": [900, 220]' in html and '"u_pop": 1.1' in html


def test_text_layer_falls_back_to_plain_frames(monkeypatch, tmp_path):
    from reelly import kinetic
    monkeypatch.setattr(shaderfx, "active", lambda kind="outro": "smoke-aura")
    def boom(*a, **k):
        raise RuntimeError("no WebGL")
    monkeypatch.setattr(shaderfx, "render_aura", boom)
    assert kinetic._aura(str(tmp_path), 10, 900, 220, 60, 0.5, True, "hook") is None
    monkeypatch.setattr(shaderfx, "render_aura", lambda *a, **k: "/a/f%05d.png")
    pat, w, h, pad = kinetic._aura(str(tmp_path), 10, 900, 220, 60, 0.5, True, "hook")
    off = shaderfx.text_off("smoke-aura")
    assert (pat, w, h, pad) == ("/a/f%05d.png", 900 + 2 * off, 220 + 2 * off,
                                kinetic.PAD + off)


def test_frames_are_written_as_bottom_up_tga(tmp_path):
    """readPixels rows come bottom-up: a TGA with a bottom-left origin keeps
    them as they are, and ffmpeg decodes the alpha."""
    import subprocess
    w, h = 4, 2
    bottom = bytes([255, 0, 0, 255]) * w            # first row read = bottom row: red
    top = bytes([0, 0, 255, 128]) * w               # top row: half-transparent blue
    p = tmp_path / "f00000.tga"
    shaderfx._write_tga(str(p), bottom + top, w, h)
    r = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(p), "-f", "rawvideo",
                        "-pix_fmt", "rgba", "-"], capture_output=True, check=True)
    px = r.stdout
    assert px[:4] == bytes([0, 0, 255, 128])       # decoded top-left = top row
    assert px[-4:] == bytes([255, 0, 0, 255])      # decoded bottom-right = bottom row


def test_layers_stack_in_brand_order(monkeypatch):
    monkeypatch.delenv("REELLY_AMBIENT_FX", raising=False)
    _brand(monkeypatch, {"fx": {"ambient": ["smoke-ambient",
                                            {"preset": "embers-ambient", "params": {"u_density": 0.6}},
                                            "lasers"]}})
    assert shaderfx.active_list("ambient") == ["smoke-ambient", "embers-ambient"]
    assert shaderfx.brand_params("embers-ambient") == {"u_density": 0.6}
    monkeypatch.setenv("REELLY_AMBIENT_FX", "embers-ambient,smoke-ambient")
    assert shaderfx.active_list("ambient") == ["embers-ambient", "smoke-ambient"]


def test_control_track_reaches_the_page():
    from reelly import control
    ctrl = {"beats": [3.6, 0.48], "pops": [0.55], "onsets": [1.0, 5.0, 12.0],
            "energy": [0.1, 0.5, 1.0], "fps": 60}
    html = shaderfx.ambient_html("embers-ambient", 270, 480, ctrl=ctrl)
    assert '"u_beat": [3.6, 0.48]' in html and '"u_ctrl_n": 3.0' in html
    assert '"u_onsets": [1.0, 5.0, 12.0, -1.0' in html
    assert "data:image/png;base64," in html                     # the energy texture
    u = control.host_uniforms(ctrl, t_offset=10.0)              # an end card at 10 s
    assert u["u_beat"][0] == -6.4 and u["u_onsets"][0] == 2.0 and u["u_ctrl_n"] == 0.0


def test_control_track_from_real_audio(tmp_path):
    import subprocess
    from reelly import control
    p = str(tmp_path / "hits.wav")
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc='0.02*sin(2*PI*220*t)+if(lt(mod(t,1),0.03),0.9*sin(2*PI*90*t),0)':d=4",
                    p], check=True)
    on = control.onsets(p, 4.0)
    assert 3 <= len(on) <= 5 and all(abs(o - round(o)) < 0.08 for o in on)
    en = control.energy(p, 4.0)
    assert len(en) == 240 and max(en) == 1.0


def test_endcard_opens_with_the_ink_transition(tmp_path):
    from PIL import Image
    bg, frm = tmp_path / "bg.png", tmp_path / "from.png"
    Image.new("RGB", (64, 64), (20, 10, 30)).save(bg)
    Image.new("RGB", (64, 64), (200, 100, 50)).save(frm)
    html = shaderfx.page_html("smoke-endcard", str(bg), 540, 960, from_png=str(frm))
    assert '"u_has_from": 1.0' in html and '"u_trans": 0.55' in html
    assert "texFrom(frm, 1, 'u_from')" in html
    assert "inkWipe" in shaderfx.fragment("smoke_endcard")
    assert '"u_has_from": 0.0' in shaderfx.page_html("smoke-endcard", str(bg), 540, 960)


def test_sparse_layers_render_only_around_events(tmp_path, monkeypatch):
    shot = []

    async def fake(page, frames, w, h, out_dir, profile, writer=None):
        for n, t in frames:
            shot.append(n)
            shaderfx._write_tga(os.path.join(out_dir, f"f{n:05d}.tga"), bytes(w * h * 4), w, h)
    monkeypatch.setattr(shaderfx, "_shoot_all", fake)
    pat, fps = shaderfx.render_ambient("impact-ambient", str(tmp_path), 2.0, (0, 0.3, 1, 0.7),
                                       (0.7, 0.8), ctrl={"beats": [0, 0], "pops": [], "onsets": [],
                                                         "energy": [], "fps": 60,
                                                         "events": [{"t": 1.0, "x": 0.4, "y": 0.8,
                                                                     "s": 1.0}]})
    assert fps == 60 and 14 <= len(shot) <= 19                # ~0.26 s around the impact
    assert len([f for f in os.listdir(tmp_path) if f.startswith("f0") and f.endswith(".tga")]) == 120


def test_impacts_need_sound_and_picture_together(tmp_path):
    """A click with no picture change (a UI blip) is not an impact; a click
    with a flash is, located where the flash is."""
    import subprocess
    from reelly import control
    v = str(tmp_path / "v.mp4")
    # 1080x1920, picture rect rows 656..1264; a flash in its upper-left quarter at 1.0 s and 2.0 s
    lum = "if(between(Y,656,960)*between(X,0,540)*(between(T,1.0,1.06)+between(T,2.0,2.06)),235,40)"
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=black:s=1080x1920:r=60:d=3", "-vf",
                    f"geq=lum='{lum}':cb=128:cr=128", "-pix_fmt", "yuv420p", v], check=True)
    a = str(tmp_path / "a.wav")
    click = "+".join(f"if(between(t,{c},{c}+0.02),0.9*sin(2*PI*1200*t),0)" for c in (0.5, 1.0, 2.0))
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='0.01*sin(2*PI*200*t)+{click}':d=3", a], check=True)
    pic = (0.0, 1 - 1264 / 1920, 1.0, 1 - 656 / 1920)
    ev = control.events(v, a, pic, 3.0)
    ts = [e["t"] for e in ev]
    assert len(ev) == 2 and abs(ts[0] - 1.0) < 0.06 and abs(ts[1] - 2.0) < 0.06, ev
    assert all(e["x"] < 0.5 and e["y"] > 0.5 for e in ev)      # upper-left of the picture


def test_vision_check_keeps_only_real_hits_and_moves_them(monkeypatch, tmp_path):
    from reelly import control, direct
    cands = [{"t": 1.0, "x": 0.9, "y": 0.9, "s": 1.0}, {"t": 2.0, "x": 0.5, "y": 0.5, "s": 0.6}]
    monkeypatch.setattr(control, "_sheet", lambda v, t, pic, path, w=480: path)
    monkeypatch.setattr(direct, "resolve_brain", lambda b: "codex")
    calls = []

    def ask(tool, prompt, project, label, images=None):
        calls.append(images)
        return {"moments": [{"i": 0, "impact": True, "cell": "B4", "what": "meteor lands"},
                            {"i": 1, "impact": False, "cell": None, "what": "combo text"}]}
    monkeypatch.setattr(direct, "_ask_cli", ask)
    cache = str(tmp_path / "impacts.json")
    ev = control.verify_events("v.mp4", cands, (0, 0.34, 1, 0.66), str(tmp_path), "p", cache)
    assert [e["t"] for e in ev] == [1.0]
    assert ev[0]["x"] == 0.188 and ev[0]["y"] == 0.125      # cell B4 (bottom left)
    assert len(calls) == 1 and len(calls[0]) == 2           # one call, one image per candidate
    again = control.verify_events("v.mp4", cands, (0, 0.34, 1, 0.66), str(tmp_path), "p", cache)
    assert again == ev and len(calls) == 1                  # cached: no second call


def test_bloom_is_a_preset_setting(monkeypatch):
    monkeypatch.delenv("REELLY_FX_LITE", raising=False)
    html = shaderfx.ambient_html("embers-ambient", 540, 960)
    assert '"threshold": 0.12' in html and "window.__reellyDraw = BL ? bloomed : draw;" in html
    assert '"sigma": 6.0' in html                         # 12 px at 1080 wide, scaled to 540
    assert "var BL = null" in shaderfx.ambient_html("smoke-ambient", 270, 480)
    monkeypatch.setenv("REELLY_FX_LITE", "1")
    assert "var BL = null" in shaderfx.ambient_html("embers-ambient", 270, 480)   # lite: off


def test_every_generated_page_script_parses(tmp_path):
    """A page whose script does not parse never becomes ready and silently
    falls back to a lesser render (a Python '\\n' inside a JS string did
    this twice). Every page kind goes through Node's syntax check."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    from PIL import Image
    from reelly import gputext, kinetic
    bg = tmp_path / "bg.png"
    Image.new("RGB", (64, 64)).save(bg)
    t = "Death threats delivered to your door."
    lines = kinetic.wrap_lines(t, 58, 800, "headline")
    pages = {
        "endcard": shaderfx.page_html("smoke-endcard", str(bg), 540, 960, from_png=str(bg)),
        "embers": shaderfx.ambient_html("embers-ambient", 540, 960),
        "impact": shaderfx.ambient_html("impact-ambient", 1080, 1920),
        "aura": shaderfx.aura_html("smoke-aura", ["file:///k0.png"], 900, 220, 60),
        "text": gputext.page(t, 58, 856, 227, 3.6, "headline", 2, lines, True, True, 1.0,
                             "#FCFCFB", "#a87cf0")[0],
        "text+aura": gputext.page(t, 58, 856, 227, 3.6, "headline", 2, lines, False, True,
                                  0.5, "#FCFCFB", "#a87cf0", aura="smoke-aura")[0],
        "finish": shaderfx._page(shaderfx.fragment("folk_print"), 540, 960,
                                 {"u_res": (540, 960), "u_t0": 0.0,
                                  **shaderfx.uniforms("folk-print", host={"u_pic": (0, .34, 1, .66),
                                                                          "u_clear": (.66, .8)})},
                                 frames=["file:///s0.jpg"], fps=60, blur_px=1),
    }
    for name, html in pages.items():
        js = tmp_path / f"{name}.js"
        js.write_text(html[html.index("<script>") + 8:html.rindex("</script>")])
        r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
        assert r.returncode == 0, f"{name}: {r.stderr[:300]}"


def test_finish_is_its_own_placement(monkeypatch, tmp_path):
    from reelly import finalize, kinetic
    monkeypatch.delenv("REELLY_FINISH_FX", raising=False)
    _brand(monkeypatch, {"fx": {"finish": "folk-print"}})
    assert shaderfx.active("finish") == "folk-print"
    assert shaderfx.active("ambient") is None
    u = shaderfx.uniforms("folk-print")
    assert {"u_dark", "u_mid", "u_light", "u_mix"} <= set(u)
    # off or failing: the cut goes on without it
    monkeypatch.setattr(kinetic, "enabled", lambda: True)
    monkeypatch.setattr(shaderfx, "render_finish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(finalize.subprocess, "run", lambda *a, **k: None)
    assert finalize._finish_pass("raw.mp4", "f.mp4", {"reframe": 1.0}, 10.0, str(tmp_path), None) == "raw.mp4"


def test_content_profiles_filter_effects(monkeypatch):
    monkeypatch.delenv("REELLY_PROFILE", raising=False)
    monkeypatch.delenv("REELLY_AMBIENT_FX", raising=False)
    assert set(shaderfx.profiles()) >= {"gameplay", "talking_head", "podcast", "screen_demo",
                                        "live_action", "stills", "quote_card"}
    for name in shaderfx.profiles():
        p = shaderfx.load_profile(name)
        assert p["version"] == 1 and "allow" in p and "drivers" in p
    _brand(monkeypatch, {"fx": {"ambient": ["smoke-ambient", "impact-ambient"]}})
    shaderfx.set_profile(None)
    assert shaderfx.profile_name({}) == "gameplay"                    # the default
    assert shaderfx.profile_name({"composition": {"cam": "split"}}) == "talking_head"
    assert shaderfx.active_list("ambient") == ["smoke-ambient", "impact-ambient"]
    shaderfx.set_profile("podcast")
    assert shaderfx.active_list("ambient") == ["smoke-ambient"]      # no hit marks on people
    assert not shaderfx.drives("events") and shaderfx.drives("pops")
    shaderfx.set_profile("live_action")
    assert shaderfx.active_list("ambient") == []                      # no bars to decorate
    monkeypatch.setenv("REELLY_PROFILE", "gameplay")
    assert shaderfx.active_list("ambient") == ["smoke-ambient", "impact-ambient"]
    shaderfx.set_profile(None)
    with pytest.raises(shaderfx.EffectError):
        shaderfx.load_profile("opera")
