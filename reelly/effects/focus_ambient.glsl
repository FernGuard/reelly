/*{
  "ISFVSN": "2", "VSN": "1.0", "CREDIT": "Patolli / Reelly", "LICENSE": "MIT",
  "DESCRIPTION": "Manga focus lines (shuchusen) on the biggest real impacts only: fine radial strokes converge on the exact spot of the hit from the space around the picture, never over it, ~0.35 s, line boil at 12 fps. Pairs with impact-ambient; u_min_s picks how big a hit must be.",
  "REELLY": {"kind": "overlay"},
  "INPUTS": [
    {"NAME": "u_line", "TYPE": "color", "DEFAULT": [0.96, 0.94, 1.0]},
    {"NAME": "u_accent", "TYPE": "color", "DEFAULT": [0.659, 0.486, 0.941], "SLOT": "accent"},
    {"NAME": "u_count", "TYPE": "float", "DEFAULT": 120.0, "MIN": 30.0, "MAX": 240.0},
    {"NAME": "u_hold", "TYPE": "float", "DEFAULT": 0.36, "MIN": 0.15, "MAX": 0.8},
    {"NAME": "u_min_s", "TYPE": "float", "DEFAULT": 0.8, "MIN": 0.0, "MAX": 1.0},
    {"NAME": "u_opacity", "TYPE": "float", "DEFAULT": 0.75, "MIN": 0.1, "MAX": 1.0},
    {"NAME": "u_pic", "TYPE": "vec4", "DEFAULT": [0.0, 0.0, 0.0, 0.0], "HOST": true},
    {"NAME": "u_clear", "TYPE": "vec2", "DEFAULT": [0.0, 0.0], "HOST": true},
    {"NAME": "u_pops", "TYPE": "float[8]", "DEFAULT": [-1, -1, -1, -1, -1, -1, -1, -1], "HOST": true},
    {"NAME": "u_end", "TYPE": "float", "DEFAULT": 0.0, "MIN": 0.0, "MAX": 600.0, "HOST": true}
  ]
}*/
#include "lib/finish.glsl"
float hh(float x){ return fract(sin(x * 91.345) * 47453.5453); }

void main(){
  vec2 px = gl_FragCoord.xy;
  vec2 R = u_res;
  float T = u_t + u_t0;
  float unit = R.x / 1080.0;
  float ink = 0.0;
  for (int i = 0; i < 8; i++){
    vec4 e = u_events[i];
    float dt = T - e.x;
    if (e.x < 0.0 || dt < 0.0 || dt > u_hold || e.w < u_min_s) continue;
    // the hit, in frame px: x across the picture, y inside the picture rect
    vec2 P = vec2(e.y * R.x, mix(u_pic.y, u_pic.w, e.z) * R.y);
    vec2 d = px - P;
    float r = length(d);
    float a = atan(d.y, d.x) / 6.28318 + 0.5;            // 0..1 around the hit
    float boil = floor(T * 12.0);
    float k = floor(a * u_count);
    float f = fract(a * u_count);
    float jit = hh(k + boil * 7.0);
    // each line starts at its own radius (a ragged clear zone round the hit)
    float r0 = (0.30 + 0.22 * jit) * R.x;
    // distance to this angular cell's centre line, in px at radius r
    float dpx = abs(f - 0.5) * 6.28318 * r / u_count;
    float wpx = unit * (0.6 + 2.6 * hh(k * 3.7 + boil)) * smoothstep(r0, r0 + 0.45 * R.x, r);
    float line = smoothstep(wpx + 0.9, wpx - 0.4, dpx);
    line *= step(0.45, hh(k * 1.3 + 11.0));               // about half the angles carry a line
    line *= smoothstep(r0, r0 + 18.0 * unit, r);
    float life = smoothstep(0.0, 0.03, dt) * (1.0 - smoothstep(u_hold * 0.55, u_hold, dt));
    ink = max(ink, line * life * (0.6 + 0.4 * e.w));
  }
  // never over the picture, and the text band above it stays clear
  float inside = step(u_pic.y * R.y, px.y) * step(px.y, u_pic.w * R.y);
  float uvy = px.y / R.y;
  float cc = (u_clear.x + u_clear.y) * 0.5, hw = (u_clear.y - u_clear.x) * 0.5 + 0.03;
  float clear = (u_clear.y > u_clear.x) ? 1.0 - exp(-pow((uvy - cc) / hw, 6.0)) : 1.0;
  float a = clamp(ink * u_opacity * (1.0 - inside) * clear, 0.0, 1.0);
  vec3 col = mix(u_line, u_accent, 0.25);
  gl_FragColor = vec4(col, ditherA(a, px));
}
