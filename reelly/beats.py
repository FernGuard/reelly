"""Measured, type-aware payoff anchors (screening fix, 2026-08-03).

The two symptoms reviewer flagged at screening -- endcards starting over
the payoff, and overlay "flavor text" arriving before the event it
describes -- share one cause: overlay/endcard timing was scheduled against
PLAN ESTIMATES (the planned payoff beat) instead of the measured footage.

A payoff can land on VOICE, on PICTURE, or on both (direct's refine +
picture-retry paths already distinguish them; `planned_from` and the
`payoff` jump-cut dict are the record). This module measures where the
payoff actually ENDS on the artifacts the analysis stage already produced:

- speech payoffs: the end timestamp of the LAST word of the payoff phrase,
  fuzzy-matched against analysis/words.json (payoff phrases are quotes or
  near-quotes of the transcript; transcription drift is tolerated via
  normalized-token matching over the best contiguous window);
- picture payoffs: the first scene boundary in analysis/scenes.json after
  the planned payoff moment, or -- when no scene cut lands within
  SETTLE_CAP_S -- a motion-energy drop point measured on the raw footage
  (one cheap ffmpeg signalstats pass over the tail window only, cached in
  the project's analysis dir with the activity.py cache pattern);
- both: the later of the two.

Everything degrades gracefully: missing artifacts fall back to the plan's
own numbers WITH a loud print, never a crash. All returned times are
CLIP-LOCAL seconds (the timeline overlays and endcards are scheduled on).
"""
import difflib
import hashlib
import json
import os
import re

SETTLE_CAP_S = 2.5      # a visual settle is never hunted past planned+this
RESETTLE_S = 1.2  # boundaries closer than this are the payoff still cutting, not a settle
SPEECH_SETTLE_S = 0.15  # small settle after the last payoff word
MATCH_MIN = 0.55        # below this the "match" is noise, not the phrase
# how far the payoff tail is presumed to run when a plan carries no payoff
# metadata at all (mirrors overlays.ENDCARD_S: the legacy card window is
# exactly the region the payoff was landing under)
LEGACY_TAIL_S = 3.4


# --- time mapping: source seconds <-> clip-local seconds ---------------------

def _se(seg):
    s, e = seg[0], seg[1]
    return s, e, (seg[2] if len(seg) > 2 else 1.0)


def local_time(t_src, segments):
    """Clip-local time for a source timestamp, or None when it's cut out."""
    off = 0.0
    for seg in segments:
        s, e, speed = _se(seg)
        if s <= t_src <= e:
            return off + (t_src - s) / speed
        off += (e - s) / speed
    return None


def source_time(t_local, segments):
    """Source timestamp for a clip-local time, or None past the clip end."""
    off = 0.0
    for seg in segments:
        s, e, speed = _se(seg)
        d = (e - s) / speed
        if t_local <= off + d + 1e-9:
            return s + (t_local - off) * speed
        off += d
    return None


# --- payoff phrase matching against word-level timestamps --------------------

def _tokens(text):
    return [t for t in re.sub(r"[^a-z0-9']+", " ", (text or "").lower()).split()
            if t]


def payoff_text(plan):
    """The spoken payoff phrase: the last sentence of the cut's transcript.

    Speech-planned cuts end on the landing line (C5); that final sentence is
    the phrase whose last word the endcard must breathe after. Picture-only
    plans carry no trusted transcript (P-TRUST) and return ''.
    """
    if plan.get("planned_from") == "visual" or plan.get("captions") == "none":
        return ""
    tx = (plan.get("transcript") or "").strip()
    if not tx:
        return ""
    parts = [p.strip() for p in re.split(r"[.!?]", tx) if p.strip()]
    return parts[-1] if parts else ""


