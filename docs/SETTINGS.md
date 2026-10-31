# Settings

Generated from `reelly/settings.py` (`python -m reelly.settings --markdown`). Brand-specific choices belong in the brand kit (`brand.json`); these environment variables override them for one run. `reelly config` shows the values in effect.

On/off settings accept `1/on/yes/true` and `0/off/no/false`.

## Settings


### Run

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_HOME` | path | `~/.reelly` | Reelly's home: config, models, tools, brand kit, ledger, caches. |
| `REELLY_PROJECTS` | path | `~/reelly-projects` | Where project folders are created and found. |
| `REELLY_BRAND` | text | `` | The brand to use (same as --brand NAME); else the kit's default_brand. |
| `REELLY_BRANDKIT` | path | `<REELLY_HOME>/brandkit` | The brand kit folder. |
| `REELLY_ALLOW_PAID` | flag | `off` | Allow paid API providers for this run (same as --allow-paid). |
| `REELLY_LOCAL_ONLY` | flag | `off` | Nothing leaves the machine: no Codex/Claude CLI, no provider, no vision check, no model download (same as --local-only). |
| `REELLY_CLIP_LEN` | text | `20-28` | Target clip length in seconds, e.g. 25-30 (same as --length); else the brand's video.clip_len. |
| `REELLY_FPS` | choice | `30` | Output frame rate (24, 25, 30, 50, 60); else the brand's video.fps. |
| `REELLY_CONTENT_TYPE` | choice | `auto` | gameplay | talking-head | podcast | screen-demo; else the brand's video.content_type, else a guess from the framing. |

### Text

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_KINETIC` | flag | `on` | Animated (kinetic) text; off = static text. |
| `REELLY_TEXT_GPU` | flag | `on` | Draw kinetic text on the GPU (off = the slower browser renderer). |
| `REELLY_TEXT_MOTION` | choice | `rise` | rise | blocklock; else the brand's text.motion. |
| `REELLY_TEXT_EXIT` | choice | `fade` | fade | petals; else the brand's text.exit. |
| `REELLY_TEXT_BEHIND` | flag | `brand` | Text behind the subject (experimental; talking-head and podcast only); else the brand's text.behind_subject. |

### Effects

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_FX_ENDCARD` | list | `brand` | End-card effect preset name, or off; else the brand's effects.endcard. |
| `REELLY_FX_AMBIENT` | list | `brand` | Ambient layer presets, comma-separated bottom first, or off. |
| `REELLY_FX_TEXT` | list | `brand` | Text effect preset, or off. |
| `REELLY_FX_FINISH` | list | `brand` | Finish preset (e.g. folk-print), or off. |
| `REELLY_FX_LITE` | flag | `off` | Render every preset's lighter variant (smaller, fewer passes). |
| `REELLY_OUTRO` | flag | `on` | Append the end card; off ships content-only files. |

### GPU

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_GPU` | choice | `auto` | auto | gpu | software. auto: the hardware GPU (Direct3D 11, Metal, OpenGL), the software GPU when there is none; gpu: fail rather than fall back. |
| `REELLY_CHROME` | path | `auto` | Chrome or Chromium binary (also CHROME_PATH). |

### Brains and vision

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_SIGHT` | choice | `auto` | auto | codex | local | off: the vision pass (frames to Codex or a local endpoint); off also turns the impact check off. |
| `REELLY_VISION_URL` | text | `` | A local OpenAI-compatible vision endpoint (Ollama, LM Studio). |

### Tools and models

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_ASR` | choice | `whisper` | whisper | parakeet (faster, English only, Apple Silicon). |
| `REELLY_DEPTH_MODEL` | path | `<REELLY_HOME>/models` | Depth Anything V2 Small model.onnx (`reelly models fetch depth`). |
| `REELLY_SEGMENTER` | path | `<REELLY_HOME>/models` | MediaPipe selfie_segmenter.tflite (`reelly models fetch segmenter`). |
| `REELLY_IRIS` | path | `<REELLY_HOME>/tools/iris` | EA IRIS app (IrisApp) for the photosensitivity gate. |

