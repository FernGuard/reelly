# Reelly

**Long recordings in. Finished vertical clips out.**

Reelly watches a stream, a playtest, a podcast or a talk, finds the moments
worth sharing and hands back 9:16 clips ready to post: hooked, captioned, set
to music, on brand, loudness-matched and checked for flashing. There is no
timeline to scrub and no keyframe to set. It runs on your machine and makes no
paid calls unless you ask it to.

**Reelly never publishes.** You review every file and post it yourself.

## What it does

**It edits like an editor.** Reelly picks moments that open strong and end on
a payoff, writes a hook for the very first frame, and cuts on the picture alone
when a recording has no narration.

**Type that moves to the music.** Kinetic type is drawn on the GPU, and the hot
word of every line pops on the beat of your soundtrack. Words can rise into
place or drop in and lock like falling blocks, then leave in a scatter of
petals.

**Effects that know what happened.** Each cut gets a control track (its beats,
its sharpest sounds, its loudness and its real impacts) and every effect reads
it: smoke and embers that breathe with the audio, comic impact and focus lines
that land where a hit actually happens, and an end card that inks in from the
last frame.

**Safe before you see it.** Every file passes a WCAG 2.3.1 flash check (plus
EA IRIS where installed), text contrast, platform safe zones and a -14 LUFS
loudness target. `REVIEW.md` lists anything that fell back, so a lesser render
never ships quietly.

## How it works

```sh
reelly analyze recording.mp4      # transcript, scenes, audio, what happens on screen
reelly cut recording              # plans, renders, judges
```

Each project gets `deliverables/final/` with two files per cut (`cut_01.mp4`
with music, `cut_01_trending.mp4` without, for in-app sounds), a posting block
per cut, and one `REVIEW.md` to watch and decide from.

## Quick start

You need Python 3.11+, [uv](https://docs.astral.sh/uv/), ffmpeg and Google
Chrome (kinetic type and every effect render in it).

```sh
brew install ffmpeg               # Debian/Ubuntu: sudo apt install ffmpeg
                                  # Windows: winget install Gyan.FFmpeg
git clone https://github.com/FernGuard/reelly.git
cd reelly
uv sync
uv run reelly setup               # checks everything; prints no secrets
```

A fresh install renders with Reelly's neutral defaults. To see the full look,
start from the example brand:

```sh
mkdir -p ~/.reelly/brandkit/brands
cp -r examples/brandkit/demo ~/.reelly/brandkit/brands/
uv run reelly analyze recording.mp4
uv run reelly cut recording --brand demo
```

The full guide is [docs/SETUP.md](docs/SETUP.md).

## Make it yours

A brand kit holds your type, colours, voice, soundtrack and look, outside the
repository. One `brand.json` decides it all:

```json
{
  "schema": 1,
  "name": "mybrand",
  "accent": "#7c5cff",
  "fonts": {"headline": "Display-Bold.ttf", "caption": "Text-Bold.ttf"},
  "video": {"fps": 60, "clip_len": [25, 30]},
  "text": {"motion": "blocklock", "exit": "petals"},
  "effects": {"endcard": "smoke", "ambient": ["smoke", "embers", "impact"], "text": "smoke"}
}
```

`reelly brand check mybrand` validates every key and effect name before a
render starts. See [docs/BRAND_KIT.md](docs/BRAND_KIT.md) for every option and
[docs/EFFECTS.md](docs/EFFECTS.md) to write your own effects.

## Zero paid calls by default

Reelly never calls a provider that bills per call unless a run says so with
`--allow-paid`. The editor brain and the vision pass use a subscription CLI
you are already signed in to: the [Codex CLI](https://github.com/openai/codex)
runs on your ChatGPT plan's quota, with no per-call bill. With nothing signed
in, Reelly plans with heuristics.

`--local-only` (or `REELLY_LOCAL_ONLY=1`) goes further: nothing leaves the
machine at all. What each command sends, and where, is in
[DATA_AND_PRIVACY.md](DATA_AND_PRIVACY.md).

## Platforms

| | macOS (Apple Silicon) | Windows | Linux |
|---|---|---|---|
| Effects and kinetic type on the GPU | Metal | Direct3D 11 | OpenGL |
| Without a usable GPU | software renderer | software renderer | software renderer |
| Transcription | mlx-whisper | not yet | not yet |
| EA IRIS flash analysis | not tested | yes (prebuilt app) | not tested |

Measured on an Apple M4: the smoke end card renders at 35 fps through Metal
against 9 fps on the software renderer, and a 15-second cut with every effect
renders in about a minute once its footage is cut. On an RTX 5090 a full
1080x1920 effect frame draws and reads back at 635 fps.

## Optional features

```sh
uv run reelly models fetch depth        # depth for `reelly parallax` (also: uv sync --extra depth)
uv run reelly models fetch segmenter    # person masks for text behind the subject
uv sync --extra diarize                 # who speaks when
uv sync --extra asr-fast                # faster English transcription on Apple Silicon
```

`reelly parallax key-art.png` turns a still into a slow 2.5D camera move.

## Documentation

| | |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | install, keys, models, uninstall |
| [docs/BRAND_KIT.md](docs/BRAND_KIT.md) | every `brand.json` option |
| [docs/EFFECTS.md](docs/EFFECTS.md) | the effects platform, writing an effect, golden frames |
| [docs/SETTINGS.md](docs/SETTINGS.md) | every environment setting (`reelly config` shows yours) |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | how the stages fit together |
| [CHANGELOG.md](CHANGELOG.md) | what changed, and upgrading |
| [DATA_AND_PRIVACY.md](DATA_AND_PRIVACY.md) | what stays local, what does not |

## Development

```sh
uv run --with pytest python -m pytest tests/ -q
```

Tests use synthetic fixtures and need no keys. Effects are covered by golden
frames per renderer; see [docs/EFFECTS.md](docs/EFFECTS.md).

[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) ·
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

You are responsible for the rights to your source media, participant consent,
provider terms and platform disclosure labels.

## License

[MIT](LICENSE) © 2026 Fernando Guardiola
