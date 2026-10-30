/*{
  "ISFVSN": "2",
  "CREDIT": "Patolli / Reelly",
  "LICENSE": "MIT",
  "VSN": "1.0",
  "DESCRIPTION": "Smoke rising over a dimmed backdrop, thinner across the ask's band, a smoke-torn ring of light when the ask's action word pops.",
  "REELLY": {
    "kind": "backdrop"
  },
  "INPUTS": [
    {
      "NAME": "u_accent",
      "TYPE": "color",
      "DEFAULT": [
        0.659,
        0.486,
        0.941
      ],
      "SLOT": "accent"
    },
    {
      "NAME": "u_band",
      "TYPE": "float",
      "DEFAULT": 0.5,
      "MIN": 0.0,
      "MAX": 1.0,
      "HOST": true
    },
    {
      "NAME": "u_pulse",
      "TYPE": "float",
      "DEFAULT": -1.0,
      "MIN": -1.0,
      "MAX": 60.0,
      "HOST": true
    }
  ]
}*/
#include "lib/noise.glsl"

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
