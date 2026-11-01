// Finishing helpers: dither before 8-bit quantization (dark gradients band
// after platform re-encode without it) and the protected-region mask.
// Original Reelly code (MIT, same as the repository).
float ditherNoise(vec2 px){ vec3 p3 = fract(vec3(px.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z) - 0.5; }
vec3 dither(vec3 c, vec2 px){ return c + ditherNoise(px) / 255.0; }
float ditherA(float a, vec2 px){ return clamp(a + ditherNoise(px + 17.0) / 255.0, 0.0, 1.0); }
