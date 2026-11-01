# Data and privacy

Reelly is local-first, not local-only. Read this document before using confidential, client, employee, customer, or biometric media.

## Local by default

The following normally stay on the machine running Reelly:

- source files linked (or copied on Windows) into the project workspace;
- ffmpeg/ffprobe processing;
- cached analysis artifacts, transcripts, captions, plans, renders, and reports;
- Apple-Silicon transcription with mlx-whisper;
- local pyannote diarization after model download;
- local MediaPipe face analysis and person masks;
- GPU effects, kinetic type and depth estimation (Depth Anything V2 Small, on the CPU);
- the photosensitivity checks (Reelly's own and EA IRIS);
- DaVinci Resolve handoff files.

Project workspaces default to `~/reelly-projects` and may contain personal data, voices, faces, transcripts, source URLs, and derived content. Protect, retain, and delete those directories according to your own policy. They are excluded from this repository by `.gitignore`, but Git cannot protect files stored elsewhere.

## What leaves the machine, by command

With no flags, Reelly makes **no paid calls**, and uses a subscription CLI you
have signed in to yourself for its thinking and seeing. Each row is what a
command can send and to whom.

| Command | What it can send | To |
|---|---|---|
| `analyze` (vision pass) | contact sheets of frames from the recording | the Codex CLI (OpenAI, through your ChatGPT account), or your own `REELLY_VISION_URL` endpoint |
| `direct`, `cut` (planning) | transcript excerpts, scene and audio summaries, your brand's copy brief | the Codex CLI (or the Claude CLI with `--brain claude`) |
| `hooks`, `cut` (hook writing) | a contact sheet of opening frames, the brief, candidate lines | the Codex CLI |
| `cut`, `finalize` (impact check) | three-frame strips around each candidate impact | the Codex CLI |
| anything with `--allow-paid` | the material the chosen feature needs (frames, video excerpts, prompts) | the paid provider you configured (table below) |
| `models fetch` | a download request | Hugging Face, Google (model files) |

The Codex and Claude CLIs run under your own accounts and their terms; Reelly
starts them, passes the prompt and any images, and reads the reply. Two smaller
requests carry no content: card and overlay pages load a web font from Google
Fonts when the brand has no font files of its own, and model libraries check
the Hugging Face Hub for cached models.

**`--local-only`** (or `REELLY_LOCAL_ONLY=1`) turns all of it off: no CLI, no
provider, no vision check, no model download, no web font, and the Hugging Face
Hub is set offline (cached models only). A vision endpoint you run yourself
(`REELLY_VISION_URL`) is still used when it is on this machine or your local
network (loopback, a private address or a `.local` name). Each step then takes its local
path or is skipped, and `REVIEW.md` lists what fell back. `REELLY_SIGHT=off`
turns off only the vision pass and the impact check.

### Paid providers (only with `--allow-paid`)

| Provider | Typical Reelly use |
|---|---|
| Google Gemini | video/frame understanding, visual review, placement, quality checks, sizzle surveys |
| OpenAI API | language-based edit planning and copy refinement (`--brain gpt`) |
| FAL | music, sound effects, image, and motion generation |
| Anthropic API | optional PRISM analysis |
| Hugging Face | model downloads and gated-model authentication |

Provider processing, retention, training, region, and deletion behavior is controlled by your provider account and its terms. Reelly cannot change those policies. Do not enable a provider for material you are not authorized to upload.

Reelly does not silently substitute a different cloud engine when the selected engine lacks a key.

## Local processes

Rendering starts a headless Chrome and talks to it on `127.0.0.1` (a DevTools
port and a frame socket that only accepts the render page holding a random
token). Nothing listens on other interfaces. On a shared machine, other local
users' processes could reach the DevTools port while a render runs; render on
a machine you control.

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
