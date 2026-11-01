"""Every REELLY_* environment setting, in one place.

Each setting has a group, a type, a default, a one-line help and a level:
"public" settings are documented for everyone (docs/SETTINGS.md is
generated from this table), "advanced" ones are tuning and debugging knobs.
`reelly config` prints every setting's effective value and where it came
from. A test keeps the table honest: every REELLY_* name the code reads must
be listed here.

On/off settings all parse the same way (config.env_bool): 1/on/yes/true and
0/off/no/false, any case.

    python -m reelly.settings --markdown > docs/SETTINGS.md
"""
import os
import sys

# (name, group, type, default, level, help)
SETTINGS = [
    # run
    ("REELLY_HOME", "Run", "path", "~/.reelly", "public",
     "Reelly's home: config, models, tools, brand kit, ledger, caches."),
    ("REELLY_PROJECTS", "Run", "path", "~/reelly-projects", "public",
     "Where project folders are created and found."),
    ("REELLY_BRAND", "Run", "text", "", "public",
     "The brand to use (same as --brand NAME); else the kit's default_brand."),
    ("REELLY_BRANDKIT", "Run", "path", "<REELLY_HOME>/brandkit", "public",
     "The brand kit folder."),
    ("REELLY_ALLOW_PAID", "Run", "flag", "off", "public",
     "Allow paid API providers for this run (same as --allow-paid)."),
    ("REELLY_LOCAL_ONLY", "Run", "flag", "off", "public",
     "Nothing leaves the machine: no Codex/Claude CLI, no provider, no vision "
     "check, no download, no web font (same as --local-only)."),
    ("REELLY_CLIP_LEN", "Run", "text", "20-28", "public",
     "Target clip length in seconds, e.g. 25-30 (same as --length); else the "
     "brand's video.clip_len."),
    ("REELLY_FPS", "Run", "choice", "30", "public",
     "Output frame rate (24, 25, 30, 50, 60); else the brand's video.fps."),
    ("REELLY_CONTENT_TYPE", "Run", "choice", "auto", "public",
     "gameplay | talking-head | podcast | screen-demo; else the brand's "
     "video.content_type, else a guess from the framing."),
    ("REELLY_KEEP_WORK", "Run", "flag", "off", "advanced",
     "Keep temporary work folders for inspection."),
    # text
    ("REELLY_KINETIC", "Text", "flag", "on", "public",
     "Animated (kinetic) text; off = static text."),
    ("REELLY_TEXT_GPU", "Text", "flag", "on", "public",
     "Draw kinetic text on the GPU (off = the slower browser renderer)."),
    ("REELLY_TEXT_MOTION", "Text", "choice", "rise", "public",
     "rise | blocklock; else the brand's text.motion."),
    ("REELLY_TEXT_EXIT", "Text", "choice", "fade", "public",
     "fade | petals; else the brand's text.exit."),
    ("REELLY_TEXT_BEHIND", "Text", "flag", "brand", "public",
     "Text behind the subject (experimental; talking-head and podcast only); "
     "else the brand's text.behind_subject."),
    ("REELLY_KARAOKE_COLOR", "Text", "text", "#FF69B4", "advanced",
     "Karaoke highlight colour when no brand accent is set."),
    # effects
    ("REELLY_FX_ENDCARD", "Effects", "list", "brand", "public",
     "End-card effect preset name, or off; else the brand's effects.endcard."),
    ("REELLY_FX_AMBIENT", "Effects", "list", "brand", "public",
     "Ambient layer presets, comma-separated bottom first, or off."),
    ("REELLY_FX_TEXT", "Effects", "list", "brand", "public",
     "Text effect preset, or off."),
    ("REELLY_FX_FINISH", "Effects", "list", "brand", "public",
     "Finish preset (e.g. folk-print), or off."),
    ("REELLY_FX_LITE", "Effects", "flag", "off", "public",
     "Render every preset's lighter variant (smaller, fewer passes)."),
    ("REELLY_OUTRO", "Effects", "flag", "on", "public",
     "Append the end card; off ships content-only files."),
    ("REELLY_ENDCARD", "Effects", "choice", "", "advanced",
     "legacy: the old overlay end-card path."),
    ("REELLY_POINTERS", "Effects", "flag", "off", "advanced",
     "Pointer overlays on every cut, not only plans that ask for them."),
    # GPU
    ("REELLY_GPU", "GPU", "choice", "auto", "public",
     "auto | gpu | software. auto: the hardware GPU (Direct3D 11, Metal, "
     "OpenGL), the software GPU when there is none; gpu: fail rather than "
     "fall back."),
    ("REELLY_CHROME", "GPU", "path", "auto", "public",
     "Chrome or Chromium binary (also CHROME_PATH)."),
    # brains and vision
    ("REELLY_SIGHT", "Brains and vision", "choice", "auto", "public",
     "auto | codex | local | off: the vision pass (frames to Codex or a local "
     "endpoint); off also turns the impact check off."),
    ("REELLY_CODEX_MODEL", "Brains and vision", "text", "CLI default", "advanced",
     "Pin the Codex model."),
    ("REELLY_VISION_URL", "Brains and vision", "text", "", "public",
     "A local OpenAI-compatible vision endpoint (Ollama, LM Studio)."),
    ("REELLY_VISION_MODEL", "Brains and vision", "text", "local default", "advanced",
     "Model name at REELLY_VISION_URL."),
    ("REELLY_SIGHT_WORKERS", "Brains and vision", "number", "3", "advanced",
     "Parallel vision calls."),
    ("REELLY_VISUAL_WORKERS", "Brains and vision", "number", "6", "advanced",
     "Parallel paid visual-pass calls."),
    ("REELLY_OCCUPANCY", "Brains and vision", "choice", "", "advanced",
     "gemini: paid occupancy check in `reelly motion`."),
    # tools and models
    ("REELLY_ASR", "Tools and models", "choice", "whisper", "public",
     "whisper | parakeet (faster, English only, Apple Silicon)."),
    ("REELLY_FACE", "Tools and models", "choice", "auto", "advanced",
     "auto | on | off: face detection (mediapipe)."),
    ("REELLY_FOLLOW", "Tools and models", "flag", "on", "advanced",
     "Follow faces when reframing a camera recording."),
    ("REELLY_DIAR_DEVICE", "Tools and models", "choice", "auto", "advanced",
     "cpu: run speaker diarization on the CPU."),
    ("REELLY_DEPTH_MODEL", "Tools and models", "path", "<REELLY_HOME>/models", "public",
     "Depth Anything V2 Small model.onnx (`reelly models fetch depth`)."),
    ("REELLY_SEGMENTER", "Tools and models", "path", "<REELLY_HOME>/models", "public",
     "MediaPipe selfie_segmenter.tflite (`reelly models fetch segmenter`)."),
    ("REELLY_IRIS", "Tools and models", "path", "<REELLY_HOME>/tools/iris", "public",
     "EA IRIS app (IrisApp) for the photosensitivity gate."),
    # quality gates
    ("REELLY_LEGIBILITY", "Quality gates", "flag", "on", "advanced",
     "Measure text contrast and add plates where text would be unreadable."),
    ("REELLY_ENDING_CHECK", "Quality gates", "flag", "on", "advanced",
     "Check that cuts end on a complete beat."),
    ("REELLY_ALLOW_BLACK_SOURCE", "Quality gates", "flag", "off", "advanced",
     "Accept a source that probes as black."),
    ("REELLY_GOLDEN", "Quality gates", "choice", "", "advanced",
     "required: a missing golden-frame tier fails the test (CI, render workers)."),
    # performance
    ("REELLY_HW_ENCODE", "Performance", "flag", "off", "advanced",
     "Hardware H.264 encode for intermediate passes (VideoToolbox)."),
    ("REELLY_SW_ENCODE", "Performance", "flag", "off", "advanced",
     "Force software encoding for intermediate passes."),
    ("REELLY_NO_HWDECODE", "Performance", "flag", "off", "advanced",
     "Disable hardware decoding."),
    ("REELLY_DECODE_SLOTS", "Performance", "number", "2", "advanced",
     "Concurrent full-decode analysis passes."),
    ("REELLY_RENDER_SLOTS", "Performance", "number", "5", "advanced",
     "Concurrent segment renders."),
    # reelly motion (single-image posts)
    ("REELLY_BUG_CORNER", "reelly motion", "choice", "top-left", "advanced",
     "Corner of the logo bug."),
    ("REELLY_BUG_HEIGHT", "reelly motion", "number", "112", "advanced",
     "Logo bug height in px."),
    ("REELLY_BUG_OPACITY", "reelly motion", "number", "0.62", "advanced",
     "Logo bug opacity."),
    ("REELLY_BUG_PRODUCT", "reelly motion", "text", "video", "advanced",
     "Which product's logo the bug uses."),
    ("REELLY_CAPTION_ANCHOR", "reelly motion", "choice", "auto", "advanced",
     "Caption anchor override."),
    ("REELLY_CAPTION_W_HOOK", "reelly motion", "number", "760", "advanced",
     "Hook text width in px."),
    ("REELLY_CAPTION_W_PAYOFF", "reelly motion", "number", "720", "advanced",
     "Payoff text width in px."),
    ("REELLY_ILLUS_FACE_SAMPLES", "reelly motion", "number", "3", "advanced",
     "Face samples for illustrated art."),
    ("REELLY_SINGLE_SHOT", "reelly motion", "flag", "off", "advanced",
     "One generation attempt instead of best-of."),
    ("REELLY_TYPESET_LETTERING", "reelly motion", "flag", "off", "advanced",
     "Typeset the lettering instead of generating it."),
    ("REELLY_LETTERING_OVERRIDE_BY", "reelly motion", "text", "", "advanced",
     "Who approved a lettering override (recorded)."),
    ("REELLY_LETTERING_OVERRIDE_SHA", "reelly motion", "text", "", "advanced",
     "The approved lettering image's sha256."),
    ("REELLY_SKIP_PREFLIGHT", "reelly motion", "flag", "off", "advanced",
     "Skip the preflight checks."),
]

