"""GPU kinetic text: the kinetic line drawn by the GPU from a glyph atlas.

WHY
The kinetic layer captured every animated frame of a DOM page as a browser
screenshot: the slowest step left in a cut once the shader layers moved to
the GPU. Drawing the same text from a signed-distance-field (SDF) atlas in
WebGL renders a frame in a millisecond or two and leaves through the binary
frame pipe; the distance field also gives the outline, the soft shadow and
the entrance blur for free, and opens per-letter shader effects later.

HOW (the same look and footprint as kinetic.page_html)
- Atlas: each glyph the line needs is drawn once at 2x with PIL from the
  SAME font file and size the layout gates measure (captions._font), and
  turned into a signed distance field with scipy's Euclidean distance
  transform (+-SPREAD display px around the edge).
- Layout: kinetic.wrap_lines breaks the line exactly as the static layer;
  pen positions come from the font's own advances (PIL getlength, kerning
  included), each line centred in the box, baselines on a 1.22 line height.
- Motion: the page evaluates the kinetic CSS curves frame by frame in JS
  (the same cubic-bezier easings, stagger, rise + unblur, hot-word pop and
  exit as kinetic.page_html) and the GPU draws all glyph quads in three
  passes: shadow (0 7px 12px), outline, fill (hot word in the accent).
- Frames: premultiplied RGBA read back through shaderfx's binary pipe,
  un-premultiplied and written as k%05d.png, so the aura pass and the burn
  consume them exactly like the DOM frames. Any failure falls back to the
  DOM renderer. REELLY_GPU_TEXT=off forces the DOM renderer.
"""
import base64
import io
import json
import os

ATLAS_SCALE = 2          # glyphs drawn at 2x and sampled down
SPREAD = 14              # display px of distance kept on each side of an edge
MAX_WORDS = 40


MOTIONS = ("rise", "blocklock")


def motion():
    """How entering text arrives: REELLY_TEXT_MOTION, then brand.json
    fx.text_motion, else "rise" (the kinetic default). "blocklock": each word
    drops in like a falling block, lands with a squash, flashes as it locks."""
    v = os.environ.get("REELLY_TEXT_MOTION", "").strip().lower()
    if not v:
        try:
            from . import brandkit
            fx = brandkit.brand().get("fx")
            v = str((fx or {}).get("text_motion") or "").strip().lower() if isinstance(fx, dict) else ""
        except Exception:   # noqa: BLE001
            v = ""
    return v if v in MOTIONS else "rise"


EXITS = ("fade", "petals")
PETAL_LINGER = 0.6       # s the petals may drift on after the line's own slot
PETAL_MARGIN = 70        # px of room above/around a line for petals to rise into
DISSOLVE_S = 0.75        # the dissolve + petals window at the end of a leaving line


def exit_style():
    """How a leaving line goes: REELLY_TEXT_EXIT, then brand.json
    fx.text_exit, else "fade" (the kinetic default). "petals": the letters
    erode along a burning edge and petals peel off and drift up."""
    v = os.environ.get("REELLY_TEXT_EXIT", "").strip().lower()
    if not v:
        try:
            from . import brandkit
            fx = brandkit.brand().get("fx")
            v = str((fx or {}).get("text_exit") or "").strip().lower() if isinstance(fx, dict) else ""
        except Exception:   # noqa: BLE001
            v = ""
    return v if v in EXITS else "fade"


def _petal_colors():
    from . import brandkit
    fx = brandkit.brand().get("fx") if isinstance(brandkit.brand().get("fx"), dict) else {}
    second = fx.get("petal_color") if isinstance(fx.get("petal_color"), str) else "#ffa322"
    return _rgb01(brandkit.accent()), _rgb01(second)


def enabled():
    return os.environ.get("REELLY_GPU_TEXT", "").strip().lower() not in ("off", "0", "no")


def _font(size, role):
    from . import captions
    return captions._font(int(size), role)