## Advanced (tuning and debugging)


### Run

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_KEEP_WORK` | flag | `off` | Keep temporary work folders for inspection. |

### Text

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_KARAOKE_COLOR` | text | `#FF69B4` | Karaoke highlight colour when no brand accent is set. |

### Effects

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_ENDCARD` | choice | `` | legacy: the old overlay end-card path. |
| `REELLY_POINTERS` | flag | `off` | Pointer overlays on every cut, not only plans that ask for them. |

### Brains and vision

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_CODEX_MODEL` | text | `CLI default` | Pin the Codex model. |
| `REELLY_VISION_MODEL` | text | `local default` | Model name at REELLY_VISION_URL. |
| `REELLY_SIGHT_WORKERS` | number | `3` | Parallel vision calls. |
| `REELLY_VISUAL_WORKERS` | number | `6` | Parallel paid visual-pass calls. |
| `REELLY_OCCUPANCY` | choice | `` | gemini: paid occupancy check in `reelly motion`. |

### Tools and models

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_FACE` | choice | `auto` | auto | on | off: face detection (mediapipe). |
| `REELLY_FOLLOW` | flag | `on` | Follow faces when reframing a camera recording. |
| `REELLY_DIAR_DEVICE` | choice | `auto` | cpu: run speaker diarization on the CPU. |

### Quality gates

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_LEGIBILITY` | flag | `on` | Measure text contrast and add plates where text would be unreadable. |
| `REELLY_ENDING_CHECK` | flag | `on` | Check that cuts end on a complete beat. |
| `REELLY_ALLOW_BLACK_SOURCE` | flag | `off` | Accept a source that probes as black. |
| `REELLY_GOLDEN` | choice | `` | required: a missing golden-frame tier fails the test (CI, render workers). |

### Performance

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_HW_ENCODE` | flag | `off` | Hardware H.264 encode for intermediate passes (VideoToolbox). |
| `REELLY_SW_ENCODE` | flag | `off` | Force software encoding for intermediate passes. |
| `REELLY_NO_HWDECODE` | flag | `off` | Disable hardware decoding. |
| `REELLY_DECODE_SLOTS` | number | `2` | Concurrent full-decode analysis passes. |
| `REELLY_RENDER_SLOTS` | number | `5` | Concurrent segment renders. |

### reelly motion

| Variable | Type | Default | What it does |
|---|---|---|---|
| `REELLY_BUG_CORNER` | choice | `top-left` | Corner of the logo bug. |
| `REELLY_BUG_HEIGHT` | number | `112` | Logo bug height in px. |
| `REELLY_BUG_OPACITY` | number | `0.62` | Logo bug opacity. |
| `REELLY_BUG_PRODUCT` | text | `video` | Which product's logo the bug uses. |
| `REELLY_CAPTION_ANCHOR` | choice | `auto` | Caption anchor override. |
| `REELLY_CAPTION_W_HOOK` | number | `760` | Hook text width in px. |
| `REELLY_CAPTION_W_PAYOFF` | number | `720` | Payoff text width in px. |
| `REELLY_ILLUS_FACE_SAMPLES` | number | `3` | Face samples for illustrated art. |
| `REELLY_SINGLE_SHOT` | flag | `off` | One generation attempt instead of best-of. |
| `REELLY_TYPESET_LETTERING` | flag | `off` | Typeset the lettering instead of generating it. |
| `REELLY_LETTERING_OVERRIDE_BY` | text | `` | Who approved a lettering override (recorded). |
| `REELLY_LETTERING_OVERRIDE_SHA` | text | `` | The approved lettering image's sha256. |
| `REELLY_SKIP_PREFLIGHT` | flag | `off` | Skip the preflight checks. |

