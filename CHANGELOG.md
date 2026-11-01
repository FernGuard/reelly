# Changelog

All notable changes to Reelly. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/) (before 1.0, a minor version may
change behaviour; every such change is listed under "Upgrading").

## [0.2.0] - 2026-11-01

Reelly 0.2.0 turns a recording into clips that look designed, not assembled:
type that moves to the music, effects that react to what happens in the cut,
and safety checks on every file, all without a paid call.

### Added

**Look and motion**
- Kinetic type drawn on the GPU (signed-distance-field glyphs): the hot word of
  every hook, line and call to action pops on the beat of the music. Two
  entrances (`rise`, `blocklock`) and two exits (`fade`, `petals`).
- An effects platform: GLSL effect files with typed, clamped inputs; versioned,
  brand-neutral presets per placement (`endcard`, `ambient`, `text`, `finish`);
  brand kits choose and tune them. See [docs/EFFECTS.md](docs/EFFECTS.md).
- Presets: smoke end card with an ink transition from the last frame, ambient
  smoke, embers, comic impact lines, manga focus lines, a smoky text aura, and
  an experimental folk-print finish. Bloom as a preset setting.
- A control track per cut (beat grid, hot-word pops, sharp sounds, loudness,
  real impacts) that every effect reads. Impacts are confirmed and located by
  a vision model through the Codex CLI.
- Content types (`gameplay`, `talking-head`, `podcast`, `screen-demo`) that
  decide which effects suit the footage and whether faces are protected.
- `reelly parallax IMAGE`: a 2.5D camera move on a still from estimated depth
  (Depth Anything V2 Small).
- Experimental: text behind the subject on talking-head and podcast footage,
  with a readability guard and edge-aware mattes.
- 60 fps output (per brand), a richer encode, brand music and SFX with
  beat-accurate pops, loudness-levelled soundtrack mixes at -14 LUFS.

**Editing**
- Hook and opening frame chosen together from several written variants, judged
  for stopping power; the brand's copy brief and approved and rejected lines
  feed every writing step.
- Zero-paid vision: the Codex CLI reads contact sheets of the recording, so
  silent footage gets cut plans without a paid call.
- Configurable clip length, one-beat cuts for picture-only footage, the game
  view found inside capture chrome, action-following and full-width reframes,
  and one layout that stays clear of TikTok, Reels and Shorts interface.

**Quality and safety**
- Photosensitivity gates on every deliverable: Reelly's WCAG 2.3.1 flash
  check, plus EA IRIS where installed.
- Measured text contrast, with plates only where text would be unreadable.
- Fallbacks are visible: anything that did not render as configured is listed
  under its cut in `REVIEW.md` and raised by the judge.
- Golden-frame tests for every effect, per renderer tier.

**Platform and setup**
- GPU rendering through Chrome on every OS: Direct3D 11 on Windows, Metal on
  macOS, OpenGL on Linux, with an automatic software fallback.
- `reelly brand check` and `reelly brand migrate`; every render validates the
  active brand first.
- `reelly config`, `reelly models [fetch]`, `reelly --version`, and the
  global `--local-only` switch (nothing leaves the machine).
- `reelly setup` now test-renders on the GPU and reports the renderer, and checks models, IRIS and the active brand.
- Windows support for tests and renders; UTF-8 everywhere.

### Changed

- The default editor brain is `auto`: the signed-in Codex CLI (the Claude CLI
  when asked for by name), else heuristics. It was `gpt` (paid API).
- Paid providers are off unless a run passes `--allow-paid`.
- The end card's backdrop dims the last frame instead of darkening it to black.
- Noise in every effect uses a sine-free hash, so all GPUs draw the same frames.

### Upgrading from 0.1.0

- **Paid calls are opt-in.** Runs that relied on Gemini or the OpenAI API now
  skip those steps (and say so) unless you pass `--allow-paid`. Sign in to the
  Codex CLI for the zero-paid brain and vision.
- **Chrome is required** for kinetic type and effects. Without it, text is
  static and effects are skipped, which `REVIEW.md` reports.
- **Larger, slower encodes.** Final encodes moved to a higher-quality preset
  (`medium`, CRF 17), and brands can choose 60 fps.
- **New dependency:** `websockets` (installed by `uv sync`).
- **brand.json schema 1.** Add `"schema": 1`. Settings added during
  development (`fx`, `outro_fx`, `fps`, `reframe`, `copy.clip_len`) moved into
  `video`, `text` and `effects`; `reelly brand migrate NAME --write` rewrites a
  kit and `reelly brand check NAME` confirms it.
- **Settings.** Every environment setting is listed in
  [docs/SETTINGS.md](docs/SETTINGS.md); `reelly config` shows the values in
  effect.

### Known limitations

- Transcription runs on Apple Silicon only (mlx-whisper); other platforms cut
  on picture and audio.
- Text behind the subject and depth parallax run on the CPU: a 30-second cut
  with text behind the subject adds several minutes.
- EA IRIS is tested on Windows only.
- The vision pass and impact check need the Codex CLI signed in; without it,
  planning is heuristic and there are no impact marks.
- Golden references ship for three tiers (RTX 5090 Direct3D 11, Apple M4
  Metal, SwiftShader on macOS); other GPUs skip the golden test until they
  record their own.

## [0.1.0]

The first public version: project workspaces, transcripts and analysis,
auditable edit plans, short-form and long-form renders, captions, DaVinci
Resolve handoff, and deterministic quality checks.

[0.2.0]: https://github.com/FernGuard/reelly/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/FernGuard/reelly/releases/tag/v0.1.0
