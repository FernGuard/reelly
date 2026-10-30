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
            "uniform sampler2D u_ctrl;", "uniform float u_ctrl_n;",
            # real impacts: (t, x, y, strength), x/y in picture uv (control.events)
            "uniform vec4 u_events[8];"],
    "backdrop": ["uniform sampler2D u_bg;", "uniform sampler2D u_from;"],
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
window.onerror = function(m){{ window.__reellyError = String(m); }};
var gl = document.getElementById('c').getContext('webgl',
  {{preserveDrawingBuffer: true, antialias: false, premultipliedAlpha: false,
    alpha: true}});
if (!gl) {{ window.__reellyError = 'NO_WEBGL_CONTEXT'; throw new Error('no WebGL context'); }}
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
  else if (k === 'u_events') gl.uniform4fv(l, new Float32Array(v));
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
var BL = {bloom};
if (BL) {{
  // bloom: the effect renders into a texture; its bright part is blurred
  // (separable Gaussian) and added back, then written to the canvas
  var VSQ = 'attribute vec2 a; void main(){{ gl_Position = vec4(a, 0.0, 1.0); }}';
  function prog2(fs){{ var p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, VSQ));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p)); return p; }}
  var BLUR = 'precision highp float; uniform sampler2D s; uniform vec2 r; uniform vec2 dir; uniform float sg; uniform float thr; uniform float ext;\\n' +
    'vec3 pick(vec2 uv){{ vec4 c = texture2D(s, uv); vec3 pm = c.rgb * (ext > 0.5 ? c.a : 1.0);\\n' +
    '  return ext > 0.5 ? max(pm - vec3(thr), vec3(0.0)) : c.rgb; }}\\n' +
    'void main(){{ vec2 uv = gl_FragCoord.xy / r; vec3 acc = vec3(0.0); float ws = 0.0;\\n' +
    '  for (int i = -24; i <= 24; i++){{ float x = float(i) * sg / 8.0; float w = exp(-0.5 * x * x / (sg * sg));\\n' +
    '    acc += pick(uv + dir * x / r) * w; ws += w; }}\\n' +
    '  gl_FragColor = vec4(acc / ws, 1.0); }}';
  var COMP = 'precision highp float; uniform sampler2D a; uniform sampler2D g; uniform vec2 r; uniform float k;\\n' +
    'void main(){{ vec2 uv = gl_FragCoord.xy / r; vec4 c = texture2D(a, uv); vec3 gl3 = texture2D(g, uv).rgb * k;\\n' +
    '  vec3 pm = c.rgb * c.a + gl3; float al = clamp(max(c.a, max(gl3.r, max(gl3.g, gl3.b))), 0.0, 1.0);\\n' +
    '  gl_FragColor = vec4(al > 0.0 ? min(pm / al, vec3(1.0)) : vec3(0.0), al); }}';
  function tgt(){{ var t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, {w}, {h}, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    var f = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, f);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null); return [t, f]; }}
  BL.blur = prog2(BLUR); BL.comp = prog2(COMP); BL.A = tgt(); BL.B = tgt(); BL.C = tgt();
  gl.activeTexture(gl.TEXTURE0);
}}
function quad(p){{ var l = gl.getAttribLocation(p, 'a'); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, 2, gl.FLOAT, false, 0, 0); }}
function bloomed(ms){{
  gl.useProgram(prog); quad(prog);
  gl.bindFramebuffer(gl.FRAMEBUFFER, BL.A[1]); gl.viewport(0, 0, {w}, {h});
  draw(ms);
  var bu = function(n){{ return gl.getUniformLocation(BL.blur, n); }};
  gl.useProgram(BL.blur); quad(BL.blur);
  gl.uniform2f(bu('r'), {w}, {h}); gl.uniform1f(bu('sg'), BL.sigma); gl.uniform1f(bu('thr'), BL.threshold);
  gl.uniform1i(bu('s'), 5); gl.activeTexture(gl.TEXTURE5);
  gl.bindFramebuffer(gl.FRAMEBUFFER, BL.B[1]); gl.bindTexture(gl.TEXTURE_2D, BL.A[0]);
  gl.uniform2f(bu('dir'), 1, 0); gl.uniform1f(bu('ext'), 1); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  gl.bindFramebuffer(gl.FRAMEBUFFER, BL.C[1]); gl.bindTexture(gl.TEXTURE_2D, BL.B[0]);
  gl.uniform2f(bu('dir'), 0, 1); gl.uniform1f(bu('ext'), 0); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  gl.bindTexture(gl.TEXTURE_2D, null);
  var cu = function(n){{ return gl.getUniformLocation(BL.comp, n); }};
  gl.useProgram(BL.comp); quad(BL.comp);
  gl.bindFramebuffer(gl.FRAMEBUFFER, null);
  gl.uniform2f(cu('r'), {w}, {h}); gl.uniform1f(cu('k'), BL.strength);
  gl.activeTexture(gl.TEXTURE5); gl.bindTexture(gl.TEXTURE_2D, BL.A[0]); gl.uniform1i(cu('a'), 5);
  gl.activeTexture(gl.TEXTURE6); gl.bindTexture(gl.TEXTURE_2D, BL.C[0]); gl.uniform1i(cu('g'), 6);
  gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  gl.bindTexture(gl.TEXTURE_2D, null); gl.activeTexture(gl.TEXTURE0);
  gl.useProgram(prog); quad(prog); gl.finish();
}}
window.__reellyDraw = BL ? bloomed : draw;
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
var bg = {bg}, ctrl = {ctrl}, frm = {frm}, pending = 0;
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
if (frm) texFrom(frm, 1, 'u_from');
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
          ctrl=None, t_offset=0.0, from_png=None, bloom=None):
    import io
    import json
    def data_uri(png):
        from PIL import Image
        im = Image.open(png).convert("RGB").resize((w, h), Image.LANCZOS)
        b = io.BytesIO()
        im.save(b, "JPEG", quality=94)
        return json.dumps("data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode())
    bg = data_uri(backdrop_png) if backdrop_png else "null"
    frm = data_uri(from_png) if from_png else "null"
    from . import control
    uniforms = {**control.host_uniforms(ctrl, t_offset), **uniforms}
    ctrl_png = control.energy_png(ctrl.get("energy")) if ctrl and t_offset == 0 else None
    return _PAGE.format(w=w, h=h, frag=json.dumps(frag), bg=bg,
                        ctrl=json.dumps(ctrl_png) if ctrl_png else "null", frm=frm,
                        bloom=json.dumps(_bloom(bloom, w)) if bloom and not frames else "null",
                        frames=json.dumps(frames) if frames else "null", fps=int(fps),
                        blur_px=int(blur_px),
                        uniforms=json.dumps({k: (list(v) if isinstance(v, (list, tuple))
                                                 else round(float(v), 4))
                                             for k, v in uniforms.items()}))


def _bloom(b, w):
    """Preset bloom settings, sigma scaled to the layer's render size (the
    preset states it for a 1080-wide frame)."""
    return {"threshold": float(b.get("threshold", 0.5)), "strength": float(b.get("strength", 1.0)),
            "sigma": max(1.0, float(b.get("sigma", 12.0)) * w / 1080.0)}


def _render_opts(preset):
    p = load_preset(preset)
    lite = os.environ.get("REELLY_FX_LITE", "").strip().lower() in ("1", "on", "yes")
    return {**(p.get("render") or {}), **(((p.get("lite") or {}).get("render") or {}) if lite else {})}


def page_html(fx, backdrop_png, w, h, accent_hex=None, pulse_t=None, band=0.53, t0=0.0,
              ctrl=None, ctrl_t0=None, from_png=None):
    """The end-card page (kind backdrop): backdrop as a data-URI texture (no
    file-origin taint), the effect, the __reellyDraw hook."""
    over = {"u_accent": accent_hex} if accent_hex else {}
    U = uniforms(fx, brand_params(fx), overrides=over,
                 host={"u_pulse": -1.0 if pulse_t is None else pulse_t, "u_band": band,
                       "u_has_from": 1.0 if from_png else 0.0})
    return _page(fragment(load_preset(fx)["effect"]), w, h,
                 {"u_res": (w, h), "u_t0": t0, **U}, backdrop_png, ctrl=ctrl,
                 t_offset=t0 if ctrl_t0 is None else ctrl_t0, from_png=from_png,
                 bloom=_render_opts(fx).get("bloom"))


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
                 {"u_res": (w, h), "u_t0": 0.0, **U}, ctrl=ctrl,
                 bloom=_render_opts(fx).get("bloom"))


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


