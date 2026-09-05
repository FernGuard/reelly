"""DIRECT: the editor brain. Turns an analysis bundle into cut plans as data.

Candidates come from deterministic signal fusion (visual scores, topic
segments, audio energy). Line-level refinement is an AI pass with hard
guardrails in code: cuts snap into silences, boundaries land on sentence
edges, and a cut without a clean landing is dropped (playbook C4, learned
from the fragmentary-clip failure). Every plan carries `because:` fields
citing the playbook rules and signals that produced it.
"""
import json
import re
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from . import audiotail, config, ledger, media, performance, speech, timing
from . import topics as topics_mod

# refine workers run concurrently; the ledger is a read-modify-write JSON file
_LEDGER_LOCK = threading.Lock()

# rule ids cited in because: fields; full text lives in playbook/EDITING-PLAYBOOK.md
RULES = {
    "H2": "text hook on screen by frame 1, max 8 words",
    "H3": "open on a stressed word or reveal, never a fragment",
    "C1": "cut on pauses, snapped into silence",
    "C11": "retakes: keep the last take, drop earlier attempts",
    "C4": "complete thought: setup, moment, landing",
    "C6": "show the payoff: hard jump cut to the result, marked, never a second action",
    "C5": "end on a payoff or question",
    "CA1": "short readable cues, sentence-shaped",
    "P1": "TikTok 9:16, hook hardest",
    "CO1": "split layout: cam top ~40%, content bottom (researched winner format)",
    "CO2": "captions large at the cam/content seam (researched)",
    "CO3": "nothing critical in platform UI zones",
    "CO4": "cam aligned by waveform offset, lip sync is the proof",
    "CO5": "face detected and centered in its region",
    "CO6": "corner circle only as labeled fallback for full-height screen moments",
}

RULES.update({
    "P-LEN": "20-28s: the public default short-form window",
    "P-HANDLES": "2+ engagement handles, or reach does not convert",
    "P-TEXT": "the best writing belongs on screen, not only in the caption",
    "P-TRUST": "never caption a transcript the ASR check rejected",
    "P-PLAIN": ("every word a viewer reads must make sense with no context: "
                "no internal vocabulary, no retired product names"),
    "P-CUT": ("never hold one picture past ~3.5s: visual monotony is the "
              "cheapest way to lose a viewer"),
    "P-LOOP": ("end near where it started: rewatch is one of the strongest "
               "short-form signals and a loop earns it without asking"),
    "P-BAIT": ("ask for one thing: a comment, a share, a save or a rewatch. "
               "Saves lead on Instagram, shares and rewatch on TikTok"),
    "P-REWARD": "pay off the hook within 5-15s, not at the end",
    "P-SCREEN": ("a cut is not deliverable until a human has watched it end to "
                 "end and recorded a verdict: guns/blood, retired branding, "
                 "real trademarks, children in peril, burned-in text"),
})

PLAYBOOK_VERSION = "0.17.0"

VISUAL_REFINE_PROMPT = """You are a short-form video editor cutting one vertical clip (TikTok/Reels/Shorts) from gameplay footage. This footage has NO usable narration: either it is music and ambience, or the automatic transcript failed its reliability check. You are cutting on PICTURE ALONE. There will be no subtitles, so every word you write will be burned on screen as text.

CANDIDATE MOMENT: {why}
VISUAL SEQUENCES available near this moment (time | scores | what happens):
{visuals}
SCENE CUTS (safe edit points, seconds): {scenes}
AUDIO ENERGY PEAKS (impacts, stings, reveals): {peaks}
SOURCE RUNS: 0 to {duration:.1f}s

PUBLIC DEFAULTS (tune these with your own private evidence):
- Target 20 to 28 seconds for this template.
- Every clip MUST carry at least 2 of these visible engagement handles:
  - persistent_character: a character on screen long enough to attach to
  - readable_detail: on-screen text or a micro-detail worth pausing on
  - narrative_turn: something changes; there is a before and an after
  - creator_credit: real creator work credited by handle
  Only claim a handle you can actually SEE in the sequences described above. Do not invent one.
- The best writing must be ON SCREEN, not just in the post caption.

FORMAT LIBRARY (build to the closest shape):
- F1 pov_role: POV: you're the [role] when [event]
- F2 what_if_twist: What if [familiar thing] but [twist]
- F4 satisfying_process: oddly satisfying start-to-finish process
- F5 one_vs_escalation: small stake escalating to huge stake
- F6 late_reveal: normal scene, the last 2 seconds flip it
- F7 day_in_life: day in the life of [unexpected subject]

Pick ONE contiguous time range in SECONDS that forms one complete beat: something moving at the very first frame, a middle that escalates the SAME image, and a landing (a reveal, a result, a title card, a turn). Hard rules:
- SINGLE SCENE ONLY: return exactly ONE unbroken range [[start_s, end_s]]. Never split across gaps or stitch multiple ranges -- a cut is one continuous take. If no single continuous stretch lands, set "landing_ok": false.
- Total duration 20 to 28 seconds. This is not negotiable.
- Open on motion. Never open on a static establishing shot or a menu.
- The last second must land. If this moment has no landing on screen, set "landing_ok": false.
- ENDING: the clip must END on the payoff fully COMPLETING plus a natural breath. The promised result must be fully on screen and settled before the cut ends -- never end mid-action, mid-generation, or while the result is still playing out. A branded outro is appended AFTER your content automatically; do not reserve room for a card. Report "payoff_complete_by": the second (from the clip's start) by which the payoff has fully completed. It must be at or before the clip's end.
- The single range must be inside 0 to {duration:.1f}s.
- Would a stranger scrolling stop for this? Set "stranger_appeal" 1-10, judging the footage, not the edit.

Write the on-screen text yourself:
- "hook": max 8 words, on screen from frame 1. No emojis, no em dashes.
- "overlay_lines": 1 to 3 further lines timed in seconds FROM THE START OF THE CLIP, each max 10 words, spaced at least 4s apart, none in the first 4s (the hook owns that), none in the last 1.5s. Use them to carry the mood or the turn. Leave empty only if the picture genuinely says it all.
- Every overlay line carries a "role": "tease" DELIBERATELY precedes its event (a hook, a question, building dread); "reveal" NAMES or DESCRIBES a specific on-screen moment (a result, an action, a turn that is visible when the line is up). A reveal must never appear before the moment it describes: the render will hold it back to that moment.
- "caption": the post caption. Make it a self-insertion prompt ("you get one door, which one") or a 3-fragment lore tease. Never a bare credit line.
- "cta": the ask, max 6 words, burned on screen at the end. It must tell a viewer what to DO, not what was made. "watch the full version", "try it yourself", "join the community". Imperative, no hype, no exclamation marks.
- Pay the hook off within 5 to 15 seconds. A viewer who feels baited leaves and does not engage, and the payoff arriving at the end is the same as no payoff.
- Never hold one picture past about 3.5 seconds. Each visual change reads as new content; a held frame reads as a stall.
- Prefer an ending that returns near the opening image. Rewatch is one of the strongest short-form signals and a loop earns it without asking for it.
- The "cta" must ask for exactly one thing, and it should be the thing that platform rewards: a comment or a save on Instagram, a share or a rewatch on TikTok. A cut that asks for nothing gets nothing.
- Write for someone with no project context. Every word must make sense on its own. Name what happens, not an internal label. Avoid internal workflow terms, metrics, and phase labels.

Return ONLY JSON:
{{"landing_ok": true/false,
 "segments": [[start_s, end_s]],
 "title": "3-6 word working title",
 "hook": "on-screen hook text, max 8 words",
 "overlay_lines": [{{"t": 6.0, "text": "line on screen", "role": "tease|reveal"}}],
 "caption": "post caption",
 "cta": "the ask, max 6 words",
 "handles": ["persistent_character", "readable_detail"],
 "format": "F1|F2|F4|F5|F6|F7",
 "payoff_complete_by": 24.5,
 "stranger_appeal": 1-10,
 "why": "one line on why this works as a clip"}}"""

