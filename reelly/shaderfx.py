"""GPU shader layers for designed moments (the end card), rendered in the
same headless Chrome as the kinetic type.

WHY
The end card is brand space, not gameplay: it can carry motion the footage
never had. A fragment shader (GLSL through WebGL) paints every pixel of every
frame, which is how real-time motion graphics tools (Unreal Motion Design and
Notch in HLSL, TouchDesigner in GLSL) make living backgrounds. Here it costs
nothing: no assets, no paid generation, deterministic frames.

HOW
One page per effect: the dimmed backdrop PNG is a texture, the shader
composites its effect over it, and card._SEEK calls window.__reellyDraw(ms)
so frame n is exactly time n/fps (seeking, never wall clock). Frames render
at half resolution (the effects are soft) and are scaled up in the encode.

PLATFORM (one contract for every effect, every brand, every content type)
- Effect files, reelly/effects/<name>.glsl: an ISF-style JSON header in a
  leading /*{...}*/ comment declares the effect's kind (backdrop | overlay |
  text) and its typed INPUTS (DEFAULT, MIN, MAX, SLOT = a brand colour, HOST
  = filled per frame by Reelly). The host writes the uniform block and
  expands #include "lib/..." so effects never declare or copy plumbing.
- Presets, reelly/effects/presets/<name>.json (brand-neutral, versioned):
  which effect, where it plays (outro | ambient | text), parameter values,
  render options (scale, fps, ...) and a "lite" variant.
- Brand kits choose presets: brand.json "fx": {"outro": "smoke-endcard",
  "ambient": {"preset": "smoke-ambient", "params": {"u_level": 0.3}},
  "text": "smoke-aura"}. Legacy keys (outro_fx / ambient_fx / text_fx:
  "smoke") still work. REELLY_<PLACEMENT>_FX=<preset>|off overrides a run.
- Resolution order per input: effect DEFAULT < brand SLOT colour < preset
  params < brand params < host values; every value is type-checked and
  clamped to MIN/MAX, and an unknown parameter name is refused.
"""
import asyncio
import base64
import json
import os
import re
import shutil
import tempfile

from . import config

EFFECTS_DIR = os.path.join(os.path.dirname(__file__), "effects")
PLACEMENTS = ("outro", "ambient", "text")
KINDS = ("backdrop", "overlay", "text")
TYPES = {"float": "float", "color": "vec3", "vec2": "vec2", "vec3": "vec3",
         "vec4": "vec4", "float[8]": "float"}
# Uniforms the host always provides, per effect kind (never in INPUTS).
HOST_UNIFORMS = {
    "all": ["uniform vec2 u_res;", "uniform float u_t;", "uniform float u_t0;",
            # the control track (reelly/control.py; effects/lib/control.glsl)
            "uniform vec2 u_beat;", "uniform float u_onsets[16];",
            "uniform sampler2D u_ctrl;", "uniform float u_ctrl_n;"],
    "backdrop": ["uniform sampler2D u_bg;"],
    "overlay": [],
    "text": ["uniform sampler2D u_txt;", "uniform sampler2D u_blur;",
             "uniform vec2 u_tsize;", "uniform float u_off;"],
}
# Legacy brand values ("smoke") -> the preset each placement used to mean.
LEGACY = {("outro", "smoke"): "smoke-endcard", ("ambient", "smoke"): "smoke-ambient",
          ("text", "smoke"): "smoke-aura"}


class EffectError(ValueError):
    """A malformed effect, preset or brand fx entry (refused before render)."""


