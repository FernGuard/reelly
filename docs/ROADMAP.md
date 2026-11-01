# Roadmap

## Stable core

- Ingest recordings into isolated project workspaces.
- Produce transcripts, scene maps, speech maps, and loudness analysis.
- Generate structured edit plans with auditable reasons.
- Render short-form and long-form outputs.
- Export captions and DaVinci Resolve handoff artifacts.
- Run deterministic quality checks.

## In progress

- Transcription beyond Apple Silicon.
- Text behind the subject and depth parallax on the GPU (both run on the CPU today).
- Golden-frame references for more GPU tiers (CI records them for its runners).

- Broader non-Apple transcription support.
- Clearer command-level capability and cost preflight.
- Reproducible evaluation fixtures using synthetic media.
- Better optional-dependency grouping for PRISM, OCR, and diarization.

## Later

- More effect presets and content types, contributed as effect files.

- Additional local model backends.
- A documented plugin system for organization-specific products and accounts.
- More portable editor handoff formats.
