/*{
 "VSN": "1.2",
 "LICENSE": "MIT",
 "DESCRIPTION": "2.5D camera move on a still from its depth map: a slow push-in (near layers grow faster than far ones) or a dolly (near layers slide more). u_fit 1 (default) shows the WHOLE still across the width over a blurred, darkened copy of itself (wide key art keeps its composition); u_fit 0 fills the frame (cover crop around u_center). Depth sampled iteratively so edges stay attached to their layer.",
 "REELLY": {
  "kind": "backdrop"
 },
 "INPUTS": [
  {
   "NAME": "u_push",
   "TYPE": "float",
   "DEFAULT": 0.1,
   "MIN": 0.0,
   "MAX": 0.4
  },
  {
   "NAME": "u_depthk",
   "TYPE": "float",
   "DEFAULT": 0.1,
   "MIN": 0.0,
   "MAX": 0.3
  },
  {
   "NAME": "u_dolly",
   "TYPE": "float",
   "DEFAULT": 0.0,
   "MIN": 0.0,
   "MAX": 0.08
  },
  {
   "NAME": "u_focus",
   "TYPE": "float",
   "DEFAULT": 0.35,
   "MIN": 0.0,
   "MAX": 1.0
  },
  {
   "NAME": "u_center",
   "TYPE": "vec2",
   "DEFAULT": [
    0.5,
    0.5
   ]
  },
  {
   "NAME": "u_fit",
   "TYPE": "float",
   "DEFAULT": 1.0,
   "MIN": 0.0,
   "MAX": 1.0
  },
  {
   "NAME": "u_dur",
   "TYPE": "float",
   "DEFAULT": 6.0,
   "MIN": 0.5,
   "MAX": 60.0,
   "HOST": true
  },
  {
   "NAME": "u_img",
   "TYPE": "vec2",
   "DEFAULT": [
    1920.0,
    1080.0
   ],
   "HOST": true
  }
 ]
}*/
#include "lib/finish.glsl"
vec4 img(vec2 s){ return texture2D(u_bg, vec2(s.x, 1.0 - s.y)); }
float dep(vec2 s){ return texture2D(u_depth, vec2(s.x, 1.0 - s.y)).r; }

// base: where the still sits (fit width: its centre; cover: the crop centre);
// the zoom heads for u_center, so a push can close in on the logo or a face
// without sliding the picture off it
vec2 parallax(vec2 base, vec2 c, float p){
  vec2 q = base + c;
  vec2 s = q;
  for (int i = 0; i < 4; i++){
    float d = dep(s) - u_focus;
    float z = 1.0 + u_push * p + u_depthk * p * d;               // near grows faster
    s = u_center + (q - u_center) / z + vec2(u_dolly * (p - 0.5) * d, 0.0);
  }
  return clamp(s, vec2(0.001), vec2(0.999));
}

void main(){
  vec2 uv = gl_FragCoord.xy / u_res;
  float fa = u_res.x / u_res.y, ia = u_img.x / u_img.y;
  float p = clamp(u_t / u_dur, 0.0, 1.0);                       // holds at the end
  p = p * p * (3.0 - 2.0 * p);                                   // eased
  vec2 c = uv - 0.5;
  vec3 col;
  if (u_fit > 0.5 && ia > fa) {
    // the whole still across the width; a blurred, darkened copy around it
    vec2 cw = vec2(c.x, c.y * ia / fa);
    if (abs(cw.y) <= 0.5) {
      col = img(parallax(vec2(0.5), cw, p)).rgb;
    } else {
      vec2 cc = c; cc.x *= fa / ia;
      vec2 b = vec2(0.5) + cc / (1.0 + 0.04 * p);
      vec3 acc = vec3(0.0);
      for (int i = 0; i < 16; i++){
        float a = 6.2831853 * float(i) / 16.0;
        acc += img(clamp(b + 0.045 * vec2(cos(a), sin(a) * fa), 0.001, 0.999)).rgb;
      }
      col = acc / 16.0 * 0.45;
    }
  } else {
    if (ia > fa) c.x *= fa / ia; else c.y *= ia / fa;            // cover crop
    col = img(parallax(u_center, c, p)).rgb;
  }
  float vig = 1.0 - 0.25 * smoothstep(0.35, 0.85, length((uv - 0.5) * vec2(fa, 1.0)) * 1.2);
  gl_FragColor = vec4(dither(col * vig, gl_FragCoord.xy), 1.0);
}