REFINE_PROMPT = """You are a short-form video editor picking the exact lines for one vertical clip (TikTok/Reels/Shorts) from a screen recording of someone building with a creative AI tool.

CANDIDATE MOMENT: {why}
VISUAL CONTEXT in this window: {visuals}
AUDIENCE-ENERGY PEAKS (speaker got loud/excited): {peaks}

FORMAT LIBRARY (proven short-form shapes; build the clip to the closest one and engineer the hook to that shape):
- F1 pov_role: POV: you're the [role] when [event]
- F2 what_if_twist: What if [familiar thing] but [twist]
- F4 satisfying_process: oddly satisfying start-to-finish process or generation reveal
- F5 one_vs_escalation: small stake escalating to huge stake
- F6 late_reveal: normal scene, the last 2 seconds flip it
- F7 day_in_life: day in the life of [unexpected subject]
- F8 build_reveal: screen build with a human reaction payoff

UPCOMING VISUAL EVENTS (payoff candidates; index | time | what happens):
{payoffs}
SINGLE SCENE: your chosen range should already CONTAIN this clip's payoff. A payoff event is only honoured when it is IMMEDIATELY CONTIGUOUS with the end of your range; a distant event will NOT be jump-cut in (no cross-gap stitching). If the payoff only exists as a distant event, extend your single range to reach it or set "landing_ok": false. Rules:
- Pick the event showing the COMPLETION of this clip's action, and only if it directly abuts your range. Never the start of the NEXT stage (finished storyboards, not the video-generation screen that follows).
- Never use it to pack a second, unrelated action into the clip. Otherwise set -1.
- If the chosen lines flag a problem or complaint, the clip MUST also include the fix or its result (via later sentence ranges or the payoff event). A complaint without its resolution is not a landing: set "landing_ok": false.
- Judge the raw content: would a stranger scrolling stop for this moment? Set "stranger_appeal" 1-10. Routine setup screens and low-stakes narration score low.

Numbered transcript sentences in this window (index | time | text):
{sents}

Pick the sentence indices that form ONE complete little story: setup (what they set out to do), the moment, and a landing (result, reveal, or punchline). Hard rules:
- First sentence must be a clean self-contained opener, never a fragment or the tail of an earlier thought.
- Last sentence must land: a payoff, reveal, reaction, or question. Trailing filler is forbidden.
- ENDING: the clip must END on the payoff fully COMPLETING plus a natural breath -- the landing line fully spoken, the shown result fully on screen and settled. Never end mid-sentence, mid-action, or while the result is still generating or playing out. A branded outro is appended AFTER your content automatically; do not reserve room for a card. Report "payoff_complete_by": the second (from the clip's start) by which the payoff has fully completed, at or before the clip's end.
- SINGLE SCENE ONLY: return exactly ONE contiguous range of sentence indices [[first_idx, last_idx]]. Do NOT return multiple ranges and do NOT skip sentences in the middle -- a cut is one unbroken take, never stitched across gaps. Splicing narration from separate moments makes the audio a non-sequitur. If the only way to make this work is to jump across boring middle sentences, this window is not a clean single-scene cut: set "landing_ok": false.
- Total 20 to 28 seconds for the public default template. Fewer, cleaner lines beat more.
- If this window has no clean opener or no landing, set "landing_ok": false and stop.
- The hook text lands on the FIRST frame of the clip and the viewer is reading it before anything else happens. Write it so it works cold over the opening line.
- Every overlay line carries a "role": "tease" DELIBERATELY precedes its event (a hook, a question, building anticipation); "reveal" NAMES or DESCRIBES a specific spoken or on-screen moment. A reveal must never appear before the moment it describes: the render will hold it back to that moment.
- When more than one landing would work, prefer the one whose final beat echoes the opening line or image (a loop): rewatch is one of the strongest short-form signals and a loop earns it without asking.

PUBLIC DEFAULT: every clip must carry at least 2 visible engagement handles. Only claim one you can actually see:
- persistent_character: a character on screen long enough to attach to
- readable_detail: on-screen text or a micro-detail worth pausing on
- narrative_turn: something changes; there is a before and an after
- creator_credit: real creator work credited by handle

Return ONLY JSON:
{{"landing_ok": true/false,
 "ranges": [[first_idx, last_idx]],
 "title": "3-6 word working title",
 "hook": "on-screen hook text, on screen from frame 1, max 8 words, no emojis, no em dashes",
 "overlay_lines": [{{"t": 8.0, "text": "extra line burned on screen, max 10 words", "role": "tease|reveal"}}],
 "caption": "post caption: a self-insertion prompt or a 3-fragment lore tease, never a bare credit",
 "cta": "the ask, max 6 words, burned on screen at the end: tell the viewer what to DO",
 "handles": ["persistent_character", "readable_detail"],
 "format": "F1|F2|F4|F5|F6|F7|F8",
 "payoff_event": -1,
 "payoff_why": "why that event completes this clip's topic (or empty)",
 "payoff_complete_by": 22.0,
 "stranger_appeal": 1-10,
 "why": "one line on why this works as a clip"}}"""

# text-refine economics: ~2k prompt + ~600 output tokens per call.
# 2026-08-02: current gpt-5.6-sol per-token pricing could NOT be verified from
# here (model postdates every price sheet on this machine), so the estimate is
# a conservative 2x of the previous value rather than a computed one.
# FLAG: replace both with (tokens x published rate) once Sol/Gemini pricing is
# confirmed; overestimating only trips the budget cap earlier, never later.
EST_REFINE_COST = 0.004
EST_REFINE_COST_GPT = 0.04
GPT_MODEL = "gpt-5.6-sol"


def _load(an, f):
    p = os.path.join(an, f)
    return json.load(open(p)) if os.path.exists(p) else None


def resolve_project(project):
    if os.path.isdir(project):
        return os.path.abspath(project)
    p = os.path.join(config.DEFAULT_PROJECTS, project)
    if os.path.isdir(p):
        return p
    raise SystemExit(f"project not found: {project}")


def _source_video(root):
    src = os.path.join(root, "source")
    for f in sorted(os.listdir(src)):
        if f.startswith("screen"):
            return os.path.realpath(os.path.join(src, f))
    raise SystemExit(f"no screen source in {src}")