def match_phrase(text, words, window=None):
    """Best contiguous word-window match for a phrase.

    Returns (t_start_src, t_end_src, score) or None. `words` is the flat
    [{t, s, e}] list (speech.words_from); `window` optionally restricts to
    source seconds [w0, w1]. Matching is normalized-token similarity
    (difflib ratio) over sliding windows of the phrase length +/- 2, so a
    near-quote with transcription drift still lands on the right words.
    """
    ptoks = _tokens(text)
    if not ptoks or not words:
        return None
    if window:
        w0, w1 = window
        cand = [w for w in words if w["e"] >= w0 and w["s"] <= w1]
    else:
        cand = list(words)
    if not cand:
        return None
    wtoks = [_tokens(w["t"]) for w in cand]
    wtoks = [(t[0] if t else "") for t in wtoks]
    best = None
    n = len(ptoks)
    for size in range(max(1, n - 2), min(len(cand), n + 2) + 1):
        for i in range(0, len(cand) - size + 1):
            seg = wtoks[i:i + size]
            score = difflib.SequenceMatcher(None, ptoks, seg).ratio()
            # >= : among equal scores prefer the LATEST window -- a payoff
            # phrase repeated earlier in the cut must anchor to its landing
            if best is None or score >= best[0]:
                best = (score, i, i + size - 1)
    score, i0, i1 = best
    if score < MATCH_MIN:
        return None
    return (float(cand[i0]["s"]), float(cand[i1]["e"]), round(score, 3))


# --- motion-energy settle probe (cached, tail window only) --------------------

def _cache_path(analysis_dir, video, params):
    try:
        st = os.stat(video)
        ident = "|".join([os.path.abspath(video), str(st.st_mtime),
                          str(st.st_size), json.dumps(params, sort_keys=True)])
    except OSError:
        ident = json.dumps(params, sort_keys=True)
    d = os.path.join(analysis_dir, "settle_cache")
    return os.path.join(d, hashlib.sha1(ident.encode()).hexdigest() + ".json")


def settle_point(samples, cap):
    """First time motion energy falls away after its peak, capped at `cap`.

    `samples` is [(t, ydif)] from signalstats over the tail window. The
    settle is the first sample after the energy peak whose motion drops to
    <= 40% of that peak; a window that never calms settles at the cap.
    """
    if not samples:
        return None
    samples = sorted(samples)
    peak_i = max(range(len(samples)), key=lambda i: samples[i][1])
    peak = samples[peak_i][1]
    if peak <= 1e-9:
        return min(samples[0][0], cap)
    for t, y in samples[peak_i + 1:]:
        if y <= 0.4 * peak:
            return min(t, cap)
    return cap


def motion_settle(video, w0, w1, analysis_dir):
    """Motion-energy drop point in SOURCE seconds over [w0, w1], or None.

    One cheap ffmpeg signalstats pass over the tail window only, decoded
    small. Cached in <analysis_dir>/settle_cache keyed on file identity +
    window (the activity.py pattern), so re-planning is free.
    """
    from . import config, media
    params = {"w0": round(w0, 3), "w1": round(w1, 3), "probe": "signalstats.v1"}
    cp = _cache_path(analysis_dir, video, params)
    try:
        return json.load(open(cp))["settle"]
    except (OSError, ValueError, KeyError):
        pass
    r = media.sh(config.FFMPEG, "-v", "info",
                 "-ss", f"{max(0.0, w0):.3f}", "-to", f"{w1:.3f}", "-i", video,
                 "-an", "-vf",
                 "scale=160:-2,signalstats,metadata=print:key=lavfi.signalstats.YDIF",
                 "-f", "null", "-")
    samples, t = [], None
    for line in r.stderr.splitlines():
        if "pts_time:" in line:
            try:
                t = float(line.split("pts_time:")[1].split()[0])
            except (ValueError, IndexError):
                t = None
        elif "lavfi.signalstats.YDIF=" in line and t is not None:
            try:
                samples.append((w0 + t, float(line.split("=")[1])))
            except (ValueError, IndexError):
                pass
            t = None
    settle = settle_point(samples, cap=w1)
    try:
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        json.dump({"settle": settle, "samples": len(samples)}, open(cp, "w"))
    except OSError:
        pass
    return settle


# --- the anchor ---------------------------------------------------------------

def payoff_kind(plan):
    """'speech' | 'picture' | 'both' from the plan's own landing metadata.

    - picture-planned cuts (planned_from:"visual" / captions:"none") land on
      picture by definition: their transcript was rejected (P-TRUST);
    - a speech plan carrying a `payoff` jump-cut dict flags a picture
      landing (C6); when it ALSO has spoken payoff text, the landing is both;
    - a speech plan without a payoff event lands on its last spoken line.
    """
    if plan.get("planned_from") == "visual" or plan.get("captions") == "none":
        return "picture"
    if plan.get("payoff"):
        return "both" if payoff_text(plan) else "picture"
    return "speech"


