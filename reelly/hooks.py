"""The hook and the opening frame, chosen together (playbook H5).

WHY
The editor brain wrote ONE hook per cut, from text descriptions, blind to
the picture, and frame 0 was simply wherever the chosen range began. But the
first frame and the first line are read as one thing in the half second a
scrolling viewer gives a clip. So for every plan this step:

1. Finds the strongest candidate OPENING FRAMES near the start of the cut
   (sight's per-frame opening_score, else evenly spaced) and shows them to
   the brain as one contact sheet.
2. Asks for FIVE hook + frame pairs, one per path, so they cannot all be the
   same idea in five costumes:
     world     a concrete detail only this game has
     emotion   the feeling the moment gives
     audience  the viewer's expectation, met or broken
     inverted  a genre cliche turned upside down
     format    an unusual shape: a rule, a warning, a dare, a list, a POV
   At least two must be choices the model itself rates unlikely (typicality
   under 0.10): the obvious line is the one every other clip already uses.
   (Sampling idea after the "pitch round" in Jake Schincariol's public
   video-production-skills; no text taken.)
3. Drops look-alikes (same opening word, or mostly the same words), lints
   the copy, and scores each pair: the brain's stop-the-scroll score, the
   frame's own opening score, a small bonus for an unlikely pick, and a
   penalty for opening with the same word as another cut in this batch.
4. Applies the winner: the hook (with its hot word and why), and the cut
   starts ON the chosen frame (the start moves later by at most
   MAX_SHIFT_S). All five, with scores, stay on the plan for review.

Zero-paid like the rest: the default brain is a subscription CLI. Codex sees
the frames; other brains get the frame descriptions as text.
"""
import json
import os
import re
import subprocess
import tempfile

from . import config, media

PATHS = (
    ("world", "a concrete detail only this game has"),
    ("emotion", "the feeling this moment gives the viewer"),
    ("audience", "the viewer's expectation: meet it, or break it"),
    ("inverted", "a genre cliche turned upside down"),
    ("format", "an unusual shape: a rule, a warning, a dare, a list, a POV"),
)
MAX_HOOK_WORDS = 7
MAX_HOOK_LINES = 2      # read at a glance: a third line is a paragraph
N_FRAMES = 6
MAX_SHIFT_S = 4.0       # how far the start may move later to open on a frame
LOOK_S = 6.0            # candidate frames come from the first LOOK_S of the cut
MIN_CONTENT_S = 12.0
LETTERS = "ABCDEF"

PROMPT = """You write the on-screen HOOK for a vertical short (TikTok/Reels/Shorts) cut from game footage. The hook is burned on screen from the FIRST frame, fully readable at once, while a muted viewer decides whether to keep watching. The words and the opening picture are ONE design decision: write each hook FOR its frame.

THE CUT: "{title}". What happens in it: {story}
On-screen lines already planned after the hook: {lines}

CANDIDATE OPENING FRAMES{image_note}:
{frames}

Write exactly FIVE hook + frame pairs, one for each path:
{paths}

Rules:
- Each hook: at most {max_words} words, readable in one glance, no emojis, no em dashes, no exclamation marks, no hashtags.
- Name what is visible or about to happen; never promise something the footage does not show.
- Pair each hook with the frame (a letter) it works best over. Different paths may share a frame.
- Rate "typicality": how likely a typical model would be to write this exact hook for this clip, 0 to 1. At least TWO of the five must be under 0.10.
- No two hooks may have the same shape (same opening word, same sentence pattern).
- "stop_score" 1-10: how hard this pair stops a stranger's scroll, judged honestly.
- "hot": the one word that carries the hook (it is highlighted), and "hot_reason" in a few words.
{brief}
Return ONLY JSON:
{{"variants": [{{"path": "world|emotion|audience|inverted|format", "frame": "A", "hook": "...", "hot": "...", "hot_reason": "...", "typicality": 0.3, "stop_score": 7, "why": "one line"}}]}}"""


JUDGE_PROMPT = """You are a demanding short-form editor judging hook + opening-frame pairs written by someone else for a vertical game short. The hook is on screen from the first frame over its frame{image_note}.

FRAMES:
{frames}

PAIRS:
{pairs}
{brief}
Score every pair 1-10 on three things, honestly and independently:
- "natural": does it read like a sharp human wrote it? Awkward, padded or ungrammatical lines score low.
- "stop": would it stop a muted stranger's scroll over THAT frame?
- "on_brief": does it fit the voice and only claim what the footage shows?
Return ONLY JSON: {{"scores": [{{"i": 0, "natural": 7, "stop": 7, "on_brief": 7, "note": "a few words"}}]}}"""


