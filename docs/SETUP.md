# SETUP

Audience: a human or agent on a fresh machine. Follow top to bottom.
Reelly degrades loudly when an optional piece is missing — section 8 says what you lose.

## 1. System binaries

ffmpeg and ffprobe must be on your PATH (or set `FFMPEG` / `FFPROBE`).

| What | Install | Check |
|---|---|---|
| ffmpeg / ffprobe | `brew install ffmpeg` · `sudo apt install ffmpeg` · `winget install Gyan.FFmpeg` | `ffmpeg -version` |
| uv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` · Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` or `pip install uv` | `uv --version` |
| tesseract (PRISM OCR only) | `brew install tesseract` · `sudo apt install tesseract-ocr` · `winget install UB-Mannheim.TesseractOCR` | `tesseract --version` |
| Chrome (overlays only) | Chrome/Chromium, or `CHROME_PATH` | `uv run reelly setup` |

## 2. Python environment

Python 3.11+.

```sh
git clone https://github.com/FernGuard/reelly.git
cd reelly
uv sync                      # core
uv sync --extra diarize      # + pyannote speaker diarization (optional)
uv sync --extra asr-fast     # + parakeet-mlx fast English ASR (optional, Apple Silicon)
uv sync --extra prism        # + URL ingestion and Tesseract OCR helper (optional)
uv run reelly setup
```

Without uv:

```sh
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e .
```

Optional extras: `pip install -e '.[diarize]'` etc.

Check: `uv run python -c "import reelly.cli; print('ok')"`

## 3. API keys — you must request your own

**Reelly ships no keys.** Do not put keys in this repo, in git, or in issues.
Reelly does **not** load a `.env` file. Export variables in your shell, or write
`~/.reelly/config.json`.

Preferred: environment variables. They take precedence over `~/.reelly/config.json`.

```sh
export GEMINI_API_KEY=...      # https://aistudio.google.com/apikey
export OPENAI_API_KEY=...      # https://platform.openai.com/api-keys
export FAL_KEY=...             # https://fal.ai/dashboard/keys
# optional
export HUGGINGFACE_TOKEN=...   # https://huggingface.co/settings/tokens
export ANTHROPIC_API_KEY=...   # https://console.anthropic.com/settings/keys
```

Also accepted, same values:

- `GOOGLE_API_KEY` or `GOOGLE_GENAI_API_KEY` for Gemini
- `FAL_API_KEY` for FAL
- `HF_TOKEN` for Hugging Face

Alternative: `~/.reelly/config.json` (create the directory, `chmod 600` the file):

```json
{
  "gemini_api_key": "...",
  "openai_api_key": "...",
  "fal_key": "...",
  "huggingface_token": "...",
  "anthropic_api_key": "...",
  "projects": "~/reelly-projects"
}
```

Check (does not print values):

```sh
uv run reelly setup
```

If a command needs a key you have not set, it exits with the variable name and the signup URL.

## 4. Models — what downloads itself vs. what you must do

**Auto-downloading (no action, first use pays the download):**

| Model | Used for | Cache location |
|---|---|---|
| `mlx-community/whisper-medium.en-mlx` | transcription (Apple Silicon) | HF hub cache |
| `parakeet-tdt-0.6b-v2` (if `REELLY_ASR=parakeet`) | fast English ASR | HF hub cache |
| MediaPipe `face_landmarker.task` (FaceMesh) | face detection / reframing | `~/.reelly/models/` |

**Manual — diarization (gated models).** After installing the extra and the
token, accept the terms on huggingface.co for **all three** repos:
`pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0`,
`pyannote/speaker-diarization-community-1`. Verify with a real download
(`huggingface-cli download pyannote/speaker-diarization-3.1` is a real check;
`model_info()` is not). Runs on Apple
GPU (MPS) automatically; `REELLY_DIAR_DEVICE=cpu` forces CPU.

**Manual — offline face fallback (only if you need face detection with no
network on first run).** FaceMesh normally auto-downloads; the offline
fallback expects:

```sh
mkdir -p ~/.reelly/models
curl -Lo ~/.reelly/models/blaze_face_short_range.tflite \
  https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite
```

## 5. Machine config

`~/.reelly/config.json`:

```json
{
  "projects": "~/where/rendered/projects/should/live",
  "logos": {"video": "/abs/path/video-logo.png", "story": "...",
            "games": "...", "adventure": "..."}
}
```

