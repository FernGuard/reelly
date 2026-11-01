# SETUP

Audience: a human or agent on a fresh machine. Follow top to bottom.
Reelly degrades loudly when an optional piece is missing; section 9 says what you lose, and `REVIEW.md` lists anything a render had to skip.

## 1. System binaries

ffmpeg and ffprobe must be on your PATH (or set `FFMPEG` / `FFPROBE`).

| What | Install | Check |
|---|---|---|
| ffmpeg / ffprobe | `brew install ffmpeg` · `sudo apt install ffmpeg` · `winget install Gyan.FFmpeg` | `ffmpeg -version` |
| EGL (Linux only) | `sudo apt install libegl1 libgles2`: MediaPipe (face tracking, person masks) needs it | `reelly setup` |
| uv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` · Windows: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` or `pip install uv` | `uv --version` |
| tesseract (PRISM OCR only) | `brew install tesseract` · `sudo apt install tesseract-ocr` · `winget install UB-Mannheim.TesseractOCR` | `tesseract --version` |
| Chrome | Google Chrome or Chromium, or `CHROME_PATH`: kinetic type, every effect and cards render in it | `uv run reelly setup` |

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

## 3. API keys (optional, your own)

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

## 4. Models: what downloads itself and what you fetch

**Auto-downloading (no action, first use pays the download):**

| Model | Used for | Cache location |
|---|---|---|
| `mlx-community/whisper-medium.en-mlx` | transcription (Apple Silicon) | HF hub cache |
| `parakeet-tdt-0.6b-v2` (if `REELLY_ASR=parakeet`) | fast English ASR | HF hub cache |
| MediaPipe `face_landmarker.task` (FaceMesh) | face detection / reframing | `~/.reelly/models/` |

**On request, verified (`reelly models` shows what is installed):**

```sh
uv run reelly models fetch depth        # Depth Anything V2 Small, ~99 MB: reelly parallax
uv sync --extra depth                   # onnxruntime, which runs it
uv run reelly models fetch segmenter    # MediaPipe selfie segmenter, 250 KB: text behind the subject
```

Each download is pinned to a version and checked against its SHA-256 before it
is installed under `<REELLY_HOME>/models/`. `REELLY_DEPTH_MODEL` and
`REELLY_SEGMENTER` point at copies elsewhere.

**EA IRIS (optional photosensitivity analysis).** Reelly's own WCAG flash
check always runs. For IRIS's red-flash, extended-rule and pattern checks,
install its Windows app from https://github.com/electronicarts/IRIS into
`<REELLY_HOME>/tools/iris/` (or set `REELLY_IRIS`), then `reelly setup`
shows it. IRIS writes its results inside its own folder, so install it where
your user can write (not under Program Files).

**GPU.** Effects use the hardware GPU through Chrome automatically (Direct3D
11 on Windows, Metal on macOS, OpenGL on Linux) and fall back to the software
renderer when there is none. `REELLY_GPU=gpu` fails instead of falling back;
`REELLY_GPU=software` forces the software renderer.

**Manual: diarization (gated models).** After installing the extra and the
token, accept the terms on huggingface.co for **all three** repos:
`pyannote/speaker-diarization-3.1`, `pyannote/segmentation-3.0`,
`pyannote/speaker-diarization-community-1`. Verify with a real download
(`huggingface-cli download pyannote/speaker-diarization-3.1` is a real check;
`model_info()` is not). Runs on Apple
GPU (MPS) automatically; `REELLY_DIAR_DEVICE=cpu` forces CPU.

**Manual: offline face fallback (only if you need face detection with no
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

- `projects` (or env `REELLY_PROJECTS`): defaults to `~/reelly-projects`.
- `logos`: per-studio wordmark PNGs; consumed by descriptions, overlays,
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
(no diarizer, no transcript, etc.): that output is the "what is missing
on this machine" report. `reelly setup` is the shorter version.

Before processing confidential media, read [../DATA_AND_PRIVACY.md](../DATA_AND_PRIVACY.md).

## 8. Settings

Every environment setting is listed in [SETTINGS.md](SETTINGS.md), generated
from the code. `reelly config` prints the values in effect (`--all` includes
the advanced ones). Provider keys: `GEMINI_API_KEY`, `OPENAI_API_KEY`,
`FAL_KEY`, `ANTHROPIC_API_KEY`, `HUGGINGFACE_TOKEN` / `HF_TOKEN`. Binary
overrides: `FFMPEG`, `FFPROBE`, `CHROME_PATH`.

## 9. What breaks without what (graceful degradation map)

| Missing | Behavior |
|---|---|
| ffmpeg/ffprobe | hard fail (`reelly setup` tells you) |
| Chrome | static text instead of kinetic type, no effects, no cards; the rest of the pipeline runs (and `REVIEW.md` says so) |
| Codex CLI (signed in) | heuristic planning, no vision pass, no impact marks; with a Gemini key and `--allow-paid` the paid vision path runs instead |
| Gemini key | no paid visual review / occupancy / QC vision; analyze says so |
| OpenAI key | not needed: the default `--brain auto` uses a signed-in `codex` or `claude` CLI, else heuristics; `--brain gpt` needs the key plus `--allow-paid` |
| `--allow-paid` | every per-call provider stays off (keys idle); analyze skips the Gemini pass, uncached SFX and beds are skipped |
| FAL key | no music/SFX/lettering generation; kit-served beds still work |
| diarize extra / HF terms | analyze completes; voice identity UNVERIFIED (clearance gate weakened) |
| Brand kit | system fonts, default accent, no effects (copy `examples/brandkit/demo` to start) |
| FaceMesh + Blaze both absent | face detection fails, so face-dependent reframing is unavailable |
| mlx-whisper (non-Apple Silicon) | on-device ASR is skipped loudly; other analyze stages still run |
| tesseract (PRISM OCR only) | PRISM OCR skips; core editing still runs |
| depth model / onnxruntime | `reelly parallax` stops and says which command installs it |
| segmenter model | text stays on top of the subject (a fallback in `REVIEW.md`) |
| GPU | effects render on the software renderer (slower, same look) |

## 10. Uninstall and clean up

Reelly keeps everything in a few places:

- `~/.reelly/` (or `REELLY_HOME`): config and keys, models, tools, brand kits,
  caches, the spend ledger;
- `~/reelly-projects/` (or `REELLY_PROJECTS`): your projects and renders;
- temporary folders named `reelly-*` in your system temp folder (removed after
  each run unless `REELLY_KEEP_WORK` is set);
- EA IRIS's own `TestVideos/` and `Results/` folders, if you installed it.

Delete the repository folder, then whichever of these you no longer want.

