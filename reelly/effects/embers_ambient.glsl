/*{
  "ISFVSN": "2", "VSN": "1.0", "CREDIT": "Patolli / Reelly", "LICENSE": "MIT",
  "DESCRIPTION": "Stateless embers rising outside the protected picture and text band: three depth layers, hashed per grid cell (any frame renders on its own), flaring on beats, onsets and hot-word pops, denser when the audio is loud.",
  "REELLY": {"kind": "overlay"},
  "INPUTS": [
    {"NAME": "u_accent", "TYPE": "color", "DEFAULT": [0.659, 0.486, 0.941], "SLOT": "accent"},
    {"NAME": "u_core", "TYPE": "color", "DEFAULT": [1.0, 0.62, 0.25]},
    {"NAME": "u_density", "TYPE": "float", "DEFAULT": 0.35, "MIN": 0.0, "MAX": 1.0},
    {"NAME": "u_speed", "TYPE": "float", "DEFAULT": 0.12, "MIN": 0.0, "MAX": 1.0},
    {"NAME": "u_size", "TYPE": "float", "DEFAULT": 1.0, "MIN": 0.3, "MAX": 3.0},
    {"NAME": "u_react", "TYPE": "float", "DEFAULT": 1.0, "MIN": 0.0, "MAX": 2.0},
    {"NAME": "u_pic", "TYPE": "vec4", "DEFAULT": [0.0, 0.0, 0.0, 0.0], "HOST": true},
    {"NAME": "u_clear", "TYPE": "vec2", "DEFAULT": [0.0, 0.0], "HOST": true},
    {"NAME": "u_pops", "TYPE": "float[8]", "DEFAULT": [-1, -1, -1, -1, -1, -1, -1, -1], "HOST": true},
    {"NAME": "u_end", "TYPE": "float", "DEFAULT": 0.0, "MIN": 0.0, "MAX": 600.0, "HOST": true}
  ]
}*/
#include "lib/control.glsl"
#include "lib/finish.glsl"
float h1(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
vec2 h2(vec2 p){ return vec2(h1(p), h1(p + 19.19)); }

void main(){
  vec2 uv = gl_FragCoord.xy / u_res;
  float T = u_t + u_t0;
  float aspect = u_res.x / u_res.y;
  // reactions: a flare on each beat, each onset, each hot-word pop
  float pop = 0.0;
  for (int i = 0; i < 8; i++){
    float d = u_t - u_pops[i];
    if (u_pops[i] >= 0.0 && d >= 0.0 && d < 1.5) pop += exp(-d * 3.0);
  }
  float react = u_react * (0.45 * beatPulse(T, 6.0) + 0.9 * exp(-sinceOnset(T) * 5.0) + 0.7 * pop);
  float loud = energy(T);
  float density = clamp(u_density * (0.75 + 0.6 * loud) + 0.25 * react, 0.0, 1.0);
  float ramp = smoothstep(u_end - 2.0, u_end, u_t) * step(0.01, u_end);

  vec3 col = vec3(0.0); float a = 0.0;
  for (int L = 0; L < 3; L++){
    float fl = float(L);
    float cells = 7.0 + fl * 5.0;                    // far layers: more, smaller, slower
    float speed = u_speed * (1.0 - 0.25 * fl);
    vec2 p = vec2(uv.x * aspect, uv.y) * cells;
    p.y -= T * speed * cells;
    vec2 cell = floor(p);
    vec2 f = fract(p) - 0.5;
    vec2 r = h2(cell + fl * 37.0);
    if (r.x > density) continue;                     // this cell has no ember
    float ph = h1(cell + 7.3 + fl);
    vec2 c = (r - 0.5) * 0.7;
    c.x += 0.18 * sin(T * (1.2 + ph) + ph * 6.28);   // sway as it rises
    float d = length(f - c);
    float size = u_size * mix(0.025, 0.07, h1(cell + 3.1)) * (1.0 - 0.2 * fl) * (1.0 + 0.6 * react);
    float flick = 0.65 + 0.35 * sin(T * (7.0 + 5.0 * ph) + ph * 40.0);
    float core = smoothstep(size, 0.0, d);
    float glow = smoothstep(size * 3.5, 0.0, d) * 0.35;
    float e = (core + glow) * flick * (1.0 - 0.25 * fl) * (0.8 + 0.8 * react);
    vec3 tint = mix(u_core, u_accent, smoothstep(0.2, 1.0, uv.y + 0.3 * ph));  // cool as they rise
    col += tint * e; a += e;
  }
  // protected regions: never over the picture or the text band
  float edge = 0.01;
  float outside = 1.0 - smoothstep(u_pic.y - edge, u_pic.y, uv.y)
                      * (1.0 - smoothstep(u_pic.w, u_pic.w + edge, uv.y));
  float cc = (u_clear.x + u_clear.y) * 0.5, hw = (u_clear.y - u_clear.x) * 0.5 + 0.02;
  float clear = 1.0 - exp(-pow((uv.y - cc) / hw, 6.0));
  float below = 1.0 - smoothstep(0.0, max(u_pic.y, 0.001), uv.y);   // embers live low
  float above = smoothstep(u_pic.w, 1.0, uv.y);
  float zone = clamp(0.15 + 0.95 * below + 0.18 * above + 0.4 * ramp, 0.0, 1.0);
  a = clamp(a * outside * clear * zone, 0.0, 1.0);
  vec3 rgb = a > 0.0 ? col / max(a, 1e-4) : vec3(0.0);
  gl_FragColor = vec4(min(rgb, vec3(1.0)), ditherA(a, gl_FragCoord.xy));
}