def load_effect(name):
    """(header, glsl body with includes expanded) for reelly/effects/<name>.glsl."""
    if not re.fullmatch(r"[a-z0-9_]+", name or ""):
        raise EffectError(f"bad effect name {name!r}")
    path = os.path.join(EFFECTS_DIR, name + ".glsl")
    if not os.path.exists(path):
        raise EffectError(f"no effect {name!r}")
    src = open(path, encoding="utf-8").read()
    m = re.match(r"\s*/\*(\{.*?\})\*/", src, re.S)
    if not m:
        raise EffectError(f"{name}: missing the /*{{...}}*/ JSON header")
    header = json.loads(m.group(1))
    kind = (header.get("REELLY") or {}).get("kind")
    if kind not in KINDS:
        raise EffectError(f"{name}: REELLY.kind must be one of {KINDS}")
    for inp in header.get("INPUTS", []):
        if inp.get("TYPE") not in TYPES or not re.fullmatch(r"u_[a-z0-9_]+", inp.get("NAME", "")):
            raise EffectError(f"{name}: bad input {inp}")
    body = src[m.end():]

    def inc(mm):
        p = os.path.normpath(os.path.join(EFFECTS_DIR, mm.group(1)))
        if not p.startswith(os.path.join(EFFECTS_DIR, "lib")):
            raise EffectError(f"{name}: includes only from effects/lib")
        return open(p, encoding="utf-8").read()
    body = re.sub(r'#include "([^"]+)"', inc, body)
    return header, body


def fragment(name):
    """The complete fragment shader: precision, host + declared uniforms, body."""
    header, body = load_effect(name)
    kind = header["REELLY"]["kind"]
    decl = HOST_UNIFORMS["all"] + HOST_UNIFORMS[kind]
    for inp in header.get("INPUTS", []):
        t = inp["TYPE"]
        decl.append(f"uniform {TYPES[t]} {inp['NAME']}" + ("[8];" if t == "float[8]" else ";"))
    return "precision highp float;\n" + "\n".join(decl) + "\n" + body


def load_preset(name):
    if not re.fullmatch(r"[a-z0-9-]+", name or ""):
        raise EffectError(f"bad preset name {name!r}")
    path = os.path.join(EFFECTS_DIR, "presets", name + ".json")
    if not os.path.exists(path):
        raise EffectError(f"no preset {name!r}")
    p = json.load(open(path, encoding="utf-8"))
    if p.get("version") != 1 or p.get("placement") not in PLACEMENTS:
        raise EffectError(f"{name}: needs version 1 and a placement in {PLACEMENTS}")
    load_effect(p.get("effect", ""))
    return p


def presets():
    d = os.path.join(EFFECTS_DIR, "presets")
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


def _slot(slot):
    from . import brandkit
    if slot == "accent":
        return brandkit.accent()
    if slot == "ink":
        return brandkit.ink()
    if slot == "surface":
        return brandkit.surface()
    return None


def _coerce(inp, v):
    t = inp["TYPE"]
    if t == "color":
        if isinstance(v, str):
            v = _rgb01(v)
        v = [float(x) for x in v]
        if len(v) != 3:
            raise EffectError(f"{inp['NAME']}: a colour needs 3 values")
        return v
    if t == "float":
        x = float(v)
        lo, hi = inp.get("MIN"), inp.get("MAX")
        if lo is not None:
            x = max(float(lo), x)
        if hi is not None:
            x = min(float(hi), x)
        return x
    n = {"vec2": 2, "vec3": 3, "vec4": 4, "float[8]": 8}[t]
    v = [float(x) for x in v]
    if t == "float[8]":
        v = (v + [-1.0] * 8)[:8]
    if len(v) != n:
        raise EffectError(f"{inp['NAME']}: needs {n} values")
    return v


def uniforms(preset, brand_params=None, host=None, overrides=None):
    """Resolved input values for a preset: DEFAULT < SLOT < preset params <
    brand params < overrides (explicit caller values) < host values."""
    p = load_preset(preset) if isinstance(preset, str) else preset
    header, _ = load_effect(p["effect"])
    inputs = {i["NAME"]: i for i in header.get("INPUTS", [])}
    out = {}
    for name, inp in inputs.items():
        v = inp.get("DEFAULT")
        if inp.get("SLOT"):
            sv = _slot(inp["SLOT"])
            v = sv if sv is not None else v
        out[name] = v
    for layer in (p.get("params") or {}, brand_params or {}, overrides or {}):
        for k, v in layer.items():
            if k not in inputs:
                raise EffectError(f"{p['effect']}: unknown parameter {k!r}")
            if inputs[k].get("HOST") and layer is not overrides:
                raise EffectError(f"{p['effect']}: {k!r} is set by Reelly per frame")
            out[k] = v
    for k, v in (host or {}).items():
        if k in inputs:
            out[k] = v
    return {k: _coerce(inputs[k], v) for k, v in out.items()}


