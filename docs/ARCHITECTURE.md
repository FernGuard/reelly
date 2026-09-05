# Reelly Architecture

Six stages. Each stage writes structured artifacts to a per-project workspace so any stage can be re-run alone, results are inspectable, and the engine's decisions are auditable.

```
INGEST -> UNDERSTAND -> DIRECT -> ASSEMBLE -> JUDGE -> LEARN
                                     ^                    |
                                     +---- playbook ------+
```

## 0. Project workspace

Every source video becomes a project folder:

```
projects/<name>/
  source/            original files (screen, facecam, external audio)
  analysis/          everything UNDERSTAND produces (json, srt, maps)
  edl/               cut decisions as data (before any rendering)
  deliverables/
    full/            full-video edit (mp4 + resolve handoff)
    cuts/            viral cuts per platform
    captions/        srt per deliverable
    audio/           music/sfx stems, always separate files
  qc/                judge reports, variant scores
  ledger.json        AI spend for this project
  verdicts.md        human feedback on this project's deliverables
```

## 1. INGEST

- Watch folder (or CLI). New media is probed (ffprobe), fingerprinted, and registered in a project database (sqlite).
- Multi-file awareness: screen + facecam + mic are one *session*, synced by audio waveform cross-correlation (handles OBS split recordings and camera clock drift).
- Proxy generation for fast iteration on long recordings.

## 2. UNDERSTAND

All analysis is cached and content-addressed; nothing runs twice.

- **Transcript**: mlx-whisper word-level timestamps on Apple Silicon; other platforms skip ASR loudly and continue.
- **Speech map**: silences (silencedetect), filler words (um, uh, like, you know), restarts and repeated takes (near-duplicate sentence detection).
- **Speaker map**: who talks when (needed for podcast/interview cuts).
- **Shot/scene detection**: visual cut points, so edits never land mid-shot awkwardly.
- **Visual understanding**: Gemini video analysis in chunks (proven pipeline) scoring trailer/short potential, describing on-screen action, flagging highlights, reading on-screen UI text.
- **Audio map**: loudness curve (EBU R128), music vs speech segments, claps/laughs/reaction spikes (energy peaks are hook candidates).
- **Face/subject tracking**: where the subject is in frame, to drive smart 9:16 reframes instead of blind center-crop.

## 3. DIRECT (the editor brain)

Consumes UNDERSTAND artifacts + the Playbook. Produces EDL-as-data (json cut lists), no rendering.

- **Full edit plan**: dead air out, filler out, retakes collapsed, chapters marked; pacing rules from the playbook (max seconds without a visual change, etc.).
- **Viral cut plans**: candidate moments ranked by combined transcript-topic score, visual score, and audio-energy score; each cut gets a hook plan (text hook, cold-open frame, or spoken line pulled forward), a caption style, and an ending (loop point or CTA).
- **Per-platform variants**: length, aspect, caption style, safe zones, and disclosure labels differ per platform; one cut plan fans out into platform variants.
- **Sound design plan**: music bed (genre/energy/length spec for generation), SFX cues (whooshes on cuts, risers into reveals, impacts on hook text), ducking automation under speech.
- **Title plan**: which title cards, lower thirds, and end cards, using the proven animated-card renderer (typewriter/slam, alpha ProRes).

Every decision carries a `because:` field referencing the playbook rule that produced it. That is what makes the LEARN stage possible.

## 4. ASSEMBLE

Two outputs per deliverable, always:

- **Resolve handoff**: timeline via FCPXML/OTIO import AND (preferred) the DaVinci Resolve scripting API (Resolve ships a Python API that can create projects, import media, build timelines, add markers, and apply LUTs directly). Music, SFX, captions, and titles arrive as separate tracks. Markers carry the engine's notes ("hook", "trim candidate", "SFX: riser").
- **Rendered MP4**: ffmpeg render of the same EDL for post-now use. Loudness normalized to platform target (-14 LUFS integrated, true peak -1 dBTP), styled captions burned via PNG overlays, blurred-fill or tracked-crop reframe for 9:16.

## 5. JUDGE

- **QC gates (deterministic)**: caption/word sync drift, caption overlap, audio clipping, loudness compliance, black/frozen frames, cut-on-word violations, safe-zone violations (platform UI overlap), first-frame legibility.
- **AI judge (optional, budget-gated)**: 2-3 variants of a cut scored against the playbook rubric (hook strength, pacing, clarity, ending); only the winner ships. Off by default to stay under budget; on for hero content.

## 6. LEARN

Three inputs, one output: a new version of the Playbook.

- **Performance**: a user-supplied private analytics export can be joined to the EDL decisions that produced it. Each post gets an outlier score relative to the supplied channel median. Real exports and results stay outside this repository.
- **Verdicts**: one-line human ratings (keep/kill/why) stored per deliverable, parsed into structured taste rules.
- **Trend research**: optional research can propose playbook diffs. Proposals require human approval before they are applied.

The Playbook is versioned in git. Every deliverable records which playbook version cut it. That is the self-improvement loop made auditable.

## Cross-cutting

- **Cost ledger**: every AI call logged with tokens/price; monthly cap enforced with a warning at 80 percent. The cap is a default, not a wall: `reelly budget sprint <cap> --days N --reason "..."` raises it temporarily for heavy pushes, and the sprint is recorded so spikes stay explainable.
- **Provenance**: deliverables that contain AI-generated media get a manifest so the human knows to toggle TikTok's AI label.
- **Determinism**: same source + same playbook version + same seed = same output. Cache everything.
- **Eval bench**: golden footage with known-good outputs; playbook or code changes run the bench before they merge, so the engine cannot silently get worse.
