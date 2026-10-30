/*{
  "ISFVSN": "2", "VSN": "2.0", "CREDIT": "Patolli / Reelly", "LICENSE": "MIT",
  "DESCRIPTION": "Comic impact lines (emanata) on real impacts only (control events: the sound AND the picture jump). A fan of tapered ink strokes shoots out of the picture's edge right where the impact happened, then retracts tip-first (~0.25 s); ink outline, bright accent body, a short flash at the root, line boil at 15 fps. Lives outside the picture, entering it by at most u_bite px.",
  "REELLY": {"kind": "overlay"},
  "INPUTS": [
    {"NAME": "u_accent", "TYPE": "color", "DEFAULT": [0.659, 0.486, 0.941], "SLOT": "accent"},
    {"NAME": "u_ink", "TYPE": "color", "DEFAULT": [0.03, 0.025, 0.045]},
    {"NAME": "u_size", "TYPE": "float", "DEFAULT": 1.0, "MIN": 0.4, "MAX": 2.0},
    {"NAME": "u_hold", "TYPE": "float", "DEFAULT": 0.26, "MIN": 0.1, "MAX": 0.6},
    {"NAME": "u_bite", "TYPE": "float", "DEFAULT": 26.0, "MIN": 0.0, "MAX": 80.0},
    {"NAME": "u_min_s", "TYPE": "float", "DEFAULT": 0.12, "MIN": 0.0, "MAX": 1.0},
    {"NAME": "u_pic", "TYPE": "vec4", "DEFAULT": [0.0, 0.0, 0.0, 0.0], "HOST": true},
    {"NAME": "u_clear", "TYPE": "vec2", "DEFAULT": [0.0, 0.0], "HOST": true},
    {"NAME": "u_pops", "TYPE": "float[8]", "DEFAULT": [-1, -1, -1, -1, -1, -1, -1, -1], "HOST": true},
    {"NAME": "u_end", "TYPE": "float", "DEFAULT": 0.0, "MIN": 0.0, "MAX": 600.0, "HOST": true}
  ]
}*/
#include "lib/finish.glsl"
float hh(float x){ return fract(sin(x * 91.345) * 47453.5453); }
float easeOut(float x){ x = clamp(x, 0.0, 1.0); return 1.0 - (1.0 - x) * (1.0 - x) * (1.0 - x); }

void main(){
  vec2 px = gl_FragCoord.xy;                         // pixels, y up
  vec2 R = u_res;
  float T = u_t + u_t0;
  float unit = R.x / 1080.0;                         // 1 = a 1080-wide frame
  float body = 0.0, edge = 0.0, flash = 0.0;
  for (int i = 0; i < 8; i++){
    vec4 e = u_events[i];
    float dt = T - e.x;
    if (e.x < 0.0 || dt < 0.0 || dt > u_hold || e.w < u_min_s) continue;
    bool top = e.z > 0.5;                            // nearest edge of the picture
    float seam = (top ? u_pic.w : u_pic.y) * R.y;
    float out_dir = top ? 1.0 : -1.0;                // away from the picture
    vec2 root = vec2(e.y * R.x, seam - out_dir * u_bite * unit * 0.6);
    float boil = floor(T * 15.0);
    float strength = 0.55 + 0.45 * sqrt(e.w);       // weaker hits: smaller, never absent
    float n = 10.0 + floor(4.0 * e.w);
    float grow = easeOut(dt / 0.05);                 // 3 frames out
    float back = smoothstep(0.08, u_hold, dt);       // then the tail chases the tip
    flash = max(flash, exp(-dt * 30.0) * smoothstep(60.0 * unit * u_size, 0.0, length(px - root)));
    for (int k = 0; k < 14; k++){
      float fk = float(k);
      if (fk >= n) break;
      float seed = e.x * 7.31 + fk * 1.618;
      // fan of +-72 degrees around the outward normal, jittered, boiling
      float ang = (fk / (n - 1.0) - 0.5) * 2.5 + (hh(seed + boil) - 0.5) * 0.12;
      vec2 dir = vec2(sin(ang), out_dir * cos(ang));
      float len = u_size * unit * strength * mix(90.0, 210.0, hh(seed * 3.1)) * mix(0.85, 1.0, hh(seed + boil * 1.7));
      float r0 = u_size * unit * mix(18.0, 40.0, hh(seed * 5.3));
      float head = r0 + len * grow;
      float tail = r0 + len * back;
      vec2 q = px - root;
      float along = dot(q, dir);
      float perp = abs(dot(q, vec2(-dir.y, dir.x)));
      float s = clamp((along - r0) / max(len, 1.0), 0.0, 1.0);
      float w = u_size * unit * mix(9.0, 0.6, s) * strength;     // thick root, sharp tip
      float inLen = smoothstep(tail - 1.0, tail + 1.0, along) * smoothstep(head + 1.0, head - 1.0, along);
      float fill = smoothstep(w + 1.0, w - 1.0, perp) * inLen;
      float line = smoothstep(w + 4.5 * unit + 1.0, w + 4.5 * unit - 1.0, perp)
                   * smoothstep(tail - 4.0 * unit, tail, along) * smoothstep(head + 4.0 * unit, head, along);
      // a stroke that has almost retracted fades whole (no leftover specks)
      float left = smoothstep(4.0 * unit, 26.0 * unit, head - tail);
      body = max(body, fill * left);
      edge = max(edge, line * left);
    }
  }
  // outside the picture, and at most u_bite px into it
  float bite = u_bite * unit;
  float inside = step(u_pic.y * R.y + bite, px.y) * step(px.y, u_pic.w * R.y - bite);
  float allowed = 1.0 - inside;
  vec3 fillc = mix(u_accent, vec3(1.0), 0.35);
  vec3 col = mix(u_ink, fillc, body);
  col = mix(col, vec3(1.0), flash * 0.8);
  float a = clamp(max(edge, max(body, flash * 0.9)) * allowed, 0.0, 1.0);
  gl_FragColor = vec4(col, ditherA(a, px));
}
