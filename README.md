# Reelly

Reelly is a local-first video editing engine. Give it a recording and it can analyze, transcribe, plan cuts, render captioned MP4s, and create a DaVinci Resolve handoff.

**Reelly never publishes content. You review and publish every output.**

**Reelly contains no API credentials. You must provide your own keys for cloud features.** When a command needs a missing key, it stops and tells you the exact environment variable and provider signup URL. Key values are never printed by `reelly setup`.

## Quick start

### 1. Install system requirements

You need:

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/) (recommended) or pip — `curl -LsSf https://astral.sh/uv/install.sh | sh` (Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` or `pip install uv`)
- ffmpeg and ffprobe on `PATH`

```sh
# macOS
brew install ffmpeg

# Debian/Ubuntu
sudo apt install ffmpeg

# Windows
winget install Gyan.FFmpeg
```

### 2. Install Reelly

```sh
git clone https://github.com/FernGuard/reelly.git
cd reelly
uv sync
uv run reelly setup
```

Without uv:

```sh
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e .
reelly setup
```

`reelly setup` is a diagnostic check, not an installer or credential wizard. A fresh install can run local checks before any API key is configured.

### 3. Provide the keys for the features you want

Reelly does not create provider accounts or keys for you. Create keys directly with each provider, then export them in your shell:

| Provider | Environment variable | Used for | Create your key |
|---|---|---|---|
| Gemini | `GEMINI_API_KEY` | visual review, occupancy, QC, sizzle survey | https://aistudio.google.com/apikey |
| OpenAI | `OPENAI_API_KEY` | default editor brain | https://platform.openai.com/api-keys |
| FAL | `FAL_KEY` | music, sound effects, motion generation | https://fal.ai/dashboard/keys |
| Anthropic | `ANTHROPIC_API_KEY` | optional PRISM analysis | https://console.anthropic.com/settings/keys |
| Hugging Face | `HUGGINGFACE_TOKEN` | optional gated diarization models | https://huggingface.co/settings/tokens |

```sh
export GEMINI_API_KEY="your-key"
export OPENAI_API_KEY="your-key"
export FAL_KEY="your-key"
uv run reelly setup
```

Do not put real values in `.env.example`, repository files, commits, issues, or logs.
Reelly does **not** load a `.env` file. Export the variables, or write `~/.reelly/config.json`.

Instead of environment variables, you may use `~/.reelly/config.json`. Reelly creates it with owner-only permissions when written by its configuration helper. If you create it manually:

```sh
mkdir -p ~/.reelly
cat > ~/.reelly/config.json <<'EOF'
{
  "gemini_api_key": "your-key",
  "openai_api_key": "your-key",
  "fal_key": "your-key",
  "anthropic_api_key": "your-key",
  "huggingface_token": "your-key",
  "projects": "~/reelly-projects"
}
EOF
chmod 700 ~/.reelly
chmod 600 ~/.reelly/config.json
```

On Windows PowerShell, create `%USERPROFILE%\.reelly\config.json` with the same JSON, then restrict it to your user (`icacls %USERPROFILE%\.reelly\config.json /inheritance:r /grant:r %USERNAME%:R`). Reelly still writes mode `0600` when it creates the file itself.

Environment variables take precedence over the config file.

### 4. Run your first local analysis

```sh
uv run reelly analyze path/to/clip.mp4 --skip-visual
```

The project is created under `~/reelly-projects`. Set `REELLY_PROJECTS` to change that location. On Windows, source files are copied into the project if the filesystem cannot create a symlink.

For cloud-assisted analysis and editing after configuring keys:

```sh
uv run reelly analyze path/to/clip.mp4
uv run reelly cut <project-name>
```

Useful commands:

```text
reelly setup               check binaries and key availability without printing values
reelly analyze <video>     analyze a recording
reelly cut <project>       plan, render, and run quality checks
reelly budget              show recorded AI spend
reelly --help              list every command
```

## What works without each key

- No Gemini key: `analyze --skip-visual` runs local stages; visual review and vision QC are unavailable.
- No OpenAI key: use `direct --brain gemini` with a Gemini key, or `direct --no-ai`.
- No FAL key: cached/generated assets already on disk remain usable; new media generation is unavailable.
- No Anthropic key: only Anthropic-backed PRISM analysis is unavailable.
- No Hugging Face token: core editing still works; gated speaker diarization is unavailable.

A selected cloud engine never silently switches to a different paid provider.

## Optional features

```sh
uv sync --extra diarize    # speaker diarization
uv sync --extra asr-fast   # faster English ASR on Apple Silicon only
uv sync --extra prism      # URL ingestion and OCR helpers for PRISM
```

Diarization requires accepting the terms for:

- `pyannote/speaker-diarization-3.1`
- `pyannote/segmentation-3.0`
- `pyannote/speaker-diarization-community-1`

See [docs/SETUP.md](docs/SETUP.md) for the complete setup guide.

## Platform support

| Platform | Status |
|---|---|
| macOS on Apple Silicon | Most complete local path, including mlx-whisper |
| macOS Intel, Linux, Windows | ffmpeg, configuration, and most commands work; mlx-whisper is unavailable |

Chrome or Chromium is required only for browser-rendered cards. Reelly resolves tools from `PATH`; `FFMPEG`, `FFPROBE`, and `CHROME_PATH` can override discovery.

## Private customization

The repository ships only neutral example accounts and products. Keep organization names, domains, campaign IDs, logos, vocabulary, verdicts, and analytics outside Git:

- `~/.reelly/accounts.json`
- `~/.reelly/products.json`
- `~/.reelly/config.json`
- `~/.reelly/brandkit/`
- your project directory under `REELLY_PROJECTS`

See [docs/CUSTOMIZATION.md](docs/CUSTOMIZATION.md).

## Data and privacy

Most media processing is local, but cloud-assisted commands send data to the selected provider. Reelly has no telemetry and never auto-publishes. Read [DATA_AND_PRIVACY.md](DATA_AND_PRIVACY.md) before processing confidential or personal media.

You are responsible for source-media rights, participant consent, provider terms, platform disclosure, and generated-media labeling.

## Development

```sh
uv run --with pytest python -m pytest tests/ -q
```

Tests use synthetic fixtures and should not need production keys.

## Contributing and security

- [CONTRIBUTING.md](CONTRIBUTING.md)
- [SECURITY.md](SECURITY.md)
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

Never submit client data, real metrics, transcripts, personal identifiers, source URLs, or credentials.

## License

[MIT](LICENSE). You may use, copy, modify, and distribute Reelly under the license terms.