def _plan_delivery_end(plan, dur):
    """The plan's own (estimated) delivery end, clip-local."""
    de = plan.get("delivery_end_s")
    if de is not None:
        return float(de)
    p = plan.get("payoff") or {}
    if p.get("local_e") is not None:
        return float(p["local_e"])
    return max(0.0, dur - LEGACY_TAIL_S)


def _clip_duration(plan):
    try:
        return float(plan["duration_s"])
    except (KeyError, TypeError, ValueError):
        return sum((e - s) / sp for s, e, sp in map(_se, plan.get("segments") or []))


def payoff_anchor(plan, analysis_dir, clip_bounds=None, video=None):
    """Measured payoff anchor for one cut plan, clip-local seconds.

    Returns {t_speech_end, t_visual_settle, t_anchor, kind, resolved, basis}.
    `clip_bounds` optionally overrides the source window the payoff phrase is
    searched in (defaults to the plan's own segments). `video` is the raw
    source footage, used only for the motion-settle probe when scenes.json
    has no boundary within SETTLE_CAP_S of the planned moment.

    Degrades gracefully: whatever cannot be measured (missing words.json,
    missing scenes.json, no video) falls back to the plan's own numbers with
    a LOUD print -- never a crash. resolved=False means every consumer
    should treat t_anchor as the old plan-estimated timing.
    """
    kind = payoff_kind(plan)
    segments = plan.get("segments") or []
    dur = _clip_duration(plan)
    planned = _plan_delivery_end(plan, dur)
    out = {"kind": kind, "t_speech_end": None, "t_visual_settle": None,
           "t_anchor": None, "resolved": False, "basis": "plan"}

    # -- speech component ------------------------------------------------------
    if kind in ("speech", "both"):
        phrase = payoff_text(plan)
        words = _load_words(analysis_dir)
        win = clip_bounds
        if win is None and segments:
            win = (segments[0][0], segments[-1][1])
        if phrase and words:
            m = match_phrase(phrase, words, window=win)
            if m:
                t_loc = local_time(m[1], segments) if segments else None
                if t_loc is not None:
                    out["t_speech_end"] = round(t_loc, 2)
            if out["t_speech_end"] is None:
                print(f"[beats] {plan.get('id', '?')}: payoff phrase "
                      f"{phrase[:50]!r} not found in words.json window; "
                      f"speech anchor falls back to the plan estimate")
        elif kind in ("speech", "both"):
            print(f"[beats] {plan.get('id', '?')}: no payoff phrase/words.json "
                  f"({'' if words else 'words.json missing'}); speech anchor "
                  f"falls back to the plan estimate")

    # -- picture component -----------------------------------------------------
    if kind in ("picture", "both"):
        out["t_visual_settle"] = _visual_settle(plan, analysis_dir, video,
                                                segments, planned, dur)
        if out["t_visual_settle"] is None:
            print(f"[beats] {plan.get('id', '?')}: no scene boundary or motion "
                  f"probe available after the planned payoff; visual anchor "
                  f"falls back to the plan estimate")

    # -- combine ---------------------------------------------------------------
    sp = (out["t_speech_end"] + SPEECH_SETTLE_S
          if out["t_speech_end"] is not None else None)
    vi = out["t_visual_settle"]
    if kind == "speech":
        anchor = sp
    elif kind == "picture":
        # The plan's own payoff-end estimate FLOORS a measured settle: a
        # probe dip or early scene boundary must never pull the card back
        # onto footage the plan itself says is still payoff.
        pj = plan.get("payoff") or {}
        floor = float(pj["local_e"]) if pj.get("local_e") is not None else None
        anchor = (max(x for x in (vi, floor) if x is not None)
                  if vi is not None else None)
    else:
        # both: when the picture side could not be measured, the plan's own
        # payoff-end estimate FLOORS the anchor -- a measured speech end that
        # precedes the payoff picture must never pull the card back onto it
        floor = None
        pj = plan.get("payoff") or {}
        if vi is None and pj.get("local_e") is not None:
            floor = float(pj["local_e"])
        anchor = max(x for x in (sp, vi, floor) if x is not None) \
            if (sp is not None or vi is not None) else None
    if anchor is not None:
        out["t_anchor"] = round(min(anchor, dur), 2)
        out["resolved"] = True
        out["basis"] = "measured"
    else:
        out["t_anchor"] = round(planned, 2)
        print(f"[beats] {plan.get('id', '?')}: PAYOFF ANCHOR UNRESOLVED "
              f"(kind={kind}); falling back to plan-based timing "
              f"({out['t_anchor']:.2f}s)")
    return out


