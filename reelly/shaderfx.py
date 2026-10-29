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

brand.json "outro_fx": "smoke" turns it on for a brand; REELLY_OUTRO_FX
(smoke | off) overrides per run.
"""
import asyncio
import base64
import os
import shutil
import tempfile

from . import config

SCALE = 0.5                  # render at half size; the encode scales back up
FX_NAMES = ("smoke",)

_NOISE = r"""
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p){
  vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p){
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 5; i++){ v += a * noise(p); p = p * 2.03 + vec2(1.7, 9.2); a *= 0.5; }
  return v;
}
// The smoke field at uv and absolute time T (one field for the whole video:
// the cut's ambient layer and the end card sample the same smoke).
float smoke(vec2 uv, float T){
  vec2 p = vec2(uv.x * u_res.x / u_res.y, uv.y) * 3.2;
  float t = T * 0.32;
  vec2 q = vec2(fbm(p + vec2(0.0, -t)), fbm(p + vec2(5.2, 1.3 - t)));
  vec2 r = vec2(fbm(p + 3.6 * q + vec2(1.7, 9.2) - vec2(0.0, 1.4 * t)),
                fbm(p + 3.6 * q + vec2(8.3, 2.8)));
  return fbm(p + 3.6 * r);
}
"""

# Cursed smoke end card: the field rising from the bottom, thinner across the
# ask's band so the type stays clean, one smoke-torn ring of light when the
# ask's action word pops (u_pulse, beat-timed), additive over the dimmed
# final frame. u_t0 continues the cut's ambient smoke without a jump.
_SMOKE = r"""
precision highp float;
uniform vec2 u_res; uniform float u_t; uniform float u_t0; uniform float u_pulse;
uniform float u_band; uniform vec3 u_accent; uniform sampler2D u_bg;
""" + _NOISE + r"""
void main(){
  vec2 uv = gl_FragCoord.xy / u_res;                 // y up
  float f = smoke(uv, u_t + u_t0);
  float rise = smoothstep(1.1, 0.05, uv.y);          // denser low, gone at the top
  float band = 1.0 - 0.6 * exp(-pow((uv.y - u_band) / 0.075, 2.0));
  float d = pow(f, 2.4) * rise * band;
  float ring = 0.0;
  float dt = u_t - u_pulse;
  if (u_pulse >= 0.0 && dt > 0.0 && dt < 1.3){
    vec2 rel = (uv - vec2(0.5, u_band)) * vec2(u_res.x / u_res.y, 1.0);
    // the ring is torn by the same smoke: a shockwave, not a UI circle
    float dist = length(rel) + 0.06 * (f - 0.5) + 0.03 * (fbm(rel * 9.0 + u_t) - 0.5);
    ring = exp(-pow((dist - dt * 0.5) / 0.05, 2.0)) * (1.0 - dt / 1.3) * (0.55 + 0.9 * f);
    d += 0.25 * exp(-dt * 3.0) * rise;                // the smoke swells with it
  }
  // continuing the cut's smoke (u_t0 > 0) the field is already there:
  // it only needs to spread over the picture, not appear from nothing
  float fade = smoothstep(u_t0 > 0.0 ? -0.35 : 0.0, 0.45, u_t);
  vec3 bg = texture2D(u_bg, vec2(uv.x, 1.0 - uv.y)).rgb;
  vec3 col = bg + u_accent * (1.2 * d + 0.7 * ring) * fade;
  gl_FragColor = vec4(min(col, vec3(1.0)), 1.0);
}
"""

# Ambient smoke for the whole cut: only OUTSIDE the game picture (u_pic,
# the picture rect in uv, y up) and outside the text band above it (u_clear),
# denser low, a light veil at the top, a swell on each beat-timed pop
# (u_pops), building over the last 2 s into the end card (u_end). Straight
# alpha over transparent: the footage is never tinted.
_AMBIENT = r"""
precision highp float;
uniform vec2 u_res; uniform float u_t; uniform float u_t0; uniform vec4 u_pic;
uniform vec2 u_clear; uniform float u_pops[8]; uniform float u_end;
uniform float u_level; uniform vec3 u_accent;
""" + _NOISE + r"""
void main(){
  vec2 uv = gl_FragCoord.xy / u_res;
  float f = smoke(uv, u_t + u_t0);
  float below = 1.0 - smoothstep(0.0, u_pic.y, uv.y);      // 1 at the frame bottom
  float above = smoothstep(u_pic.w, 1.0, uv.y);             // 1 at the frame top
  float edge = 0.012;
  float outside = 1.0 - smoothstep(u_pic.y - edge, u_pic.y, uv.y)
                      * (1.0 - smoothstep(u_pic.w, u_pic.w + edge, uv.y));
  float c = (u_clear.x + u_clear.y) * 0.5, hw = (u_clear.y - u_clear.x) * 0.5 + 0.02;
  float clear = 1.0 - exp(-pow((uv.y - c) / hw, 6.0));
  float swell = 0.0;
  for (int i = 0; i < 8; i++){
    float dt = u_t - u_pops[i];
    if (u_pops[i] >= 0.0 && dt > 0.0 && dt < 1.2) swell += exp(-dt * 3.5);
  }
  float ramp = smoothstep(u_end - 2.0, u_end, u_t);
  float amount = u_level * (1.0 + 0.6 * swell) + 0.55 * ramp;
  float d = pow(f, 2.2) * (1.5 * pow(below, 0.7) + 0.55 * pow(above, 1.3));
  float a = clamp(d * amount * outside * clear * 1.6, 0.0, 0.85);
  gl_FragColor = vec4(u_accent, a);
}
"""

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
  else if (k === 'u_pops') gl.uniform1fv(l, new Float32Array(v));
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
var bg = {bg};
if (!bg) {{ window.__reellyReady = true; window.__reellyDraw(0); }}
else {{
  var img = new Image();
  img.onload = function(){{
    var tex = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, gl.RGB, gl.UNSIGNED_BYTE, img);
    gl.uniform1i(u('u_bg'), 0);
    window.__reellyReady = true; window.__reellyDraw(0);
  }};
  img.src = bg;
}}
</script></body></html>"""

