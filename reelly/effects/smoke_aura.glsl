/*{
 "ISFVSN": "2",
 "CREDIT": "Patolli / Reelly",
 "LICENSE": "MIT",
 "VSN": "1.1",
 "DESCRIPTION": "Dark smoky wisps off a rendered text layer and an accent flare on the hot word's pop; the type itself is untouched.",
 "REELLY": {
  "kind": "text"
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
   "NAME": "u_ink",
   "TYPE": "color",
   "DEFAULT": [
    0.043,
    0.027,
    0.063
   ],
   "SLOT": "ink_dark"
  },
  {
   "NAME": "u_pop",
   "TYPE": "float",
   "DEFAULT": -1.0,
   "MIN": -1.0,
   "MAX": 60.0,
   "HOST": true
  },
  {
   "NAME": "u_enter",
   "TYPE": "float",
   "DEFAULT": 1.0,
   "MIN": 0.0,
   "MAX": 1.0,
   "HOST": true
  },
  {
   "NAME": "u_fbo",
   "TYPE": "float",
   "DEFAULT": 0.0,
   "MIN": 0.0,
   "MAX": 1.0,
   "HOST": true
  }
 ]
}*/
#include "lib/noise.glsl"

vec2 tq(vec2 px, out float inside){                  // canvas px (y up) -> text uv
  vec2 q = px - vec2(u_off);
  inside = step(0.0, q.x) * step(0.0, q.y) * step(q.x, u_tsize.x) * step(q.y, u_tsize.y);
  // image files are top-down; GPU-rendered textures (u_fbo) are bottom-up
  return vec2(q.x / u_tsize.x, u_fbo > 0.5 ? q.y / u_tsize.y : 1.0 - q.y / u_tsize.y);
}
// GPU-rendered textures are premultiplied; image files are straight alpha
vec4 unpm(vec4 c){ if (u_fbo > 0.5 && c.a > 0.0) c.rgb /= c.a; return c; }
vec4 txt(vec2 px){ float i; vec2 q = tq(px, i); return unpm(texture2D(u_txt, q)) * i; }
// the same frame, Gaussian-blurred by the browser: smoke and flare come from
// this soft cloud, never from offset copies of the sharp glyphs
vec4 blr(vec2 px){ float i; vec2 q = tq(px, i); return unpm(texture2D(u_blur, q)) * i; }
float hot(vec4 c){ return c.a * (1.0 - smoothstep(0.12, 0.40, distance(c.rgb, u_accent))); }
void main(){
  vec2 px = gl_FragCoord.xy;
  vec2 uv = px / u_res;
  float T = u_t + u_t0;
  vec4 me = txt(px);
  // rising wisps: text alpha sampled BELOW this pixel, sideways warped
  float wisp = 0.0, ember = 0.0, wsum = 0.0, esum = 0.0;
  for (int i = 1; i <= 10; i++){
    float k = float(i);
    vec2 at = px + vec2(0.0, -k * 3.2);
    float w = fbm(vec2(at.x * 0.018, at.y * 0.012 - T * 0.9));
    at.x += (w - 0.5) * k * 5.0;
    vec4 c = blr(at);
    float fall = exp(-k * 0.22);
    wisp += c.a * fall; wsum += fall;
    // embers: only the far samples, so they rise ABOVE the word and never
    // smear across its strokes (a near-sample flare read as a melted double)
    if (i >= 5) { ember += hot(c) * fall; esum += fall; }
  }
  wisp /= wsum; ember /= max(esum, 1e-4);
  // soft halos: a tight one (the dark body that lifts the type) and a wide
  // one around the hot word (the flare)
  vec4 b = blr(px);
  float halo = b.a;
  float hhot = hot(b);
  float n = fbm(uv * vec2(6.0, 3.0) + vec2(0.0, -T * 0.6));
  float arrive = u_enter * exp(-max(u_t, 0.0) * 2.2);
  float body = clamp((0.55 * wisp * (0.45 + n) + 0.6 * halo) * (0.75 + 1.3 * arrive), 0.0, 0.78);
  float dt = u_t - u_pop;
  float burst = (u_pop >= 0.0 && dt > 0.0) ? exp(-dt * 3.2) : 0.0;
  float glow = clamp((hhot * 3.2 + ember * 1.6 * n) * burst, 0.0, 0.9);
  // the wisps carry a little accent so the smoke reads on dark footage too
  vec3 aura = mix(u_ink, u_accent, clamp(glow * 1.4 + 0.55 * wisp * n, 0.0, 1.0));
  float aa = max(body, glow);
  float outA = me.a + aa * (1.0 - me.a);
  vec3 rgb = (me.rgb * me.a + aura * aa * (1.0 - me.a)) / max(outA, 1e-4);
  gl_FragColor = vec4(rgb, outA);
}
