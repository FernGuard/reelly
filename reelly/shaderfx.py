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

# Cursed smoke: domain-warped fbm rising from the bottom, thinner across the
# ask's band so the type stays clean, one ring of light when the ask's action
# word pops (u_pulse, beat-timed), additive over the dimmed final frame.
_SMOKE = r"""
precision highp float;
uniform vec2 u_res; uniform float u_t; uniform float u_pulse; uniform float u_band;
uniform vec3 u_accent; uniform sampler2D u_bg;
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
void main(){
  vec2 uv = gl_FragCoord.xy / u_res;                 // y up
  vec2 p = vec2(uv.x * u_res.x / u_res.y, uv.y) * 3.2;
  float t = u_t * 0.32;
  vec2 q = vec2(fbm(p + vec2(0.0, -t)), fbm(p + vec2(5.2, 1.3 - t)));
  vec2 r = vec2(fbm(p + 3.6 * q + vec2(1.7, 9.2) - vec2(0.0, 1.4 * t)),
                fbm(p + 3.6 * q + vec2(8.3, 2.8)));
  float f = fbm(p + 3.6 * r);
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
  float fade = smoothstep(0.0, 0.45, u_t);
  vec3 bg = texture2D(u_bg, vec2(uv.x, 1.0 - uv.y)).rgb;
  vec3 col = bg + u_accent * (1.2 * d + 0.7 * ring) * fade;
  gl_FragColor = vec4(min(col, vec3(1.0)), 1.0);
}
"""

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;background:#000}}
canvas{{display:block}}</style></head><body>
<canvas id="c" width="{w}" height="{h}"></canvas>
<script>
var gl = document.getElementById('c').getContext('webgl',
  {{preserveDrawingBuffer: true, antialias: false, premultipliedAlpha: false}});
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
gl.uniform2f(u('u_res'), {w}, {h});
gl.uniform3f(u('u_accent'), {ar}, {ag}, {ab});
gl.uniform1f(u('u_pulse'), {pulse});
gl.uniform1f(u('u_band'), {band});
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
img.src = '{bg}';
window.__reellyDraw = function(ms){{
  gl.uniform1f(u('u_t'), ms / 1000.0);
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); gl.finish();
}};
</script></body></html>"""


def active():
    """The outro effect for this run: REELLY_OUTRO_FX, then brand.json
    "outro_fx", else None (off)."""
    v = os.environ.get("REELLY_OUTRO_FX", "").strip().lower()
    if not v:
        try:
            from . import brandkit
            v = str(brandkit.brand().get("outro_fx") or "").strip().lower()
        except Exception:   # noqa: BLE001 -- no kit, no effect
            v = ""
    return v if v in FX_NAMES else None


def _rgb01(hex_):
    h = (hex_ or "#a87cf0").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.66, 0.49, 0.94)


def page_html(fx, backdrop_png, w, h, accent_hex, pulse_t=None, band=0.53):
    """The effect page: backdrop as a data-URI texture (no file-origin
    taint), the fragment shader, the __reellyDraw hook."""
    from PIL import Image
    import io
    frag = {"smoke": _SMOKE}[fx]
    im = Image.open(backdrop_png).convert("RGB").resize((w, h), Image.LANCZOS)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=92)
    bg = "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()
    ar, ag, ab = _rgb01(accent_hex)
    import json
    return _PAGE.format(w=w, h=h, frag=json.dumps(frag), ar=f"{ar:.4f}", ag=f"{ag:.4f}",
                        ab=f"{ab:.4f}", pulse=f"{-1.0 if pulse_t is None else pulse_t:.4f}",
                        band=f"{band:.4f}", bg=bg)


async def _shoot_all(page, frames, w, h, out_dir, profile):
    from . import card
    async with card._Browser(profile, webgl=True) as b:
        await b("Emulation.setDeviceMetricsOverride",
                {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": False})
        await b.load("file://" + page)
        for _ in range(100):
            if await b._eval("window.__reellyReady === true"):
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("shader page never became ready (no WebGL?)")
        for n, t in frames:
            await b.shoot(t, os.path.join(out_dir, f"f{n:05d}.png"))


def render(fx, backdrop_png, out_dir, dur, accent_hex=None, pulse_t=None,
           band=0.53, fps=None):
    """Render `fx` over the backdrop to out_dir/f%05d.png (half size).
    Returns the ffmpeg pattern. pulse_t: local time of the ask's pop (the
    ring of light); band: the ask's height as a fraction from the bottom."""
    from . import brandkit
    fps = fps or config.output_fps()
    os.makedirs(out_dir, exist_ok=True)
    w, h = int(1080 * SCALE) // 2 * 2, int(1920 * SCALE) // 2 * 2
    page = os.path.join(out_dir, "fx.html")
    with open(page, "w") as f:
        f.write(page_html(fx, backdrop_png, w, h, accent_hex or brandkit.accent(),
                          pulse_t, band))
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