# Text aura: reads each rendered kinetic text frame (u_txt) and builds
# effects FROM the letters' own shape, so the type stays the pixel-exact
# kinetic render (readable on frame 0, the footprint the layout gates
# measured) and only the space around it moves:
#   - dark smoky wisps rise off the letters (samples of the text alpha below
#     each pixel, warped by the smoke field), strongest while the line
#     arrives (u_enter), then lingering; their dark body also lifts the
#     type off busy footage
#   - on the beat-timed pop (u_pop) the hot word, found by its accent
#     colour, throws an accent flare
# The canvas is the text frame plus u_off px on every side.
_AURA = r"""
precision highp float;
uniform vec2 u_res; uniform vec2 u_tsize; uniform float u_off; uniform float u_t;
uniform float u_pop; uniform float u_enter; uniform vec3 u_accent; uniform vec3 u_ink;
uniform float u_t0; uniform sampler2D u_txt; uniform sampler2D u_blur;
""" + _NOISE + r"""
vec2 tq(vec2 px, out float inside){                  // canvas px (y up) -> text uv
  vec2 q = px - vec2(u_off);
  inside = step(0.0, q.x) * step(0.0, q.y) * step(q.x, u_tsize.x) * step(q.y, u_tsize.y);
  return vec2(q.x / u_tsize.x, 1.0 - q.y / u_tsize.y);
}
vec4 txt(vec2 px){ float i; vec2 q = tq(px, i); return texture2D(u_txt, q) * i; }
// the same frame, Gaussian-blurred by the browser: smoke and flare come from
// this soft cloud, never from offset copies of the sharp glyphs
vec4 blr(vec2 px){ float i; vec2 q = tq(px, i); return texture2D(u_blur, q) * i; }
float hot(vec4 c){ return c.a * (1.0 - smoothstep(0.12, 0.40, distance(c.rgb, u_accent))); }
void main(){
  vec2 px = gl_FragCoord.xy;
  vec2 uv = px / u_res;
  float T = u_t + u_t0;
  vec4 me = txt(px);
  // rising wisps: text alpha sampled BELOW this pixel, sideways warped
  float wisp = 0.0, ember = 0.0, wsum = 0.0, esum = 0.0;
  for (int i = 1; i <= 10; i++){
    float k = float(i);
    vec2 at = px + vec2(0.0, -k * 3.2);
    float w = fbm(vec2(at.x * 0.018, at.y * 0.012 - T * 0.9));
    at.x += (w - 0.5) * k * 5.0;
    vec4 c = blr(at);
    float fall = exp(-k * 0.22);
    wisp += c.a * fall; wsum += fall;
    // embers: only the far samples, so they rise ABOVE the word and never
    // smear across its strokes (a near-sample flare read as a melted double)
    if (i >= 5) { ember += hot(c) * fall; esum += fall; }
  }
  wisp /= wsum; ember /= max(esum, 1e-4);
  // soft halos: a tight one (the dark body that lifts the type) and a wide
  // one around the hot word (the flare)
  vec4 b = blr(px);
  float halo = b.a;
  float hhot = hot(b);
  float n = fbm(uv * vec2(6.0, 3.0) + vec2(0.0, -T * 0.6));
  float arrive = u_enter * exp(-max(u_t, 0.0) * 2.2);
  float body = clamp((0.55 * wisp * (0.45 + n) + 0.6 * halo) * (0.75 + 1.3 * arrive), 0.0, 0.78);
  float dt = u_t - u_pop;
  float burst = (u_pop >= 0.0 && dt > 0.0) ? exp(-dt * 3.2) : 0.0;
  float glow = clamp((hhot * 3.2 + ember * 1.6 * n) * burst, 0.0, 0.9);
  // the wisps carry a little accent so the smoke reads on dark footage too
  vec3 aura = mix(u_ink, u_accent, clamp(glow * 1.4 + 0.55 * wisp * n, 0.0, 1.0));
  float aa = max(body, glow);
  float outA = me.a + aa * (1.0 - me.a);
  vec3 rgb = (me.rgb * me.a + aura * aa * (1.0 - me.a)) / max(outA, 1e-4);
  gl_FragColor = vec4(rgb, outA);
}
"""