def _words(t):
    return re.findall(r"[a-z0-9']+", str(t).lower())


def _frames_near(an_dir, s0, seg_end):
    """Candidate opening frames in [s0, s0 + LOOK_S]: sight's per-frame
    records ranked by opening_score, else evenly spaced times."""
    hi = min(seg_end - MIN_CONTENT_S, s0 + LOOK_S)
    hi = max(s0, hi)
    out = []
    try:
        fr = json.load(open(os.path.join(an_dir, "frames.json"))).get("frames", [])
    except (OSError, ValueError):
        fr = []
    for f in fr:
        t = float(f.get("t", -1))
        if s0 - 0.01 <= t <= hi + 0.01:
            out.append(f)
    if out:
        out.sort(key=lambda f: (-int(f.get("opening_score", 0)), f["t"]))
        out = sorted(out[:N_FRAMES], key=lambda f: f["t"])
    else:
        n = N_FRAMES
        step = (hi - s0) / max(1, n - 1)
        out = [{"t": round(s0 + step * i, 2), "what_happens": "",
                "on_screen_text": "", "opening_score": 5} for i in range(n)]
    if not any(abs(float(f["t"]) - s0) < 0.05 for f in out):
        out = [{"t": round(s0, 2), "what_happens": "(the cut's current first frame)",
                "on_screen_text": "", "opening_score": 5}] + out[:N_FRAMES - 1]
    return out[:N_FRAMES]


