/*{
  "ISFVSN": "2", "VSN": "1.0", "CREDIT": "Patolli / Reelly", "LICENSE": "MIT",
  "DESCRIPTION": "Folk-print finish for the space around the picture (Reelly's own fill, never the content): the frame is reduced to a 3-colour brand palette (surface, accent, ink) through a 4x4 ordered dither, laid with rotated halftone dots and paper grain, like a loteria card or a relief print. The picture and anything inside u_pic is left untouched.",
  "REELLY": {"kind": "filter"},
  "INPUTS": [
    {"NAME": "u_dark", "TYPE": "color", "DEFAULT": [0.04, 0.03, 0.06], "SLOT": "surface"},
    {"NAME": "u_mid", "TYPE": "color", "DEFAULT": [0.659, 0.486, 0.941], "SLOT": "accent"},
    {"NAME": "u_light", "TYPE": "color", "DEFAULT": [0.92, 0.92, 0.9], "SLOT": "ink"},
    {"NAME": "u_mix", "TYPE": "float", "DEFAULT": 0.6, "MIN": 0.0, "MAX": 1.0},
    {"NAME": "u_dot", "TYPE": "float", "DEFAULT": 9.0, "MIN": 3.0, "MAX": 24.0},
    {"NAME": "u_grain", "TYPE": "float", "DEFAULT": 0.06, "MIN": 0.0, "MAX": 0.3},
    {"NAME": "u_pic", "TYPE": "vec4", "DEFAULT": [0.0, 0.0, 0.0, 0.0], "HOST": true},
    {"NAME": "u_clear", "TYPE": "vec2", "DEFAULT": [0.0, 0.0], "HOST": true}
  ]
}*/
float bayer4(vec2 p){
  vec2 q = mod(floor(p), 4.0);
  float i = q.x + q.y * 4.0;
  // 4x4 Bayer matrix, row-major
  if (i < 0.5) return 0.0;  if (i < 1.5) return 8.0;  if (i < 2.5) return 2.0;  if (i < 3.5) return 10.0;
  if (i < 4.5) return 12.0; if (i < 5.5) return 4.0;  if (i < 6.5) return 14.0; if (i < 7.5) return 6.0;
  if (i < 8.5) return 3.0;  if (i < 9.5) return 11.0; if (i < 10.5) return 1.0; if (i < 11.5) return 9.0;
  if (i < 12.5) return 15.0; if (i < 13.5) return 7.0; if (i < 14.5) return 13.0; return 5.0;
}
float hh(vec2 p){ return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453); }

void main(){
  vec2 px = gl_FragCoord.xy;
  vec2 uv = px / u_res;
  float unit = u_res.x / 1080.0;
  vec3 src = texture2D(u_txt, vec2(uv.x, 1.0 - uv.y)).rgb;       // the frame (image, top-down)
  float L = dot(src, vec3(0.299, 0.587, 0.114));
  // brand space is dark: stretch its real range (~0.03..0.45) before printing
  float Ls = smoothstep(0.02, 0.45, L);
  // a smooth duotone ramp dark -> accent -> light, stepped by an ordered
  // dither into a few printed tones (keeps the smoke's shape and colour)
  float th = (bayer4(px / max(1.0, 2.0 * unit)) + 0.5) / 16.0;
  float steps = 5.0;
  float q = floor(Ls * steps + (th - 0.5)) / steps;
  vec3 pal = q < 0.5 ? mix(u_dark, u_mid, q * 2.0) : mix(u_mid, u_light, (q - 0.5) * 2.0);
  pal = mix(pal, src * 1.3, 0.25);                                 // keep a little of the source hue
  // rotated halftone dots in the mid-tones (ink dots where it is darker)
  float s = u_dot * unit, ang = 0.26;
  vec2 r = mat2(cos(ang), -sin(ang), sin(ang), cos(ang)) * px / s;
  vec2 cell = fract(r) - 0.5;
  float radius = 0.48 * sqrt(clamp(1.0 - Ls, 0.0, 1.0));
  float dot_ = smoothstep(radius + 0.06, radius - 0.06, length(cell));
  pal = mix(pal, u_dark, dot_ * 0.45 * smoothstep(0.05, 0.3, Ls));
  // paper grain (animated, so it reads as film, not dirt)
  pal *= 1.0 - u_grain + u_grain * 2.0 * hh(floor(px / max(1.0, unit)) + floor(u_t * 24.0));
  vec3 col = mix(src, pal, u_mix);
  // only the space around the picture
  float inside = step(u_pic.y * u_res.y, px.y) * step(px.y, u_pic.w * u_res.y);
  gl_FragColor = vec4(col, 1.0 - inside);
}
