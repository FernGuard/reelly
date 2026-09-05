"""Require human screening before a cut is marked deliverable.

The software does not infer these categories reliably. A reviewer must inspect
the rendered frames and record a verdict on the plan, for example::

    plan["screened"] = {"by": "reviewer", "on": "YYYY-MM-DD",
                        "verdict": "clean"}
"""
import hashlib
import os

CATEGORIES = ("guns / blood", "retired branding", "real trademarks",
              "children in peril", "burned-in text")


def verdict(plan):
    """QC check. An unscreened cut is not finished, whatever else passes."""
    s = plan.get("screened")
    if not s:
        return ("screened", "FAIL",
                "not screened. Watch it end to end and record "
                "plan['screened'] = {'by': ..., 'on': ..., 'verdict': ...}. "
                "Check for: " + ", ".join(CATEGORIES))
    if isinstance(s, str):
        s = {"verdict": s}
    v = str(s.get("verdict", "")).strip().lower()
    who = s.get("by") or "unknown"
    when = s.get("on") or "undated"
    if not v:
        return ("screened", "FAIL", "screened block present but no verdict")
    if v.startswith("clean"):
        return ("screened", "PASS", f"clean, screened by {who} on {when}")
    # A recorded rejection is a pass for the CHECK and a stop for the cut:
    # the point is that somebody looked and wrote down what they saw.
    return ("screened", "FAIL",
            f"rejected by {who} on {when}: {s.get('verdict')}")


def fingerprint(path, chunk=1 << 20):
    """Content hash, so the same file cannot be posted twice as two posts."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()[:16]


def duplicates(paths):
    """Group finished files that are byte-identical.

    Learned 2026-07-28: a workshop promo and its day-of reminder were set to
    post the same file two days apart on the same account, which is the
    within-platform duplicate that gets flagged as unoriginal. A promo and its
    reminder are different posts and need different cuts.
    """
    seen = {}
    for p in paths:
        if os.path.isfile(p):
            seen.setdefault(fingerprint(p), []).append(os.path.basename(p))
    return {k: v for k, v in seen.items() if len(v) > 1}
