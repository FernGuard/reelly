"""The brand kit: pre-built brand assets + the copy linter, one module.

WHY THIS EXISTS
Every render re-derived brand material at runtime: the CTA/logo end lockup was
re-screenshotted per card, captions fell back to system Arial, the copy rules
lived only inside prompts (prompt-trust, enforced by a paid Gemini round-trip).
The kit moves the derivable work to BUILD TIME, once per machine:

  ~/.reelly/brandkit/            (env REELLY_BRANDKIT overrides; assets live
    kit.json                      OUTSIDE the repo -- the repo ships code only)
    copy_bank.yaml               per-studio CTAs, limits, banned names
    endcards/<studio>.png|.mov   static endcard (dark scrim + wordmark + CTA);
                                 an animated .mov is a later manual upgrade,
                                 the accessor already prefers it
    outros/<studio>.mov          brand outro clip for card.py concat (optional)
    fonts/<role>.ttf|.otf        brand type per role (caption, headline, ...)
    music/manifest.json          cleared music bed manifest (optional)

EVERYTHING DEGRADES GRACEFULLY: a missing kit, a missing asset or a broken
kit.json returns None/{}/defaults and the pipeline behaves exactly as it did
before the kit existed. Nothing in here raises for an absent asset.

INVALIDATION: kit.json records the sha256 of each SOURCE logo (the registered
wordmarks in ~/.reelly/config.json "logos"). A changed source hash marks that
studio's derived assets stale; build_defaults() re-renders them.

Build (also wired as `reelly brandkit init` in the CLI):
    uv run python -m reelly.brandkit
"""
import hashlib
import json
import os
import re
import threading

KIT_VERSION = 1
DEFAULT_KIT_DIR = "~/.reelly/brandkit"

# Copy contract defaults (memory: copy-rules-on-brand-fun-cta-short). The
# copy_bank.yaml generated below carries these so a human can tune them per
# machine; the linter reads the bank first and falls back here.
# Word limits ARE a layout input, not only a style choice (fix-list #10): the
# payoff lives in the reserved TOP text band and the lettering wraps every payoff
# over three words onto two lines. Past ~6 words it fills the band, dominates the
# frame and starves the CTA of space -- the CTA-over-payoff collision. Cap it at 6
# so it fits the band on two short lines; enforced at copy-authoring time here,
# not discovered at render time.
DEFAULT_LIMITS = {"hook": 7, "payoff": 6, "cta": 4}
# Organization-specific banned or retired names belong in copy_bank.yaml.
DEFAULT_BANNED = []

FALLBACK_ACCENT = "#17CDFF"     # Blue Smoke, the pre-kit hardcoded accent


def kit_dir():
    """The kit root, resolved at call time so tests/env can redirect it."""
    return os.path.expanduser(os.environ.get("REELLY_BRANDKIT")
                              or DEFAULT_KIT_DIR)


