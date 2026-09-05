"""Flag internal language that will not make sense without project context.

This is a configurable floor, not a substitute for editorial judgment. A pass
means only that no known term was detected.
"""
import re

# Internal vocabulary: words that name a thing instead of describing it. The
# replacement is not a synonym, it is the event the word stands for.
INTERNAL = {
    "catalog row": "describe what the viewer will see instead",
    "the row": "describe what the viewer will see instead",
    "cohort": "say 'everyone who entered'",
    "on-ramp": "say what it actually is: a way in, a first project",
    "tentpole": "internal priority word, never viewer-facing",
    "editorial calendar": "internal, never viewer-facing",
    "content queue": "internal, never viewer-facing",
    "deliverable": "say 'the video' or 'the file'",
    "harvest": "internal; say 'clips from the session'",
    "virality gate": "internal, never viewer-facing",
    "north star": "internal, never viewer-facing",
    "funnel": "internal, never viewer-facing",
    "engagement rate": "internal metric, never viewer-facing",
    "impressions": "internal metric, never viewer-facing",
    "utm": "tracking plumbing, never viewer-facing",
    "playbook": "internal, unless the post is genuinely about a guide",
    "b-roll": "trade jargon; describe the shot",
    "edl": "internal",
    "asr": "internal",
    "lufs": "internal",
    "dbtp": "internal",
}

# Optional audience-framing guidance applies only to authored promotional copy,
# not to quotations or descriptions of recorded content.
AUDIENCE_FRAMING = {
    "strangers": "say 'its folk' or 'the people who love this stuff'",
    "tribe": "appropriation baggage; say 'folk'",
}

# what marks a sentence as branded copy rather than a description of the
# recorded content: it addresses the creator or viewer, or names the act of
# being played/watched/found on a feed
_AUDIENCE_CONTEXT = re.compile(
    r"\b(you|your|yours|yourself|we|our|us|everyone|anybody|"
    r"viewers?|audience|followers?|fans?|community|"
    r"scroll\w*|watch\w*|discover\w*|follow\w*|share\w*|post\w*|"
    r"play\w*|build\w*|feed|algorithm|internet|online)\b", re.I)
_SENTENCES = re.compile(r"[.!?\n]+")


# Add organization-specific retired names in private configuration when needed.
RETIRED = {}

# Phase labels from the jam arc: P2, P5 and friends mean nothing to a viewer.
PHASE = re.compile(r"\bP[1-9]\b")


def find(text):
    """Every offender in one string, as (term, guidance) pairs."""
    if not text:
        return []
    low = text.lower()
    hits = []
    for term, why in RETIRED.items():
        if term in low:
            hits.append((term, f"retired name, use {why}"))
    for term, why in INTERNAL.items():
        # Word-boundary match so 'row' inside 'grow' or 'browse' is not a hit.
        if re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", low):
            hits.append((term, why))
    # Brand-voice terms are judged per SENTENCE, because provenance is per
    # sentence: a caption may mix a description of the footage ("Four
    # strangers. One luxury hall.") with a branded ask ("Which one are
    # you?"), and only a sentence that carries the word AND is branded copy
    # is a violation.
    for term, why in AUDIENCE_FRAMING.items():
        pat = re.compile(rf"(?<![a-z]){re.escape(term)}(?![a-z])")
        for sent in _SENTENCES.split(low):
            if pat.search(sent) and _AUDIENCE_CONTEXT.search(sent):
                hits.append((term, f"audience-framing: {why}"))
                break
    if PHASE.search(text):
        hits.append((PHASE.search(text).group(0), "campaign phase label, internal only"))
    return hits


def verdict(plan):
    """Judge check over everything a viewer actually reads.

    Deliberately covers the caption too, not only burned-in text: the caption is
    read by the same stranger and is where jargon survives longest, because it
    never goes through a render anyone watches.

    THE TRANSCRIPT BOUNDARY (reviewer, 2026-08-01): only AUTHORED fields are
    inspected -- hook, cta, caption, title, overlay_lines. The transcript and
    the caption cues derived from it are a record of what was said on camera,
    not a copy decision, and "recorded video or audio should not be measured
    by these copy decisions." Do not add plan["transcript"] (or anything
    ASR-derived) to the field list; a regression test pins this.

    EXEMPTIONS: some entries are brand-voice
    rules, not jargon -- "strangers" is banned as a description of a creator's
    AUDIENCE ("Make things. Find your folk."), but it false-positived on
    narrative copy about four fictional characters who genuinely do not know
    each other. A plan may exempt a term for THIS cut with a recorded reason:

        plan["plain_exempt"] = {"strangers": "narrative: the characters are
                                strangers to each other, not the audience"}

    The exemption is scoped to the plan, requires a reason, and is echoed in
    the PASS detail so the QC report shows someone made the call. The term
    stays in the gate for every other cut.
    """
    # hook is a dict in every plan the brain writes ({type, text, show_s});
    # the viewer reads its text, so that is what gets checked.
    hook = plan.get("hook")
    if isinstance(hook, dict):
        hook = hook.get("text")
    fields = [("hook", hook), ("cta", plan.get("cta")),
              ("caption", plan.get("caption")), ("title", plan.get("title"))]
    fields += [(f"overlay@{l.get('t')}s", l.get("text"))
               for l in (plan.get("overlay_lines") or [])]
    exempt = plan.get("plain_exempt") or {}
    bad, noted = [], []
    for where, text in fields:
        for term, why in find(text):
            reason = exempt.get(term.lower()) or exempt.get(term)
            if reason and str(reason).strip():
                noted.append(f"{where}: '{term}' exempted: {reason}")
            else:
                bad.append(f"{where}: '{term}' ({why})")
    if not bad:
        detail = "no known internal vocabulary"
        if noted:
            detail += "; recorded exemptions: " + "; ".join(noted)
        return ("P-PLAIN", "PASS", detail)
    return ("P-PLAIN", "FAIL", "; ".join(bad))
