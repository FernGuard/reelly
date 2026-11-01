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
    brands/<brand>/              one folder per brand (a studio, or each of
      brand.json                 its games): {"name", "accent", "ink",
      <font files>               "surface", "fonts": {role: file}}

BRANDS: a run picks one with `--brand NAME` (REELLY_BRAND) or kit.json
"default_brand". The active brand's fonts, accent, ink and surface win over
the flat kit; anything a brand leaves out falls back to the flat kit, then to
the built-in defaults. No brand active = exactly the pre-brand behaviour.

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
DEFAULT_KIT_DIR = None      # None -> <config.HOME>/brandkit

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
    """The kit root, resolved at call time so tests/env can redirect it:
    REELLY_BRANDKIT, else <Reelly home>/brandkit (REELLY_HOME aware)."""
    from . import config
    return os.path.expanduser(os.environ.get("REELLY_BRANDKIT")
                              or DEFAULT_KIT_DIR
                              or os.path.join(config.HOME, "brandkit"))


def _kit_json():
    p = os.path.join(kit_dir(), "kit.json")
    try:
        with open(p, encoding="utf-8") as f:
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


_HEX = re.compile(r"#[0-9a-fA-F]{6}")
# A role a brand does not define borrows from the next one down.
ROLE_FALLBACK = {"headline": ("headline", "caption"),
                 "caption": ("caption",),
                 "cta": ("cta", "caption"),
                 "kicker": ("kicker", "caption")}


def brand_name():
    """The active brand for this run, or None. REELLY_BRAND wins (what the
    CLI's --brand sets), then kit.json "default_brand"."""
    name = (os.environ.get("REELLY_BRAND") or "").strip()
    if not name:
        name = str(_kit_json().get("default_brand") or "").strip()
    if not name or not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name in (".", ".."):
        return None
    return name


def brand_dir(name=None):
    name = name or brand_name()
    return os.path.join(kit_dir(), "brands", name) if name else None


