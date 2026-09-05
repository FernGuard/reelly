"""Clearance: which parts of a session are allowed to become cuts.

Two distinct risks, one mechanism (a blocked time range the planner must not
touch and the judge must not pass):

VOICE CLEARANCE (gap 8, DEFAULT-DENY since 2026-08-02)
Voice clearance is decided per session: AI character voices are product and
always usable; human participant voices are cleared case by case. The
division of labour: the LOCAL diarizer (reelly/diarize.py, pyannote --
reviewer chose local over a recurring per-minute cloud charge) answers WHICH
speaker talks WHEN and writes analysis/speaker_turns.json; the DECLARED map
analysis/voices.json stays the source of truth for WHO is allowed. A human
marks each diarized speaker id cleared or not; an uncleared id with no
explicit ranges resolves to its diarized turns, so cuts are filterable by
speaker without anyone typing timestamps.

    analysis/voices.json:
    {"speakers": [
        {"id": "SPEAKER_00", "cleared": true, "by": "reviewer",
         "on": "2026-08-02"},
        {"id": "SPEAKER_01", "cleared": false, "by": null, "on": null,
         "note": "no release on file"},
        {"id": "phone-audio", "cleared": false,
         "ranges": [[1834.0, 2101.5]]}]}   # explicit ranges always win

DEFAULT-DENY: when the diarizer ran (speaker_turns.json status "ok"), EVERY
diarized speaker is blocked until a human marks it cleared -- a speaker with
no voices.json entry at all is blocked on its diarized turns, exactly like a
recorded cleared:false. This is what stopped guest voices from shipping: on
a guest was diarized but nobody had listed them in
voices.json, so the old opt-in blocking let their segments through. diarize
writes the cleared:false defaults into voices.json (sync_voices) and
`reelly clear <project> --speaker ID --cleared --by NAME` flips one.

When diarization is unavailable (dependency/token/model missing), the session
is treated as UNVERIFIED, loudly -- never as single-speaker -- and blocking a
speaker id that nothing can resolve is a hard, actionable error. Projects
that predate diarization (no speaker_turns.json) keep that same degrade path:
default-deny only applies where the diarizer actually produced speakers.

THIRD-PARTY CONTENT (gap 11, heuristic)
A window can be disqualified even when the host is the only speaker, because
the SCREEN is showing someone else's work (a guest's video nearly shipped in
3 cuts). Ownership cues live in the transcript ("did you make", "I don't know
how to video edit"); this pass scans for them and surfaces SUSPECTED
guest-block ranges on the session, written to analysis/guest_blocks.json so
third-party windows are excluded BEFORE planning. It is a heuristic: every
block says "suspected", carries its evidence, and counter-evidence (the host
claiming the work: "when I made that one") is recorded rather than silently
resolving the conflict. A human can delete a wrong block from the artifact;
the evidence line says exactly why it exists.
"""
import json
import os
import re

# how much footage around an ownership cue is treated as the guest's window:
# people talk about what is on screen roughly while it is on screen
PAD_BEFORE_S = 40.0
PAD_AFTER_S = 40.0

# cues that the thing on screen is somebody else's work
THIRD_PARTY_CUES = [
    re.compile(p, re.I) for p in (
        r"\bdid you (make|build|create|edit)\b",
        r"\byou (made|built|created|edited)\b",
        r"\byour (video|game|edit|work|project|film)\b",
        r"\bi didn'?t (make|build|create|edit)\b",
        r"\bi don'?t know how to (video[- ]?)?edit\b",
        r"\bsomeone else'?s\b",
        r"\bnot (mine|my work)\b",
        r"\bwho made (this|that)\b",
    )]

# cues that the host is claiming the work (counter-evidence, recorded so the
# conflict is visible instead of silently resolved either way)
HOST_CUES = [
    re.compile(p, re.I) for p in (
        r"\bwhen i made (this|that)\b",
        r"\bi (made|built|created) (this|that)\b",
        r"\bmy (video|game|edit|project)\b",
    )]


def _hits(sents, patterns):
    out = []
    for x in sents:
        text = x.get("text", "")
        for p in patterns:
            m = p.search(text)
            if m:
                out.append({"t": x["s"], "text": text.strip()[:120],
                            "cue": m.group(0).lower()})
                break
    return out


def guest_blocks(sents):
    """Suspected third-party windows from transcript ownership cues.

    Returns [{s, e, confidence, evidence, counter_evidence}]. Confidence is
    always "suspected": this is a tripwire for a human, not an assertion.
    """
    cues = _hits(sents, THIRD_PARTY_CUES)
    counters = _hits(sents, HOST_CUES)
    spans = []
    for c in cues:
        spans.append([max(0.0, c["t"] - PAD_BEFORE_S), c["t"] + PAD_AFTER_S, [c]])
    spans.sort(key=lambda s: s[0])
    merged = []
    for s, e, ev in spans:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
            merged[-1][2].extend(ev)
        else:
            merged.append([s, e, list(ev)])
    out = []
    for s, e, ev in merged:
        counter = [c for c in counters if s <= c["t"] <= e]
        out.append({"s": round(s, 2), "e": round(e, 2),
                    "confidence": "suspected",
                    "evidence": ev, "counter_evidence": counter})
    return out


