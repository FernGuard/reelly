"""Semantic ending verification: the closed loop on 'did the payoff land?'.

After a cut renders, the LAST seconds of its CONTENT portion (before the
appended outro) are compressed to a tiny proxy (visual.py's proxy pattern)
and the configured vision model is asked ONE question, with the plan's own
hook/payoff text as context: does the payoff moment fully complete on
screen before this clip ends?

On complete=false the PLAN's content end is adjusted -- the last segment
extends along the source timeline up to +MAX_EXT_S, respecting clearance
blocked_ranges and the speech map -- the cut re-renders ONCE, and the
check runs again. Still false -> loud QC FAIL `ending_incomplete` naming
the model's reason. Never an infinite loop: one adjustment, one re-render,
one re-check, then a verdict either way.

Every verdict is recorded in the project's qc/ending_check.json (the
judge gate `ending_complete` reads it). REELLY_ENDING_CHECK=off disables
the whole loop. Every model call is budget-checked and ledger-logged.
"""
import json
import os
import subprocess
import tempfile

from . import config, ledger, outro, visual

LAST_S = 8.0             # how much of the content tail the model sees
MAX_EXT_S = 4.0          # how far the content end may move per adjustment
MIN_EXT_S = 0.3          # below this an extension changes nothing visible

# economics: an 8s proxy is ~LAST_S * visual.VIDEO_TOKENS_PER_S input tokens
# plus a ~300-token JSON answer, priced at visual.py's current constants
EST_OUT_TOKENS = 300
EST_ENDING_COST = round(
    LAST_S * visual.VIDEO_TOKENS_PER_S / 1e6 * visual.PRICE_IN_PER_M
    + EST_OUT_TOKENS / 1e6 * visual.PRICE_OUT_PER_M, 4)

PROMPT = """This is the FINAL {secs:.0f} seconds of a short vertical video clip (the closing moments before the clip cuts to its brand outro).

The clip's on-screen hook was: {hook!r}
The payoff this clip promises: {payoff!r}

ONE question: does the payoff moment fully COMPLETE on screen before this excerpt ends? "Complete" means the promised result is fully shown and the action settles -- not still generating, not still playing out, not cut off mid-motion or mid-sentence. A result that is still unfolding on the very last frame is NOT complete.

Answer ONLY JSON:
{{"complete": true/false,
 "completes_at_s": <seconds into THIS excerpt where the payoff finishes, or null>,
 "reason": "one line naming what you saw at the end"}}"""


def enabled():
    return os.environ.get("REELLY_ENDING_CHECK", "").lower() != "off"


def _payoff_desc(plan):
    """The payoff description handed to the model, from the plan's own copy."""
    p = plan.get("payoff") or {}
    parts = []
    if p.get("event"):
        parts.append(str(p["event"]))
    if p.get("why"):
        parts.append(str(p["why"]))
    if not parts:
        from . import beats
        phrase = beats.payoff_text(plan)
        if phrase:
            parts.append(f"spoken landing: {phrase}")
    if not parts:
        lines = plan.get("overlay_lines") or []
        if lines:
            parts.append(f"final on-screen beat after: {lines[-1].get('text', '')}")
    if not parts:
        parts.append(plan.get("caption") or plan.get("title") or "the promised result")
    return "; ".join(parts)[:300]


def _proxy(video, t0, t1, dst):
    """Tiny model proxy of [t0, t1]: visual.py's compression recipe."""
    subprocess.run([config.FFMPEG, "-y", "-v", "error", *config.hwdecode_args(),
                    "-ss", f"{t0:.3f}", "-i", video, "-to", f"{t1 - t0:.3f}",
                    "-vf", "scale=-2:600,fps=5",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                    "-an", "-movflags", "+faststart", dst], check=True)
    return dst


def _ask_model(proxy_path, plan, secs, project=""):
    """One vision call -> the verdict dict, budget-checked and ledgered."""
    from google.genai import types
    ledger.check(EST_ENDING_COST)
    client = visual._client()
    f = client.files.upload(file=proxy_path)
    while str(getattr(f.state, "name", f.state)) not in ("ACTIVE", "FAILED"):
        visual._sleep(2)
        f = client.files.get(name=f.name)
    if str(getattr(f.state, "name", f.state)) == "FAILED":
        raise RuntimeError("ending-check proxy upload entered FAILED state")
    hook = plan.get("hook") or {}
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=[f, PROMPT.format(secs=secs,
                                   hook=hook.get("text", "") if isinstance(hook, dict) else str(hook),
                                   payoff=_payoff_desc(plan))],
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           temperature=0.1))
    ledger.add("gemini-ending", f"ending check {plan.get('id', '?')}",
               EST_ENDING_COST, project)
    return parse_answer(resp.text)


