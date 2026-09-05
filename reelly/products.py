"""Neutral product attribution and delivery routing.

Real brands, domains, campaign identifiers, logos, and community links belong
in per-machine configuration outside this public repository.
"""
import json
import os

PLATFORMS = {
    "tiktok":  {"mix": "clean", "file": "_trending",
                "note": "add licensed/trending audio in-app if authorized"},
    "reels":   {"mix": "clean", "file": "_trending",
                "note": "add licensed/trending audio in-app if authorized"},
    "shorts":  {"mix": "clean", "file": "_trending",
                "note": "add platform audio in-app if authorized"},
    "youtube": {"mix": "music", "file": "", "note": "generated bed baked in"},
    "x":       {"mix": "music", "file": "", "note": "generated bed baked in"},
    "threads": {"mix": "music", "file": "", "note": "generated bed baked in"},
    "master":  {"mix": "music", "file": "", "note": "archive/master copy"},
}
DEFAULT_TARGETS = ["tiktok", "reels", "shorts", "threads", "x"]


def delivery_targets(project_root, cli_for=None):
    """Precedence: --for flag > project delivery.json > default."""
    if cli_for:
        targets = [t.strip() for t in cli_for.split(",") if t.strip()]
    else:
        p = os.path.join(project_root, "delivery.json")
        targets = (json.load(open(p)).get("targets", DEFAULT_TARGETS)
                   if os.path.exists(p) else DEFAULT_TARGETS)
    bad = [t for t in targets if t not in PLATFORMS]
    if bad:
        raise SystemExit(f"unknown delivery targets: {bad}; "
                         f"known: {', '.join(PLATFORMS)}")
    return targets


def platform_spec(target, account=None):
    pf = dict(PLATFORMS[target])
    if account and not account.get("trending_audio", True) and pf["mix"] == "clean":
        pf.update({"mix": "music", "file": "",
                   "note": (f"generated bed baked in; account "
                            f"'{account.get('name', '?')}' does not use "
                            "in-app trending audio")})
    return pf


# Neutral examples. Override any field in ~/.reelly/products.json.
PRODUCTS = {
    "video": {"name": "Video Project", "url": "", "campaign": "video",
              "end_tag": "Edited with Reelly"},
    "story": {"name": "Story Project", "url": "", "campaign": "story",
              "end_tag": "Edited with Reelly"},
    "games": {"name": "Game Project", "url": "", "campaign": "games",
              "end_tag": "Edited with Reelly"},
    "adventure": {"name": "Adventure Project", "url": "", "campaign": "adventure",
                  "end_tag": "Edited with Reelly"},
}
ALIASES = {}
DISCORD = ""


def _load_product_overrides():
    p = os.path.expanduser("~/.reelly/products.json")
    if not os.path.exists(p):
        return
    try:
        data = json.load(open(p))
        for key, override in (data or {}).items():
            merged = dict(PRODUCTS.get(key, {}))
            merged.update(override or {})
            PRODUCTS[key] = merged
    except (ValueError, OSError) as e:
        print(f"[products] WARNING: could not read {p} ({e}); using public defaults")


_load_product_overrides()


def brand_logo(product_key):
    """Logo for a product, configured per machine in ~/.reelly/config.json."""
    cfg = os.path.expanduser("~/.reelly/config.json")
    if not os.path.exists(cfg):
        return None
    try:
        logos = json.load(open(cfg)).get("logos") or {}
    except (ValueError, OSError):
        return None
    key = ALIASES.get(product_key, product_key)
    path = logos.get(key) or logos.get(product_key)
    return os.path.expanduser(path) if path and os.path.exists(os.path.expanduser(path)) else None


def link(product_key, channel):
    p = PRODUCTS[product_key]
    base = (p.get("url") or "").strip()
    if not base:
        return ""
    medium = "newsletter" if channel == "substack" else "social"
    sep = "&" if "?" in base else "?"
    return (f"{base}{sep}utm_source={channel}&utm_medium={medium}"
            f"&utm_campaign={p.get('campaign', product_key)}")


def description_md(product_key, plan, path, targets=None, account=None,
                   variants=None):
    """Write a neutral, paste-ready posting block for a rendered cut."""
    p = PRODUCTS[product_key]
    targets = targets or DEFAULT_TARGETS
    hook = plan.get("hook", {}).get("text", "")
    suffixes = {"plain": "", "gfx": "_gfx", "trending": "_trending",
                "trending_gfx": "_trending_gfx"}
    routes = {"": ["plain", "gfx"],
              "_trending": ["trending", "trending_gfx", "gfx", "plain"]}

    def routed(base):
        if variants is None:
            return base
        return next((suffixes[v] for v in routes.get(base, []) if v in variants), base)

    def route_note(base, selected, note):
        if base == "_trending" and selected in ("", "_gfx"):
            return ("no _trending file in this variant set; this file has the "
                    "generated bed baked in")
        return note

    scope = ("in-app audio enabled"
             if not account or account.get("trending_audio", True)
             else f"in-app audio disabled for account '{account.get('name', '?')}'")
    lines = [f"# {plan['id']}: {plan['title']} - posting block", "",
             f"## Which file to post where ({scope})", ""]
    for target in targets:
        spec = platform_spec(target, account)
        suffix = routed(spec["file"])
        note = route_note(spec["file"], suffix, spec["note"])
        lines.append(f"- {target}: `{plan['id']}{suffix}.mp4` ({note})")

    cta = (plan.get("cta") or "").strip()
    lines += ["", "## The ask", "",
              f"**On screen:** {cta}" if cta else
              "**On screen:** NONE PLANNED - add one before publishing.", ""]
    base_link = link(product_key, "tiktok")
    if base_link:
        lines.append(f"- Destination: {base_link.split('?')[0]}")
    lines += ["", "## Posting checklist", "",
              "- [ ] Confirm rights and consent for source media",
              "- [ ] Enable the platform's AI-content label when required",
              "- [ ] Add commercial disclosure when required", "",
              "## Caption", "", "```",
              (plan.get("caption") or f"{hook.rstrip('.?!')}.")
              + (f"\n\n{cta[0].upper() + cta[1:]}." if cta else "")
              + f"\n\nEdited with {p['name']}.", "```", ""]

    tracked = [(t, link(product_key, {"reels": "instagram", "shorts": "youtube"}.get(t, t)))
               for t in targets]
    tracked = [(t, u) for t, u in tracked if u]
    if tracked:
        lines += ["## Tracked links", ""]
        lines += [f"- {t}: {u}" for t, u in tracked]
        lines.append("")
    lines += ["## Clip facts", "",
              f"- Duration: {plan['duration_s']}s | format {plan.get('format', '?')}",
              f"- Says: {plan.get('transcript', '')[:200]}", ""]
    open(path, "w").write("\n".join(lines))
    return path