def atlas(chars, size, role):
    """SDF glyph atlas for `chars` at 2x: (PIL L image, {ch: (x, y, w, h, ox, oy)})
    with (ox, oy) the tile's offset from the glyph's baseline origin, in 2x px."""
    import numpy as np
    from PIL import Image, ImageDraw
    from scipy.ndimage import distance_transform_edt as edt
    f2 = _font(size * ATLAS_SCALE, role)
    pad = SPREAD * ATLAS_SCALE + 2
    tiles = []
    for ch in sorted(set(chars) - {" "}):
        l, t, r, b = f2.getbbox(ch, anchor="ls")
        w, h = max(1, r - l) + 2 * pad, max(1, b - t) + 2 * pad
        img = Image.new("L", (w, h), 0)
        ImageDraw.Draw(img).text((pad - l, pad - t), ch, font=f2, fill=255, anchor="ls")
        m = np.asarray(img) >= 128
        sd = edt(m) - edt(~m)                                   # + inside, - outside (2x px)
        v = np.clip(0.5 + sd / (2.0 * SPREAD * ATLAS_SCALE), 0.0, 1.0)
        tiles.append((ch, Image.fromarray((v * 255 + 0.5).astype(np.uint8), "L"), l - pad, t - pad))
    W = 2048
    x = y = row = 0
    place = {}
    for ch, im, ox, oy in tiles:
        if x + im.width > W:
            x, y, row = 0, y + row + 1, 0
        place[ch] = (x, y, im.width, im.height, ox, oy)
        x += im.width + 1
        row = max(row, im.height)
    H = max(1, y + row + 1)
    sheet = Image.new("L", (W, H), 0)
    for ch, im, _, _ in tiles:
        sheet.paste(im, place[ch][:2])
    return sheet, place


def words_list(lines):
    return [w for ws in lines for w in ws]


def layout(text, size, width, role, lines, pad):
    """Glyph placements for the wrapped line: [(ch, pen_x, baseline_y,
    word_index, line_index)] in canvas px (y down), word anchors (pop
    origin: the word's bottom centre) and the word list."""
    f = _font(size, role)
    asc, desc = f.getmetrics()
    lineh = size * 1.22
    glyphs, anchors, words = [], [], []
    wi = 0
    for li, ws in enumerate(lines):
        s = " ".join(ws)
        x0 = pad + (width - f.getlength(s)) / 2.0
        top = pad + li * lineh
        base = top + (lineh - (asc + desc)) / 2.0 + asc
        pos = 0
        for w in ws:
            start = s.index(w, pos)
            end = start + len(w)
            wx0, wx1 = x0 + f.getlength(s[:start]), x0 + f.getlength(s[:end])
            anchors.append(((wx0 + wx1) / 2.0, top + lineh))
            words.append(w)
            for k in range(start, end):
                glyphs.append((s[k], x0 + f.getlength(s[:k]), base, wi, li))
            pos = end
            wi += 1
    return glyphs, anchors, words


def vertex_data(glyphs, anchors, place, hot):
    """Two triangles per glyph: pos(2) uv(2) word(1) anchor(2) hot(1)."""
    out = []
    sheet_w = 2048
    sheet_h = max(1, max((p[1] + p[3] for p in place.values()), default=1))
    for ch, px, base, wi, _ in glyphs:
        if ch not in place:
            continue
        x, y, w, h, ox, oy = place[ch]
        x0, y0 = px + ox / ATLAS_SCALE, base + oy / ATLAS_SCALE
        x1, y1 = x0 + w / ATLAS_SCALE, y0 + h / ATLAS_SCALE
        u0, v0, u1, v1 = x / sheet_w, y / sheet_h, (x + w) / sheet_w, (y + h) / sheet_h
        ax, ay = anchors[wi]
        hf = 1.0 if wi == hot else 0.0
        for (vx, vy, uu, vv) in ((x0, y0, u0, v0), (x1, y0, u1, v0), (x0, y1, u0, v1),
                                 (x1, y0, u1, v0), (x1, y1, u1, v1), (x0, y1, u0, v1)):
            out += [round(vx, 3), round(vy, 3), round(uu, 6), round(vv, 6), float(wi),
                    round(ax, 3), round(ay, 3), hf]
    return out


_VS = """
attribute vec2 a_pos; attribute vec2 a_uv; attribute float a_word;
attribute vec2 a_anchor; attribute float a_hot;
uniform vec2 u_res; uniform vec4 u_w[40]; uniform vec2 u_line; uniform vec2 u_shift;
uniform vec2 u_origin;
uniform vec4 u_w2[40];                          // (lock flash 0..1, squash, -, -)
varying float v_flash;
varying vec2 v_scr;
varying vec2 v_uv; varying float v_a; varying float v_blur; varying float v_hot;
void main(){
  vec4 w = u_w[int(a_word + 0.5)];              // (dy, alpha, blur px, scale)
  vec4 w2 = u_w2[int(a_word + 0.5)];
  vec2 p = a_anchor + (a_pos - a_anchor) * w.w; // pop scale about the word's bottom centre
  p = a_anchor + (p - a_anchor) * vec2(1.0 + w2.y, 1.0 - w2.y);   // landing squash
  p.y += w.x + u_line.x + u_shift.y; p.x += u_shift.x;
  p += u_origin;
  gl_Position = vec4(p.x / u_res.x * 2.0 - 1.0, 1.0 - p.y / u_res.y * 2.0, 0.0, 1.0);
  v_uv = a_uv; v_a = w.y * u_line.y; v_blur = w.z; v_hot = a_hot; v_flash = w2.x;
  v_scr = p;
}
"""

