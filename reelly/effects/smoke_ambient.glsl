/*{
  "ISFVSN": "2",
  "CREDIT": "Patolli / Reelly",
  "LICENSE": "MIT",
  "VSN": "1.0",
  "DESCRIPTION": "Smoke veil outside a protected picture rect and text band, swelling on control-track pops, building into the end.",
  "REELLY": {
    "kind": "overlay"
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
      "NAME": "u_level",
      "TYPE": "float",
      "DEFAULT": 0.45,
      "MIN": 0.0,
      "MAX": 1.5
    },
    {
      "NAME": "u_pic",
      "TYPE": "vec4",
      "DEFAULT": [
        0.0,
        0.0,
        0.0,
        0.0
      ],
      "HOST": true
    },
    {
      "NAME": "u_clear",
      "TYPE": "vec2",
      "DEFAULT": [
        0.0,
        0.0
      ],
      "HOST": true
    },
    {
      "NAME": "u_pops",
      "TYPE": "float[8]",
      "DEFAULT": [
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1,
        -1
      ],
      "HOST": true
    },
    {
      "NAME": "u_end",
      "TYPE": "float",
      "DEFAULT": 0.0,
      "MIN": 0.0,
      "MAX": 600.0,
      "HOST": true
    }
  ]
}*/
#include "lib/noise.glsl"

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
