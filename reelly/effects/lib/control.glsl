// Control track readers (host uniforms u_beat, u_onsets, u_ctrl, u_ctrl_n).
// Original Reelly code (MIT, same as the repository).
float beatPhase(float t){ return u_beat.y > 0.0 ? fract((t - u_beat.x) / u_beat.y) : 0.0; }
// 1.0 on each beat, decaying over the beat (k = sharpness)
float beatPulse(float t, float k){
  return u_beat.y > 0.0 ? exp(-beatPhase(t) * k) : 0.0;
}
// cut-audio loudness at time t, 0..1 (0 when there is no track)
float energy(float t){
  if (u_ctrl_n < 1.0) return 0.0;
  return texture2D(u_ctrl, vec2(clamp((t * 60.0 + 0.5) / u_ctrl_n, 0.0, 1.0), 0.5)).r;
}
// seconds since the latest onset at or before t (large when none)
float sinceOnset(float t){
  float best = 1000.0;
  for (int i = 0; i < 16; i++){
    float d = t - u_onsets[i];
    if (u_onsets[i] >= 0.0 && d >= 0.0) best = min(best, d);
  }
  return best;
}