def brand(name=None):
    """The active brand's brand.json as a dict ({} when no brand is active or
    its file is missing or broken). A named brand whose folder is missing is
    reported once, loudly: rendering silently in the wrong type is the
    failure this guards against."""
    d = brand_dir(name)
    if not d:
        return {}
    try:
        with open(os.path.join(d, "brand.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        if d not in _WARNED:
            _WARNED.add(d)
            print(f"[brand] WARNING: brand '{os.path.basename(d)}' has no "
                  f"readable brand.json at {d}; using the flat kit")
        return {}


_WARNED = set()


def _brand_color(key):
    v = brand().get(key)
    return v if isinstance(v, str) and _HEX.fullmatch(v) else None


def ink():
    """Text colour of the active brand, or None (callers keep their own)."""
    return _brand_color("ink")


def surface():
    """Plate / backdrop colour of the active brand, or None."""
    return _brand_color("surface")


def _brand_font(role):
    d = brand_dir()
    if not d:
        return None
    fonts = brand().get("fonts")
    fonts = fonts if isinstance(fonts, dict) else {}
    for r in ROLE_FALLBACK.get(role, (role, "caption")):
        fn = fonts.get(r)
        if isinstance(fn, str) and fn and os.path.basename(fn) == fn:
            p = os.path.join(d, fn)
            if os.path.exists(p):
                return p
    return None


def font(role="caption"):
    """Brand font file for a role, or None (caller falls back to its own
    system-font list -- captions.FONTS today). Resolution order: the active
    brand's role (headline borrows caption), then fonts/<role>.ttf|.otf,
    fonts/default.ttf|.otf, then any font in fonts/."""
    bf = _brand_font(role)
    if bf:
        return bf
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
    """The single brand accent: the active brand's, else the one sampled
    from a wordmark at kit-build time (kit.json), Blue Smoke when no kit
    exists."""
    b = _brand_color("accent")
    if b:
        return b
    a = _kit_json().get("accent")
    return a if isinstance(a, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", a) \
        else FALLBACK_ACCENT


def reframe():
    """The active brand's framing for 16:9 footage in a 9:16 frame, or None.

    brand.json "video": {"reframe": "full_width"} (or 1.0) shows the whole width of the
    game, smaller; a number up to 2.0 zooms in and crops the sides. A game
    whose screen must read whole (two boards, a HUD at both edges) sets
    full_width; None leaves Reelly's own choice."""
    v = video("reframe")
    if isinstance(v, str) and v.strip().lower() in ("full_width", "full", "fit"):
        return 1.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return max(1.0, min(2.0, f))


def copy_brief():
    """The active brand's copy brief (brand.json "copy"), {} without one.

    Keys the editor brain uses: audience, promise, proof, voice (one line
    each), cta (a fixed ask, used verbatim), cta_hot (its action word),
    avoid (words and claims never to write). All brand data lives in the
    user's kit, never in the repository."""
    c = brand().get("copy")
    return c if isinstance(c, dict) else {}


# ---------------------------------------------------------------- schema 1
# brand.json, version 1. Colours, fonts, copy, music and sfx keep their
# places; how the video looks and moves is grouped:
#   "schema": 1,
#   "video":   {"fps": 30|60, "reframe": "full_width"|1.0-2.0,
#               "content_type": "gameplay"|..., "clip_len": [min_s, max_s]},
#   "text":    {"motion": "rise"|"blocklock", "exit": "fade"|"petals",
#               "petal_color": "#RRGGBB", "behind_subject": false},
#   "effects": {"endcard": "smoke", "ambient": ["smoke", {"preset": "embers",
#               "params": {"u_density": 0.4}}], "text": "smoke", "finish": null}
# `reelly brand check NAME` validates a kit; `reelly brand migrate NAME`
# rewrites an older one.
SCHEMA = 1
TOP_KEYS = {"schema", "name", "accent", "ink", "surface", "fonts", "copy", "music", "sfx",
            "video", "text", "effects", "source", "license", "notes"}
SECTION_KEYS = {
    "video": {"fps", "reframe", "content_type", "clip_len"},
    "text": {"motion", "exit", "petal_color", "behind_subject"},
    "effects": {"endcard", "ambient", "text", "finish"},
    "copy": {"audience", "promise", "proof", "voice", "cta", "cta_hot", "avoid",
             "approved_lines", "rejected_lines"},
    "music": {"tracks", "match", "default", "cleared", "bpm", "source"},
}
# where keys from before schema 1 went (for clear errors and `migrate`)
MOVED = {"fps": "video.fps", "reframe": "video.reframe", "profile": "video.content_type",
         "fx": "effects (+ text.motion / text.exit / text.petal_color / "
               "text.behind_subject)",
         "outro_fx": "effects.endcard", "ambient_fx": "effects.ambient",
         "text_fx": "effects.text", "finish_fx": "effects.finish",
         "copy.clip_len": "video.clip_len"}


def section(name):
    """One schema-1 section of the active brand ({} when absent)."""
    v = brand().get(name)
    return v if isinstance(v, dict) else {}


def video(key, default=None):
    return section("video").get(key, default)


def text_style(key, default=None):
    return section("text").get(key, default)


def effect_entries(placement):
    """[(preset name, params)] the brand plays at a placement, stack order."""
    entry = section("effects").get(placement)
    out = []
    for e in (entry if isinstance(entry, list) else [entry]):
        if isinstance(e, dict):
            out.append((str(e.get("preset") or ""), e.get("params") or {}))
        elif isinstance(e, str) and e:
            out.append((e, {}))
    return out


def check(data):
    """(errors, warnings) for a brand.json dict. Errors stop a run at its
    first second (a typo must never silently switch an effect off)."""
    from . import gputext, performance, shaderfx
    err, warn = [], []
    if not isinstance(data, dict):
        return ["brand.json is not a JSON object"], []
    if data.get("schema") is None:
        warn.append('no "schema": 1 (add it, or run `reelly brand migrate`)')
    elif data.get("schema") != SCHEMA:
        err.append(f"schema {data.get('schema')!r} is not supported (this Reelly reads {SCHEMA})")
    for k in data:
        if k in MOVED:
            err.append(f'"{k}" moved to {MOVED[k]} (run `reelly brand migrate`)')
        elif k not in TOP_KEYS:
            err.append(f'unknown key "{k}"')
    for k in ("accent", "ink", "surface"):
        if k in data and not (isinstance(data[k], str) and _HEX.fullmatch(data[k])):
            err.append(f'"{k}" must be a #RRGGBB colour')
    fonts = data.get("fonts", {})
    if not isinstance(fonts, dict) or not all(isinstance(v, str) and os.path.basename(v) == v
                                              for v in fonts.values()):
        err.append('"fonts" must map roles to font file names in the brand folder')
    for sec, keys in SECTION_KEYS.items():
        v = data.get(sec)
        if v is None:
            continue
        if sec == "music" and isinstance(v, dict) and "tracks" not in v:
            continue                    # the flat map {key: file} is still read as tracks
        if not isinstance(v, dict):
            err.append(f'"{sec}" must be an object')
            continue
        for k in v:
            if f"{sec}.{k}" in MOVED:
                err.append(f'"{sec}.{k}" moved to {MOVED[sec + "." + k]}')
            elif k not in keys:
                (warn if sec == "copy" else err).append(f'unknown key "{sec}.{k}"')
    vid, txt, fx = (data.get(k) if isinstance(data.get(k), dict) else {}
                    for k in ("video", "text", "effects"))
    if "fps" in vid and vid["fps"] not in (24, 25, 30, 50, 60):
        err.append('"video.fps" must be 24, 25, 30, 50 or 60')
    if "reframe" in vid:
        r = vid["reframe"]
        ok = (isinstance(r, str) and r.strip().lower() in ("full_width", "full", "fit")) or \
             (isinstance(r, (int, float)) and 1.0 <= r <= 2.0)
        if not ok:
            err.append('"video.reframe" must be "full_width" or a zoom from 1.0 to 2.0')
    if "content_type" in vid and str(vid["content_type"]).strip().lower().replace("_", "-") \
            not in shaderfx.content_types():
        err.append(f'"video.content_type" must be one of {shaderfx.content_types()}')
    if "clip_len" in vid and performance._parse_len(vid["clip_len"]) is None:
        err.append('"video.clip_len" must be [min, max] seconds, 5 <= min < max <= 180')
    if "motion" in txt and txt["motion"] not in gputext.MOTIONS:
        err.append(f'"text.motion" must be one of {list(gputext.MOTIONS)}')
    if "exit" in txt and txt["exit"] not in gputext.EXITS:
        err.append(f'"text.exit" must be one of {list(gputext.EXITS)}')
    if "petal_color" in txt and not (isinstance(txt["petal_color"], str)
                                     and _HEX.fullmatch(txt["petal_color"])):
        err.append('"text.petal_color" must be a #RRGGBB colour')
    if "behind_subject" in txt and not isinstance(txt["behind_subject"], bool):
        err.append('"text.behind_subject" must be true or false')
    for placement, entry in fx.items():
        if placement not in SECTION_KEYS["effects"] or entry is None:
            continue
        if placement != "ambient" and isinstance(entry, list):
            err.append(f'"effects.{placement}" takes one preset, not a list')
            continue
        for e in (entry if isinstance(entry, list) else [entry]):
            name, params = (e.get("preset"), e.get("params") or {}) if isinstance(e, dict) \
                else (e, {})
            if not isinstance(name, str) or not name:
                err.append(f'"effects.{placement}": every entry needs a preset name')
                continue
            if name in ("off", "none"):
                continue
            pid = f"{placement}/{name}"
            if pid not in shaderfx.presets():
                have = [p.split("/", 1)[1] for p in shaderfx.presets()
                        if p.startswith(placement + "/")]
                err.append(f'"effects.{placement}": no preset "{name}" (have: {", ".join(have)})')
                continue
            if not isinstance(params, dict):
                err.append(f'"effects.{placement}" {name}: "params" must be an object')
                continue
            try:
                shaderfx.uniforms(pid, params)
            except (shaderfx.EffectError, ValueError, TypeError, AttributeError) as e2:
                err.append(f'"effects.{placement}" {name}: bad params ({e2})')
    music = data.get("music")
    if isinstance(music, dict) and "cleared" in music and not isinstance(music["cleared"], bool):
        err.append('"music.cleared" must be true or false')
    return err, warn


def load_raw(name=None):
    """(dict or None, error or None) straight from brand.json: a syntax error
    is reported, never read as an empty brand."""
    d = brand_dir(name)
    p = os.path.join(d, "brand.json") if d else None
    if not p or not os.path.exists(p):
        return None, None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f), None
    except ValueError as e:
        return None, f"brand.json is not valid JSON ({e})"
    except OSError as e:
        return None, f"brand.json cannot be read ({e})"


def validate(name=None):
    """(errors, warnings) for a brand by name, including a broken file."""
    data, err = load_raw(name)
    if err:
        return [err], []
    if data is None:
        return [], []
    errs, warns = check(data)
    d = brand_dir(name)
    for role, fn in (data.get("fonts") or {}).items() if isinstance(data.get("fonts"), dict) else []:
        if isinstance(fn, str) and os.path.basename(fn) == fn and not os.path.exists(os.path.join(d, fn)):
            errs.append(f'font "{fn}" ({role}) is not in the brand folder')
    return errs, warns


def require_valid():
    """Stop a run at its first second when the active brand.json has errors
    (a typo in an effect name must not silently switch it off at minute 10)."""
    name = brand_name()
    if not name:
        return
    errs, warns = validate(name)
    for w in warns:
        print(f"[brand] {name}: {w}")
    if errs:
        raise SystemExit(f"[brand] {name}: brand.json has {len(errs)} error(s):\n  - "
                         + "\n  - ".join(errs) + f"\nFix them, then `reelly brand check {name}`.")


def migrate(data):
    """An older brand.json dict rewritten to schema 1 (a new dict)."""
    old = dict(data)
    out = {k: v for k, v in old.items() if k not in MOVED}
    vid = dict(out.get("video") or {})
    txt = dict(out.get("text") or {})
    eff = dict(out.get("effects") or {})
    if "fps" in old:
        vid.setdefault("fps", old["fps"])
    if "reframe" in old:
        vid.setdefault("reframe", old["reframe"])
    if "profile" in old:
        vid.setdefault("content_type", str(old["profile"]).replace("_", "-"))
    copy = dict(out.get("copy") or {})
    if "clip_len" in copy:
        vid.setdefault("clip_len", copy.pop("clip_len"))
        out["copy"] = copy
    fx = old.get("fx") if isinstance(old.get("fx"), dict) else {}
    names = {"smoke-endcard": "smoke", "smoke-ambient": "smoke", "embers-ambient": "embers",
             "impact-ambient": "impact", "focus-ambient": "focus", "smoke-aura": "smoke"}

    def conv(e):
        if isinstance(e, dict):
            return {**e, "preset": names.get(e.get("preset"), e.get("preset"))}
        return names.get(e, e)
    for placement, legacy, key in (("endcard", "outro_fx", "outro"), ("ambient", "ambient_fx", "ambient"),
                                   ("text", "text_fx", "text"), ("finish", "finish_fx", "finish")):
        v = fx.get(key)
        if v is None and legacy and old.get(legacy):
            v = old[legacy]                     # "smoke"
        if v is not None:
            eff.setdefault(placement, [conv(x) for x in v] if isinstance(v, list) else conv(v))
    for a, b in (("text_motion", "motion"), ("text_exit", "exit"), ("petal_color", "petal_color"),
                 ("text_behind", "behind_subject")):
        if a in fx:
            txt.setdefault(b, fx[a])
    for k, v in (("video", vid), ("text", txt), ("effects", eff)):
        if v:
            out[k] = v
    return {"schema": SCHEMA, **{k: v for k, v in out.items() if k != "schema"}}


def record_verdict(name, approve=(), reject=(), keep=40):
    """Append approved / rejected lines to brands/<name>/brand.json "copy"
    (deduplicated, newest last, at most `keep` each), so the next hook
    writer and judge learn the brand's taste. Returns the copy dict."""
    d = brand_dir(name)
    p = os.path.join(d, "brand.json") if d else None
    if not p or not os.path.exists(p):
        raise ValueError(f"no brand.json for brand {name!r}")
    with open(p, encoding="utf-8") as f:
        spec = json.load(f)
    copy = spec.setdefault("copy", {})
    for key, new in (("approved_lines", approve), ("rejected_lines", reject)):
        cur = [x for x in copy.get(key, []) if isinstance(x, str)]
        for line in new:
            line = str(line).strip()
            if line:
                cur = [x for x in cur if x != line] + [line]
        copy[key] = cur[-keep:]
    with open(p, "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1)
    return copy


def font_css():
    """CSS that points Chrome-rendered layers (overlays, cards) at the active
    brand's own font files instead of a font CDN, or "" with no brand fonts.

    `!important` rules beat the templates' inline font-family, so every
    overlay takes the brand type from this one injection point. Headline-like
    marks (.marker) take the headline role and the brand accent."""
    from pathlib import Path
    cap, head = _brand_font("caption"), _brand_font("headline")
    if not cap and not head:
        return ""
    rules = []
    for fam, path in (("ReellyBrandCaption", cap or head),
                      ("ReellyBrandHeadline", head or cap)):
        rules.append("@font-face {font-family:'%s'; src:url('%s');}"
                     % (fam, Path(path).resolve().as_uri()))
    acc = accent()
    rules.append("body, body * {font-family:'ReellyBrandCaption', sans-serif "
                 "!important;}")
    rules.append(".marker {font-family:'ReellyBrandHeadline', sans-serif "
                 "!important; color:%s !important;}" % acc)
    return "\n".join(rules)


def add_brand(name, fonts=None, accent=None, ink=None, surface=None,
              force=False):
    """Create or update brands/<name>/ in the LOCAL kit: copy each font file
    in, write brand.json. Brand assets never live in the repository; this is
    the one-step way for any user to register their own.

    fonts: {role: path} (roles: headline, caption, cta, kicker). Colours are
    #RRGGBB. Returns the brand folder. Raises ValueError on bad input."""
    import shutil
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name or "") or name in (".", ".."):
        raise ValueError(f"brand name must be letters, digits, . _ - : {name!r}")
    for label, c in (("accent", accent), ("ink", ink), ("surface", surface)):
        if c is not None and not _HEX.fullmatch(c):
            raise ValueError(f"{label} must be #RRGGBB, got {c!r}")
    d = os.path.join(kit_dir(), "brands", name)
    spec = {}
    if os.path.exists(os.path.join(d, "brand.json")) and not force:
        try:
            with open(os.path.join(d, "brand.json"), encoding="utf-8") as f:
                spec = json.load(f) or {}
        except (OSError, ValueError):
            spec = {}
    spec = {"schema": SCHEMA, **{k: v for k, v in spec.items() if k != "schema"}}
    spec["name"] = name
    spec.setdefault("fonts", {})
    os.makedirs(d, exist_ok=True)
    for role, src in (fonts or {}).items():
        if role not in ROLE_FALLBACK:
            raise ValueError(f"unknown font role {role!r}; use one of "
                             f"{', '.join(ROLE_FALLBACK)}")
        if not os.path.isfile(src) or not src.lower().endswith((".ttf", ".otf")):
            raise ValueError(f"{role}: need a .ttf or .otf file, got {src!r} "
                             "(cut variable or web fonts to a static .ttf first)")
        fn = f"{role}{os.path.splitext(src)[1].lower()}"
        shutil.copyfile(src, os.path.join(d, fn))
        spec["fonts"][role] = fn
    for key, c in (("accent", accent), ("ink", ink), ("surface", surface)):
        if c is not None:
            spec[key] = c
    with open(os.path.join(d, "brand.json"), "w", encoding="utf-8") as f:
        json.dump(spec, f, indent=1)
    return d


def list_brands():
    """{name: brand.json dict} for every brand in the local kit."""
    root = os.path.join(kit_dir(), "brands")
    out = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for n in names:
        if os.path.isdir(os.path.join(root, n)):
            out[n] = brand(n)
    return out


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
        with open(p, encoding="utf-8") as f:
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
                with open(p, encoding="utf-8") as f:
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
            with open(tmp, "w", encoding="utf-8") as f:
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
    of truth): product names plus any configured domains."""
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
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(_default_copy_bank_data(), f, sort_keys=False,
                       allow_unicode=True)
    return path


def _dominant_accent(logo_path):
    """The brand accent: a wordmark's dominant saturated hue, lifted to
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
    with open(os.path.join(root, "kit.json"), "w", encoding="utf-8") as f:
        json.dump(kit_out, f, indent=1)
    summary = {"kit_dir": root, "accent": acc, "endcards_built": built,
               "endcards_kept": kept, "copy_bank": bank_path,
               "studios": sorted(logos)}
    print(f"[brandkit] kit at {root}: accent {acc}, endcards built "
          f"{built or 'none'}, kept {kept or 'none'}")
    return summary


if __name__ == "__main__":
    print(json.dumps(build_defaults(), indent=1))