TEXT_OFF = 44                # px of aura room around a kinetic text frame


# brand.json "ambient_fx" / REELLY_AMBIENT_FX: the cut-long layer
AMBIENT_SCALE = 0.25         # the veil is very soft: quarter size is plenty
AMBIENT_FPS = 30             # slow smoke; the encode repeats frames for 60


def active(kind="outro"):
    """The effect for this run: kind "outro" (REELLY_OUTRO_FX, brand.json
    "outro_fx"), "ambient" (REELLY_AMBIENT_FX, "ambient_fx") or "text"
    (REELLY_TEXT_FX, "text_fx"); None = off."""
    v = os.environ.get(f"REELLY_{kind.upper()}_FX", "").strip().lower()
    if not v:
        try:
            from . import brandkit
            v = str(brandkit.brand().get(f"{kind}_fx") or "").strip().lower()
        except Exception:   # noqa: BLE001 -- no kit, no effect
            v = ""
    return v if v in FX_NAMES else None


def _rgb01(hex_):
    h = (hex_ or "#a87cf0").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.66, 0.49, 0.94)


def _page(frag, w, h, uniforms, backdrop_png=None, frames=None, fps=60, blur_px=9):
    import io
    import json
    bg = "null"
    if backdrop_png:
        from PIL import Image
        im = Image.open(backdrop_png).convert("RGB").resize((w, h), Image.LANCZOS)
        b = io.BytesIO()
        im.save(b, "JPEG", quality=92)
        bg = json.dumps("data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode())
    return _PAGE.format(w=w, h=h, frag=json.dumps(frag), bg=bg,
                        frames=json.dumps(frames) if frames else "null", fps=int(fps),
                        blur_px=int(blur_px),
                        uniforms=json.dumps({k: (list(v) if isinstance(v, (list, tuple))
                                                 else round(float(v), 4))
                                             for k, v in uniforms.items()}))


def page_html(fx, backdrop_png, w, h, accent_hex, pulse_t=None, band=0.53, t0=0.0):
    """The end-card page: backdrop as a data-URI texture (no file-origin
    taint), the fragment shader, the __reellyDraw hook."""
    frag = {"smoke": _SMOKE}[fx]
    return _page(frag, w, h, {"u_res": (w, h), "u_accent": _rgb01(accent_hex),
                              "u_pulse": -1.0 if pulse_t is None else pulse_t,
                              "u_band": band, "u_t0": t0}, backdrop_png)


def ambient_html(fx, w, h, accent_hex, pic, clear, pops=(), end=0.0, level=0.45):
    """The cut-long layer: transparent page, smoke outside `pic` (x0, y0,
    x1, y1 in uv, y up) and outside the `clear` band (y0, y1)."""
    frag = {"smoke": _AMBIENT}[fx]
    pops = [float(p) for p in list(pops)[:8]] + [-1.0] * (8 - min(8, len(pops)))
    return _page(frag, w, h, {"u_res": (w, h), "u_accent": _rgb01(accent_hex),
                              "u_pic": pic, "u_clear": clear, "u_pops": pops,
                              "u_end": end, "u_level": level, "u_t0": 0.0})


async def _shoot_all(page, frames, w, h, out_dir, profile):
    from . import card
    async with card._Browser(profile, webgl=True) as b:
        await b("Emulation.setDeviceMetricsOverride",
                {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
        await b("Emulation.setDefaultBackgroundColorOverride",
                {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        await b.load("file://" + page)
        for _ in range(100):
            if await b._eval("window.__reellyReady === true"):
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("shader page never became ready (no WebGL?)")
        for n, t in frames:
            await b.shoot(t, os.path.join(out_dir, f"f{n:05d}.png"))


def aura_html(fx, frames, tw, th, fps, accent_hex, ink_hex="#0b0710", pop_t=None,
              enter=True, off=TEXT_OFF, t0=0.0):
    """The text aura page over a kinetic frame sequence (file URIs)."""
    frag = {"smoke": _AURA}[fx]
    w, h = tw + 2 * off, th + 2 * off
    return _page(frag, w, h, {"u_res": (w, h), "u_tsize": (tw, th), "u_off": off,
                              "u_accent": _rgb01(accent_hex), "u_ink": _rgb01(ink_hex),
                              "u_pop": -1.0 if pop_t is None else pop_t,
                              "u_enter": 1.0 if enter else 0.0, "u_t0": t0},
                 frames=frames, fps=fps, blur_px=12)


def render_aura(fx, in_dir, n, tw, th, fps, out_dir, pop_t=None, enter=True,
                accent_hex=None, t0=0.0):
    """Aura frames for a kinetic line: in_dir/k%05d.png (n frames, tw x th)
    -> out_dir/f%05d.png at (tw + 2*TEXT_OFF) x (th + 2*TEXT_OFF)."""
    from pathlib import Path
    from . import brandkit
    frames = [Path(os.path.join(in_dir, f"k{i:05d}.png")).resolve().as_uri() for i in range(n)]
    w, h = tw + 2 * TEXT_OFF, th + 2 * TEXT_OFF
    return _render_page(aura_html(fx, frames, tw, th, fps, accent_hex or brandkit.accent(),
                                  pop_t=pop_t, enter=enter, t0=t0),
                        out_dir, w, h, n / fps, fps)


def _render_page(html_text, out_dir, w, h, dur, fps):
    os.makedirs(out_dir, exist_ok=True)
    page = os.path.join(out_dir, "fx.html")
    with open(page, "w") as f:
        f.write(html_text)
    n = max(1, int(round(dur * fps)))
    profile = tempfile.mkdtemp(prefix="reelly-chrome-")
    try:
        asyncio.run(_shoot_all(page, [(i, i / fps) for i in range(n)], w, h,
                               out_dir, profile))
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    made = len([x for x in os.listdir(out_dir) if x.startswith("f") and x.endswith(".png")])
    if made < n:
        raise RuntimeError(f"shaderfx: only {made}/{n} frames rendered")
    return os.path.join(out_dir, "f%05d.png")


def _size(scale):
    return int(1080 * scale) // 2 * 2, int(1920 * scale) // 2 * 2


def render(fx, backdrop_png, out_dir, dur, accent_hex=None, pulse_t=None,
           band=0.53, fps=None, t0=0.0):
    """The end card: `fx` over the backdrop to out_dir/f%05d.png (half
    size). pulse_t: local time of the ask's pop (the ring of light); band:
    the ask's height from the bottom; t0: where the cut's ambient smoke
    left off, so the field continues."""
    from . import brandkit
    w, h = _size(SCALE)
    return _render_page(page_html(fx, backdrop_png, w, h, accent_hex or brandkit.accent(),
                                  pulse_t, band, t0),
                        out_dir, w, h, dur, fps or config.output_fps())


def render_ambient(fx, out_dir, dur, pic, clear, pops=(), accent_hex=None, level=0.45):
    """The cut-long layer (quarter size, AMBIENT_FPS, straight alpha)."""
    from . import brandkit
    w, h = _size(AMBIENT_SCALE)
    return _render_page(ambient_html(fx, w, h, accent_hex or brandkit.accent(), pic, clear,
                                     pops, end=dur, level=level),
                        out_dir, w, h, dur, AMBIENT_FPS)