_FS = """
precision highp float;
uniform sampler2D u_atlas; uniform float u_pass; uniform float u_spread;
uniform vec3 u_fill; uniform vec3 u_hotc; uniform float u_dis;
varying vec2 v_uv; varying float v_a; varying float v_blur; varying float v_hot;
varying float v_flash; varying vec2 v_scr;
float h21(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vn(vec2 p){ vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(h21(i), h21(i + vec2(1.0, 0.0)), u.x), mix(h21(i + vec2(0.0, 1.0)), h21(i + vec2(1.0, 1.0)), u.x), u.y); }
void main(){
  float d = texture2D(u_atlas, v_uv).r;
  float px = 1.0 / (2.0 * u_spread);            // one display px in distance units
  // CSS blur(8px) is a Gaussian with an 8 px deviation: widen every edge by
  // most of it (capped by the distance field's reach)
  float soft = min(0.7 + 1.5 * v_blur, 13.0) * px;
  vec3 c; float a;
  if (u_pass < 0.5) {                           // text-shadow: 0 7px 12px rgba(0,0,0,.85)
    // a 12px CSS blur is a Gaussian (sigma 6): ~50% cover AT the edge, fading
    // over ~13 px either side; smoothstep over +-2.2 sigma tracks its CDF
    float sg = (13.0 + 0.5 * v_blur) * px;
    a = smoothstep(0.5 - sg, 0.5 + sg, d) * 0.85;
    c = vec3(0.0);
  } else if (u_pass < 1.5) {                    // the visible 1 px of the 2 px stroke
    float sw = 1.0 + 2.5 * v_flash;              // the outline flares when a block locks
    a = smoothstep(0.5 - sw * px - soft, 0.5 - sw * px + soft, d) * 0.9;
    c = mix(vec3(0.0), u_hotc, v_flash);
  } else if (u_pass < 2.5) {
    a = smoothstep(0.5 - soft, 0.5 + soft, d);
    c = mix(mix(u_fill, u_hotc, v_hot), vec3(1.0), 0.55 * v_flash);
  } else {                                      // the aura's blurred copy (~12 px Gaussian)
    float sg = 13.0 * px;
    a = smoothstep(0.5 - sg, 0.5 + sg, d);
    c = mix(u_fill, u_hotc, v_hot);
  }
  if (u_dis > 0.0) {                            // erode along noise, edge burning hot
    float n = 0.65 * vn(v_scr * 0.045) + 0.35 * vn(v_scr * 0.16);
    float keep = smoothstep(u_dis - 0.015, u_dis + 0.015, n);
    float edge = keep * (1.0 - smoothstep(0.0, 0.13, n - u_dis));
    if (u_pass > 1.5 && u_pass < 2.5) c = mix(c, mix(u_hotc, vec3(1.0, 0.8, 0.45), 0.6), min(1.0, edge * 1.6));
    // the burn edge only on and just around the letters, never in empty space
    float near = smoothstep(0.5 - 3.0 * px, 0.5 - 0.5 * px, d);
    if (u_pass > 0.5 && u_pass < 1.5) { c = mix(c, vec3(1.0, 0.55, 0.15), edge * near); a = max(a, edge * near * 0.9 * keep); }
    a *= keep;
  }
  a *= v_a;
  gl_FragColor = vec4(c * a, a);                // premultiplied
}
"""