def write_guest_blocks(root, sents):
    path = os.path.join(root, "analysis", "guest_blocks.json")
    blocks = guest_blocks(sents)
    json.dump({"note": "SUSPECTED third-party content windows from transcript "
                       "ownership cues; heuristic, human-editable. Blocks are "
                       "excluded from planning until removed.",
               "blocks": blocks}, open(path, "w"), indent=1)
    if blocks:
        print(f"[clear] {len(blocks)} SUSPECTED third-party window(s) -> "
              f"guest_blocks.json (excluded from planning; delete a block "
              f"from the artifact if it is wrong)")
        for b in blocks:
            ev = b["evidence"][0]
            extra = " (counter-evidence on file)" if b["counter_evidence"] else ""
            print(f"        [{b['s']:.0f}s-{b['e']:.0f}s] \"{ev['text']}\"{extra}")
    return path


def _load_json(root, name):
    p = os.path.join(root, "analysis", name)
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p))
    except (ValueError, OSError):
        print(f"[clear] WARNING: unreadable {name}; treating as absent")
        return None


VOICES_NOTE = ("Clearance per diarized speaker id (DEFAULT-DENY: uncleared "
               "and unlisted speakers block planning and QC). Flip one with "
               "`reelly clear <project> --speaker ID --cleared --by NAME`.")


def sync_voices(analysis_dir, turns_artifact):
    """Write/merge analysis/voices.json at diarize time.

    Every diarized speaker id gets an entry, cleared:false by default; any
    decision a human already recorded (cleared/by/on/note/ranges) survives a
    re-diarize untouched. Returns the voices.json path, or None when the
    artifact carries no speakers (unavailable diarization writes nothing:
    the UNVERIFIED degrade path must stay exactly as it was).
    """
    speakers = sorted((turns_artifact or {}).get("speakers") or {})
    if (turns_artifact or {}).get("status") != "ok" or not speakers:
        return None
    path = os.path.join(analysis_dir, "voices.json")
    existing = {}
    if os.path.exists(path):
        try:
            existing = json.load(open(path)) or {}
        except (ValueError, OSError):
            print("[clear] WARNING: unreadable voices.json; rebuilding defaults")
    by_id = {sp.get("id"): sp for sp in existing.get("speakers", [])
             if isinstance(sp, dict) and sp.get("id")}
    for sid in speakers:
        by_id.setdefault(sid, {"id": sid, "cleared": False,
                               "by": None, "on": None})
    ordered = [by_id[k] for k in sorted(by_id)]
    json.dump({"note": VOICES_NOTE, "speakers": ordered},
              open(path, "w"), indent=1)
    uncleared = [sp["id"] for sp in ordered if not sp.get("cleared", False)]
    if uncleared:
        print(f"[clear] voices.json: {len(uncleared)} speaker(s) NOT cleared "
              f"({', '.join(uncleared)}) -- their turns are blocked from "
              f"planning and QC until cleared")
    return path


def mark_cleared(root, speaker, cleared, by=None, note=None):
    """Record a human clearance decision for one diarized speaker id.

    Backs `reelly clear`. Clearing (cleared=True) requires a name: an
    anonymous clearance is not an accountability record. An id the diarizer
    never saw (and that carries no explicit ranges) is a hard error listing
    the known ids, so a typo cannot silently clear nobody.
    """
    import datetime
    if cleared and not (by or "").strip():
        raise SystemExit("clearing a speaker needs --by NAME: the record of "
                         "WHO cleared a voice is the point of the artifact")
    an = os.path.join(root, "analysis")
    turns_art = _load_json(root, "speaker_turns.json") or {}
    sync_voices(an, turns_art)          # make sure defaults exist first
    path = os.path.join(an, "voices.json")
    data = _load_json(root, "voices.json") or {"note": VOICES_NOTE,
                                               "speakers": []}
    entry = next((sp for sp in data.get("speakers", [])
                  if sp.get("id") == speaker), None)
    known = sorted((turns_art.get("speakers") or {}))
    if entry is None:
        if turns_art.get("status") == "ok" and speaker not in known:
            raise SystemExit(
                f"unknown speaker {speaker!r}: the diarizer found "
                f"{', '.join(known) or 'none'}. Fix the id, or add an entry "
                f"with explicit ranges to analysis/voices.json by hand.")
        entry = {"id": speaker}
        data.setdefault("speakers", []).append(entry)
    entry["cleared"] = bool(cleared)
    entry["by"] = (by or "").strip() or None
    entry["on"] = datetime.date.today().isoformat()
    if note:
        entry["note"] = note
    data["speakers"].sort(key=lambda sp: sp.get("id") or "")
    json.dump(data, open(path, "w"), indent=1)
    state = "CLEARED" if cleared else "NOT cleared (blocked)"
    print(f"[clear] {speaker}: {state}"
          + (f" by {entry['by']}" if entry["by"] else "")
          + f" on {entry['on']}")
    return entry