def parse_answer(text):
    """Normalize the model's JSON into {complete, completes_at_s, reason}.
    Unparseable output is a WARN-shaped verdict, never a crash: absent data
    must not block shipping (mirrors the anchor gates)."""
    try:
        d = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"complete": None, "completes_at_s": None,
                "reason": f"unparseable model answer: {str(text)[:120]}"}
    if not isinstance(d, dict):
        return {"complete": None, "completes_at_s": None,
                "reason": "model answered non-object JSON"}
    comp = d.get("complete")
    if not isinstance(comp, bool):
        comp = None
    ca = d.get("completes_at_s")
    try:
        ca = None if ca is None else float(ca)
    except (TypeError, ValueError):
        ca = None
    return {"complete": comp, "completes_at_s": ca,
            "reason": str(d.get("reason", ""))[:300]}


def check_cut(video, plan, project=""):
    """One verdict for one rendered cut: proxy the content tail, ask once."""
    end = outro.content_len(plan)
    t0 = max(0.0, end - LAST_S)
    secs = end - t0
    with tempfile.TemporaryDirectory() as td:
        prox = _proxy(video, t0, end, os.path.join(td, "ending.mp4"))
        v = _ask_model(prox, plan, secs, project)
    v["window"] = [round(t0, 2), round(end, 2)]
    return v


# ------------------------------------------------------------ plan adjustment

def extend_plan(plan, root, max_ext=MAX_EXT_S):
    """Extend the plan's LAST segment along the source timeline, bounded by:
    the source's real end, clearance blocked_ranges, and (when a speech map
    exists) a silence-snapped end so the cut never lands mid-word. Returns
    the mutated plan with duration accounting updated, or None when there is
    no legal room. The extension is model-driven (the ending check said the
    payoff did not complete), never a silence heuristic firing on its own.
    """
    from . import clearance, direct, media, speech
    segs = [list(s) for s in plan.get("segments") or []]
    if not segs:
        return None
    s_last = segs[-1]
    src_e = float(s_last[1])
    speed = s_last[2] if len(s_last) > 2 else 1.0
    limit = src_e + max_ext
    try:
        video = direct._source_video(root)
        limit = min(limit, float(media.probe(video)["format"]["duration"]))
    except (SystemExit, OSError, KeyError, ValueError):
        pass
    for bs, be, _why in clearance.blocked_ranges(root):
        # any blocked range touching (src_e, limit) caps the extension at
        # its start; one already covering src_e leaves no room at all
        if bs < limit and be > src_e:
            limit = min(limit, bs if bs > src_e else src_e)
    if limit - src_e < MIN_EXT_S:
        return None
    new_e = limit
    sm_p = os.path.join(root, "analysis", "speech_map.json")
    if os.path.exists(sm_p) and plan.get("captions") != "none":
        try:
            sil = [tuple(x) for x in json.load(open(sm_p))["silences"]]
            snapped = speech.snap_end(new_e, sil)
            if src_e + MIN_EXT_S <= snapped <= limit:
                new_e = snapped
        except (OSError, ValueError, KeyError):
            pass
    ext = round(new_e - src_e, 2)
    if ext < MIN_EXT_S:
        return None
    s_last[1] = round(new_e, 2)
    plan["segments"] = segs
    add_local = round(ext / speed, 2)
    content = round(outro.content_len(plan) + add_local, 2)
    ob = plan.get("outro") or {}
    plan["content_s"] = content
    plan["duration_s"] = round(content + float(ob.get("len_s", 0.0)), 1)
    plan["source_range"] = [segs[0][0], segs[-1][1]]
    plan.setdefault("because", []).append(
        f"ENDING-check: content end extended {ext:.1f}s along the source "
        f"(model verdict: payoff had not completed on screen)")
    return plan