- `projects` (or env `REELLY_PROJECTS`) — defaults to `~/reelly-projects`.
- `logos` — per-studio wordmark PNGs; consumed by descriptions, overlays,
  and the brand kit build. Without them, brand moments fall back to plain
  text and the kit build skips endcards for missing studios.

Keep private accounts and products in `~/.reelly/accounts.json` and
`~/.reelly/products.json`. See [CUSTOMIZATION.md](CUSTOMIZATION.md) for exact
schemas. Do not add real organization names, domains, or campaign IDs to the
repository.

## 6. Generated asset libraries (build once, then $0)

| Library | Location | How it appears |
|---|---|---|
| Brand kit (endcards, copy bank, accent, fonts dir) | `~/.reelly/brandkit/` | `uv run python -m reelly.brandkit` (needs `logos` in config). Drop brand `.ttf`/`.otf` files into `brandkit/fonts/` to replace system-font fallback. |
| Music beds | `~/.reelly/brandkit/music/` | **Self-building**: first cut per genre generates via fal.ai and registers the bed; later cuts reuse it for $0. |
| SFX | `~/.reelly/sfx/` | Self-building the same way: first use of each named SFX generates once via fal.ai, then cached forever. |

Env overrides: `REELLY_BRANDKIT` (kit dir), `REELLY_ENDCARD=legacy`
(disable kit endcards).

## 7. Verify the whole stack

```sh
uv run --with pytest python -m pytest tests/ -q     # suite, no network needed
uv run python -m reelly.brandkit                    # kit builds / reports what's missing
uv run reelly analyze <any-short-clip.mp4> --skip-visual --out /tmp/reelly-smoke
```

The analyze summary prints a loud note for every degraded capability
(no diarizer, no transcript, etc.) — that output is the "what is missing
on this machine" report. `reelly setup` is the shorter version.

Before processing confidential media, read [../DATA_AND_PRIVACY.md](../DATA_AND_PRIVACY.md).

## 8. Environment variable reference

| Var | Effect |
|---|---|
| `GEMINI_API_KEY` | Gemini (visual review, occupancy, QC) |
| `OPENAI_API_KEY` | OpenAI editor brain |
| `FAL_KEY` | FAL music / SFX / motion |
| `ANTHROPIC_API_KEY` | PRISM analyzer |
| `HUGGINGFACE_TOKEN` / `HF_TOKEN` | gated pyannote models |
| `REELLY_PROJECTS` | projects root override |
| `FFMPEG` / `FFPROBE` | binary path override |
| `CHROME_PATH` / `REELLY_CHROME` | Chrome/Chromium path |
| `REELLY_ASR=parakeet` | fast English ASR (needs `asr-fast` extra) |
| `REELLY_DIAR_DEVICE=cpu` | force diarizer off MPS |
| `REELLY_BRANDKIT` | brand kit dir override |
| `REELLY_ENDCARD=legacy` | disable kit endcards |
| `REELLY_OCCUPANCY=gemini` | disable local occupancy hybrid |
| `REELLY_HW_ENCODE=1` | opt into VideoToolbox encode (macOS; off by default) |
| `REELLY_SW_ENCODE=1` | force software encode (beats HW flag) |
| `REELLY_NO_HWDECODE=1` | disable VideoToolbox decode |
| `REELLY_LETTERING_OVERRIDE_BY` | hand-authored lettering attribution |

## 9. What breaks without what (graceful degradation map)

| Missing | Behavior |
|---|---|
| ffmpeg/ffprobe | hard fail (`reelly setup` tells you) |
| Chrome | overlay cards fail; the rest of the pipeline runs |
| Gemini key | no visual review / occupancy / QC vision — analyze says so loudly |
| OpenAI key | default `--brain gpt` fails with the OpenAI signup URL; use `--brain gemini` or `--no-ai` only if you choose that path |
| FAL key | no music/SFX/lettering generation; kit-served beds still work |
| diarize extra / HF terms | analyze completes; voice identity UNVERIFIED (clearance gate weakened) |
| Brand kit | legacy endcard path (Gemini + Chrome per cut), system fonts, default accent |
| FaceMesh + Blaze both absent | face detection fails → face-dependent reframing unavailable |
| mlx-whisper (non-Apple Silicon) | on-device ASR is skipped loudly; other analyze stages still run |
| tesseract (PRISM OCR only) | PRISM OCR skips; core editing still runs |
