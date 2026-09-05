"""Configurable performance gates for short-form edit plans.

The values in this module are conservative public defaults. They are not based
on a bundled client, account, campaign, or private analytics export. Teams
should tune them with their own data outside the repository.
"""

# Preferred and absolute public-default duration windows.
TARGET_LEN = (20.0, 28.0)
HARD_LEN = (12.0, 32.0)

# Visible qualities that give a short-form clip something to follow.
HANDLES = {
    "persistent_character": "a character on screen long enough to follow",
    "readable_detail": "on-screen text or a detail worth pausing on",
    "narrative_turn": "something changes; a before and an after",
    "creator_credit": "source work credited when a handle is available",
}
MIN_HANDLES = 2

# ASR trust defaults. Repetitive or very sparse transcripts are unsafe inputs
# for burned captions and language-driven planning.
MIN_UNIQUE_RATIO = 0.65
MIN_WPM = 15.0


def transcript_trust(sents, duration_s):
    """Return whether a transcript is safe to caption and plan from."""
    texts = [s["text"].strip() for s in sents if s.get("text", "").strip()]
    n = len(texts)
    if not n:
        return {"trusted": False, "unique_ratio": 0.0, "wpm": 0.0, "cues": 0,
                "worst_repeat": 0, "worst_line": "",
                "reason": "no speech found: plan visually"}
    uniq = len(set(texts))
    ratio = uniq / n
    words = sum(len(t.split()) for t in texts)
    wpm = (words / duration_s * 60.0) if duration_s else 0.0
    worst_line = max(set(texts), key=texts.count)
    worst = texts.count(worst_line)
    ok = ratio >= MIN_UNIQUE_RATIO and wpm >= MIN_WPM
    if ok:
        reason = f"transcript trusted: {ratio:.0%} unique cues, {wpm:.0f} wpm"
    elif ratio < MIN_UNIQUE_RATIO:
        reason = (f"ASR repetition: only {ratio:.0%} unique cues "
                  f"({worst}x {worst_line[:40]!r}); planning visually")
    else:
        reason = f"too little speech to plan from: {wpm:.0f} wpm; planning visually"
    return {"trusted": ok, "unique_ratio": round(ratio, 3), "wpm": round(wpm, 1),
            "cues": n, "worst_repeat": worst, "worst_line": worst_line[:60],
            "reason": reason}


def length_verdict(duration_s):
    """Return ``(ok|flag|drop, message)`` for the configured duration windows."""
    lo, hi = TARGET_LEN
    hlo, hhi = HARD_LEN
    if lo <= duration_s <= hi:
        return "ok", f"P-LEN {duration_s:.1f}s inside the default {lo:.0f}-{hi:.0f}s window"
    if duration_s < hlo or duration_s > hhi:
        return "drop", (f"P-LEN {duration_s:.1f}s outside the hard {hlo:.0f}-{hhi:.0f}s "
                        f"window; configure the gate for a different format")
    return "flag", (f"P-LEN {duration_s:.1f}s outside the preferred {lo:.0f}-{hi:.0f}s "
                    "window but inside tolerance")


def clean_handles(raw):
    """Keep recognized handle names, deduplicated in input order."""
    out = []
    for h in raw or []:
        k = str(h).strip().lower().replace(" ", "_").replace("-", "_")
        if k in HANDLES and k not in out:
            out.append(k)
    return out


def handles_verdict(handles):
    """Return ``(ok|drop, message)`` for the minimum-handle gate."""
    if len(handles) >= MIN_HANDLES:
        return "ok", f"P-HANDLES {len(handles)}: {', '.join(handles)}"
    got = ", ".join(handles) if handles else "none"
    return "drop", (f"P-HANDLES only {len(handles)} ({got}); needs {MIN_HANDLES}. "
                    "Add a visible character, detail, narrative turn, or source credit.")


def outlier_score(views, channel_median):
    """Return views divided by the caller-provided channel median."""
    if not channel_median:
        return None
    return round(views / channel_median, 1)