def _sheet(video, times, out, crop=None):
    """A 3x2 contact sheet of the candidate frames, letters in reading order
    (cropped to the game view when the capture has chrome)."""
    from PIL import Image
    tiles = []
    with tempfile.TemporaryDirectory() as td:
        for i, t in enumerate(times):
            p = os.path.join(td, f"f{i}.jpg")
            subprocess.run([config.FFMPEG, "-y", "-v", "error", "-ss", f"{t:.3f}",
                            "-i", video, "-frames:v", "1", "-vf",
                            (f"crop={crop}," if crop else "") + "scale=480:-2", p],
                           check=True)
            tiles.append(Image.open(p).convert("RGB"))
    w, h = tiles[0].size
    sheet = Image.new("RGB", (w * 3, h * ((len(tiles) + 2) // 3)))
    for i, im in enumerate(tiles):
        sheet.paste(im, ((i % 3) * w, (i // 3) * h))
    sheet.save(out, quality=88)
    return out


def _story(plan, vr):
    s, e = plan["source_range"]
    near = [v for v in vr or [] if v.get("end_s", 0) >= s and v.get("start_s", 0) <= e]
    return "; ".join(v.get("what_happens", "") for v in near[:4]) or plan.get("title", "")


def _typical(v):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def _int10(v, default=5):
    try:
        return max(1, min(10, int(round(float(v)))))
    except (TypeError, ValueError):
        return default


def hook_lines(text):
    """How many lines the hook sets in, at the size and box it renders with."""
    from PIL import Image, ImageDraw
    from . import captions
    size = captions.hook_size(text)
    d = ImageDraw.Draw(Image.new("RGBA", (captions.HOOK_WIDTH, 10)))
    return len(captions.wrap_balanced(d, text, captions._font(size, captions.HOOK_ROLE),
                                      captions.HOOK_WIDTH - 2 * captions._stroke_px(None)))


def clean(variants, frames, used_openers=()):
    """Validated, deduplicated, scored variants, best first."""
    out, seen_sets = [], []
    for v in variants or []:
        if not isinstance(v, dict):
            continue
        hook = re.sub(r"\s*[—–]\s*", ", ", str(v.get("hook", ""))).strip()
        hook = hook.replace("!", ".").strip()
        words = _words(hook)
        if not words or len(hook.split()) > MAX_HOOK_WORDS:
            continue
        if hook_lines(hook) > MAX_HOOK_LINES:
            continue                          # would set in 3+ lines
        letter = str(v.get("frame", "A")).strip().upper()[:1]
        idx = LETTERS.find(letter)
        if idx < 0 or idx >= len(frames):
            idx = 0
        wset = set(words)
        if any(words[0] == other[0] or len(wset & s) / max(1, len(wset | s)) > 0.6
               for other, s in seen_sets):
            continue                          # same silhouette as a kept one
        seen_sets.append((words, wset))
        typ = _typical(v.get("typicality"))
        stop = _int10(v.get("stop_score"))
        frame = frames[idx]
        opening = _int10(frame.get("opening_score"), 5)
        score = 0.55 * stop + 0.45 * opening + (1.0 if typ < 0.10 else 0.0)
        repeat = words[0] in used_openers
        if repeat:
            score -= 3.0
        out.append({"path": str(v.get("path", ""))[:12], "hook": hook,
                    "hot": str(v.get("hot", ""))[:30],
                    "hot_reason": str(v.get("hot_reason", ""))[:80],
                    "frame": LETTERS[idx], "t": float(frame["t"]),
                    "typicality": round(typ, 2), "stop_score": stop,
                    "opening_score": opening, "repeats_batch_opener": repeat,
                    "score": round(score, 2), "why": str(v.get("why", ""))[:160]})
    out.sort(key=lambda v: -v["score"])
    return out


def _shift_start(plan, t_open):
    """Start the cut ON the chosen frame: move the first segment's start to
    t_open (only later, at most MAX_SHIFT_S) and keep every clip-relative
    time (overlay lines, payoff, durations) consistent. Returns the shift."""
    seg = plan["segments"][0]
    d = round(float(t_open) - float(seg[0]), 2)
    if d <= 0.04 or d > MAX_SHIFT_S:
        return 0.0
    if float(plan.get("content_s") or plan["duration_s"]) - d < MIN_CONTENT_S:
        return 0.0
    seg[0] = round(float(seg[0]) + d, 2)
    plan["source_range"] = [plan["segments"][0][0], plan["segments"][-1][1]]
    for k in ("duration_s", "content_s", "payoff_complete_by"):
        if isinstance(plan.get(k), (int, float)):
            plan[k] = round(float(plan[k]) - d, 2)
    lines = []
    for o in plan.get("overlay_lines") or []:
        t = round(float(o.get("t", 0)) - d, 2)
        if t >= 3.0:
            lines.append({**o, "t": t})
    plan["overlay_lines"] = lines
    return d


LINE_GAP_S = 0.3        # between the hook leaving and the next line arriving
LINE_MIN_SPACING_S = 3.0


def _clear_hook_window(plan):
    """The hook and the timed lines share one slot: no line may start while
    the (possibly longer) new hook is still up. A line that starts too early
    moves to just after the hook if that keeps its spacing to the next line,
    else it is dropped."""
    end = float(plan["hook"]["show_s"]) + LINE_GAP_S
    lines = sorted(plan.get("overlay_lines") or [], key=lambda o: float(o["t"]))
    kept = []
    for i, o in enumerate(lines):
        t = float(o["t"])
        if t < end:
            nxt = float(lines[i + 1]["t"]) if i + 1 < len(lines) else None
            if nxt is not None and nxt - end < LINE_MIN_SPACING_S:
                plan.setdefault("because", []).append(
                    f"line {o.get('text', '')!r} dropped: the new hook holds the slot")
                continue
            o = {**o, "t": round(end, 2)}
        kept.append(o)
    plan["overlay_lines"] = kept


def rescore(variants, judged):
    """Fold a fresh-eyes judge into the ranking. The judge never saw the
    writer's self-scores, so its view carries the most weight: final =
    0.5 judge + 0.2 writer stop + 0.3 frame, + 0.5 for an unlikely pick,
    - 3 for a repeated batch opener. Without a judge the variants keep
    their first score."""
    by_i = {}
    for j in (judged or {}).get("scores") or []:
        try:
            i = int(j.get("i"))
        except (TypeError, ValueError):
            continue
        vals = [_int10(j.get(k)) for k in ("natural", "stop", "on_brief")]
        by_i[i] = (sum(vals) / 3.0, str(j.get("note", ""))[:80])
    if not by_i:
        return variants
    for i, v in enumerate(variants):
        if i not in by_i:
            continue
        judge, note = by_i[i]
        v["judge_score"] = round(judge, 2)
        v["judge_note"] = note
        # the writer rates almost everything 8-9 (measured on real runs), so
        # its self-score separates little; the judge and the frame decide
        v["score"] = round(0.5 * judge + 0.2 * v["stop_score"] + 0.3 * v["opening_score"]
                           + (0.5 if v["typicality"] < 0.10 else 0.0)
                           - (3.0 if v.get("repeats_batch_opener") else 0.0), 2)
    variants.sort(key=lambda v: -v["score"])
    return variants


def compose(plan, an_dir, video, brain, project, vr=None, used_openers=(),
            ask=None):
    """Choose the hook and opening frame for one plan, in place. Returns the
    scored variants ([] when the brain gave nothing usable: the plan keeps
    its hook). `ask(prompt, images)` is injectable for tests."""
    from . import direct
    s0, s1 = float(plan["segments"][0][0]), float(plan["segments"][0][1])
    frames = _frames_near(an_dir, s0, s1)
    lines = "; ".join(o.get("text", "") for o in plan.get("overlay_lines") or []) or "none"
    flist = "\n".join(
        f"{LETTERS[i]} = {float(f['t']):.1f}s"
        + (f": {f['what_happens']}" if f.get("what_happens") else "")
        + (f" [text on screen: {f['on_screen_text'][:80]}]" if f.get("on_screen_text") else "")
        + f" (opening score {int(f.get('opening_score', 5))}/10)"
        for i, f in enumerate(frames))
    use_images = brain == "codex"
    prompt = PROMPT.format(
        title=plan.get("title", ""), story=_story(plan, vr), lines=lines,
        image_note=(" (the attached image: letters A-F in reading order, left to right, top to bottom)"
                    if use_images else ""),
        frames=flist, paths="\n".join(f"- {k}: {d}" for k, d in PATHS),
        max_words=MAX_HOOK_WORDS, brief=direct.brief_block())
    with tempfile.TemporaryDirectory() as td:
        images = []
        if use_images:
            try:
                images = [_sheet(video, [float(f["t"]) for f in frames],
                                 os.path.join(td, "openings.jpg"),
                                 crop=direct.source_crop(os.path.dirname(an_dir)))]
            except Exception as e:  # noqa: BLE001 -- text-only is still useful
                print(f"  [hooks] {plan['id']}: no frame sheet ({e}); text only")
        def _call(text, label):
            if ask:
                return ask(text, images)
            if brain in ("codex", "claude"):
                return direct._ask_cli(brain, text, project, label,
                                       images=images or None)
            return direct._ask_json(text, brain, project, label)

        reply = _call(prompt, f"hooks {plan['id']}")
        variants = clean((reply or {}).get("variants"), frames, used_openers)
        if not variants:
            print(f"  [hooks] {plan['id']}: no usable variants; keeping the planned hook")
            return []
        if len(variants) > 1:
            pairs = "\n".join(f"{i}: frame {v['frame']}: \"{v['hook']}\""
                              for i, v in enumerate(variants))
            judged = _call(JUDGE_PROMPT.format(
                image_note=(" (the attached image: letters A-F in reading order)"
                            if images else ""),
                frames=flist, pairs=pairs, brief=direct.brief_block()),
                f"hooks judge {plan['id']}")
            variants = rescore(variants, judged)
    best = variants[0]
    d = _shift_start(plan, best["t"])
    plan["hook"] = {**(plan.get("hook") or {}), "type": "text", "text": best["hook"],
                    "hot": best["hot"] or None,
                    "hot_reason": best["hot_reason"] or None,
                    "show_s": round(min(6.0, max(3.6, len(best["hook"].split()) * 0.45 + 1.6)), 1)}
    _clear_hook_window(plan)
    plan["opening"] = {"t": round(s0 + d, 2), "frame": best["frame"],
                       "opening_score": best["opening_score"], "shifted_s": d}
    plan["hook_variants"] = variants
    plan.setdefault("because", []).append(
        f"H5 hook and opening frame chosen together: {best['path']} path, "
        f"frame {best['frame']} at {s0 + d:.1f}s (score {best['score']}), "
        f"{len(variants)} variant(s) kept")
    print(f"  [hooks] {plan['id']}: '{best['hook']}' over frame {best['frame']} "
          f"({best['path']}, score {best['score']}"
          + (f", start +{d:.1f}s" if d else "") + ")")
    return variants
