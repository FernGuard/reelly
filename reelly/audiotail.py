"""Tail hygiene: the content end must never sit ON a rising sound onset.

THE FAILURE: the content
window ended at 20.0s, the source went silent at ~19.4s, and a NEW loud
sound began at ~19.96s. The cut shipped the first ~60ms of that onset;
loudness normalisation amplified it ~10x, and the appended outro's silence
followed -- a clipped-syllable blast right at the content->outro boundary.
The designed-endings vision check passed it: it watches video, not audio.

THE RULE: when the final TAIL_SCAN_S of content contains a real silence
stretch (RMS < SILENCE_RMS for >= SILENCE_MIN_S) followed by an onset that
is still loud at the very last window (RMS > ONSET_RMS), the content end is
pulled back to the end of that silence (BREATH_S before the onset starts).
Trims are small by construction (capped at MAX_TRIM_S): bigger moves belong
to the planner and the ending check, never to a hygiene pass.

Applied twice, same detector:
- plan time (direct._plan_from/_visual_plan_from): new plans never freeze
  an on-onset end into cut_plans.json;
- render time (finalize, before the outro append): the safety net for
  legacy plans -- the raw cut is trimmed and the plan accounting moves
  with it.
The judge gate (judge.clean_audio_tail) runs the same detector on the
shipped file's content tail so a regression can never ship silently.
"""
import array
import math
import os
import subprocess

from . import config

TAIL_SCAN_S = 0.40      # how much of the content tail is examined
WIN_S = 0.03            # RMS window size (the beats/motion probe granularity)
SR = 16000
SILENCE_RMS = 120.0     # below this a window reads as silence (s16 RMS)
SILENCE_MIN_S = 0.15    # shorter quiet stretches are word gaps, not silence
ONSET_RMS = 400.0       # the chopped burst must peak at least this loud
BREATH_S = 0.05         # the trimmed end keeps this much room before the onset
MAX_TRIM_S = 0.5        # hygiene cap; bigger moves belong to the planner
# A chopped burst runs right up to the boundary; quiet trailing windows up
# to this long are codec padding / the burst's own chopped decay, not a real
# silence ending (AAC pads ~20-60ms past the last content sample: measured
# on cut_04's raw, whose probe duration overshot the segment sum by 0.02s
# and whose last windows read 376 -> 106 while the burst peaked at 800).
END_SLACK_S = 0.09