NAMES = {s[0] for s in SETTINGS}


def effective():
    """[(name, value, source, level, help)] for `reelly config`."""
    out = []
    for name, _group, _t, default, level, help_ in SETTINGS:
        v = os.environ.get(name)
        out.append((name, v if v is not None else default,
                    "env" if v is not None else "default", level, help_))
    return out


def report(show_all=False):
    """Print the effective settings (public ones, or all)."""
    from . import brandkit, config
    print(f"Reelly settings (home {config.HOME}, brand {brandkit.brand_name() or 'none'})")
    for name, value, source, level, help_ in effective():
        if level != "public" and not show_all and source == "default":
            continue
        mark = "*" if source == "env" else " "
        print(f" {mark} {name:<26} {str(value):<22} {help_}")
    print("\n * = set in the environment. `reelly config --all` lists advanced settings too.")


def markdown():
    """docs/SETTINGS.md, generated from SETTINGS."""
    L = ["# Settings", "",
         "Generated from `reelly/settings.py` (`python -m reelly.settings --markdown`). "
         "Brand-specific choices belong in the brand kit (`brand.json`); these "
         "environment variables override them for one run. `reelly config` shows the "
         "values in effect.", "",
         "On/off settings accept `1/on/yes/true` and `0/off/no/false`.", ""]
    for level, title in (("public", "Settings"), ("advanced", "Advanced (tuning and debugging)")):
        L += [f"## {title}", ""]
        group = None
        for name, g, t, default, lv, help_ in SETTINGS:
            if lv != level:
                continue
            if g != group:
                L += ["", f"### {g}", "", "| Variable | Type | Default | What it does |",
                      "|---|---|---|---|"]
                group = g
            L.append(f"| `{name}` | {t} | `{default}` | {help_} |")
        L.append("")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    if "--markdown" in sys.argv:
        sys.stdout.write(markdown())
    else:
        report("--all" in sys.argv)