def _kit_json():
    p = os.path.join(kit_dir(), "kit.json")
    try:
        with open(p) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _sha256(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _studio_logos():
    """{canonical studio key: source logo path} for every studio that has a
    registered wordmark on this machine (products.brand_logo)."""
    from . import products
    out = {}
    for key in ("video", "story", "games", "adventure"):
        logo = products.brand_logo(key)
        if logo:
            out[key] = logo
    return out


# ---------------------------------------------------------------- accessors
# All of these are safe to call with no kit on disk: they return None/{} and
# the caller keeps its pre-kit behavior.

def endcard(studio):
    """Path to the studio's endcard (.mov preferred over .png), or None."""
    from . import products
    key = products.ALIASES.get(studio, studio)
    for ext in (".mov", ".png"):
        p = os.path.join(kit_dir(), "endcards", key + ext)
        if os.path.exists(p):
            return p
    return None


def outro(studio):
    """Brand outro clip for card.py to concat instead of re-rendering the
    logo rise, or None (card.py then renders the rise as before)."""
    from . import products
    key = products.ALIASES.get(studio, studio)
    p = os.path.join(kit_dir(), "outros", key + ".mov")
    return p if os.path.exists(p) else None


def font(role="caption"):
    """Brand font file for a role, or None (caller falls back to its own
    system-font list -- captions.FONTS today). Resolution order:
    fonts/<role>.ttf|.otf, fonts/default.ttf|.otf, then any font in fonts/."""
    d = os.path.join(kit_dir(), "fonts")
    for name in (role, "default"):
        for ext in (".ttf", ".otf", ".ttc"):
            p = os.path.join(d, name + ext)
            if os.path.exists(p):
                return p
    try:
        for fn in sorted(os.listdir(d)):
            if fn.lower().endswith((".ttf", ".otf", ".ttc")):
                return os.path.join(d, fn)
    except OSError:
        pass
    return None


def accent():
    """The single brand accent for caption highlights: sampled from the managed account
    wordmark at kit-build time (kit.json), Blue Smoke when no kit exists."""
    a = _kit_json().get("accent")
    return a if isinstance(a, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", a) \
        else FALLBACK_ACCENT


def copy_bank():
    """Parsed copy_bank.yaml. Written as a code-generated default on first
    use, so the repo ships schema+code and the kit dir owns the data."""
    p = os.path.join(kit_dir(), "copy_bank.yaml")
    if not os.path.exists(p):
        try:
            _write_default_copy_bank(p)
        except OSError:
            return {}
    try:
        import yaml
        with open(p) as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def music_manifest():
    """Parsed music manifest ({} when absent -- music behaves as today)."""
    d = os.path.join(kit_dir(), "music")
    for fn, loader in (("manifest.json", json.load),):
        p = os.path.join(d, fn)
        if os.path.exists(p):
            try:
                with open(p) as f:
                    data = loader(f)
                return data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                return {}
    return {}


# The music library is SELF-BUILDING: every fresh FAL generation registers its
# bed here, and the next cut with the same genre + a shorter-or-equal length
# gets it for $0. register_bed is called from finalize's worker threads (and
# the music prefetch pool), so the manifest read-modify-write is locked.
_MUSIC_LOCK = threading.Lock()


def find_bed(genre, min_s):
    """Absolute path of a registered bed with this genre and duration >= min_s,
    or None (caller generates as it always did). Prefers the SHORTEST bed that
    fits, so long beds stay available for long cuts."""
    beds = music_manifest().get("beds")
    if not isinstance(beds, list):
        return None
    best = None
    for b in beds:
        try:
            if b.get("genre") != genre or float(b["duration_s"]) < float(min_s):
                continue
            p = os.path.join(kit_dir(), b["file"])
            if not os.path.exists(p):
                continue
            if best is None or float(b["duration_s"]) < best[0]:
                best = (float(b["duration_s"]), p)
        except (KeyError, TypeError, ValueError):
            continue
    return best[1] if best else None


def register_bed(path, genre, duration, source="fal-ai/elevenlabs/music"):
    """Copy a freshly generated bed into music/<genre>/ and append a manifest
    entry, so the library grows itself with every generation. Returns the
    kit-side path, or None on any failure (registration must never fail a
    render -- the bed the caller holds is still good)."""
    import datetime
    import shutil
    try:
        with _MUSIC_LOCK:
            d = os.path.join(kit_dir(), "music", genre)
            os.makedirs(d, exist_ok=True)
            digest = _sha256(path)
            if digest is None:
                return None
            dst = os.path.join(d, digest[:16] + ".mp3")
            rel = os.path.relpath(dst, kit_dir())
            man_p = os.path.join(kit_dir(), "music", "manifest.json")
            man = music_manifest()
            beds = man.get("beds") if isinstance(man.get("beds"), list) else []
            if any(b.get("file") == rel for b in beds if isinstance(b, dict)):
                return dst          # same audio already registered
            shutil.copyfile(path, dst)
            beds.append({"file": rel, "genre": genre,
                         "duration_s": round(float(duration), 2),
                         "source": source,
                         "date": datetime.date.today().isoformat()})
            man.update({"version": KIT_VERSION, "beds": beds})
            tmp = man_p + ".tmp"
            with open(tmp, "w") as f:
                json.dump(man, f, indent=1)
            os.replace(tmp, man_p)
            return dst
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- copy linter

def _words(text):
    return [w for w in str(text or "").split() if w]


def _banned_hits(text, banned):
    hits = []
    for name in banned:
        if re.search(r"\b" + re.escape(str(name)) + r"\b", str(text or ""),
                     re.IGNORECASE):
            hits.append(str(name))
    return hits


def _wordmarks():
    """Canonical registered wordmark spellings from products (single source
    of truth): studio names + the managed account domains."""
    from . import products
    marks = {products.PRODUCTS[k]["name"]
             for k in ("video", "story", "games", "adventure")}
    marks.add("example.invalid")
    return marks


def lint_copy(hook, payoff, cta, studio=""):
    """CODE-enforced copy contract. Returns a list of violation strings
    (empty = clean). This is the gate that runs BEFORE the paid Gemini
    sense-gate in motion._author: limits, one-CTA, retired names and wordmark
    spelling are mechanical rules, so a model round-trip to catch them is a
    wasted call and an unreliable one."""
    bank = copy_bank()
    limits = dict(DEFAULT_LIMITS)
    if isinstance(bank.get("limits"), dict):
        limits.update({k: int(v) for k, v in bank["limits"].items()
                       if isinstance(v, (int, float))})
    banned = bank.get("banned") if isinstance(bank.get("banned"), list) \
        else DEFAULT_BANNED

    v = []
    fields = {"hook": hook, "payoff": payoff, "cta": cta}
    for name, text in fields.items():
        n = len(_words(text))
        if n > limits.get(name, 999):
            v.append(f"{name} is {n} words (limit {limits[name]}): {text!r}")
    if not _words(cta):
        v.append("cta is empty: every post needs exactly ONE ask")
    # ONE CTA: the ask is a single imperative, not a chain of them.
    cta_s = str(cta or "").strip()
    # split only on terminal punctuation followed by whitespace, so a domain
    # like example.invalid is one token, not two "sentences"
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", cta_s) if s.strip()]
    if len(sentences) > 1:
        v.append(f"cta carries more than one ask ({len(sentences)} sentences): "
                 f"{cta_s!r}")
    if re.search(r"\b(and|then|plus|also)\b", cta_s, re.IGNORECASE):
        v.append(f"cta chains asks together ('and/then/plus/also'): {cta_s!r} "
                 "-- one clear CTA only")
    # Names listed in the per-machine copy bank never ship.
    for name, text in fields.items():
        for hit in _banned_hits(text, banned):
            v.append(f"{name} names retired brand {hit!r}: never ship it")
    # Wordmark spelling: a studio named with the wrong casing/spelling is not
    # the registered mark. Compare case-insensitively, flag inexact casing.
    all_text = " ".join(str(t or "") for t in fields.values())
    for mark in _wordmarks():
        for m in re.finditer(re.escape(mark), all_text, re.IGNORECASE):
            if m.group(0) != mark:
                v.append(f"wordmark misspelled: {m.group(0)!r} should read "
                         f"{mark!r}")
    return v


# ---------------------------------------------------------------- kit build

def _default_copy_bank_data():
    from . import products
    ctas = {k: products.PRODUCTS[k]["end_tag"]
            for k in ("video", "story", "games", "adventure")}
    return {"version": KIT_VERSION,
            "limits": dict(DEFAULT_LIMITS),
            "one_cta": True,
            "banned": list(DEFAULT_BANNED),
            "ctas": ctas}


def _write_default_copy_bank(path):
    import yaml
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(_default_copy_bank_data(), f, sort_keys=False,
                       allow_unicode=True)
    return path


def _dominant_accent(logo_path):
    """The brand accent: the managed account wordmark's dominant saturated hue, lifted to
    caption-highlight lightness. Sampled ONCE at build time and pinned in
    kit.json so every caption uses the same accent."""
    import colorsys
    from PIL import Image
    try:
        im = Image.open(logo_path).convert("RGBA")
        im.thumbnail((160, 160))
        raw = im.tobytes()        # RGBA, 4 bytes per pixel
        px = [(raw[i], raw[i + 1], raw[i + 2])
              for i in range(0, len(raw), 4) if raw[i + 3] > 40]
        if not px:
            return FALLBACK_ACCENT
        tmp = Image.new("RGB", (len(px), 1))
        tmp.putdata(px)
        q = tmp.quantize(colors=8, method=Image.MEDIANCUT).convert("RGB")
        counts = sorted(q.getcolors(1 << 16) or [], reverse=True)
        best, score_best = None, -1.0
        total = sum(n for n, _ in counts) or 1
        for n, (r, g, b) in counts:
            h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
            score = s * (n / total) ** 0.4 * (1.0 - abs(l - 0.5))
            if score > score_best:
                best, score_best = (h, l, s), score
        if best is None or best[2] < 0.15:      # a monochrome mark has no hue
            return FALLBACK_ACCENT
        r, g, b = colorsys.hls_to_rgb(best[0], 0.62, max(0.78, best[2]))
        return "#%02X%02X%02X" % (int(r * 255), int(g * 255), int(b * 255))
    except Exception:
        return FALLBACK_ACCENT


# The endcard is a LAYER, not a slide (reviewer, 2026-08-02): a dark
# translucent scrim over the still-playing video with the wordmark and CTA on
# top. The old fully opaque RGB cards replaced the frame and killed the
# layered look. 0.60 sits inside the decided 55-65% band.
ENDCARD_SCRIM_ALPHA = 0.60


def _render_endcard_png(logo_path, cta_text, out_path, size=(1080, 1920)):
    """Static endcard overlay: TRANSPARENT background, a ~60% black scrim
    layer across the frame, the REAL registered wordmark centered and the CTA
    line beneath it in the kit font (captions' fallback chain otherwise).
    RGBA on purpose -- composited over the video, the footage keeps playing
    through the scrim instead of being replaced by an opaque card."""
    from PIL import Image, ImageDraw, ImageFont
    W, H = size
    a = int(round(ENDCARD_SCRIM_ALPHA * 255))
    img = Image.new("RGBA", (W, H), (9, 12, 10, a))      # translucent INK scrim
    d = ImageDraw.Draw(img)
    logo = Image.open(logo_path).convert("RGBA")
    scale = min(640 / logo.width, 320 / logo.height)
    logo = logo.resize((max(1, int(logo.width * scale)),
                        max(1, int(logo.height * scale))), Image.LANCZOS)
    ly = int(H * 0.46) - logo.height // 2
    img.paste(logo, ((W - logo.width) // 2, ly), logo)
    fpath = font("headline") or font("caption")

    def _font(sz):
        try:
            if fpath:
                return ImageFont.truetype(fpath, sz)
        except OSError:
            pass
        from . import captions
        return captions._font(sz)

    maxw = int(W * 0.86)

    def _wrap(text, fnt):
        lines, cur = [], ""
        for w in text.split():
            t = (cur + " " + w).strip()
            if not cur or d.textlength(t, font=fnt) <= maxw:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    # An authored CTA can be a title + ask ("Sample Title - watch the
    # full episode on example.invalid"). Split on a spaced dash into logical lines, then
    # word-wrap and shrink the font so it fits the card instead of overflowing
    # the single centered line the static renderer used to draw.
    for dash in (" — ", " – ", " - "):
        cta_text = cta_text.replace(dash, "\n")
    parts = [p.strip() for p in cta_text.split("\n") if p.strip()]
    size = 46
    f = _font(size)
    lines = [ln for p in parts for ln in _wrap(p, f)]
    while len(lines) > 2 and size > 28:
        size -= 4
        f = _font(size)
        lines = [ln for p in parts for ln in _wrap(p, f)]
    y = ly + logo.height + 72
    lh = int(size * 1.3)
    for ln in lines:
        tw = d.textlength(ln, font=f)
        d.text(((W - tw) / 2, y), ln, font=f, fill=(252, 252, 251))
        y += lh
    img.save(out_path)
    return out_path


def stale_studios():
    """Studios whose SOURCE logo hash no longer matches kit.json (their
    derived assets are stale), plus studios missing derived assets."""
    kit = _kit_json()
    recorded = kit.get("sources") or {}
    out = []
    for key, logo in _studio_logos().items():
        if recorded.get(key) != _sha256(logo) or not endcard(key):
            out.append(key)
    return out


def build_defaults(force=False):
    """`reelly brandkit init`: create the tree, generate copy_bank.yaml,
    render static endcard PNGs per studio, sample the brand accent, and write
    kit.json with source hashes for invalidation. Idempotent; only stale or
    missing assets are re-rendered unless force=True. Animated .mov endcards
    and outros are a later manual upgrade -- the accessors already prefer
    them when dropped into the kit."""
    root = kit_dir()
    for d in ("endcards", "outros", "fonts", "music"):
        os.makedirs(os.path.join(root, d), exist_ok=True)

    bank_path = os.path.join(root, "copy_bank.yaml")
    if force or not os.path.exists(bank_path):
        _write_default_copy_bank(bank_path)

    logos = _studio_logos()
    kit = _kit_json()
    recorded = kit.get("sources") or {}
    built, kept = [], []
    from . import products
    bank = copy_bank()
    for key, logo in logos.items():
        digest = _sha256(logo)
        target = os.path.join(root, "endcards", key + ".png")
        fresh = (recorded.get(key) == digest and os.path.exists(target))
        if fresh and not force:
            kept.append(key)
        else:
            cta = (bank.get("ctas") or {}).get(key) \
                or products.PRODUCTS[key]["end_tag"]
            _render_endcard_png(logo, cta, target)
            built.append(key)
        recorded[key] = digest

    # accent: first available studio wordmark on disk
    acc_src = next(iter(logos.values()), None)
    acc = _dominant_accent(acc_src) if acc_src else FALLBACK_ACCENT

    kit_out = {"version": KIT_VERSION, "sources": recorded, "accent": acc,
               "accent_source": acc_src}
    with open(os.path.join(root, "kit.json"), "w") as f:
        json.dump(kit_out, f, indent=1)
    summary = {"kit_dir": root, "accent": acc, "endcards_built": built,
               "endcards_kept": kept, "copy_bank": bank_path,
               "studios": sorted(logos)}
    print(f"[brandkit] kit at {root}: accent {acc}, endcards built "
          f"{built or 'none'}, kept {kept or 'none'}")
    return summary


if __name__ == "__main__":
    print(json.dumps(build_defaults(), indent=1))
