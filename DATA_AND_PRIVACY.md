# Data and privacy

Reelly is local-first, not local-only. Read this document before using confidential, client, employee, customer, or biometric media.

## Local by default

The following normally stay on the machine running Reelly:

- source files copied into the project workspace;
- ffmpeg/ffprobe processing;
- cached analysis artifacts, transcripts, captions, plans, renders, and reports;
- Apple-Silicon transcription with mlx-whisper;
- local pyannote diarization after model download;
- local MediaPipe face/subject analysis;
- DaVinci Resolve handoff files.

Project workspaces default to `~/reelly-projects` and may contain personal data, voices, faces, transcripts, source URLs, and derived content. Protect, retain, and delete those directories according to your own policy. They are excluded from this repository by `.gitignore`, but Git cannot protect files stored elsewhere.

## Cloud providers

A cloud-assisted command sends only to the provider selected for that feature. Depending on the command, transmitted material can include prompts, transcript text, frame images, video excerpts, source images, OCR text, analysis summaries, or generation references.

| Provider | Typical Reelly use |
|---|---|
| Google Gemini | video/frame understanding, visual review, placement, quality checks, sizzle surveys |
| OpenAI | language-based edit planning and copy refinement |
| FAL | music, sound effects, image, and motion generation |
| Anthropic | optional PRISM analysis |
| Hugging Face | model downloads and gated-model authentication |

Provider processing, retention, training, region, and deletion behavior is controlled by your provider account and its terms. Reelly cannot change those policies. Do not enable a provider for material you are not authorized to upload.

Reelly does not silently substitute a different cloud engine when the selected engine lacks a key.

## Other network access

- PRISM can use `yt-dlp` to download a URL you provide. The destination site receives the usual network request information.
- Some graphics paths may fetch remote fonts or model assets on first use.
- Model libraries may contact their registries to download weights.

Use an offline environment and pre-provisioned assets when those requests are not acceptable.

## Credentials

Keys resolve in this order:

1. environment variables;
2. `~/.reelly/config.json`.

Never commit credentials. Keep the config file at mode `0600`. `reelly setup` reports only whether a key is present, never its value.

## No publishing or telemetry

Reelly does not post to social networks and does not contain a general analytics or telemetry client. It creates files for a human to review and publish.

## Your responsibilities

Before processing or publishing media:

- obtain the necessary rights and participant consent;
- avoid uploading confidential media to an unapproved provider;
- check whether faces, voices, names, or transcripts are personal or biometric data;
- comply with applicable retention, deletion, employment, and privacy requirements;
- review every output for hallucinated text, unsafe content, trademarks, and unwanted disclosure;
- enable platform AI/generated-media and commercial-content labels when required.
