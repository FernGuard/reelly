// Shared value noise, fbm and the domain-warped smoke field.
// Original Reelly code (MIT, same as the repository).
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p){
  vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p){
  float v = 0.0, a = 0.5;
  for (int i = 0; i < 5; i++){ v += a * noise(p); p = p * 2.03 + vec2(1.7, 9.2); a *= 0.5; }
  return v;
}
// The smoke field at uv and absolute time T (one field for the whole video:
// the cut's ambient layer and the end card sample the same smoke).
float smoke(vec2 uv, float T){
  vec2 p = vec2(uv.x * u_res.x / u_res.y, uv.y) * 3.2;
  float t = T * 0.32;
  vec2 q = vec2(fbm(p + vec2(0.0, -t)), fbm(p + vec2(5.2, 1.3 - t)));
  vec2 r = vec2(fbm(p + 3.6 * q + vec2(1.7, 9.2) - vec2(0.0, 1.4 * t)),
                fbm(p + 3.6 * q + vec2(8.3, 2.8)));
  return fbm(p + 3.6 * r);
}