def _save_plan(root, plan, tag=None):
    sfx = f"_{tag}" if tag else ""
    p = os.path.join(root, "edl", f"cut_plans{sfx}.json")
    plans = json.load(open(p))
    plans = [plan if x.get("id") == plan["id"] else x for x in plans]
    json.dump(plans, open(p, "w"), indent=1)


def _watch_file(root, plan, tag=None):
    """The most-treated rendered file for a cut (gfx first)."""
    sfx = f"_{tag}" if tag else ""
    fin = os.path.join(root, "deliverables", f"final{sfx}")
    for name in (f"{plan['id']}_gfx.mp4", f"{plan['id']}.mp4",
                 f"{plan['id']}_trending_gfx.mp4", f"{plan['id']}_trending.mp4"):
        p = os.path.join(fin, name)
        if os.path.exists(p):
            return p
    return None


def verify_cut(root, plan, render_fn, tag=None, project=""):
    """The closed loop for ONE cut. render_fn(plan) re-renders it after a
    plan adjustment (finalize + its graphics). Returns the recorded entry."""
    video = _watch_file(root, plan, tag)
    entry = {"cut_id": plan["id"], "attempts": [], "adjusted": False,
             "final": None}
    if video is None:
        entry["final"] = {"complete": None,
                          "reason": "no rendered file to check"}
        outro.record_verdict(root, plan["id"], entry)
        return entry
    v = check_cut(video, plan, project)
    entry["attempts"].append(v)
    if v["complete"] is False:
        adjusted = extend_plan(dict(plan, segments=[list(s) for s in plan["segments"]]),
                               root)
        if adjusted is not None:
            print(f"[ending] {plan['id']}: payoff INCOMPLETE ({v['reason']}); "
                  f"extending the content end and re-rendering ONCE")
            _save_plan(root, adjusted, tag)
            entry["adjusted"] = True
            render_fn(adjusted)
            video2 = _watch_file(root, adjusted, tag) or video
            v2 = check_cut(video2, adjusted, project)
            entry["attempts"].append(v2)
            plan.update(adjusted)
            v = v2
        else:
            print(f"[ending] {plan['id']}: payoff INCOMPLETE and the source "
                  f"has no legal room to extend into")
    entry["final"] = v
    if v["complete"] is False:
        print(f"[ending] {plan['id']}: QC FAIL ending_incomplete -- "
              f"{v['reason']}")
    elif v["complete"] is None:
        print(f"[ending] {plan['id']}: verdict UNAVAILABLE ({v['reason']}); "
              f"gate degrades to WARN")
    else:
        print(f"[ending] {plan['id']}: payoff completes on screen "
              f"({v['reason']})")
    outro.record_verdict(root, plan["id"], entry)
    return entry


def run(project, tag=None, product="video", account=None, variants=None,
        gfx=True, cut_id=None):
    """Verify every rendered cut of a project; adjust + re-render at most
    once per cut. Called from `reelly cut` after the graphics layer."""
    from . import direct
    if not enabled():
        print("[ending] REELLY_ENDING_CHECK=off: skipping ending verification")
        return []
    root = direct.resolve_project(project)
    sfx = f"_{tag}" if tag else ""
    plans_p = os.path.join(root, "edl", f"cut_plans{sfx}.json")
    plans = json.load(open(plans_p))
    name = os.path.basename(root)

    def _rerender(plan):
        from . import finalize, overlays
        finalize.run(project, cut_id=plan["id"], tag=tag, product=product,
                     account=account, variants=variants)
        if gfx:
            overlays.autoplan(project, product=product, cut_id=plan["id"],
                              tag=tag)
            overlays.apply(project, cut_id=plan["id"], tag=tag)

    entries = []
    for p in plans:
        if cut_id and p["id"] != cut_id:
            continue
        if not p.get("outro"):
            # legacy plan: the outro architecture (and its gate) do not
            # apply; the old endcard machinery still owns this cut
            continue
        entries.append(verify_cut(root, p, _rerender, tag=tag, project=name))
    bad = [e["cut_id"] for e in entries
           if e["final"] and e["final"].get("complete") is False]
    if bad:
        print(f"[ending] {len(bad)} cut(s) FAIL ending_incomplete: "
              + ", ".join(bad) + " (see qc/ending_check.json)")
    return entries