_PETALS_FS = """
precision highp float;
uniform vec2 u_res; uniform float u_t; uniform float u_d0; uniform float u_dd;
uniform vec4 u_wd[40]; uniform float u_nw; uniform float u_size;
uniform vec3 u_c1; uniform vec3 u_c2; uniform float u_life;
float h(float x){ return fract(sin(x * 78.233) * 43758.5453); }
void main(){
  vec2 px = vec2(gl_FragCoord.x, u_res.y - gl_FragCoord.y);     // canvas px, y down
  vec4 acc = vec4(0.0);
  for (int w = 0; w < 40; w++){
    if (float(w) >= u_nw) break;
    vec4 wd = u_wd[w];                                          // (cx, baseline, half width, -)
    for (int k = 0; k < 10; k++){
      float sd = float(w) * 13.7 + float(k) * 3.1;
      float spawn = u_d0 + h(sd) * u_dd * 0.5;
      float age = u_t - spawn;
      if (age < 0.0 || age > u_life) continue;
      vec2 o = vec2(wd.x + (h(sd + 1.0) - 0.5) * 2.0 * wd.z, wd.y - h(sd + 2.0) * 0.55 * u_size);
      vec2 pos = o + vec2(sin(age * 3.0 + sd) * 0.18 * u_size + age * 0.35 * u_size,
                          -age * 1.15 * u_size - age * age * 0.4 * u_size);
      float ang = sd * 6.0 + age * (3.0 + 4.0 * h(sd + 3.0));
      vec2 q = px - pos;
      q = vec2(cos(ang) * q.x - sin(ang) * q.y, sin(ang) * q.x + cos(ang) * q.y);
      float sz = u_size * mix(0.16, 0.27, h(sd + 4.0));
      float dd = length(q / vec2(sz, sz * 0.5));
      float al = smoothstep(1.0, 0.75, dd) * (1.0 - smoothstep(0.5 * u_life, u_life, age)) * smoothstep(0.0, 0.05, age);
      vec3 col = mix(u_c1, u_c2, step(0.5, h(sd + 5.0)));
      col = mix(col, vec3(1.0, 0.95, 0.85), 0.35 * smoothstep(0.5, 0.0, dd));
      acc.rgb = col * al + acc.rgb * (1.0 - al);                 // premultiplied over
      acc.a = al + acc.a * (1.0 - al);
    }
  }
  gl_FragColor = acc;
}
"""

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;background:transparent}}
canvas{{display:block}}</style></head><body>
<canvas id="c" width="{w}" height="{h}"></canvas>
<script>
window.onerror = function(m){{ window.__reellyError = String(m); }};
var gl = document.getElementById('c').getContext('webgl',
  {{preserveDrawingBuffer: true, antialias: false, premultipliedAlpha: false, alpha: true}});
if (!gl) {{ window.__reellyError = 'NO_WEBGL_CONTEXT'; throw new Error('no WebGL context'); }}
var P = {params};
function sh(t, s){{ var x = gl.createShader(t); gl.shaderSource(x, s); gl.compileShader(x);
  if (!gl.getShaderParameter(x, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(x)); return x; }}
var prog = gl.createProgram();
gl.attachShader(prog, sh(gl.VERTEX_SHADER, {vs})); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, {fs}));
gl.linkProgram(prog); gl.useProgram(prog);
function u(n){{ return gl.getUniformLocation(prog, n); }}
var V = new Float32Array(P.verts);
var buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.bufferData(gl.ARRAY_BUFFER, V, gl.STATIC_DRAW);
var S = 8 * 4;
[['a_pos', 2, 0], ['a_uv', 2, 2], ['a_word', 1, 4], ['a_anchor', 2, 5], ['a_hot', 1, 7]].forEach(function(a){{
  var l = gl.getAttribLocation(prog, a[0]); if (l < 0) return;
  gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, a[1], gl.FLOAT, false, S, a[2] * 4); }});
gl.uniform2f(u('u_res'), {w}, {h}); gl.uniform1f(u('u_spread'), P.spread);
gl.uniform3fv(u('u_fill'), P.fill); gl.uniform3fv(u('u_hotc'), P.hotc);
gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
// the same easings as kinetic.page_html's CSS
function bez(x1, y1, x2, y2){{
  function cx(t){{ return ((1 - 3 * x2 + 3 * x1) * t + (3 * x2 - 6 * x1)) * t * t + 3 * x1 * t; }}
  function cy(t){{ return ((1 - 3 * y2 + 3 * y1) * t + (3 * y2 - 6 * y1)) * t * t + 3 * y1 * t; }}
  return function(x){{ if (x <= 0) return 0; if (x >= 1) return 1;
    var lo = 0, hi = 1, t = x; for (var i = 0; i < 40; i++){{ var v = cx(t);
      if (Math.abs(v - x) < 1e-6) break; if (v < x) lo = t; else hi = t; t = (lo + hi) / 2; }}
    return cy(t); }};
}}
function setU(pr, U){{ Object.keys(U).forEach(function(k){{
  var v = U[k], l = gl.getUniformLocation(pr, k); if (l === null) return;
  if (!Array.isArray(v)) gl.uniform1f(l, v);
  else if (k === 'u_events' || k === 'u_wd') gl.uniform4fv(l, new Float32Array(v));
  else if (v.length > 4) gl.uniform1fv(l, new Float32Array(v));
  else gl['uniform' + v.length + 'f'].apply(gl, [l].concat(v)); }}); }}