# The same frame, sent as raw bytes over a local WebSocket instead of
# base64 through the DevTools socket (measured 7-37 fps that way: the GPU
# itself draws + reads back in ~1.6 ms).
_GRAB_WS = """(async function(ms){
  var d = window.__reellyDraw(ms); if (d && d.then) { await d; }
  var c = document.querySelector('canvas'); var gl = c.getContext('webgl');
  var px = new Uint8Array(c.width * c.height * 4);
  gl.readPixels(0, 0, c.width, c.height, gl.RGBA, gl.UNSIGNED_BYTE, px);
  window.__reellyWs.send(px.buffer);
  return 1;
})(%f)"""


async def _open_pipe(b):
    """A local binary WebSocket the page sends frames into: (queue, server),
    or (None, None) when it cannot open (the base64 path is used)."""
    try:
        import websockets
        q = asyncio.Queue()

        async def handler(ws):
            async for msg in ws:
                await q.put(msg)
        server = await websockets.serve(handler, "127.0.0.1", 0, max_size=None)
        port = list(server.sockets)[0].getsockname()[1]
        ok = await b._eval(f"""new Promise(function(res){{
          var ws = new WebSocket('ws://127.0.0.1:{port}'); ws.binaryType = 'arraybuffer';
          window.__reellyWs = ws; ws.onopen = function(){{ res(1); }};
          ws.onerror = function(){{ res(0); }}; setTimeout(function(){{ res(0); }}, 5000); }})""", True)
        if not ok:
            server.close()
            return None, None
        return q, server
    except Exception as e:   # noqa: BLE001 -- the slower path still works
        print(f"[fx   ] binary frame pipe unavailable ({e}); base64 frames")
        return None, None


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


