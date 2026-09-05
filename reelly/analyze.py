"""M1 orchestrator: one command turns a raw recording into the analysis bundle.

Every step is cached: if its artifact already exists it is skipped, so re-runs
are cheap and interrupted runs resume where they stopped. --force re-runs all.

THE STAGE DAG RUNS CONCURRENTLY (2026-08-02, measured ~96s saved on a 40-min
session). The stages fall into resource-disjoint chains:

  A transcript  transcribe -> clean SRT -> speech map -> topics -> guest scan
                (GPU/MLX first, then word-dependent CPU steps, in order)
  B visual      Gemini visual review           (network; needs nothing local)
  C session     probe -> facecam waveform sync (ffmpeg CPU)
  D scenes      scene-cut detection            (ffmpeg CPU, full video decode)
  E loudness    R128 loudness map              (ffmpeg CPU, audio decode)
  F diarize     pyannote speaker turns         (MPS/CPU; extracts its own wav,
                so it does NOT wait for the transcript chain)

Chains only depend on their own predecessors, so they run on a thread pool.
ffmpeg-decode-heavy steps additionally share a 2-slot semaphore so CPU decode
never starves the GPU and network chains of cores. Output stays per-step
([run ]/[done] lines carry the stage name, so interleaving stays readable);
the summary prints only after every chain has finished, over the same
artifact set the serial orchestrator produced.

Window-scoped diarization: diarize.run_windows(video, [(s, e), ...]) diarizes
only the given ranges into the same speaker_turns.json schema (see
REELLY_DIAR_SCOPE=windows in diarize.py). analyze itself always covers the
full session; the windows API exists for the cut flow to adopt when a plan
only needs voice identity inside its own segments.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

from . import audio as audio_mod
from . import (config, ingest, media, scenes, slots, speech, timing, topics,
               transcribe)

# full-decode ffmpeg stages compete for the same performance cores; more than
# two at once slows every chain (including the light ffmpeg probes the GPU and
# network chains make). The cap is MACHINE-WIDE (slots.py): parallel analyze
# runs share the two decode slots instead of each bringing their own pair.


def _step(label, path, force, fn):
    if os.path.exists(path) and not force:
        print(f"[skip] {label} (cached: {os.path.basename(path)})")
        return None
    print(f"[run ] {label} ...")
    # Artifacts live in <root>/analysis/, so the project timings file is one
    # level up. Every stage that actually runs gets a timings.json row --
    # before this, only the visual stage was measurable after the fact.
    root = os.path.dirname(os.path.dirname(os.path.abspath(path)))
    with timing.stage(label, timing.timings_path(root)):
        return fn()


def _cpu_step(label, path, force, fn):
    """A _step whose fn is a heavy ffmpeg decode: capped machine-wide.
    Cache hits never take a slot."""
    if os.path.exists(path) and not force:
        return _step(label, path, force, fn)
    with slots.hold("decode", label):
        return _step(label, path, force, fn)


def run(video, facecam=None, name=None, out_root=None, skip_visual=False,
        force=False, crop=None):
    video = os.path.abspath(video)
    name = name or os.path.splitext(os.path.basename(video))[0]
    root = ingest.workspace(name, out_root or config.DEFAULT_PROJECTS)
    an = os.path.join(root, "analysis")
    print(f"project: {root}")

    # --- INGEST -----------------------------------------------------------
    ingest.link_source(root, video, "screen")
    if facecam:
        ingest.link_source(root, os.path.abspath(facecam), "facecam")

    # A capture that recorded only black (dead OBS display source: audio
    # records, picture never does) must fail HERE in seconds, not 30 minutes
    # and extra Gemini/GPT spend later with a black deliverable.
    # Five spread luma probes; all black => stop.
    media.assert_not_black(video)

    # --- the DAG (see module docstring): one function per dependency chain --
    def chain_session():  # C: probe -> facecam waveform sync
        probe_p = os.path.join(an, "probe.json")
        _step("probe", probe_p, force, lambda: json.dump(
            {os.path.basename(f): media.probe(f) for f in [video] + ([facecam] if facecam else [])},
            open(probe_p, "w"), indent=1))
        if facecam:
            session_p = os.path.join(an, "session.json")

            def _sync():
                off, conf = ingest.sync_offset(video, facecam)
                data = {"screen": video, "facecam": facecam,
                        "facecam_offset_s": round(off, 3), "confidence": round(conf, 3),
                        "note": "screen_time = facecam_time + offset"}
                json.dump(data, open(session_p, "w"), indent=1)
                return data
            _cpu_step("session sync (waveform)", session_p, force, _sync)

    def chain_transcript():  # A: everything that needs words, in order
        words_p = os.path.join(an, "words.json")
        _step("transcribe (word-level)", words_p, force,
              lambda: transcribe.transcribe(video, words_p))
        words = speech.words_from(words_p)

        srt_p = os.path.join(an, f"{name}.srt")
        _step("clean SRT (DaVinci)", srt_p, force,
              lambda: speech.clean_srt(words, srt_p))

        speech_p = os.path.join(an, "speech_map.json")
        _cpu_step("speech map (silence + fillers)", speech_p, force, lambda: json.dump(
            speech.speech_map(video, words), open(speech_p, "w"), indent=1))

        topics_p = os.path.join(an, "topics.json")
        _step("topic clips (TF-IDF)", topics_p, force, lambda: json.dump(
            topics.topic_clips(topics.sentences(words)), open(topics_p, "w"), indent=1))

        from . import clearance
        guest_p = os.path.join(an, "guest_blocks.json")
        _step("third-party content scan (ownership cues)", guest_p, force,
              lambda: clearance.write_guest_blocks(root, topics.sentences(words)))

    def chain_scenes():  # D
        scenes_p = os.path.join(an, "scenes.json")
        _cpu_step("scene cuts", scenes_p, force, lambda: json.dump(
            scenes.scene_cuts(video), open(scenes_p, "w")))

    def chain_loudness():  # E
        loud_p = os.path.join(an, "loudness.json")
        _cpu_step("loudness map (R128)", loud_p, force, lambda: json.dump(
            audio_mod.loudness(video), open(loud_p, "w")))

    def chain_diarize():  # F: extracts its own wav -> independent of transcribe
        from . import diarize
        turns_p = os.path.join(an, "speaker_turns.json")

        def _diarize():
            try:
                return diarize.run(video, turns_p)
            except RuntimeError as e:
                # loud, honest degrade: the artifact records the failure and is
                # marked unverified; the transcript-cue heuristic stays the
                # only (clearly labelled) guess. Never pretend single-speaker.
                print(f"[diar ] DIARIZATION UNAVAILABLE -- voice identity is "
                      f"UNVERIFIED for this session.\n{e}")
                json.dump(diarize.unavailable_artifact(e), open(turns_p, "w"),
                          indent=1)
        # an unavailable artifact is retried every run (mirrors the
        # visual-stage rule: incomplete work is never a cache hit)
        _step("speaker diarization (local pyannote)", turns_p,
              force or diarize.needs_rerun(turns_p), _diarize)

    def chain_visual():  # B: network-bound, needs nothing from the others
        from . import visual
        vis_json = os.path.join(an, "visual_review.json")
        vis_md = os.path.join(an, "visual_review.md")
        # An artifact that records missing ranges is not a cache hit: re-run
        # the stage so the holes are retried. The per-chunk cache inside
        # visual.review makes this cost only the missing chunks.
        force_vis = force or visual.needs_rerun(vis_json)
        if force_vis and not force:
            print("[note] visual_review.json records INCOMPLETE coverage; "
                  "re-running the visual stage to fill the holes (cached "
                  "chunks are free)")
        _step("visual review (Gemini)", vis_json, force_vis,
              lambda: visual.review(video, vis_json, vis_md, crop=crop, project=name))

    chains = [("session", chain_session), ("transcript", chain_transcript),
              ("scenes", chain_scenes), ("loudness", chain_loudness),
              ("diarize", chain_diarize)]
    if not skip_visual:
        chains.append(("visual", chain_visual))

    errors = []
    with ThreadPoolExecutor(max_workers=len(chains)) as ex:
        futures = [(cname, ex.submit(fn)) for cname, fn in chains]
        for cname, fu in futures:
            try:
                fu.result()
            except Exception as e:  # noqa: BLE001 — every chain must finish
                errors.append((cname, e))
                print(f"[FAIL] {cname} chain: {e}")

    # the summary reflects whatever artifacts exist, even on partial failure,
    # so a rerun resumes from a truthful picture
    summary_p = os.path.join(an, "ANALYSIS.md")
    _summary(name, an, summary_p)
    print(f"\nbundle: {an}")
    print(open(summary_p).read())
    if errors:
        raise RuntimeError(
            "analyze finished with failing stage chain(s): "
            + "; ".join(f"{c}: {e}" for c, e in errors))
    return root


def _summary(name, an, out_path):
    def j(fname):
        p = os.path.join(an, fname)
        return json.load(open(p)) if os.path.exists(p) else None

    from . import visual
    sm = j("speech_map.json") or {}
    loud = j("loudness.json") or {}
    sc = j("scenes.json") or []
    tp = j("topics.json") or []
    vr_art = j("visual_review.json")
    vr = visual.sequences(vr_art)
    vr_missing = visual.missing_ranges(vr_art)
    sess = j("session.json")

    dur = sm.get("duration_s", 0)
    lines = [f"# Analysis: {name}", "",
             f"- Duration: {media.fmt(dur)} | talk ratio {sm.get('talk_ratio', '?')} | "
             f"{sm.get('wpm', '?')} wpm | {sm.get('filler_count', '?')} fillers",
             f"- Loudness: integrated {loud.get('integrated_lufs', '?')} LUFS, "
             f"true peak {loud.get('true_peak_dbtp', '?')} dBTP "
             f"(target -14 / -1; delta is what ASSEMBLE will correct)",
             f"- Scene cuts: {len(sc)} "
             f"({sum(1 for c in sc if isinstance(c, dict) and c.get('score', 0) >= 0.12)} strong) | "
             f"topic segments: {len(tp)} | visual sequences: {len(vr)}"
             + (f" (INCOMPLETE: {len(vr_missing)} missing range(s))" if vr_missing else "")]
    for m in vr_missing:
        lines.append(f"- MISSING VISUAL COVERAGE [{m.get('start_abs')}-{m.get('end_abs')}]: "
                     f"{m.get('error', 'chunk failed')}")
    if sess:
        lines.append(f"- Facecam sync: offset {sess['facecam_offset_s']}s "
                     f"(confidence {sess['confidence']})")
    if vr:
        lines += ["", "## Top visual moments", ""]
        for s in vr[:10]:
            hook = " HOOK" if s.get("strong_hook") else ""
            lines.append(f"- [{s['start_abs']}-{s['end_abs']}] "
                         f"T{s.get('trailer_score', '?')}/S{s.get('short_score', '?')}{hook} "
                         f"{s.get('label', '')}: {s.get('what_happens', '')}")
    peaks = (loud.get("energy_peaks") or [])[:8]
    if peaks:
        lines += ["", "## Audio energy peaks (reaction/hook candidates)", ""]
        lines += [f"- {media.fmt(p['t'])} ({p['above_median']:+.1f} dB over baseline)" for p in peaks]
    if tp:
        lines += ["", "## Topic segments", ""]
        for i, c in enumerate(tp[:20], 1):
            lines.append(f"- {i}. {media.fmt(c['s'])}-{media.fmt(c['e'])} "
                         f"(~{int(c['e'] - c['s'])}s) {c['text'][:110]}")
    lines += ["", "## Artifacts", ""]
    for f in sorted(os.listdir(an)):
        if f != "ANALYSIS.md":
            lines.append(f"- `{f}`")
    open(out_path, "w").write("\n".join(lines) + "\n")