def _candidates(vr, tp, peaks):
    """Fuse signals into ranked candidate windows."""
    cands = []
    for s in vr:
        if s.get("short_score", 0) >= 7:
            cands.append({"s": s["start_s"], "e": s["end_s"],
                          "score": s["short_score"] * 10,
                          "why": f"visual T{s.get('trailer_score')}/S{s.get('short_score')} "
                                 f"{s.get('label', '')}: {s.get('what_happens', '')}",
                          "signals": [f"visual S{s.get('short_score')} @{s.get('start_abs')}"]})
    for c in tp:
        d = c["e"] - c["s"]
        if 12 <= d <= 75:
            cands.append({"s": c["s"], "e": c["e"], "score": 40,
                          "why": f"topic segment: {c['text'][:120]}",
                          "signals": [f"topic {media.fmt(c['s'])}-{media.fmt(c['e'])}"]})
    for c in cands:  # a loud reaction inside the window is a strong buy signal
        for p in peaks:
            if c["s"] - 5 <= p["t"] <= c["e"] + 5:
                c["score"] += 15
                c["signals"].append(f"energy {p['above_median']:+.1f}dB @{media.fmt(p['t'])}")
                break
    # dedupe overlapping windows, keep the stronger
    cands.sort(key=lambda c: -c["score"])
    kept = []
    for c in cands:
        if all(c["e"] <= k["s"] + 3 or c["s"] >= k["e"] - 3 for k in kept):
            kept.append(c)
    return kept


def _window_sents(sents, s, e, pre=15, post=20):
    return [(i, x) for i, x in enumerate(sents) if x["e"] >= s - pre and x["s"] <= e + post]


def _refine_ai(cand, wsents, vr, peaks, project):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=config.provider_key("google-genai"))
    lines = "\n".join(f"{i} | {media.fmt(x['s'])}-{media.fmt(x['e'])} | {x['text']}"
                      for i, x in wsents)
    visuals = "; ".join(f"[{v.get('start_abs')}-{v.get('end_abs')}] {v.get('what_happens', '')}"
                        for v in vr if cand["s"] - 20 <= v.get("start_s", 0) <= cand["e"] + 20) or "none noted"
    pk = ", ".join(f"{media.fmt(p['t'])} ({p['above_median']:+.1f}dB)"
                   for p in peaks if cand["s"] - 10 <= p["t"] <= cand["e"] + 10) or "none"
    with _LEDGER_LOCK:
        ledger.check(EST_REFINE_COST)
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=REFINE_PROMPT.format(why=cand["why"], visuals=visuals, peaks=pk,
                                      sents=lines, payoffs=_payoff_lines(cand, vr)),
        config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.3))
    with _LEDGER_LOCK:
        ledger.add("gemini-direct", f"refine {cand['why'][:40]}", EST_REFINE_COST, project)
    try:
        return json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        return None


def _refine_gpt(cand, wsents, vr, peaks, project):
    """Same refine contract, GPT-5.6 brain (EXP-001)."""
    import requests
    lines = "\n".join(f"{i} | {media.fmt(x['s'])}-{media.fmt(x['e'])} | {x['text']}"
                      for i, x in wsents)
    visuals = "; ".join(f"[{v.get('start_abs')}-{v.get('end_abs')}] {v.get('what_happens', '')}"
                        for v in vr if cand["s"] - 20 <= v.get("start_s", 0) <= cand["e"] + 20) or "none noted"
    pk = ", ".join(f"{media.fmt(p['t'])} ({p['above_median']:+.1f}dB)"
                   for p in peaks if cand["s"] - 10 <= p["t"] <= cand["e"] + 10) or "none"
    with _LEDGER_LOCK:
        ledger.check(EST_REFINE_COST_GPT)
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {config.provider_key('openai')}"},
        json={"model": GPT_MODEL,
              "messages": [{"role": "user", "content": REFINE_PROMPT.format(
                  why=cand["why"], visuals=visuals, peaks=pk, sents=lines,
                  payoffs=_payoff_lines(cand, vr))}],
              "response_format": {"type": "json_object"}},
        timeout=180)
    with _LEDGER_LOCK:
        ledger.add("gpt-direct", f"refine {cand['why'][:40]}", EST_REFINE_COST_GPT, project)
    try:
        return json.loads(r.json()["choices"][0]["message"]["content"])
    except (KeyError, json.JSONDecodeError, TypeError):
        return None


def _refine_heuristic(cand, wsents):
    """No-AI fallback: whole sentences inside the candidate window."""
    inside = [(i, x) for i, x in wsents if x["s"] >= cand["s"] - 2 and x["e"] <= cand["e"] + 2]
    if not inside:
        return None
    first = inside[0][1]["text"].split()
    return {"landing_ok": True, "ranges": [[inside[0][0], inside[-1][0]]],
            "title": " ".join(first[:5]), "hook": " ".join(first[:8]),
            "why": "heuristic: whole sentences in candidate window"}


def _ask_json(prompt, brain, project, label):
    """One JSON completion from whichever brain is selected."""
    if brain == "gpt":
        import requests
        with _LEDGER_LOCK:
            ledger.check(EST_REFINE_COST_GPT)
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {config.provider_key('openai')}"},
            json={"model": GPT_MODEL,
                  "messages": [{"role": "user", "content": prompt}],
                  "response_format": {"type": "json_object"}},
            timeout=180)
        with _LEDGER_LOCK:
            ledger.add("gpt-direct", label, EST_REFINE_COST_GPT, project)
        try:
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except (KeyError, json.JSONDecodeError, TypeError):
            return None
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=config.provider_key("google-genai"))
    with _LEDGER_LOCK:
        ledger.check(EST_REFINE_COST)
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           temperature=0.3))
    with _LEDGER_LOCK:
        ledger.add("gemini-direct", label, EST_REFINE_COST, project)
    try:
        return json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        return None


def _refine_visual(cand, vr, peaks, scenes, duration, brain, project):
    """Plan a cut from picture alone: no transcript, no captions."""
    near = [v for v in vr if cand["s"] - 60 <= v.get("start_s", 0) <= cand["e"] + 180]
    visuals = "\n".join(
        f"[{v.get('start_s', 0):.1f}-{v.get('end_s', 0):.1f}] "
        f"T{v.get('trailer_score')}/S{v.get('short_score')} "
        f"{v.get('label', '')}: {v.get('what_happens', '')}" for v in near) or "none noted"
    sc = ", ".join(f"{t:.1f}" for t in scenes
                   if cand["s"] - 30 <= t <= cand["e"] + 60)[:600] or "none"
    pk = ", ".join(f"{p['t']:.1f}s ({p['above_median']:+.1f}dB)" for p in peaks
                   if cand["s"] - 15 <= p["t"] <= cand["e"] + 60) or "none"
    return _ask_json(VISUAL_REFINE_PROMPT.format(
        why=cand["why"], visuals=visuals, scenes=sc, peaks=pk, duration=duration),
        brain, project, f"visual refine {cand['why'][:40]}")


def _snap_scene(t, scenes, window=1.5):
    """Land an edit on a real shot boundary when one is close (C1 for picture)."""
    near = [s for s in scenes if abs(s - t) <= window]
    return min(near, key=lambda s: abs(s - t)) if near else t


