"""Shared configuration: binaries, keys, defaults.

Keys are NEVER stored in this repository. A user must bring their own.
Resolution order for each provider:

  1. Environment variables (preferred)
  2. ~/.reelly/config.json

`reelly setup` prints what is missing and where to get it. It never prints
key values.
"""
import json
import os
import shutil
import stat
import sys

HOME = os.path.expanduser("~/.reelly")
USER_CFG = os.path.join(HOME, "config.json")

# --- binaries ---------------------------------------------------------------

_FFMPEG_FALLBACKS = (
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
)
_FFPROBE_FALLBACKS = (
    "/opt/homebrew/bin/ffprobe",
    "/usr/local/bin/ffprobe",
    "/usr/bin/ffprobe",
)
_CHROME_FALLBACKS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def _first_existing(paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def find_ffmpeg():
    return (os.environ.get("FFMPEG")
            or os.environ.get("FFMPEG_PATH")
            or shutil.which("ffmpeg")
            or _first_existing(_FFMPEG_FALLBACKS)
            or "ffmpeg")


def find_ffprobe():
    return (os.environ.get("FFPROBE")
            or os.environ.get("FFPROBE_PATH")
            or shutil.which("ffprobe")
            or _first_existing(_FFPROBE_FALLBACKS)
            or "ffprobe")


def find_chrome():
    env = os.environ.get("CHROME_PATH") or os.environ.get("REELLY_CHROME")
    if env:
        return env
    found = (shutil.which("google-chrome-stable")
             or shutil.which("google-chrome")
             or shutil.which("chromium-browser")
             or shutil.which("chromium")
             or shutil.which("chrome"))
    if found:
        return found
    return _first_existing(_CHROME_FALLBACKS) or ""


FFMPEG = find_ffmpeg()
FFPROBE = find_ffprobe()
CHROME = find_chrome()


def _default_projects():
    """Where rendered projects live. Per-user, never hardcoded to one person's
    machine: set REELLY_PROJECTS, or `projects` in ~/.reelly/config.json. Falls
    back to ~/reelly-projects so a fresh checkout works with no setup and writes
    nothing into anyone else's content folders.
    """
    env = os.environ.get("REELLY_PROJECTS")
    if env:
        return os.path.expanduser(env)
    cfg = load_user_config()
    p = cfg.get("projects")
    if p:
        return os.path.expanduser(p)
    return os.path.expanduser("~/reelly-projects")


def load_user_config():
    """~/.reelly/config.json, or {} if missing / unreadable.

    Reads from the current HOME so tests can redirect it.
    """
    path = os.path.join(HOME, "config.json")
    if not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


DEFAULT_PROJECTS = _default_projects()
WHISPER_MODEL = "mlx-community/whisper-medium.en-mlx"
GEMINI_MODEL = "gemini-3.5-flash"  # 2.5-flash retired by Google 2026-07-09
CLAUDE_MODEL = "claude-sonnet-5"
PRISM_WORK = os.path.join(HOME, "prism")

# Honor a system CA bundle when present (corporate SSL interception).
_CAFILE = "/etc/ssl/cert.pem"
if os.path.exists(_CAFILE):
    os.environ.setdefault("SSL_CERT_FILE", _CAFILE)

# --- API keys ---------------------------------------------------------------

PROVIDERS = {
    "google-genai": {
        "env": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"),
        "config": ("gemini_api_key", "google_genai_api_key", "google_api_key"),
        "label": "Gemini",
        "used_for": "visual review, occupancy, QC, sizzle survey",
        "url": "https://aistudio.google.com/apikey",
        "required": True,
    },
    "openai": {
        "env": ("OPENAI_API_KEY",),
        "config": ("openai_api_key",),
        "label": "OpenAI",
        "used_for": "editor brain (default --brain gpt)",
        "url": "https://platform.openai.com/api-keys",
        "required": True,
    },
    "fal-ai": {
        "env": ("FAL_KEY", "FAL_API_KEY"),
        "config": ("fal_key", "fal_api_key"),
        "label": "FAL",
        "used_for": "music beds, SFX, motion / image-to-video",
        "url": "https://fal.ai/dashboard/keys",
        "required": True,
    },
    "anthropic": {
        "env": ("ANTHROPIC_API_KEY",),
        "config": ("anthropic_api_key",),
        "label": "Anthropic",
        "used_for": "optional PRISM analyzer (python -m reelly.prism)",
        "url": "https://console.anthropic.com/settings/keys",
        "required": False,
    },
    "huggingface": {
        "env": ("HUGGINGFACE_TOKEN", "HF_TOKEN"),
        "config": ("huggingface_token", "hf_token"),
        "label": "Hugging Face",
        "used_for": "optional local speaker diarization (pyannote)",
        "url": "https://huggingface.co/settings/tokens",
        "required": False,
    },
}


class MissingKeyError(RuntimeError):
    """A cloud provider key was requested but the user has not supplied one."""


def get_key(name):
    """Return the key for `name`, or None. Never prints the value."""
    spec = PROVIDERS.get(name)
    if spec is None:
        return None
    for var in spec["env"]:
        val = os.environ.get(var, "").strip()
        if val:
            return val
    cfg = load_user_config()
    nested = cfg.get("keys")
    if not isinstance(nested, dict):
        nested = {}
    for key in spec["config"]:
        val = str(cfg.get(key) or nested.get(key) or nested.get(name) or "").strip()
        if val:
            return val
    return None


def have_key(name):
    return bool(get_key(name))


def missing_key_message(name):
    spec = PROVIDERS.get(name)
    if spec is None:
        return (f"No API key for {name}. Set it in the environment or in "
                f"~/.reelly/config.json, then run: reelly setup")
    env = spec["env"][0]
    cfg = spec["config"][0]
    return (
        f"No API key for {spec['label']} ({name}). Reelly does not ship keys; "
        f"you must provide your own.\n"
        f"  1. Create a key: {spec['url']}\n"
        f"  2. Export it:    export {env}=your-key\n"
        f"     or add \"{cfg}\" to ~/.reelly/config.json (file mode 0600)\n"
        f"  3. Confirm:      reelly setup\n"
        f"Used for: {spec['used_for']}"
    )


def provider_key(name):
    """API key for a cloud provider. Never stored in this repo.

    Raises MissingKeyError with the exact env var and signup URL when absent.
    """
    key = get_key(name)
    if not key:
        raise MissingKeyError(missing_key_message(name))
    return key


# --- intermediate encode selection ------------------------------------------
# Throwaway intermediates only need to be visually lossless enough for the
# final encode. libx264 veryfast is the portable default.
# REELLY_HW_ENCODE=1 opts into the hardware encoder after a capability probe.
# Final-quality
# encodes (burnin/overlays/finalize) keep libx264 and are NOT routed through
# this.
_HW_ENCODE = None  # None = not probed yet; True/False after the one-time probe

_SW_INTERMEDIATE_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21"]
_HW_INTERMEDIATE_ARGS = ["-c:v", "h264_videotoolbox", "-b:v", "10M", "-allow_sw", "1"]


def _videotoolbox_works():
    """One cheap probe per process: encode 0.2s of testsrc to null."""
    import subprocess
    try:
        r = subprocess.run(
            [FFMPEG, "-v", "error", "-f", "lavfi",
             "-i", "testsrc=duration=0.2:size=320x240:rate=30",
             "-c:v", "h264_videotoolbox", "-f", "null", "-"],
            capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def intermediate_encode_args():
    """Video codec args for throwaway intermediate encodes.

    Default: the exact libx264 args these sites always used (benchmarked
    faster than videotoolbox here -- see block comment above). Set
    REELLY_HW_ENCODE=1 to request h264_videotoolbox (probed once; videotoolbox
    has no CRF, so a generous bitrate stands in). REELLY_SW_ENCODE=1 is kept
    as a back-compat force-software override and beats REELLY_HW_ENCODE.
    """
    global _HW_ENCODE
    if os.environ.get("REELLY_SW_ENCODE") == "1" \
            or os.environ.get("REELLY_HW_ENCODE") != "1":
        return list(_SW_INTERMEDIATE_ARGS)
    if _HW_ENCODE is None:
        _HW_ENCODE = _videotoolbox_works()
        if not _HW_ENCODE:
            print("[enc  ] h264_videotoolbox unavailable, intermediates use libx264")
    return list(_HW_INTERMEDIATE_ARGS if _HW_ENCODE else _SW_INTERMEDIATE_ARGS)


# --- hardware decode ---------------------------------------------------------
# Decode-heavy passes over REAL video files (preview segment cuts, scene-cut
# detection, judge filter analysis, visual proxy compression) offload H.264/
# HEVC decode to the Apple media engine via VideoToolbox. Software filtergraphs
# keep working: ffmpeg auto-downloads hardware frames whenever a software
# filter needs them. Never applied to lavfi/concat/still-image inputs, where a
# decode accelerator is meaningless. REELLY_NO_HWDECODE=1 disables it (also the
# escape hatch if a source codec turns out unsupported by VideoToolbox).
HWDECODE = ["-hwaccel", "videotoolbox"]


def hwdecode_args():
    """Per-input decode-accel args: HWDECODE, or [] when disabled by env.

    Must appear BEFORE the `-i` of the input it accelerates. Read the env at
    call time (not import time) so tests and one-off runs can toggle it.
    """
    if os.environ.get("REELLY_NO_HWDECODE") == "1":
        return []
    if sys.platform != "darwin":
        return []
    return list(HWDECODE)


# --- setup / doctor ---------------------------------------------------------

def _bin_ok(path):
    return bool(path) and (os.path.isfile(path) or shutil.which(path))


def doctor(as_json=False):
    """Print a machine check. Returns process exit code. Never prints secrets."""
    checks = []

    py = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 11)
    checks.append({
        "id": "python", "status": "ok" if py_ok else "missing",
        "detail": f"{py} (need 3.11+)",
    })

    ff = find_ffmpeg()
    checks.append({
        "id": "ffmpeg", "status": "ok" if _bin_ok(ff) else "missing",
        "detail": ff if _bin_ok(ff) else "not found on PATH (brew install ffmpeg / apt install ffmpeg)",
    })
    fp = find_ffprobe()
    checks.append({
        "id": "ffprobe", "status": "ok" if _bin_ok(fp) else "missing",
        "detail": fp if _bin_ok(fp) else "not found on PATH (comes with ffmpeg)",
    })
    ch = find_chrome()
    checks.append({
        "id": "chrome",
        "status": "ok" if ch and _bin_ok(ch) else "missing",
        "detail": (ch if ch and _bin_ok(ch)
                   else "not found (needed for overlay cards). Install Chrome or set CHROME_PATH"),
    })

    for name, spec in PROVIDERS.items():
        present = have_key(name)
        if present:
            status, detail = "ok", spec["used_for"]
        elif spec["required"]:
            status = "missing"
            detail = (f"MISSING — {spec['used_for']}. Get a key at {spec['url']} "
                      f"then: export {spec['env'][0]}=your-key")
        else:
            status, detail = "skip", f"optional — {spec['used_for']}"
        checks.append({"id": spec["env"][0], "status": status, "detail": detail})

    if as_json:
        print(json.dumps({"checks": checks}, indent=2))
    else:
        print("Reelly setup")
        print("Keys are never read from this repo. You must supply your own.")
        print()
        width = max(len(c["id"]) for c in checks)
        for c in checks:
            mark = {"ok": "ok     ", "missing": "MISSING", "skip": "skip   "}[c["status"]]
            print(f"  {c['id']:<{width}}  {mark}  {c['detail']}")
        print()
        print("Required keys (create them, then export — do not commit them):")
        for name, spec in PROVIDERS.items():
            if spec["required"]:
                print(f"  {spec['env'][0]:<22} {spec['url']}")
        print()
        print("Optional keys:")
        for name, spec in PROVIDERS.items():
            if not spec["required"]:
                print(f"  {spec['env'][0]:<22} {spec['url']}")
        print()
        print("Or write ~/.reelly/config.json (chmod 600) using .env.example as a guide.")
        print("Then re-run:  reelly setup")

    hard = [c for c in checks if c["id"] in ("python", "ffmpeg", "ffprobe")
            and c["status"] != "ok"]
    return 1 if hard else 0


def write_user_config(data, path=None):
    """Write ~/.reelly/config.json with owner-only permissions. Test helper / future use."""
    path = path or USER_CFG
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, path)
    return path