def envelope(path, t0, t1, sr=SR, win_s=WIN_S):
    """Mono RMS envelope of path's audio over [t0, t1]: [(t_start, rms)].

    Windows are anchored to t1 (walked backward) so the FINAL window is
    exactly the last win_s of content -- the boundary the detector judges.
    Decoded s16le mono, the same cheap ffmpeg pattern the other probes use.
    """
    r = subprocess.run(
        [config.FFMPEG, "-v", "error", "-ss", f"{max(0.0, t0):.3f}",
         "-to", f"{t1:.3f}", "-i", path, "-vn", "-ac", "1", "-ar", str(sr),
         "-f", "s16le", "-"], capture_output=True)
    data = array.array("h")
    data.frombytes(r.stdout[: len(r.stdout) // 2 * 2])
    n = int(sr * win_s)
    out = []
    end = len(data)
    while end - n >= 0:
        seg = data[end - n:end]
        rms = math.sqrt(sum(x * x for x in seg) / n)
        out.append((t1 - (len(data) - (end - n)) / sr, rms))
        end -= n
    out.reverse()
    return out


def clipped_onset(samples, end_t):
    """The chopped-onset signature in a tail envelope, or None.

    `samples` is [(t, rms)] over the content tail; `end_t` is the content
    end on the same clock. Fires ONLY when all three hold:
    - the tail went genuinely silent (RMS < SILENCE_RMS for >= SILENCE_MIN_S),
    - a NEW sound rose out of that silence and peaks above ONSET_RMS,
    - the sound runs right up to the boundary (any quiet trailing windows
      are shorter than END_SLACK_S -- codec padding or the chopped decay,
      never a real silence ending).
    Sustained sound to the end (no silence stretch), tails that genuinely
    end in silence, and soft blips (peak <= ONSET_RMS) return None: nothing
    audible is being chopped.

    Returns {onset_start, new_end, silence_s, onset_rms, last_rms}; new_end
    is BREATH_S before the onset (the end of the silence stretch);
    onset_rms/last_rms are the burst's first-window and boundary RMS.
    """
    if len(samples) < 2:
        return None
    win = samples[1][0] - samples[0][0]
    k = len(samples) - 1
    while k >= 0 and samples[k][1] < SILENCE_RMS:
        k -= 1
    if k < 0:
        return None                 # silent throughout: nothing to chop
    if (len(samples) - 1 - k) * win >= END_SLACK_S:
        return None                 # tail genuinely ends in silence
    i = k
    while i >= 0 and samples[i][1] >= SILENCE_RMS:
        i -= 1
    if i < 0:
        return None                 # loud all the way: sustained, not an onset
    onset_i = i + 1
    if max(r for _, r in samples[onset_i:]) <= ONSET_RMS:
        return None                 # a soft blip, not a blast worth a trim
    j = i
    while j >= 0 and samples[j][1] < SILENCE_RMS:
        j -= 1
    silence_s = (i - j) * win
    if silence_s < SILENCE_MIN_S - 1e-9:
        return None                 # a gap between words, not a real silence
    onset_start = samples[onset_i][0]
    new_end = round(onset_start - BREATH_S, 3)
    trim = end_t - new_end
    if trim <= 0 or trim > MAX_TRIM_S:
        return None
    slack_n = max(1, int(round(END_SLACK_S / win)) + 1)
    return {"onset_start": round(onset_start, 3), "new_end": new_end,
            "silence_s": round(silence_s, 2),
            "onset_rms": round(samples[onset_i][1], 1),
            "last_rms": round(max(r for _, r in samples[-slack_n:]), 1)}


def plan_tail_trim(video, merged, total, floor_local, cut_id):
    """Plan-time application: trim the last segment's end off a clipped
    onset, measured on the SOURCE footage.

    `merged` is the plan's segment list (source seconds, mutated in place),
    `total` the clip-local content length, `floor_local` the plan's
    payoff_complete_by (clip-local) -- the end never moves before it.
    Returns (merged, total, note_or_None).
    """
    if not merged or not video or not os.path.exists(video):
        return merged, total, None
    s, e = float(merged[-1][0]), float(merged[-1][1])
    speed = float(merged[-1][2]) if len(merged[-1]) > 2 else 1.0
    try:
        hit = clipped_onset(envelope(video, max(s, e - TAIL_SCAN_S), e), e)
    except Exception as ex:  # noqa: BLE001 -- a probe must never kill planning
        print(f"[tail ] {cut_id}: tail probe failed ({ex}); end left as planned")
        return merged, total, None
    if not hit:
        return merged, total, None
    trim_local = round((e - hit["new_end"]) / speed, 2)
    if floor_local is not None and total - trim_local < float(floor_local) - 1e-6:
        print(f"[tail ] {cut_id}: clipped onset at the content end, but "
              f"trimming {trim_local:.2f}s would end before "
              f"payoff_complete_by ({float(floor_local):.2f}s); leaving the "
              f"end to the planner/ending check")
        return merged, total, None
    merged[-1][1] = round(e - trim_local * speed, 2)
    total = round(total - trim_local, 2)
    print(f"[tail ] {cut_id}: trimmed {trim_local:.2f}s clipped onset off "
          f"the content tail")
    note = (f"TAIL-hygiene: content end pulled {trim_local:.2f}s back into "
            f"the tail silence -- it was cutting off a new sound onset "
            f"(RMS {hit['onset_rms']:.0f}->{hit['last_rms']:.0f} at the "
            f"boundary)")
    return merged, total, note


def raw_tail_trim(raw_path, floor_local, cut_id):
    """Render-time application: seconds to trim off the END of a rendered
    cut (clip-local clock), or 0.0 when the tail is clean.

    The safety net for legacy plans whose frozen segments end on an onset.
    `floor_local` is the plan's payoff_complete_by; the end never moves
    before it (print and leave -- the ending check owns bigger moves).
    """
    from . import media
    try:
        dur = float(media.probe(raw_path)["format"]["duration"])
        hit = clipped_onset(
            envelope(raw_path, max(0.0, dur - TAIL_SCAN_S), dur), dur)
    except Exception as ex:  # noqa: BLE001 -- hygiene must never fail a render
        print(f"[tail ] {cut_id}: tail probe failed ({ex}); no trim")
        return 0.0
    if not hit:
        return 0.0
    if floor_local is not None and hit["new_end"] < float(floor_local) - 1e-6:
        print(f"[tail ] {cut_id}: clipped onset at the content end, but "
              f"trimming to {hit['new_end']:.2f}s would end before "
              f"payoff_complete_by ({float(floor_local):.2f}s); leaving the "
              f"end to the planner/ending check")
        return 0.0
    return round(dur - hit["new_end"], 2)


def trim_file(src, trim_s, dst):
    """src minus trim_s off the end -> dst, re-encoded (a stream copy cannot
    cut mid-GOP) with the standard intermediate encoder args."""
    from . import media
    dur = float(media.probe(src)["format"]["duration"])
    subprocess.run(
        [config.FFMPEG, "-y", "-v", "error", "-i", src,
         "-t", f"{max(0.1, dur - trim_s):.3f}",
         *config.intermediate_encode_args(),
         "-c:a", "aac", "-b:a", "192k", dst],
        check=True, capture_output=True)
    return dst