def _visual_settle(plan, analysis_dir, video, segments, planned, dur):
    """Clip-local visual settle: first scene boundary after the planned
    payoff moment, else the motion-energy drop on the raw footage; capped
    at planned + SETTLE_CAP_S and at the clip end."""
    cap_local = min(planned + SETTLE_CAP_S, dur)
    src_planned = source_time(planned, segments) if segments else None
    if src_planned is None:
        return None
    src_cap = source_time(cap_local, segments)
    if src_cap is None:
        src_cap = segments[-1][1]
    scenes = _load_scenes(analysis_dir)
    hits = [t for t in scenes if src_planned < t <= src_cap]
    if hits:
        # The FIRST boundary after the planned moment is not the payoff being
        # over -- a visual payoff that is still cutting (montage, transition
        # demo) fires boundaries all the way through it (reviewer, 2026-08-03:
        # cards were landing mid-payoff on picture cuts). Settle = the end of
        # the consecutive cutting run: keep advancing while boundaries arrive
        # within RESETTLE_S of the last one.
        settle_src = hits[0]
        for t in hits[1:]:
            if t - settle_src <= RESETTLE_S:
                settle_src = t
            else:
                break
        t_loc = local_time(settle_src, segments)
        if t_loc is not None:
            return round(min(t_loc, cap_local), 2)
    if video and os.path.exists(video) and src_cap - src_planned >= 0.25:
        settle = motion_settle(video, src_planned, src_cap, analysis_dir)
        if settle is not None:
            t_loc = local_time(min(settle, src_cap), segments)
            if t_loc is not None:
                return round(min(t_loc, cap_local), 2)
    return None


# --- reveal resolution (overlay flavor text must follow its event) ------------

def resolve_reveal(plan, text, t_planned, analysis_dir):
    """Clip-local time the moment a reveal line references, or None.

    A reveal names a specific spoken or on-screen moment, so it must not
    appear before that moment. Spoken: fuzzy-match the line against
    words.json inside the cut's window and return the matched phrase START.
    Visual: when the plan carries a payoff beat and the line was planned
    near it, the payoff's start is the referenced moment. Unresolvable
    lines return None (QC degrades to WARN, never blocks on absent data).
    """
    segments = plan.get("segments") or []
    if not (plan.get("planned_from") == "visual" or plan.get("captions") == "none"):
        words = _load_words(analysis_dir)
        if words and segments:
            m = match_phrase(text, words,
                             window=(segments[0][0], segments[-1][1]))
            if m:
                t_loc = local_time(m[0], segments)
                if t_loc is not None:
                    return round(t_loc, 2)
    p = plan.get("payoff") or {}
    if p.get("local_t") is not None:
        lt = float(p["local_t"])
        if lt - 6.0 <= float(t_planned) <= float(p.get("local_e", lt)) + 2.0:
            return round(lt, 2)
    return None


# --- artifact loading (missing files are a fallback, never a crash) -----------

def _load_words(analysis_dir):
    from . import speech
    p = os.path.join(analysis_dir or "", "words.json")
    if not os.path.exists(p):
        return []
    try:
        return speech.words_from(p)
    except (ValueError, OSError, KeyError):
        return []


def _load_scenes(analysis_dir):
    p = os.path.join(analysis_dir or "", "scenes.json")
    if not os.path.exists(p):
        return []
    try:
        return [c["t"] for c in json.load(open(p))]
    except (ValueError, OSError, KeyError, TypeError):
        return []