def _enforce_single_segment(merged):
    """Single-scene rule (verdict 2026-08-15, reviewer): a cut is ONE unbroken
    take. Adjacent ranges within the merge tolerance are already one segment by
    here; >1 entry means a real gap. Stitching footage/narration from separate
    moments made spliced cuts a non-sequitur, so keep only the longest
    contiguous segment and drop the rest."""
    if len(merged) <= 1:
        return merged
    return [max(merged, key=lambda seg: seg[1] - seg[0])]


def _visual_plan_from(cand, ref, idx, scenes, duration, reframe,
                      anchor_ctx=None):
    """Build a captions-free plan from picture-only refinement."""
    segs = []
    for r in ref.get("segments", []):
        try:
            s, e = float(r[0]), float(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        s, e = max(0.0, min(s, duration)), max(0.0, min(e, duration))
        if e - s < 1.5:
            continue
        segs.append([round(_snap_scene(s, scenes), 2), round(_snap_scene(e, scenes), 2)])
    if not segs:
        return None
    segs.sort()
    merged = [segs[0]]
    for s, e in segs[1:]:
        if s <= merged[-1][1] + 0.4:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    merged = _enforce_single_segment(merged)
    total = sum(e - s for s, e in merged)
    verdict, msg = performance.length_verdict(total)
    if verdict == "drop":
        return {"_rejected": msg}
    handles = performance.clean_handles(ref.get("handles"))
    hv, hmsg = performance.handles_verdict(handles)
    if hv == "drop":
        return {"_rejected": hmsg}
    hook = str(ref.get("hook", "")).replace("—", ",").replace("–", ",")[:60]
    # Measured payoff anchor: picture plans land on picture by definition, so
    # the anchor is the visual settle after the planned landing (scene
    # boundary, else motion-energy drop), measured on the raw footage.
    anchor = _measured_anchor({"id": f"cut_{idx:02d}", "segments": merged,
                               "duration_s": total, "payoff": None,
                               "planned_from": "visual", "captions": "none"},
                              anchor_ctx)
    anchor_t = float(anchor["t_anchor"]) if (anchor or {}).get("resolved") else None
    pcb = _payoff_complete_by(ref, total, anchor_t)
    # Tail hygiene: a content end sitting on a rising sound onset ships a
    # clipped blast at the content->outro boundary (cut_04 screening bug).
    merged, total, tail_note = audiotail.plan_tail_trim(
        (anchor_ctx or {}).get("video"), merged, total, pcb, f"cut_{idx:02d}")
    overlays = _resolve_reveals(
        _overlay_lines(ref, total),
        {"segments": merged, "payoff": None, "planned_from": "visual",
         "captions": "none"},
        anchor_ctx, total)
    because = ["H1 open on motion", "H2 " + RULES["H2"], "C4 " + RULES["C4"],
               "C5 " + RULES["C5"], "P-TRUST " + RULES["P-TRUST"],
               f"P-LEN {msg}", f"P-HANDLES {hmsg}"] + cand["signals"]
    if tail_note:
        because.append(tail_note)
    if overlays:
        because.append(f"P-TEXT {len(overlays)} timed line(s) on screen beyond the hook")
    because.append(f"ai(visual): {ref.get('why', '')}")
    plan = {
        "id": f"cut_{idx:02d}", "platform": "tiktok",
        "format": str(ref.get("format", "F6"))[:3],
        "title": str(ref.get("title", "untitled"))[:60],
        "segments": merged, "duration_s": round(total, 1),
        "source_range": [merged[0][0], merged[-1][1]],
        "hook": {"type": "text", "text": hook,
                 "show_s": round(min(6.0, max(3.6, len(hook.split()) * 0.45 + 1.6)), 1)},
        "overlay_lines": overlays,
        "caption": str(ref.get("caption", ""))[:300],
        "cta": str(ref.get("cta", ""))[:60],
        "handles": handles,
        "captions": "none",          # P-TRUST: nothing transcribed reaches the screen
        "reframe": reframe,
        "payoff": None, "payoff_anchor": anchor,
        "payoff_complete_by": pcb,
        "transcript": "",
        "planned_from": "visual",
        "because": because, "playbook_version": PLAYBOOK_VERSION,
    }
    return _with_outro(plan, ref, total)


def _payoff_events(cand, vr, horizon=240.0, top=6):
    """Upcoming visual events a clip could jump-cut to as its payoff."""
    evs = [v for v in sorted(vr, key=lambda v: v.get("start_s", 0))
           if cand["s"] <= v.get("start_s", 0) <= cand["e"] + horizon
           and max(v.get("trailer_score", 0), v.get("short_score", 0)) >= 6]
    return evs[:top]


def _payoff_lines(cand, vr):
    evs = _payoff_events(cand, vr)
    if not evs:
        return "none"
    return "\n".join(f"{i} | {v.get('start_abs')}-{v.get('end_abs')} | "
                     f"{v.get('label', '')}: {v.get('what_happens', '')}"
                     for i, v in enumerate(evs))


def _apply_payoff(merged, cand, ref, vr, hold=10.0):
    """C6, brain-chosen: hard jump cut to the reveal, marked when the gap is
    real. Returns (payoff_meta or None). Mutates merged."""
    try:
        pi = int(ref.get("payoff_event", -1))
    except (ValueError, TypeError):
        pi = -1
    evs = _payoff_events(cand, vr)
    if not 0 <= pi < len(evs):
        return None
    v = evs[pi]
    E = merged[-1][1]
    rs = max(E, v["start_s"])
    re_ = round(min(v["end_s"], v["start_s"] + hold), 2)
    if re_ - rs < 1.0:
        return None
    local_t = round(sum(seg[1] - seg[0] for seg in merged), 2)
    # local_t/local_e are ALWAYS recorded (gap 2026-07-31: local_t was None on
    # every cut of a session, so the endcard could never locate the payoff and
    # sat on top of it). local_e is where the payoff finishes in clip time:
    # the point the closing card must breathe after.
    if rs - E <= 0.5:  # contiguous, just extend the last segment
        merged[-1][1] = re_
        return {"event": v.get("label", ""), "jump": False,
                "local_t": local_t, "local_e": round(local_t + (re_ - E), 2),
                "why": ref.get("payoff_why", "")}
    # Single-scene rule (2026-08-15): a DISTANT payoff is never jump-cut in --
    # cross-gap stitching is the failure this blocks. Only a payoff
    # contiguous with the take (handled above) is honoured; a distant one is
    # dropped and the clip lands on its own footage.
    return None


def _breathe_tail(merged, total, delivery_end, room, hard_hi=None):
    """Designed endings (2026-08-03): the CONTENT ends on the payoff fully
    landing plus a natural breath -- nothing more. The closing card is no
    longer carved out of the content (the outro is APPENDED after it, see
    outro.py), so this reserves NO card room: it only grows the tail so the
    delivery is not clipped at the cut, and only into the trailing pause
    (`room`), capped by the hard length gate. Returns
    (merged, total, note_or_None); mutates merged."""
    from . import outro as outro_mod
    hard_hi = hard_hi if hard_hi is not None else performance.HARD_LEN[1]
    need = delivery_end + outro_mod.TAIL_BREATH_S
    if total >= need - 1e-6:
        return merged, total, None
    ext = min(need - total, max(0.0, room), hard_hi - total)
    if ext <= 0.05:
        return merged, total, ("ENDING-breathe: no trailing pause (or hard "
                               "length cap) after the delivery; the content "
                               "ends as tightly as the footage allows -- the "
                               "outro is appended after it either way")
    merged[-1][1] = round(merged[-1][1] + ext, 2)
    total = round(total + ext, 2)
    note = (f"ENDING-breathe: tail extended {ext:.1f}s so the content ends "
            f"{outro_mod.TAIL_BREATH_S:.2f}s after the payoff lands (the "
            f"breath), never mid-delivery; the outro is appended after it")
    return merged, total, note


def _payoff_complete_by(ref, total, delivery_end=None):
    """The planner's own claim of when the payoff fully completes, clamped
    to the content window. Falls back to the delivery end (or the content
    end) when the brain omitted or garbled it."""
    try:
        pcb = float(ref.get("payoff_complete_by"))
    except (TypeError, ValueError):
        pcb = None
    if pcb is None or not (0.0 < pcb <= total + 1.0):
        pcb = delivery_end if delivery_end is not None else total
    return round(min(float(pcb), total), 2)


def _with_outro(plan, ref, total):
    """Stamp the designed-ending fields onto a finished plan dict: the
    content length, the appended outro block (when the plan carries an ask
    and the architecture is on) and the full-deliverable duration_s the QC
    duration gate measures."""
    from . import outro as outro_mod
    plan["content_s"] = round(total, 2)
    ob = None
    if (str(ref.get("cta") or "")).strip() and outro_mod.enabled():
        ob = outro_mod.plan_block()
        plan["because"].append(
            f"ENDING outro appended after content ({ob['len_s']:.1f}s, "
            f"{ob['style']}): the card never overlays the payoff")
    plan["outro"] = ob
    plan["duration_s"] = round(total + (float(ob["len_s"]) if ob else 0.0), 1)
    return plan


def _trailing_pause_room(last_e, sil):
    """How much silence follows the cut's end (the only legal tail room)."""
    for ss, se in sil:
        if ss - 0.25 <= last_e <= se:
            return max(0.0, se - last_e)
    return 0.0


def _overlay_role(o):
    """'tease' | 'reveal' from the brain's classification; anything else --
    including legacy plans from before roles existed -- reads as 'tease'
    (frame-one/tease behaviour is the safe default; only a certain reveal
    gets held back to its moment)."""
    r = str(o.get("role", "")).strip().lower()
    return r if r in ("tease", "reveal") else "tease"


def _overlay_lines(ref, total):
    """P-TEXT: timed on-screen lines beyond the opening hook."""
    out = []
    for o in (ref.get("overlay_lines") or [])[:3]:
        try:
            t = float(o["t"])
        except (TypeError, ValueError, KeyError):
            continue
        text = str(o.get("text", "")).replace("—", ",").replace("–", ",").strip()[:60]
        if text and 0 <= t <= total - 1.0:
            out.append({"t": round(t, 2), "text": text, "show_s": 3.0,
                        "role": _overlay_role(o)})
    return out


def _resolve_reveals(overlays, plan_like, anchor_ctx, total):
    """Reveal lines must not precede the moment they describe.

    For each role:"reveal" line, resolve the referenced moment (fuzzy speech
    match / the plan's payoff beat, via beats.resolve_reveal), record it as
    o["anchor"] for QC inspection, and push the line's start to it when the
    plan had it early. Teases are exempt by definition. Unresolvable reveals
    keep their planned time and a None anchor (QC degrades to WARN). A
    reveal whose moment lands in the final second is dropped loudly: there
    is no legal time left to show it. Mutates and returns `overlays`.
    """
    if not anchor_ctx:
        return overlays
    from . import beats
    kept = []
    for o in overlays:
        if o.get("role") != "reveal":
            kept.append(o)
            continue
        try:
            a = beats.resolve_reveal(plan_like, o["text"], o["t"],
                                     anchor_ctx.get("analysis_dir"))
        except Exception as e:  # noqa: BLE001 -- resolution must never kill a plan
            print(f"[beats] reveal resolution failed ({e}); keeping planned time")
            a = None
        o["anchor"] = a
        if a is not None:
            if a > total - 1.0:
                print(f"[direct] dropped reveal line {o['text']!r}: its moment "
                      f"({a:.2f}s) leaves no screen time before the clip ends")
                continue
            if o["t"] < a:
                print(f"[direct] reveal line {o['text']!r} moved "
                      f"{o['t']:.2f}s -> {a:.2f}s (must not precede its moment)")
                o["t"] = round(a, 2)
        kept.append(o)
    return kept


def _measured_anchor(plan_like, anchor_ctx):
    """beats.payoff_anchor with plan-time context, or None without it.
    Never raises: a broken probe degrades to plan-based timing loudly."""
    if not anchor_ctx:
        return None
    from . import beats
    try:
        return beats.payoff_anchor(plan_like, anchor_ctx.get("analysis_dir"),
                                   video=anchor_ctx.get("video"))
    except Exception as e:  # noqa: BLE001
        print(f"[beats] payoff anchor probe failed ({e}); "
              f"falling back to plan-based timing")
        return None


def _plan_from(cand, ref, sents, sil, idx, vr=(), anchor_ctx=None):
    """Guardrails: sentence bounds -> silence snap -> duration gate -> payoff."""
    segs = []
    for r in ref.get("ranges", []):
        try:
            i0, i1 = int(r[0]), int(r[1])
        except (ValueError, TypeError, IndexError):
            continue
        if not (0 <= i0 <= i1 < len(sents)):
            continue
        s = speech.snap_start(sents[i0]["s"], sil)
        e = speech.snap_end(sents[i1]["e"], sil)
        if e - s >= 1.5:
            segs.append([round(s, 2), round(e, 2)])
    if not segs:
        return None
    segs.sort()
    merged = [segs[0]]
    for s, e in segs[1:]:
        if s <= merged[-1][1] + 0.4:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    merged = _enforce_single_segment(merged)
    total = sum(e - s for s, e in merged)
    verdict, lmsg = performance.length_verdict(total)
    if verdict == "drop":
        return {"_rejected": lmsg}
    handles = performance.clean_handles(ref.get("handles"))
    hv, hmsg = performance.handles_verdict(handles)
    if hv == "drop":
        return {"_rejected": hmsg}
    payoff = _apply_payoff(merged, cand, ref, vr)
    total = sum((seg[1] - seg[0]) / (seg[2] if len(seg) > 2 else 1.0) for seg in merged)
    transcript = " ".join(x["text"] for x in sents
                          if any(seg[0] - 0.3 <= x["s"] and x["e"] <= seg[1] + 0.3
                                 for seg in merged))
    # Measured payoff anchor (screening fix 2026-08-03): where the payoff
    # actually ENDS on the footage -- last payoff word in words.json, scene
    # boundary / motion settle after the planned moment -- not where the plan
    # estimated it. Computed HERE so the plan's duration accounting
    # (_breathe_tail below), the render's endcard window and the duration QC
    # gate all consume the same number.
    anchor = _measured_anchor({"id": f"cut_{idx:02d}", "segments": merged,
                               "duration_s": total, "payoff": payoff,
                               "planned_from": "speech",
                               "transcript": transcript}, anchor_ctx)
    # Endcard breathing (gap 13): the delivery ends where the payoff ends, or
    # at the last spoken word when no payoff event was chosen. The closing
    # card must land after it, so the tail grows into the trailing pause when
    # the clip is too tight.
    breathe_note, delivery_end = None, (payoff.get("local_e") if payoff else None)
    if (str(ref.get("cta") or "")).strip():
        delivery_end = delivery_end if delivery_end is not None else total
        if anchor and anchor.get("resolved"):
            delivery_end = max(delivery_end, float(anchor["t_anchor"]))
        room = _trailing_pause_room(merged[-1][1], sil)
        merged, total, breathe_note = _breathe_tail(merged, total, delivery_end, room)
    # P-LEN again: the payoff append lengthens the cut, so the pre-payoff check
    # is not the one that counts. Without this a 33s cut walked past a 32s gate.
    verdict, lmsg = performance.length_verdict(total)
    if verdict == "drop":
        return {"_rejected": lmsg + " (after payoff)"}
    pcb = _payoff_complete_by(ref, total, delivery_end)
    # Tail hygiene: a content end sitting on a rising sound onset ships a
    # clipped blast at the content->outro boundary (cut_04 screening bug).
    merged, total, tail_note = audiotail.plan_tail_trim(
        (anchor_ctx or {}).get("video"), merged, total, pcb, f"cut_{idx:02d}")
    hook = str(ref.get("hook", "")).replace("—", ",").replace("–", ",")[:60]
    because = ["C1 " + RULES["C1"], "C4 " + RULES["C4"], "H2 " + RULES["H2"],
               "P1 " + RULES["P1"], f"P-LEN {lmsg}", f"P-HANDLES {hmsg}"] + cand["signals"]
    if payoff:
        because.append(f"C6 {RULES['C6']}: '{payoff['event']}' because {payoff['why']}")
    if breathe_note:
        because.append(breathe_note)
    if tail_note:
        because.append(tail_note)
    because.append(f"ai: {ref.get('why', '')}")
    overlays_l = _resolve_reveals(
        _overlay_lines(ref, total),
        {"segments": merged, "payoff": payoff, "planned_from": "speech"},
        anchor_ctx, total)
    plan = {
        "id": f"cut_{idx:02d}",
        "delivery_end_s": round(delivery_end, 2) if delivery_end is not None else None,
        "payoff_complete_by": pcb,
        "payoff_anchor": anchor,
        "platform": "tiktok",
        "format": str(ref.get("format", "F8"))[:3],
        "title": str(ref.get("title", "untitled"))[:60],
        "segments": merged,
        "duration_s": round(total, 1),
        "source_range": [merged[0][0], merged[-1][1]],
        # H6: slow readers; ~2.2 words/s reading pace plus settle time
        "hook": {"type": "text", "text": hook,
                 "show_s": round(min(6.0, max(3.6, len(hook.split()) * 0.45 + 1.6)), 1)},
        "captions": "burned",
        "overlay_lines": overlays_l,
        "caption": str(ref.get("caption", ""))[:300],
        "cta": str(ref.get("cta", ""))[:60],
        "handles": handles,
        "planned_from": "speech",
        "payoff": payoff,
        "transcript": transcript,
        "because": because,
        "playbook_version": PLAYBOOK_VERSION,
    }
    return _with_outro(plan, ref, total)


MAX_OFFSET_S = 30.0  # beyond this, waveform sync failed rather than found a real lag


def _plausible_offset(session):
    """Never freeze a failed sync into a cut plan: the render trusts this number."""
    off = session.get("facecam_offset_s", 0.0) or 0.0
    if abs(off) > MAX_OFFSET_S:
        print(f"[sync] discarding implausible facecam offset {off:+.3f}s, using 0.0")
        return 0.0
    return off


def _compose(plan, session, peaks):
    """System-decided composition for a cut when a facecam session exists."""
    if not session:
        return None
    features = []
    for p in peaks:
        for seg in plan["segments"]:
            s, e = seg[0], seg[1]
            if s <= p["t"] <= e:
                features.append({"t_src": p["t"], "dur": 2.5,
                                 "why": f"reaction {p['above_median']:+.1f}dB @{media.fmt(p['t'])}"})
    comp = {
        "cam": "split",           # researched default: cam top ~40%, content below
        "cam_h": 768,
        "d": 280,                 # circle fallback sizes (labeled seed, CO6)
        "feature_d": 430,
        "offset_s": _plausible_offset(session),
        "features": features[:3],
        "because": ["CO1 " + RULES["CO1"], "CO2 " + RULES["CO2"],
                    "CO3 " + RULES["CO3"], "CO4 " + RULES["CO4"],
                    "CO5 " + RULES["CO5"]],
    }
    return comp


def _tokens(text):
    return [t for t in re.sub(r"[^a-z0-9' ]", " ", text.lower()).split() if t]


_NEG = {"not", "no", "don't", "dont", "won't", "wont", "isn't", "isnt",
        "can't", "cant", "never", "without"}
_CONTRAST = {"left", "right", "before", "after"}


def _retake_drops(sents, window_s=35.0, sim=0.65, min_tok=5):
    """C11: spans of earlier takes of repeated lines, in source seconds.

    A sentence is a retake when a LATER sentence nearby says nearly the same
    thing (max of token-sequence ratio and token-set containment), or when it
    restarts itself mid-sentence (repeated n-gram). Intentional contrast pairs
    that differ by a negation are never retakes. Only earlier members of a
    match drop, so the last take always survives.
    """
    import difflib
    toks = [_tokens(x["text"]) for x in sents]
    drop = set()
    spans = []
    for i in range(len(sents)):
        if i in drop:
            continue
        for j in range(i + 1, len(sents)):
            gap = sents[j]["s"] - sents[i]["e"]
            if gap > window_s:
                break
            a, b = toks[i], toks[j]
            if not a or not b:
                continue
            if (set(a) ^ set(b)) & (_NEG | _CONTRAST):
                continue  # negation or left/right contrast: intentional, not a retake
            if min(len(a), len(b)) >= min_tok:
                seq = difflib.SequenceMatcher(None, a, b).ratio()
                # containment only counts when the takes LOOK like takes:
                # similar length and the same opening words
                contain = 0.0
                if (min(len(a), len(b)) / max(len(a), len(b)) >= 0.55
                        and difflib.SequenceMatcher(None, a[:3], b[:3]).ratio() >= 0.5):
                    contain = len(set(a) & set(b)) / min(len(set(a)), len(set(b)))
                if max(seq, contain) >= sim:
                    drop.add(i)
                    break
            elif 2 <= len(a) < min_tok and len(b) > len(a):
                r = difflib.SequenceMatcher(None, a, b[: len(a) + 1]).ratio()
                if r >= 0.7:  # false start: restarted the same phrase
                    drop.add(i)
                    break
    # mid-sentence self-restart: a repeated 3-gram inside one sentence drops
    # everything before the restart point
    for idx, x in enumerate(sents):
        if idx in drop:
            continue
        ws = x.get("words")
        a = toks[idx]
        if len(a) < 8:
            continue
        grams = {}
        for k in range(len(a) - 2):
            g = tuple(a[k:k + 3])
            if g in grams:
                first = grams[g]
                if k - first >= 3:  # real restart, not a stutter
                    frac = k / len(a)
                    cut_e = x["s"] + (x["e"] - x["s"]) * frac
                    if ws:  # word timings when available
                        cut_e = ws[k]["s"] - 0.05
                    spans.append([x["s"], cut_e])
                break
            grams[g] = k
    spans += [[sents[i]["s"], sents[i]["e"]] for i in drop]
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + 0.6:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _subtract_regions(keep, drops, min_keep=0.4):
    out = []
    for s, e in keep:
        segs = [[s, e]]
        for ds, de in drops:
            nxt = []
            for a, b in segs:
                if de <= a or ds >= b:
                    nxt.append([a, b])
                    continue
                if ds - a > min_keep:
                    nxt.append([a, ds])
                if b - de > min_keep:
                    nxt.append([de, b])
            segs = nxt
        out.extend(segs)
    return out


# PACING (taste wave, 2026-08-02): how much silence survives around kept
# speech when dead air is collapsed. The old 0.35s pad clipped breaths a touch
# tight — deliveries landed and were immediately cut off, which reads rushed on
# rewatch. One notch less aggressive (+20%) keeps the beat AFTER a line without
# reopening real dead air (join_gap still merges anything under 2.5s apart).
PACING = {"keep_silence_pad_s": 0.42}   # was 0.35


def _full_edit(sm, sents=None, join_gap=2.5, pad=None):
    """Full-edit keep list: speech blocks with dead air collapsed."""
    pad = PACING["keep_silence_pad_s"] if pad is None else pad
    dur = sm["duration_s"]
    sil = sm["silences"]
    keep, t = [], 0.0
    for s, e in sil:
        if s - t > 0.2:
            keep.append([max(0, t - pad), min(dur, s + pad)])
        t = e
    if dur - t > 0.2:
        keep.append([max(0, t - pad), dur])
    merged = [keep[0]] if keep else []
    for s, e in keep[1:]:
        if s <= merged[-1][1] + join_gap:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    because = ["C1 " + RULES["C1"], "C3 keep breathing room in long-form"]
    if sents:
        drops = _retake_drops(sents)
        if drops:
            cut_s = sum(e - s for s, e in drops)
            merged = _subtract_regions(merged, drops)
            because.append(f"C11 {RULES['C11']} ({len(drops)} regions, {cut_s:.0f}s)")
            print(f"[direct] C11 retake dedupe: {len(drops)} regions, {cut_s:.0f}s dropped")
    kept = sum(e - s for s, e in merged)
    return {"keep": [[round(s, 2), round(e, 2)] for s, e in merged],
            "original_s": dur, "edited_s": round(kept, 1),
            "removed_s": round(dur - kept, 1),
            "because": because}


def _auto_reframe(root):
    """Cinematic 16:9 with no facecam can lose its sides; screen recordings cannot."""
    try:
        probe = media.probe(_source_video(root))
        v = next(x for x in probe["streams"] if x.get("codec_type") == "video")
        w, h = float(v.get("width", 0)), float(v.get("height", 0))
    except (OSError, StopIteration, ValueError, KeyError, SystemExit):
        return 1.0
    if not h:
        return 1.0
    return 1.5 if (w / h) >= 1.5 else 1.0


def _batched_refine(cands, worker, consume, batch_size=12, max_workers=12):
    """Refine candidates in submission batches so cost semantics survive the
    thread pool.

    batch_size=12 matches the 12-candidate cap in run(): a normal run submits
    everything in ONE batch (two sequential 6-wide batches measured 48.9s; one
    12-wide batch is bounded by the slowest single call). The batch machinery
    stays because the two cost properties below still need it when a caller
    passes a smaller batch_size, and the abort Event is what bounds spend on a
    budget-cap failure inside the single wide batch.

    The old serial loop had two properties worth money: a budget-cap
    RuntimeError stopped all further LLM calls, and --max-cuts stopped
    refining once enough plans existed. A fire-everything pool loses both.
    So: submit one batch -> collect in candidate order -> run the sequential
    bookkeeping (consume) for that batch -> stop submitting once consume says
    it has enough. An abort Event is set the moment any worker raises; workers
    check it before each LLM call, which bounds post-cap spend to the calls
    already in flight.

    worker(ci, cand, abort) -> result (None when aborted early);
    consume(cand, result) -> True to stop (max_cuts reached).
    Any worker exception is re-raised for the earliest failing candidate of
    its batch, before that batch is consumed -- exactly where the serial loop
    would have died.
    """
    abort = threading.Event()

    def guarded(ci, cand):
        try:
            return worker(ci, cand, abort)
        except BaseException:
            abort.set()
            raise

    for b0 in range(0, len(cands), batch_size):
        batch = cands[b0:b0 + batch_size]
        results, first_exc = [], None
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = [ex.submit(guarded, b0 + i, c) for i, c in enumerate(batch)]
            for f in futures:  # candidate order
                try:
                    results.append(f.result())
                except BaseException as e:
                    if first_exc is None:
                        first_exc = e
                    results.append(None)
        if first_exc is not None:
            raise first_exc
        for cand, res in zip(batch, results):
            if consume(cand, res):
                return


def run(project, ai=True, max_cuts=8, brain="gemini", tag=None):
    root = resolve_project(project)
    an = os.path.join(root, "analysis")
    edl = os.path.join(root, "edl")
    sfx = f"_{tag}" if tag else ""
    words = speech.words_from(os.path.join(an, "words.json"))
    sents = topics_mod.sentences(words)
    from . import visual as visual_mod
    sm = _load(an, "speech_map.json")
    vr_art = _load(an, "visual_review.json")
    vr = visual_mod.sequences(vr_art)
    for m in visual_mod.missing_ranges(vr_art):
        print(f"[direct] WARNING: no visual coverage for "
              f"[{m.get('start_abs')}-{m.get('end_abs')}]; re-run analyze to fill it")
    tp = _load(an, "topics.json") or []
    loud = _load(an, "loudness.json") or {}
    sil = [tuple(x) for x in sm["silences"]]
    peaks = loud.get("energy_peaks", [])
    session = _load(an, "session.json")
    name = os.path.basename(root)

    scenes = [c["t"] for c in (_load(an, "scenes.json") or [])]
    probe = _load(an, "probe.json") or {}
    try:
        duration = float(probe.get("format", {}).get("duration", 0)) or (
            max((v.get("end_s", 0) for v in vr), default=0) + 5)
    except (TypeError, ValueError):
        duration = max((v.get("end_s", 0) for v in vr), default=0) + 5
    reframe = _auto_reframe(root)

    # P-TRUST: decide up front whether this project's transcript can be believed.
    trust = performance.transcript_trust(sents, duration)
    print(f"[trust] {trust['reason']}")
    visual_only = not trust["trusted"]

    # Clearance: uncleared voices and suspected third-party windows are
    # excluded BEFORE planning, so a guest's work can never reach a cut plan.
    from . import clearance
    dstat, ddetail = clearance.diarization_status(root)
    if dstat != "ok":
        print(f"[clear] voice identity UNVERIFIED ({ddetail}); only the "
              f"transcript-cue heuristic and any declared ranges apply -- "
              f"this session is NOT assumed single-speaker")
    blocked = clearance.blocked_ranges(root)
    cands = _candidates(vr, tp, peaks)
    if blocked:
        print(f"[clear] {len(blocked)} blocked range(s) on this session")
        kept_cands = []
        for c in cands:
            hits = clearance.overlapping([[c["s"], c["e"]]], blocked)
            if hits:
                print(f"  excluded [{c['s']:.0f}s-{c['e']:.0f}s]: {hits[0][1]}")
            else:
                kept_cands.append(c)
        cands = kept_cands
    n_cands = len(cands)
    cands = cands[:12]
    # plan-time context for the measured payoff anchor (beats.py): the source
    # footage and analysis artifacts exist at plan time, so the plan, the
    # render and the QC gates can all consume the SAME measured number
    anchor_ctx = {"analysis_dir": an, "video": _source_video(root)}
    print(f"{n_cands} candidate windows, refining top {len(cands)} "
          f"({'picture only' if visual_only else 'speech + picture'}) ...")

    def _refine_one(ci, cand, abort):
        """Per-candidate refinement: independent LLM calls, safe to run in a
        worker. Everything order-dependent (plan ids, overlap dedupe, the
        max_cuts stop) stays in the sequential pass below. `abort` is set the
        moment any sibling raises (network, budget cap): checked before every
        LLM call so a capped run stops paying instead of finishing the pool."""
        if abort.is_set():
            return None
        wsents = [] if visual_only else _window_sents(sents, cand["s"], cand["e"])
        # No speech to plan from? Cut it on picture instead of throwing it away.
        # This is where every high-scoring silent sequence used to be lost.
        as_visual = visual_only or not wsents
        began_visual = as_visual
        if as_visual:
            ref = _refine_visual(cand, vr, peaks, scenes, duration, brain, name) if ai else None
        else:
            refine = {"gemini": _refine_ai, "gpt": _refine_gpt}.get(brain, _refine_ai)
            ref = (refine(cand, wsents, vr, peaks, name) if ai else None) \
                or _refine_heuristic(cand, wsents)
            # A window can have speech nearby and still have no spoken landing:
            # the moment is carried by picture. Falling back instead of dropping
            # is what stops high-scoring silent sequences from being thrown away
            # (an S10 monster reveal and an S10 corridor encounter were lost
            # exactly this way, then cut by hand).
            if (not ref or not ref.get("landing_ok")) and ai:
                if abort.is_set():
                    return None
                print(f"  [cand {ci + 1}] speech has no landing, retrying on picture: "
                      f"{cand['why'][:50]}")
                ref = _refine_visual(cand, vr, peaks, scenes, duration, brain, name)
                as_visual = True
        return ref, as_visual, began_visual

    plans, seen = [], []

    def _consume(cand, res):
        """Sequential bookkeeping for one refined candidate; returns True once
        max_cuts plans exist (stop refining further batches)."""
        ref, as_visual, began_visual = res
        if not ref or not ref.get("landing_ok"):
            if began_visual:
                print(f"  drop (no landing on picture): {cand['why'][:60]}")
            else:
                print(f"  drop (no landing on speech or picture): {cand['why'][:55]}")
            return False
        try:
            appeal = int(ref.get("stranger_appeal", 7))
        except (ValueError, TypeError):
            appeal = 7
        if appeal < 6:  # V1: editing cannot save unappealing content
            print(f"  drop (appeal {appeal}): {cand['why'][:70]}")
            return False
        plan = (_visual_plan_from(cand, ref, len(plans) + 1, scenes, duration,
                                  reframe, anchor_ctx=anchor_ctx)
                if as_visual else
                _plan_from(cand, ref, sents, sil, len(plans) + 1, vr=vr,
                           anchor_ctx=anchor_ctx))
        if plan and plan.get("_rejected"):
            print(f"  drop ({plan['_rejected'][:90]})")
            return False
        if not plan:
            print(f"  drop (guardrails): {cand['why'][:70]}")
            return False
        plan.setdefault("reframe", reframe)
        rng = plan["source_range"]
        if any(rng[0] < e and rng[1] > s for s, e in seen):  # overlap with a kept plan
            return False
        seen.append(rng)
        comp = _compose(plan, session, peaks)
        if comp:
            plan["composition"] = comp
        plans.append(plan)
        print(f"  plan {plan['id']}: {plan['title']} ({plan['duration_s']}s, "
              f"{plan.get('planned_from', 'speech')}, "
              f"handles: {'+'.join(plan.get('handles') or ['none'])})"
              + (f" cam:{comp['cam']}+{len(comp['features'])}feat" if comp else ""))
        return len(plans) >= max_cuts

    with timing.stage("direct refine", timing.timings_path(root)):
        _batched_refine(cands, _refine_one, _consume)

    full = _full_edit(sm, sents)
    json.dump(plans, open(os.path.join(edl, f"cut_plans{sfx}.json"), "w"), indent=1)
    json.dump(full, open(os.path.join(edl, "full_edit.json"), "w"), indent=1)
    _write_md(name, plans, full, os.path.join(edl, f"CUT-PLANS{sfx}.md"))
    print(f"\n{len(plans)} cut plans ({brain}) -> {edl}/CUT-PLANS{sfx}.md")
    return plans


def _write_md(name, plans, full, path):
    L = [f"# Cut plans: {name}", "",
         f"Playbook v{PLAYBOOK_VERSION}. This file is the PLAN, not the review surface. "
         f"Review the fully treated videos listed in `deliverables/REVIEW.md` "
         f"(produced by `reelly cut`), then verdict each: KEEP or KILL plus one line why "
         f"(goes to playbook/feedback/VERDICTS.md).", ""]
    for p in plans:
        segs = " + ".join(f"{media.fmt(seg[0])}-{media.fmt(seg[1])}"
                          + (f" x{seg[2]}" if len(seg) > 2 else "")
                          for seg in p["segments"])
        L += [f"## {p['id']}: {p['title']} ({p['duration_s']}s, format {p.get('format', '?')})", "",
              f"- Watch: `deliverables/final/{p['id']}.mp4` (fully treated)",
              f"- Hook: **{p['hook']['text']}**",
              f"- Source: {segs}",
              f"- Planned from: {p.get('planned_from', 'speech')} | "
              f"handles: {', '.join(p.get('handles') or ['NONE'])} | "
              f"captions: {p.get('captions', 'burned')}"]
        for o in p.get("overlay_lines") or []:
            L.append(f"- On screen at {o['t']:.1f}s: \"{o['text']}\"")
        if p.get("caption"):
            L.append(f"- Caption: {p['caption']}")
        L += [f"- Says: {p['transcript'][:300]}" if p.get("transcript") else "", "",
              "Because:"]
        L += [f"- {b}" for b in p["because"]]
        comp = p.get("composition")
        if comp:
            feats = "; ".join(f["why"] for f in comp["features"]) or "none"
            L += ["", f"Composition: {comp['cam']} layout, reaction peaks: {feats}"]
            L += [f"- {b}" for b in comp["because"]]
        L += ["", "Verdict: _pending_", ""]
    L += ["## Full edit plan", "",
          f"- {media.fmt(full['original_s'])} -> {media.fmt(full['edited_s'])} "
          f"({full['removed_s']:.0f}s of dead air out, {len(full['keep'])} kept blocks)",
          "- Renders in M4; data in `full_edit.json`", ""]
    open(path, "w").write("\n".join(L))