async def _shoot_all(page, frames, w, h, out_dir, profile, writer=None):
    from . import card
    async with card._Browser(profile, webgl=True) as b:
        await b("Emulation.setDeviceMetricsOverride",
                {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
        await b("Emulation.setDefaultBackgroundColorOverride",
                {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        await b.load("file://" + page)
        await _log_renderer(b)
        for _ in range(400):                       # up to 20 s (GPU process start)
            if await b._eval("window.__reellyReady === true"):
                break
            err = await b._eval("window.__reellyError || ''")
            if err:
                raise RuntimeError(f"shader page error: {err}")
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("shader page never became ready (no WebGL?)")
        q, server = await _open_pipe(b)
        try:
            for n, t in frames:
                if q is not None:
                    await b._eval(_GRAB_WS % (t * 1000.0), True)
                    raw = await asyncio.wait_for(q.get(), 30)
                else:
                    raw = base64.b64decode(await b._eval(_GRAB % (t * 1000.0), True) or "")
                if len(raw) != w * h * 4:
                    raise RuntimeError(f"shaderfx: frame {n} read back {len(raw)} bytes")
                if writer:
                    writer(n, raw)
                else:
                    _write_tga(os.path.join(out_dir, f"f{n:05d}.tga"), raw, w, h)
        finally:
            if server is not None:
                server.close()


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


def _blank_tga(path, w, h):
    _write_tga(path, bytes(w * h * 4), w, h)
    return path


import threading
# One GPU browser at a time: cuts finalize in parallel, and several headless
# Chromes starting their GPU processes together lost WebGL contexts on the
# worker. Each layer is short on the GPU, so taking turns costs little.
_GPU_LOCK = threading.Lock()


def _no_context(e):
    return "NO_WEBGL_CONTEXT" in str(e) or "no WebGL context" in str(e)


def _render_page(html_text, out_dir, w, h, dur, fps, windows=None):
    """Render a page's frames to out_dir/f%05d.tga (see render_frames)."""
    return render_frames(html_text, out_dir, w, h, dur, fps, windows=windows)


def _link(src, dst):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copyfile(src, dst)


def render_frames(html_text, out_dir, w, h, dur, fps, windows=None, writer=None,
                  pattern="f%05d.tga", still=None):
    """Render a page's frames (n = dur * fps) to out_dir/<pattern>.
    windows: [(t0, t1)] when the layer is SPARSE (only those instants can
    show anything): the other frames hard-link one transparent frame.
    still: (t0, t1) during which the page does not change: rendered once,
    the rest hard-linked. writer(n, raw_rgba_bottom_up) writes a frame
    (default: TGA)."""
    os.makedirs(out_dir, exist_ok=True)
    page = os.path.join(out_dir, "fx.html")
    with open(page, "w") as f:
        f.write(html_text)
    n = max(1, int(round(dur * fps)))
    todo = [(i, i / fps) for i in range(n)]
    links = {}                                   # frame -> the frame it copies
    if windows is not None:
        todo = [(i, t) for i, t in todo if any(a - 1.0 / fps <= t <= b for a, b in windows)]
        blank = _blank_tga(os.path.join(out_dir, "blank.tga"), w, h)
        done = {i for i, _ in todo}
        for i in range(n):
            if i not in done:
                _link(blank, os.path.join(out_dir, pattern % i))
    if still is not None:
        first = None
        keep = []
        for i, t in todo:
            if still[0] <= t < still[1]:
                if first is None:
                    first = i
                    keep.append((i, t))
                else:
                    links[i] = first
            else:
                keep.append((i, t))
        todo = keep
    profile = tempfile.mkdtemp(prefix="reelly-chrome-")
    import time
    t0 = time.time()
    try:
        if todo:
            with _GPU_LOCK:
                try:
                    asyncio.run(_shoot_all(page, todo, w, h, out_dir, profile, writer))
                except RuntimeError as e:
                    if "shader page error" in str(e) and not _no_context(e):
                        raise                              # a broken effect: no retry
                    for attempt in (1, 2):             # the GPU process can lose a start race
                        print(f"[fx   ] {e}; retry {attempt} in a fresh browser")
                        from . import card
                        card.reset_shared()
                        shutil.rmtree(profile, ignore_errors=True)
                        profile = tempfile.mkdtemp(prefix="reelly-chrome-")
                        time.sleep(1.0 * attempt)
                        try:
                            asyncio.run(_shoot_all(page, todo, w, h, out_dir, profile, writer))
                            break
                        except RuntimeError as e2:
                            if attempt == 2 or ("shader page error" in str(e2)
                                                and not _no_context(e2)):
                                raise
                            e = e2
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    dt = time.time() - t0
    print(f"[fx   ] {len(todo)} of {n} frames {w}x{h} in {dt:.1f}s"
          f" ({len(todo) / max(dt, 1e-6):.0f} fps)")
    for i, src in links.items():
        _link(os.path.join(out_dir, pattern % src), os.path.join(out_dir, pattern % i))
    made = sum(1 for i in range(n) if os.path.exists(os.path.join(out_dir, pattern % i)))
    if made < n:
        raise RuntimeError(f"shaderfx: only {made}/{n} frames rendered")
    return os.path.join(out_dir, pattern)


def _size(scale):
    return int(1080 * scale) // 2 * 2, int(1920 * scale) // 2 * 2


def render(fx, backdrop_png, out_dir, dur, accent_hex=None, pulse_t=None,
           band=0.53, fps=None, t0=0.0, ctrl=None, ctrl_t0=None, from_png=None):
    """The end card: preset `fx` over the backdrop to out_dir/f%05d.png.
    pulse_t: local time of the ask's pop; band: the ask's height from the
    bottom; t0: where the cut's ambient field left off."""
    w, h = _size(_render_opts(fx).get("scale", SCALE))
    return _render_page(page_html(fx, backdrop_png, w, h, accent_hex, pulse_t, band, t0, ctrl,
                                  ctrl_t0, from_png),
                        out_dir, w, h, dur, fps or config.output_fps())


def render_ambient(fx, out_dir, dur, pic, clear, pops=(), accent_hex=None, level=None,
                   ctrl=None):
    """The cut-long overlay (straight alpha) at the preset's scale and fps.
    Returns (pattern, fps)."""
    ro = _render_opts(fx)
    w, h = _size(ro.get("scale", AMBIENT_SCALE))
    fps = int(ro.get("fps", AMBIENT_FPS))
    windows = None
    hold = uniforms(fx, brand_params(fx)).get("u_hold", 0.2)
    if ro.get("sparse") == "onsets":
        windows = [(o, o + hold) for o in (ctrl or {}).get("onsets", [])]
    elif ro.get("sparse") == "events":
        # only the events this layer reacts to (its own u_min_s, if any)
        min_s = uniforms(fx, brand_params(fx)).get("u_min_s", 0.0)
        windows = [(e["t"], e["t"] + hold) for e in (ctrl or {}).get("events", [])
                   if e.get("s", 1.0) >= min_s]
    pat = _render_page(ambient_html(fx, w, h, accent_hex, pic, clear, pops, end=dur, level=level,
                                    ctrl=ctrl),
                       out_dir, w, h, dur, fps, windows=windows)
    return pat, fps