var tl = {{}};
['a_pos', 'a_uv', 'a_word', 'a_anchor', 'a_hot'].forEach(function(n){{ tl[n] = gl.getAttribLocation(prog, n); }});
function bindText(){{
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  [['a_pos', 2, 0], ['a_uv', 2, 2], ['a_word', 1, 4], ['a_anchor', 2, 5], ['a_hot', 1, 7]].forEach(function(a){{
    var l = tl[a[0]]; if (l < 0) return; gl.enableVertexAttribArray(l);
    gl.vertexAttribPointer(l, a[1], gl.FLOAT, false, S, a[2] * 4); }});
}}
gl.uniform2f(u('u_origin'), P.origin, P.origin);
var A = null;
if (P.aura) {{
  function fboTex(){{ var t = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, {w}, {h}, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    var f = gl.createFramebuffer(); gl.bindFramebuffer(gl.FRAMEBUFFER, f);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, t, 0);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null); return [t, f]; }}
  var p2 = gl.createProgram();
  gl.attachShader(p2, sh(gl.VERTEX_SHADER, 'attribute vec2 a; void main(){{ gl_Position = vec4(a, 0.0, 1.0); }}'));
  gl.attachShader(p2, sh(gl.FRAGMENT_SHADER, P.aura.frag)); gl.linkProgram(p2);
  if (!gl.getProgramParameter(p2, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p2));
  var qb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, qb);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
  var pb = gl.createProgram();
  gl.attachShader(pb, sh(gl.VERTEX_SHADER, 'attribute vec2 a; void main(){{ gl_Position = vec4(a, 0.0, 1.0); }}'));
  gl.attachShader(pb, sh(gl.FRAGMENT_SHADER, [
    'precision highp float; uniform sampler2D u_src; uniform vec2 u_res; uniform vec2 u_dir; uniform float u_sigma;',
    'void main(){{ vec2 uv = gl_FragCoord.xy / u_res; vec4 acc = vec4(0.0); float ws = 0.0;',
    '  for (int i = -24; i <= 24; i++){{ float x = float(i) * u_sigma / 8.0;',
    '    float w = exp(-0.5 * x * x / (u_sigma * u_sigma));',
    '    acc += texture2D(u_src, uv + u_dir * x / u_res) * w; ws += w; }}',
    '  gl_FragColor = acc / ws; }}'].join('\\n')));
  gl.linkProgram(pb);
  if (!gl.getProgramParameter(pb, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(pb));
  A = {{ prog: p2, quad: qb, loc: gl.getAttribLocation(p2, 'a'), blur: pb,
         bloc: gl.getAttribLocation(pb, 'a'), sharp: fboTex(), soft: fboTex(), tmp: fboTex() }};
  gl.useProgram(p2); setU(p2, P.aura.uniforms);
  gl.uniform1i(gl.getUniformLocation(p2, 'u_txt'), 1); gl.uniform1i(gl.getUniformLocation(p2, 'u_blur'), 2);
  gl.useProgram(prog);
}}
var PT = null;
if (P.exit_style === 'petals') {{
  var pp = gl.createProgram();
  gl.attachShader(pp, sh(gl.VERTEX_SHADER, 'attribute vec2 a; void main(){{ gl_Position = vec4(a, 0.0, 1.0); }}'));
  gl.attachShader(pp, sh(gl.FRAGMENT_SHADER, P.petals_fs)); gl.linkProgram(pp);
  if (!gl.getProgramParameter(pp, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(pp));
  var pq = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, pq);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, 1,1]), gl.STATIC_DRAW);
  PT = {{ prog: pp, quad: pq, loc: gl.getAttribLocation(pp, 'a') }};
  gl.useProgram(pp); setU(pp, P.petals_u); gl.useProgram(prog);
}}
var eT = bez.apply(null, P.ease_type), eP = bez.apply(null, P.ease_pop), eO = bez.apply(null, P.ease_out);
function clamp(x){{ return Math.max(0, Math.min(1, x)); }}
var W = new Float32Array(160), W2 = new Float32Array(160);
function easeIn(x){{ return x * x; }}
window.__reellyDraw = function(ms){{
  var t = ms / 1000;
  gl.useProgram(prog);   // uniforms below belong to the text program (the aura's was current)
  for (var i = 0; i < 40; i++){{
    var dy = 0, a = 1, blur = 0, s = 1, flash = 0, squash = 0;
    if (P.enter && P.motion === 'blocklock'){{
      // each word drops in like a falling block, lands, bounces once, locks
      // stacking order: bottom line first, so a falling word only ever
      // crosses empty space, never text that already landed
      var t0 = P.rank[i] * P.stagger * 1.5, fall = 0.34, land = t0 + fall;
      var f = clamp((t - t0) / fall);
      a = clamp(f * 4); dy = -(1 - easeIn(f)) * 1.5 * P.size;
      if (t >= land){{ var k2 = t - land;
        dy += Math.sin(k2 * 38) * Math.exp(-k2 * 18) * 0.07 * P.size;
        squash = 0.10 * Math.exp(-k2 * 22);
        flash = Math.exp(-k2 * 9); }}
    }} else if (P.enter){{ var e = eT(clamp((t - i * P.stagger) / P.enter_s));
      a = e; dy = (1 - e) * 0.34 * P.size; blur = (1 - e) * 8; }}
    if (i === P.hot && P.pop >= 0){{ var q = (t - P.pop) / P.pop_s;
      if (q > 0 && q < 1){{ var k = q < 0.45 ? eP(q / 0.45) : 1 - eP((q - 0.45) / 0.55);
        s = 1 + (P.pop_scale - 1) * k; dy -= P.pop_lift * P.size * k; }} }}
    W[i * 4] = dy; W[i * 4 + 1] = a; W[i * 4 + 2] = blur; W[i * 4 + 3] = s;
    W2[i * 4] = flash; W2[i * 4 + 1] = squash;
  }}
  gl.uniform4fv(u('u_w[0]'), W);
  gl.uniform4fv(u('u_w2[0]'), W2);
  var ly = 0, la = 1;
  if (P.leave && t >= P.exit_t){{ var o = eO(clamp((t - P.exit_t) / P.exit_s)); la = 1 - o; ly = -0.22 * P.size * o; }}
  gl.uniform2f(u('u_line'), ly, la);
  var dis = 0;
  if (PT) dis = clamp((t - P.dis0) / P.dis_s);
  gl.uniform1f(u('u_dis'), dis > 0 ? 0.02 + 1.0 * dis : 0.0);
  var n = V.length / 8;
  gl.useProgram(prog); bindText(); gl.enable(gl.BLEND);
  gl.bindFramebuffer(gl.FRAMEBUFFER, A ? A.sharp[1] : null);
  gl.viewport(0, 0, {w}, {h}); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
  gl.uniform1f(u('u_pass'), 0); gl.uniform2f(u('u_shift'), 0, 7); gl.drawArrays(gl.TRIANGLES, 0, n);
  gl.uniform1f(u('u_pass'), 1); gl.uniform2f(u('u_shift'), 0, 0); gl.drawArrays(gl.TRIANGLES, 0, n);
  gl.uniform1f(u('u_pass'), 2); gl.drawArrays(gl.TRIANGLES, 0, n);
  if (PT && t >= P.dis0) {{
    gl.useProgram(PT.prog);
    gl.uniform1f(gl.getUniformLocation(PT.prog, 'u_t'), t);
    for (var k1 = 0; k1 < 8; k1++) if (k1 !== PT.loc) gl.disableVertexAttribArray(k1);
    gl.bindBuffer(gl.ARRAY_BUFFER, PT.quad); gl.enableVertexAttribArray(PT.loc);
    gl.vertexAttribPointer(PT.loc, 2, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.useProgram(prog); bindText();
  }}
  if (A) {{
    // the soft copy: a separable Gaussian of the sharp frame (premultiplied,
    // which is the correct space to blur in), sharp -> tmp -> soft
    gl.disable(gl.BLEND);
    gl.useProgram(A.blur);
    for (var k0 = 0; k0 < 8; k0++) if (k0 !== A.bloc) gl.disableVertexAttribArray(k0);
    gl.bindBuffer(gl.ARRAY_BUFFER, A.quad); gl.enableVertexAttribArray(A.bloc);
    gl.vertexAttribPointer(A.bloc, 2, gl.FLOAT, false, 0, 0);
    gl.uniform2f(gl.getUniformLocation(A.blur, 'u_res'), {w}, {h});
    gl.uniform1f(gl.getUniformLocation(A.blur, 'u_sigma'), P.aura.blur);
    gl.uniform1i(gl.getUniformLocation(A.blur, 'u_src'), 3);
    gl.activeTexture(gl.TEXTURE3);
    gl.bindFramebuffer(gl.FRAMEBUFFER, A.tmp[1]); gl.bindTexture(gl.TEXTURE_2D, A.sharp[0]);
    gl.uniform2f(gl.getUniformLocation(A.blur, 'u_dir'), 1, 0); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.bindFramebuffer(gl.FRAMEBUFFER, A.soft[1]); gl.bindTexture(gl.TEXTURE_2D, A.tmp[0]);
    gl.uniform2f(gl.getUniformLocation(A.blur, 'u_dir'), 0, 1); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.bindTexture(gl.TEXTURE_2D, null); gl.activeTexture(gl.TEXTURE0);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(A.prog);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, A.sharp[0]);
    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, A.soft[0]);
    gl.activeTexture(gl.TEXTURE0);
    gl.uniform1f(gl.getUniformLocation(A.prog, 'u_t'), t);
    for (var k = 0; k < 8; k++) if (k !== A.loc) gl.disableVertexAttribArray(k);
    gl.bindBuffer(gl.ARRAY_BUFFER, A.quad); gl.enableVertexAttribArray(A.loc);
    gl.vertexAttribPointer(A.loc, 2, gl.FLOAT, false, 0, 0);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }}
  gl.finish();
}};
var img = new Image();
img.onload = function(){{
  var tex = gl.createTexture(); gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.LUMINANCE, gl.LUMINANCE, gl.UNSIGNED_BYTE, img);
  gl.uniform1i(u('u_atlas'), 0);
  window.__reellyReady = true; window.__reellyDraw(0);
}};
img.src = P.atlas;
</script></body></html>"""


def _rgb01(hex_):
    h = hex_.lstrip("#")
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]


def _bez(css):
    import re
    return [float(x) for x in re.findall(r"-?\d*\.?\d+", css)]


def page(text, size, width, height, dur, role, hot_index, lines, enter, leave, pop_t,
         fill_hex, hot_hex, aura=None, linger=0.0):
    """The GPU text page for one kinetic line (canvas width x height). aura:
    a text-effect preset name: the page grows by its margin on every side
    and the effect is composited in the same frame (no image round trip)."""
    from . import kinetic
    words = text.split()
    if len(words) > MAX_WORDS:
        raise ValueError(f"gputext: more than {MAX_WORDS} words")
    sheet, place = atlas(text, size, role)
    glyphs, anchors, _ = layout(text, size, width - 2 * kinetic.PAD, role, lines, kinetic.PAD)
    settle, exit_t, stagger = kinetic.timeline(len(words), dur, hot_index, enter, leave, pop_t)
    b = io.BytesIO()
    sheet.save(b, "PNG")
    off, aura_p, W, H = 0, None, width, height
    petals = leave and exit_style() == "petals" and dur > DISSOLVE_S + 0.3
    if aura:
        from . import control, shaderfx
        off = shaderfx.text_off(aura)
        U = shaderfx.uniforms(aura, shaderfx.brand_params(aura),
                              host={"u_pop": -1.0 if pop_t is None else float(pop_t),
                                    "u_enter": 1.0 if enter else 0.0, "u_fbo": 1.0})
        aura_p = {"frag": shaderfx.fragment(shaderfx.load_preset(aura)["effect"]),
                  "_W": None,
                  "uniforms": {**control.host_uniforms(None), **U},
                  "blur": float(shaderfx._render_opts(aura).get("blur_px", 12))}
    if petals:
        off += PETAL_MARGIN
    W, H = width + 2 * off, height + 2 * off
    if aura_p:
        aura_p.pop("_W", None)
        aura_p["uniforms"].update({"u_res": [W, H], "u_tsize": [W, H], "u_off": 0.0, "u_t0": 0.0})
    petals_p = {}
    if petals:
        f = _font(size, role)
        wd = []
        for (ax, ay), wtxt in zip(anchors, words_list(lines)):
            wd += [ax + off, ay - (size * 1.22 - (sum(f.getmetrics()))) / 2.0 - f.getmetrics()[1] + off,
                   f.getlength(wtxt) / 2.0, 0.0]
        c1, c2 = _petal_colors()
        d0 = max(settle, dur - DISSOLVE_S)
        petals_p = {"petals_fs": _PETALS_FS, "dis0": d0, "dis_s": DISSOLVE_S * 0.75,
                    "petals_u": {"u_res": [W, H], "u_d0": d0, "u_dd": DISSOLVE_S * 0.75,
                                 "u_wd": (wd + [0.0, 0.0, 0.0, 0.0] * MAX_WORDS)[:MAX_WORDS * 4],
                                 "u_nw": float(len(words)), "u_size": float(size),
                                 "u_c1": c1, "u_c2": c2,
                                 # petals drift on into the linger, every one gone
                                 # before the layer (line + linger) ends
                                 "u_life": max(0.3, dur + linger - d0
                                               - DISSOLVE_S * 0.75 * 0.5 - 0.03)}}
        exit_t = dur + 1.0                             # the dissolve replaces the fade-out
    word_line = [li for li, ws in enumerate(lines) for _ in ws]
    order = sorted(range(len(word_line)), key=lambda i: (-word_line[i], i))
    rank = [0] * len(word_line)
    for k, i in enumerate(order):
        rank[i] = k
    params = {"origin": off, "aura": aura_p, "motion": motion(), "rank": rank,
              "exit_style": "petals" if petals else "fade", **petals_p,"verts": vertex_data(glyphs, anchors, place, hot_index if hot_index is not None else -1),
              "atlas": "data:image/png;base64," + base64.b64encode(b.getvalue()).decode(),
              "spread": SPREAD, "size": size, "fill": _rgb01(fill_hex), "hotc": _rgb01(hot_hex),
              "enter": bool(enter), "leave": bool(leave), "stagger": stagger,
              "enter_s": kinetic.ENTER, "hot": -1 if hot_index is None else int(hot_index),
              "pop": -1.0 if pop_t is None else float(pop_t), "pop_s": kinetic.POP,
              "pop_scale": kinetic.POP_SCALE, "pop_lift": kinetic.POP_LIFT,
              "exit_t": exit_t, "exit_s": kinetic.EXIT,
              "ease_type": _bez(kinetic.EASE_TYPE), "ease_pop": _bez(kinetic.EASE_POP),
              "ease_out": _bez(kinetic.EASE_OUT)}
    hold_end = petals_p.get("dis0", exit_t)
    return _PAGE.format(w=W, h=H, params=json.dumps(params), vs=json.dumps(_VS),
                        fs=json.dumps(_FS)), settle, hold_end, off


def _png_writer(out_dir, w, h):
    """Frame writer: premultiplied, bottom-up RGBA from readPixels -> a
    straight-alpha, top-down PNG named like the DOM frames (k%05d.png)."""
    import numpy as np
    from PIL import Image

    def write(n, raw):
        a = np.frombuffer(raw, np.uint8).reshape(h, w, 4)[::-1].astype(np.float32)
        al = a[..., 3:4]
        rgb = np.where(al > 0, np.clip(a[..., :3] * 255.0 / np.maximum(al, 1.0), 0, 255), 0)
        out = np.concatenate([rgb, al], axis=2).astype(np.uint8)
        Image.fromarray(out, "RGBA").save(os.path.join(out_dir, f"k{n:05d}.png"), compress_level=1)
    return write


def render_aura(text, out_dir, dur, size, width, height, role, hot_index, lines, enter,
                leave, pop_t, fps, fill_hex, hot_hex, aura, linger=0.0):
    """Text + its shader effect in one GPU page -> out_dir/f%05d.tga at
    (width + 2*off) x (height + 2*off). Returns (pattern, W, H, off)."""
    from . import shaderfx
    os.makedirs(out_dir, exist_ok=True)
    html, _, _, off = page(text, size, width, height, dur, role, hot_index, lines, enter,
                           leave, pop_t, fill_hex, hot_hex, aura=aura, linger=linger)
    W, H = width + 2 * off, height + 2 * off
    return shaderfx.render_frames(html, out_dir, W, H, dur + linger, fps), W, H, off


def render(text, out_dir, dur, size, width, height, role, hot_index, lines, enter, leave,
           pop_t, fps, fill_hex, hot_hex, linger=0.0):
    """Frames k%05d.png (n = dur * fps) of the kinetic line, drawn by the GPU.
    Returns the hold window (settle, hold_end) that was rendered once."""
    from . import shaderfx
    os.makedirs(out_dir, exist_ok=True)
    html, settle, exit_t, off = page(text, size, width, height, dur, role, hot_index, lines,
                                     enter, leave, pop_t, fill_hex, hot_hex, linger=linger)
    hold_end = exit_t if leave else dur + 1.0
    still = (settle, hold_end) if hold_end > settle else None
    W, H = width + 2 * off, height + 2 * off
    shaderfx.render_frames(html, out_dir, W, H, dur + linger, fps,
                           writer=_png_writer(out_dir, W, H), pattern="k%05d.png",
                           still=still)
    return off
