# Reelly

[![tests](https://github.com/FernGuard/reelly/actions/workflows/tests.yml/badge.svg)](https://github.com/FernGuard/reelly/actions/workflows/tests.yml)

**Long recordings in. Finished vertical clips out.**

![A raw gameplay recording becomes a finished vertical clip](https://github.com/FernGuard/reelly/releases/download/v0.2.0/reelly-hero.webp)

Reelly finds the moments worth sharing in a stream, a playtest, a podcast or a
talk, and hands back 9:16 clips ready to post. It runs on your machine, makes
no paid calls unless you ask, and never publishes for you.

![Kinetic type on the beat, effects that react to the hit, an end card that inks in](https://github.com/FernGuard/reelly/releases/download/v0.2.0/reelly-features.webp)

- **Edits like an editor.** Strong openings, a payoff at the end, a hook on the first frame.
- **Type on the beat.** Drawn on the GPU; the hot word pops on your soundtrack's beat.
- **Effects that react.** Smoke, embers and impact lines follow what happens in the cut.
- **Safe before you see it.** Flash, contrast, safe-zone and loudness checks on every file.

<sub>Footage: Curses Cleared by Patolli.</sub>

## Quick start

You need Python 3.11+, [uv](https://docs.astral.sh/uv/), ffmpeg and Google Chrome.

```sh
git clone https://github.com/FernGuard/reelly.git && cd reelly
uv sync
uv run reelly setup                          # checks everything, prints no secrets

uv run reelly analyze recording.mp4          # what happens, when
uv run reelly cut recording                  # finished clips + REVIEW.md
```

Each cut ships twice (with music, and without for in-app sounds), with a
posting block and one `REVIEW.md` to decide from. Full guide:
[docs/SETUP.md](docs/SETUP.md).

## Make it yours

A brand kit sets your type, colours, voice, soundtrack and look. Start from
`examples/brandkit/demo`, or write one `brand.json`:

```json
{
  "schema": 1,
  "name": "mybrand",
  "accent": "#7c5cff",
  "fonts": {"headline": "Display-Bold.ttf", "caption": "Text-Bold.ttf"},
  "text": {"motion": "blocklock", "exit": "petals"},
  "effects": {"endcard": "smoke", "ambient": ["smoke", "embers", "impact"], "text": "smoke"}
}
```

`reelly brand check mybrand` validates it before any render.
[Every option](docs/BRAND_KIT.md) · [Write your own effect](docs/EFFECTS.md)

## Zero paid calls by default

Thinking and seeing run on the [Codex CLI](https://github.com/openai/codex)
you are signed in to (your ChatGPT plan, no per-call bill). Paid providers only
run with `--allow-paid`; `--local-only` keeps everything on your machine.
[What leaves the machine](DATA_AND_PRIVACY.md)

## Platforms

| | macOS (Apple Silicon) | Windows | Linux |
|---|---|---|---|
| GPU effects and type | Metal | Direct3D 11 | OpenGL |
| No GPU | software renderer | software renderer | software renderer |
| Transcription | yes | not yet | not yet |

On an Apple M4, a 15-second cut with every effect renders in about a minute.

## Docs

[Setup](docs/SETUP.md) · [Brand kit](docs/BRAND_KIT.md) ·
[Effects](docs/EFFECTS.md) · [Settings](docs/SETTINGS.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Changelog](CHANGELOG.md) ·
[Privacy](DATA_AND_PRIVACY.md) · [Contributing](CONTRIBUTING.md) ·
[Notices](THIRD_PARTY_NOTICES.md)

You are responsible for the rights to your media, participant consent and
platform disclosure labels.

[MIT](LICENSE) © 2026 Fernando Guardiola
