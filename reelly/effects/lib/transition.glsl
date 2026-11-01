// Ink-blot transition: a noise-shaped blot grows from `origin` and eats the
// outgoing image, its edge lit in the accent. Needs lib/noise.glsl.
// Original Reelly code (MIT, same as the repository).
// returns (mask, edge): mask 1 = the incoming image shows, edge = rim light
vec2 inkWipe(vec2 uv, vec2 origin, float progress, float T){
  float aspect = u_res.x / u_res.y;
  vec2 d = (uv - origin) * vec2(aspect, 1.0);
  float r = length(d) / 1.15;                         // 0 at the origin, ~1 at the far corner
  float warp = fbm(uv * 4.0 + vec2(0.0, T * 0.3)) * 0.35 + fbm(uv * 13.0) * 0.12;
  float field = r + warp - 0.24;
  float p = progress * progress * (3.0 - 2.0 * progress);   // eased
  float front = mix(-0.3, 1.25, p);
  float mask = smoothstep(front + 0.025, front - 0.025, field);
  float edge = exp(-pow((field - front) / 0.03, 2.0)) * (1.0 - p);
  return vec2(mask, edge);
}
