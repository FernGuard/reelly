"""Publishing-account profiles.

Public defaults are neutral. Organization-specific account names and workflows
belong in ~/.reelly/accounts.json, never in this repository.
"""
import json
import os

from . import config

VARIANTS = ("plain", "gfx", "trending", "trending_gfx")
_SUFFIX = {"plain": "", "gfx": "_gfx", "trending": "_trending",
           "trending_gfx": "_trending_gfx"}

DEFAULTS = {
    "creator": {
        "posts_natively": True,
        "trending_audio": True,
        "variants": ["plain", "gfx", "trending", "trending_gfx"],
        "note": "generic profile for native posting",
    },
    "managed": {
        "posts_natively": False,
        "trending_audio": False,
        "variants": ["gfx"],
        "note": "generic profile for a third-party scheduling workflow",
    },
}
DEFAULT_ACCOUNT = "creator"
DEFAULT_VARIANTS = ["gfx"]


def registry():
    """Built-in neutral profiles plus per-machine overrides."""
    reg = {k: dict(v) for k, v in DEFAULTS.items()}
    p = os.path.join(config.HOME, "accounts.json")
    if os.path.exists(p):
        try:
            for name, prof in json.load(open(p)).items():
                merged = reg.get(name, {})
                merged.update(prof or {})
                reg[name] = merged
        except (ValueError, OSError) as e:
            print(f"[accounts] WARNING: could not read {p} ({e}); "
                  "using built-in profiles")
    return reg


def load(name=None):
    reg = registry()
    name = name or DEFAULT_ACCOUNT
    if name not in reg:
        raise SystemExit(f"unknown account: {name}; known: {', '.join(sorted(reg))}")
    prof = dict(reg[name])
    prof["name"] = name
    return prof


def _delivery_json(project_root):
    p = os.path.join(project_root, "delivery.json")
    if os.path.exists(p):
        try:
            return json.load(open(p)) or {}
        except (ValueError, OSError):
            pass
    return {}


def for_project(project_root, cli_account=None):
    """Precedence: --account flag > delivery.json > neutral default."""
    if cli_account:
        prof = load(cli_account)
        prof["explicit"] = True
        return prof
    name = _delivery_json(project_root).get("account")
    prof = load(name)
    prof["explicit"] = bool(name)
    return prof


def variants_for(project_root, profile, cli_variants=None):
    """Choose output variants from CLI, project config, profile, then default."""
    if cli_variants:
        wanted = [v.strip() for v in cli_variants.split(",") if v.strip()]
    else:
        wanted = (_delivery_json(project_root).get("variants")
                  or (profile.get("variants") if profile.get("explicit") else None)
                  or list(DEFAULT_VARIANTS))
    bad = [v for v in wanted if v not in VARIANTS]
    if bad:
        raise SystemExit(f"unknown variants: {bad}; known: {', '.join(VARIANTS)}")
    if not profile.get("trending_audio", True):
        dropped = [v for v in wanted if v.startswith("trending")]
        if dropped:
            print(f"[accounts] dropping {', '.join(dropped)}: account "
                  f"'{profile['name']}' has no in-app trending-audio workflow")
        wanted = [v for v in wanted if not v.startswith("trending")]
    if not wanted:
        raise SystemExit("no deliverable variants left after account scoping; "
                         "check delivery.json / --variants")
    return wanted


def suffix(variant):
    return _SUFFIX[variant]