def _brand_entries(placement):
    """[(preset name, brand params)] the brand kit plays at a placement, in
    stack order: "fx": {placement: name | {preset, params} | [those...]},
    else the legacy "<placement>_fx" key."""
    try:
        from . import brandkit
        b = brandkit.brand()
    except Exception:   # noqa: BLE001 -- no kit, no effect
        return []
    entry = (b.get("fx") or {}).get(placement) if isinstance(b.get("fx"), dict) else None
    if entry is None:
        entry = b.get(f"{placement}_fx")
    out = []
    for e in (entry if isinstance(entry, list) else [entry]):
        if isinstance(e, dict):
            out.append((e.get("preset"), e.get("params") or {}))
        elif e:
            out.append((e, {}))
    return out


def _brand_choice(placement):
    e = _brand_entries(placement)
    return e[0] if e else (None, {})


# brand.json "ambient_fx" / REELLY_AMBIENT_FX: the cut-long layer
SCALE = 0.5                  # render at half size; the encode scales back up
_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;background:transparent}}
canvas{{display:block}}</style></head><body>
<canvas id="c" width="{w}" height="{h}"></canvas>
<script>
var gl = document.getElementById('c').getContext('webgl',
  {{preserveDrawingBuffer: true, antialias: false, premultipliedAlpha: false,
    alpha: true}});
window.__reellyReady = false;
function sh(type, src){{ var s = gl.createShader(type); gl.shaderSource(s, src);
  gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
  throw new Error(gl.getShaderInfoLog(s)); return s; }}
var prog = gl.createProgram();
gl.attachShader(prog, sh(gl.VERTEX_SHADER,
  'attribute vec2 a; void main(){{ gl_Position = vec4(a, 0.0, 1.0); }}'));
gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, {frag}));
gl.linkProgram(prog); gl.useProgram(prog);
var buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
var loc = gl.getAttribLocation(prog, 'a'); gl.enableVertexAttribArray(loc);
gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
function u(n){{ return gl.getUniformLocation(prog, n); }}
var U = {uniforms};
Object.keys(U).forEach(function(k){{
  var v = U[k], l = u(k);
  if (!Array.isArray(v)) gl.uniform1f(l, v);
  else if (v.length > 4) gl.uniform1fv(l, new Float32Array(v));
  else gl['uniform' + v.length + 'f'].apply(gl, [l].concat(v));
}});
function draw(ms){{
  gl.uniform1f(u('u_t'), ms / 1000.0);
  gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); gl.finish();
}}
function upload(img){{
  var tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
  return tex;
}}
window.__reellyDraw = draw;
var FR = {frames}, FPS = {fps}, cur = null, blur = null;
if (FR) {{
  // input frames (the kinetic text): load frame n for the instant, then draw
  window.__reellyDraw = function(ms){{
    var i = Math.max(0, Math.min(FR.length - 1, Math.round(ms / 1000 * FPS)));
    return new Promise(function(res){{
      var img = new Image();
      img.onload = function(){{
        if (cur) {{ gl.deleteTexture(cur[0]); gl.deleteTexture(cur[1]); }}
        if (!blur) {{ blur = document.createElement('canvas');
          blur.width = img.width; blur.height = img.height; }}
        var x = blur.getContext('2d');
        x.clearRect(0, 0, blur.width, blur.height);
        x.filter = 'blur({blur_px}px)'; x.drawImage(img, 0, 0); x.filter = 'none';
        gl.activeTexture(gl.TEXTURE1); var tb = upload(blur);
        gl.activeTexture(gl.TEXTURE0); var tt = upload(img);
        cur = [tt, tb];
        gl.uniform1i(u('u_txt'), 0); gl.uniform1i(u('u_blur'), 1);
        draw(ms); res(1);
      }};
      img.onerror = function(){{ res(0); }};
      img.src = FR[i];
    }});
  }};
}}
var bg = {bg}, ctrl = {ctrl}, pending = 0;
function done(){{ if (--pending <= 0) {{ gl.activeTexture(gl.TEXTURE0);
  window.__reellyReady = true; window.__reellyDraw(0); }} }}
