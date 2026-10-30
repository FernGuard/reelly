/*{
  "ISFVSN": "2", "VSN": "1.0", "CREDIT": "Patolli / Reelly", "LICENSE": "MIT",
  "DESCRIPTION": "Comic impact starbursts (emanata) on real hits: each onset in the cut's own audio snaps a jagged ink burst with an accent rim onto the picture's edge, boiling at 12 fps, held ~7 frames. Lives in the space around the picture and crosses into it by at most u_bite px.",
  "REELLY": {"kind": "overlay"},
  "INPUTS": [
    {"NAME": "u_accent", "TYPE": "color", "DEFAULT": [0.659, 0.486, 0.941], "SLOT": "accent"},
    {"NAME": "u_ink", "TYPE": "color", "DEFAULT": [0.035, 0.03, 0.05]},
    {"NAME": "u_size", "TYPE": "float", "DEFAULT": 1.0, "MIN": 0.4, "MAX": 2.0},
    {"NAME": "u_hold", "TYPE": "float", "DEFAULT": 0.12, "MIN": 0.05, "MAX": 0.4},
    {"NAME": "u_bite", "TYPE": "float", "DEFAULT": 30.0, "MIN": 0.0, "MAX": 80.0},
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
  vec2 uv = px / u_res;
  float T = u_t + u_t0;
  float aspect = u_res.x / u_res.y;
  float body = 0.0, rim = 0.0;
  for (int i = 0; i < 16; i++){
    float on = u_onsets[i];
    float dt = T - on;
    if (on < 0.0 || dt < 0.0 || dt > u_hold) continue;
    float seed = on * 13.17 + float(i);
    bool top = hh(seed) > 0.5;
    float seam = top ? u_pic.w : u_pic.y;
    if (seam <= 0.001 || seam >= 0.999) seam = top ? 0.9 : 0.1;      // full-bleed: near the frame edge
    vec2 c = vec2(mix(0.16, 0.84, hh(seed + 1.0)), seam + (top ? 0.035 : -0.035));
    vec2 d = (uv - c) * vec2(aspect, 1.0);
    float r = length(d), a = atan(d.y, d.x);
    float boil = floor(T * 12.0);                                      // hand-drawn 12 fps wobble
    float n = 11.0 + floor(hh(seed + 2.0) * 5.0);
    float k = floor((a + 3.14159) / 6.28318 * n);
    float frac = fract((a + 3.14159) / 6.28318 * n);
    float spike = 1.0 - abs(frac - 0.5) * 2.0;                         // triangle wave: points
    float len = mix(0.55, 1.0, hh(k + seed * 3.0 + boil));
    float grow = smoothstep(0.0, 0.035, dt);                          // snaps in over 2 frames
    float base = 0.055 * u_size * grow;
    float R = base * (0.55 + 0.75 * spike * len);
    float px1 = 1.0 / u_res.y;
    body = max(body, smoothstep(R, R - 1.5 * px1, r));
    rim = max(rim, smoothstep(R + 4.0 * px1, R + 1.0 * px1, r) - smoothstep(R, R - 1.5 * px1, r));
  }
  // where it may draw: everywhere outside the picture, and u_bite px into it
  float bite = u_bite / u_res.y;
  float inside = step(u_pic.y + bite, uv.y) * step(uv.y, u_pic.w - bite);
  float allowed = 1.0 - inside;
  float a = clamp(max(body, rim) * allowed, 0.0, 1.0);
  vec3 col = mix(u_ink, u_accent, clamp(rim * 1.2, 0.0, 1.0));
  gl_FragColor = vec4(col, ditherA(a, px));
}