def diarization_status(root):
    """('ok'|'unavailable'|'absent', detail) from speaker_turns.json."""
    art = _load_json(root, "speaker_turns.json")
    if art is None:
        return "absent", "no speaker_turns.json (run `reelly analyze`)"
    if art.get("status") == "ok":
        return "ok", f"{len(art.get('speakers') or {})} diarized speaker(s)"
    return "unavailable", art.get("error", "diarization did not run")


def blocked_ranges(root):
    """All ranges cuts must not use: [(s, e, reason)].

    Sources, in order:
      1. DEFAULT-DENY: when diarization ran ("ok"), every diarized speaker
         with no voices.json entry is blocked on its diarized turns. Nobody
         is allowed by omission.
      2. Uncleared speakers recorded in voices.json (explicit ranges, else
         their diarized turns from speaker_turns.json).
      3. Suspected third-party windows in guest_blocks.json (heuristic).
    All artifacts are optional; a session that predates diarization and has
    no voices/guest artifacts has no blocks (the UNVERIFIED degrade path).
    An uncleared speaker that NOTHING can resolve to ranges is a hard error,
    not a silently empty block.
    """
    out = []
    turns_art = _load_json(root, "speaker_turns.json") or {}
    diarized = {k: (v.get("ranges") or [])
                for k, v in (turns_art.get("speakers") or {}).items()}
    v = _load_json(root, "voices.json") or {}
    listed = {sp.get("id") for sp in v.get("speakers", [])}
    if turns_art.get("status") == "ok":
        # default-deny: a diarized speaker nobody recorded a decision for is
        # blocked exactly like a recorded cleared:false
        for sid in sorted(set(diarized) - listed):
            why = (f"voice not cleared: {sid} (no voices.json entry; "
                   f"speakers are blocked until a human clears them -- "
                   f"`reelly clear <project> --speaker {sid} --cleared "
                   f"--by NAME`)")
            for r in diarized[sid]:
                try:
                    out.append((float(r[0]), float(r[1]), why))
                except (TypeError, ValueError, IndexError):
                    continue
    for sp in v.get("speakers", []):
        if sp.get("cleared", False):
            continue
        sid = sp.get("id", "?")
        why = f"voice not cleared: {sid}"
        if sp.get("note"):
            why += f" ({sp['note']})"
        rngs = sp.get("ranges") or []
        if not rngs:
            if sid in diarized:
                rngs = diarized[sid]
            elif turns_art.get("status") == "ok":
                raise SystemExit(
                    f"voices.json blocks speaker {sid!r} but the diarizer "
                    f"found no such id (known: "
                    f"{', '.join(sorted(diarized)) or 'none'}); fix the id "
                    f"or record explicit ranges")
            else:
                raise SystemExit(
                    f"voices.json blocks speaker {sid!r} with no explicit "
                    f"ranges and diarization is unavailable "
                    f"({turns_art.get('error', 'not run')}). Set up local "
                    f"diarization (README: 'Speaker diarization') or record "
                    f"explicit ranges; refusing to guess what is blocked.")
        for r in rngs:
            try:
                out.append((float(r[0]), float(r[1]), why))
            except (TypeError, ValueError, IndexError):
                continue
    g = _load_json(root, "guest_blocks.json") or {}
    for b in g.get("blocks", []):
        ev = (b.get("evidence") or [{}])[0].get("text", "")
        try:
            out.append((float(b["s"]), float(b["e"]),
                        f"suspected third-party content ({ev[:60]!r})"))
        except (TypeError, ValueError, KeyError):
            continue
    return sorted(out)


def overlapping(segments, blocked):
    """Which blocked ranges a plan's segments touch: [(seg, reason)]."""
    hits = []
    for seg in segments or []:
        s, e = float(seg[0]), float(seg[1])
        for bs, be, why in blocked:
            if s < be and e > bs:
                hits.append(((s, e), why))
    return hits


def verdict(plan, blocked):
    """Judge gate: a cut overlapping an uncleared range must not ship."""
    if not blocked:
        return ("clearance", "SKIP", "no voice/third-party blocks on this session")
    hits = overlapping(plan.get("segments"), blocked)
    if not hits:
        return ("clearance", "PASS",
                f"clear of all {len(blocked)} blocked range(s)")
    detail = "; ".join(f"[{s:.0f}s-{e:.0f}s] hits {why}" for (s, e), why in hits[:3])
    return ("clearance", "FAIL", detail)