function texFrom(src, unit, name){{
  pending++;
  var img = new Image();
  img.onload = function(){{ gl.activeTexture(gl.TEXTURE0 + unit); upload(img);
    gl.uniform1i(u(name), unit); done(); }};
  img.onerror = function(){{ done(); }};
  img.src = src;
}}
if (ctrl) texFrom(ctrl, 2, 'u_ctrl');
if (bg) texFrom(bg, 0, 'u_bg');
if (!pending) {{ pending = 1; done(); }}
</script></body></html>"""

TEXT_OFF = 44                # px of aura room around a kinetic text frame


AMBIENT_SCALE = 0.25         # the veil is very soft: quarter size is plenty
AMBIENT_FPS = 30             # slow smoke; the encode repeats frames for 60


def active_list(kind="outro"):
    """Every preset playing at `kind` for this run, in stack order (bottom
    first): REELLY_<KIND>_FX (comma-separated) or the brand kit. Legacy
    "smoke" maps to the preset it always meant; unknown or misplaced presets
    are dropped with a log line."""
    env = os.environ.get(f"REELLY_{kind.upper()}_FX", "").strip().lower()
    names = [x.strip() for x in env.split(",")] if env else \
        [str(n or "").strip().lower() for n, _ in _brand_entries(kind)]
    out = []
    for v in names:
        if not v or v in ("off", "none", "0"):
            continue
        v = LEGACY.get((kind, v), v)
        try:
            p = load_preset(v)
        except EffectError as e:
            print(f"[fx   ] {kind}: {e}; layer off")
            continue
        if p["placement"] != kind:
            print(f"[fx   ] preset {v!r} plays at {p['placement']}, not {kind}; layer off")
            continue
        out.append(v)
    return out


def active(kind="outro"):
    """The (first) preset playing at `kind` for this run, or None (off)."""
    lst = active_list(kind)
    return lst[0] if lst else None


def brand_params(preset):
    """The brand kit's parameter overrides for a preset."""
    p = load_preset(preset)
    for name, params in _brand_entries(p["placement"]):
        if LEGACY.get((p["placement"], str(name).lower()), str(name).lower()) == preset:
            return params
    return {}


def _rgb01(hex_):
    h = (hex_ or "#a87cf0").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.66, 0.49, 0.94)


def _page(frag, w, h, uniforms, backdrop_png=None, frames=None, fps=60, blur_px=9,
          ctrl=None, t_offset=0.0):
    import io
    import json
    bg = "null"
    if backdrop_png:
        from PIL import Image
        im = Image.open(backdrop_png).convert("RGB").resize((w, h), Image.LANCZOS)
        b = io.BytesIO()
        im.save(b, "JPEG", quality=92)
        bg = json.dumps("data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode())
    from . import control
    uniforms = {**control.host_uniforms(ctrl, t_offset), **uniforms}
    ctrl_png = control.energy_png(ctrl.get("energy")) if ctrl and t_offset == 0 else None
    return _PAGE.format(w=w, h=h, frag=json.dumps(frag), bg=bg,
                        ctrl=json.dumps(ctrl_png) if ctrl_png else "null",
                        frames=json.dumps(frames) if frames else "null", fps=int(fps),
                        blur_px=int(blur_px),
                        uniforms=json.dumps({k: (list(v) if isinstance(v, (list, tuple))
                                                 else round(float(v), 4))
                                             for k, v in uniforms.items()}))


def _render_opts(preset):
    p = load_preset(preset)
    lite = os.environ.get("REELLY_FX_LITE", "").strip().lower() in ("1", "on", "yes")
    return {**(p.get("render") or {}), **(((p.get("lite") or {}).get("render") or {}) if lite else {})}


def page_html(fx, backdrop_png, w, h, accent_hex=None, pulse_t=None, band=0.53, t0=0.0,
              ctrl=None, ctrl_t0=None):
    """The end-card page (kind backdrop): backdrop as a data-URI texture (no
    file-origin taint), the effect, the __reellyDraw hook."""
    over = {"u_accent": accent_hex} if accent_hex else {}
    U = uniforms(fx, brand_params(fx), overrides=over,
                 host={"u_pulse": -1.0 if pulse_t is None else pulse_t, "u_band": band})
    return _page(fragment(load_preset(fx)["effect"]), w, h,
                 {"u_res": (w, h), "u_t0": t0, **U}, backdrop_png, ctrl=ctrl,
                 t_offset=t0 if ctrl_t0 is None else ctrl_t0)


def ambient_html(fx, w, h, accent_hex=None, pic=(0, 0, 0, 0), clear=(0, 0), pops=(),
                 end=0.0, level=None, ctrl=None):
    """The cut-long overlay page: transparent, the effect keeps out of `pic`
    (x0, y0, x1, y1 in uv, y up) and the `clear` band (y0, y1)."""
    over = {"u_accent": accent_hex} if accent_hex else {}
    if level is not None:
        over["u_level"] = level
    U = uniforms(fx, brand_params(fx), overrides=over,
                 host={"u_pic": pic, "u_clear": clear, "u_pops": list(pops)[:8], "u_end": end})
    return _page(fragment(load_preset(fx)["effect"]), w, h,
                 {"u_res": (w, h), "u_t0": 0.0, **U}, ctrl=ctrl)


# One frame, straight off the GPU: seek/draw (awaiting a frame-loading
# page), read the canvas back, hand it out as one base64 blob. No screenshot,
# no compositor, no PNG encode: the probe on the PC measured the full-page
# PNG screenshot at ~0.24 s/frame, the GPU draw + readback at ~1.6 ms.
_GRAB = """(async function(ms){
  var d = window.__reellyDraw(ms); if (d && d.then) { await d; }
  var c = document.querySelector('canvas'); var gl = c.getContext('webgl');
  var px = new Uint8Array(c.width * c.height * 4);
  gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return await new Promise(function(res){ var f = new FileReader();
    f.onload = function(){ res(f.result.slice(f.result.indexOf(',') + 1)); };
    f.readAsDataURL(new Blob([px])); });
})(%f)"""


def _write_tga(path, rgba_bottom_up, w, h):
    """Uncompressed 32-bit TGA with a bottom-left origin: exactly the row
    order readPixels returns, so no flip; ffmpeg reads it with alpha."""
    import numpy as np
    a = np.frombuffer(rgba_bottom_up, np.uint8).reshape(h, w, 4)[..., [2, 1, 0, 3]]
    head = bytes([0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                  w & 255, w >> 8, h & 255, h >> 8, 32, 8])
    with open(path, "wb") as f:
        f.write(head)
        f.write(a.tobytes())


_RENDERER = [None]


async def _log_renderer(b):
    """Log the WebGL renderer once per process; with REELLY_GPU=gpu a silent
    fallback to a software renderer is an error, not a slow surprise."""
    if _RENDERER[0] is not None:
        return
    r = await b._eval("""(function(){ var g = document.createElement('canvas').getContext('webgl');
      if (!g) return 'no WebGL'; var d = g.getExtension('WEBGL_debug_renderer_info');
      return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : g.getParameter(g.RENDERER); })()""")
    _RENDERER[0] = r or "unknown"
    print(f"[gpu  ] {_RENDERER[0]}")
    if os.environ.get("REELLY_GPU", "").strip().lower() == "gpu" and \
            ("swiftshader" in _RENDERER[0].lower() or "no webgl" in _RENDERER[0].lower()):
        raise RuntimeError(f"REELLY_GPU=gpu but Chrome rendered with {_RENDERER[0]}")


async def _shoot_all(page, frames, w, h, out_dir, profile):
    from . import card
    async with card._Browser(profile, webgl=True) as b:
        await b("Emulation.setDeviceMetricsOverride",
                {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
        await b("Emulation.setDefaultBackgroundColorOverride",
                {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        await b.load("file://" + page)
        await _log_renderer(b)
        for _ in range(100):
            if await b._eval("window.__reellyReady === true"):
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("shader page never became ready (no WebGL?)")
        for n, t in frames:
            data = await b._eval(_GRAB % (t * 1000.0), True)
            raw = base64.b64decode(data or "")
            if len(raw) != w * h * 4:
                raise RuntimeError(f"shaderfx: frame {n} read back {len(raw)} bytes")
            _write_tga(os.path.join(out_dir, f"f{n:05d}.tga"), raw, w, h)


def aura_html(fx, frames, tw, th, fps, accent_hex=None, ink_hex=None, pop_t=None,
              enter=True, off=None, t0=0.0):
    """The text page (kind text) over a kinetic frame sequence (file URIs)."""
    ro = _render_opts(fx)
    off = int(ro.get("text_off", TEXT_OFF) if off is None else off)
    over = {k: v for k, v in (("u_accent", accent_hex), ("u_ink", ink_hex)) if v}
    U = uniforms(fx, brand_params(fx), overrides=over,
                 host={"u_pop": -1.0 if pop_t is None else pop_t, "u_enter": 1.0 if enter else 0.0})
    w, h = tw + 2 * off, th + 2 * off
    return _page(fragment(load_preset(fx)["effect"]), w, h,
                 {"u_res": (w, h), "u_tsize": (tw, th), "u_off": off, "u_t0": t0, **U},
                 frames=frames, fps=fps, blur_px=ro.get("blur_px", 12))


def render_aura(fx, in_dir, n, tw, th, fps, out_dir, pop_t=None, enter=True,
                accent_hex=None, t0=0.0):
    """Text-effect frames for a kinetic line: in_dir/k%05d.png (n frames,
    tw x th) -> out_dir/f%05d.png at (tw + 2*off) x (th + 2*off)."""
    from pathlib import Path
    frames = [Path(os.path.join(in_dir, f"k{i:05d}.png")).resolve().as_uri() for i in range(n)]
    off = text_off(fx)
    return _render_page(aura_html(fx, frames, tw, th, fps, accent_hex, pop_t=pop_t,
                                  enter=enter, off=off, t0=t0),
                        out_dir, tw + 2 * off, th + 2 * off, n / fps, fps)


def text_off(fx):
    return int(_render_opts(fx).get("text_off", TEXT_OFF))


def _render_page(html_text, out_dir, w, h, dur, fps):
    os.makedirs(out_dir, exist_ok=True)
    page = os.path.join(out_dir, "fx.html")
    with open(page, "w") as f:
        f.write(html_text)
    n = max(1, int(round(dur * fps)))
    profile = tempfile.mkdtemp(prefix="reelly-chrome-")
    import time
    t0 = time.time()
    try:
        asyncio.run(_shoot_all(page, [(i, i / fps) for i in range(n)], w, h,
                               out_dir, profile))
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    dt = time.time() - t0
    print(f"[fx   ] {n} frames {w}x{h} in {dt:.1f}s ({n / max(dt, 1e-6):.0f} fps)")
    made = len([x for x in os.listdir(out_dir) if x.startswith("f") and x.endswith(".tga")])
    if made < n:
        raise RuntimeError(f"shaderfx: only {made}/{n} frames rendered")
    return os.path.join(out_dir, "f%05d.tga")


def _size(scale):
    return int(1080 * scale) // 2 * 2, int(1920 * scale) // 2 * 2


def render(fx, backdrop_png, out_dir, dur, accent_hex=None, pulse_t=None,
           band=0.53, fps=None, t0=0.0, ctrl=None, ctrl_t0=None):
    """The end card: preset `fx` over the backdrop to out_dir/f%05d.png.
    pulse_t: local time of the ask's pop; band: the ask's height from the
    bottom; t0: where the cut's ambient field left off."""
    w, h = _size(_render_opts(fx).get("scale", SCALE))
    return _render_page(page_html(fx, backdrop_png, w, h, accent_hex, pulse_t, band, t0, ctrl,
                                  ctrl_t0),
                        out_dir, w, h, dur, fps or config.output_fps())


def render_ambient(fx, out_dir, dur, pic, clear, pops=(), accent_hex=None, level=None,
                   ctrl=None):
    """The cut-long overlay (straight alpha) at the preset's scale and fps.
    Returns (pattern, fps)."""
    ro = _render_opts(fx)
    w, h = _size(ro.get("scale", AMBIENT_SCALE))
    fps = int(ro.get("fps", AMBIENT_FPS))
    pat = _render_page(ambient_html(fx, w, h, accent_hex, pic, clear, pops, end=dur, level=level,
                                    ctrl=ctrl),
                       out_dir, w, h, dur, fps)
    return pat, fps
