"""M5 judge: deterministic QC gates on every deliverable.

No AI, no taste: measurable failure modes only. FAIL blocks shipping,
WARN ships with a note. The gate exists so a broken render can never
reach a platform silently.
"""
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

from . import clearance, config, direct, media, plain, retention, safety, timing

LUFS_LO, LUFS_HI = -15.5, -12.5
TP_MAX = -0.9
VERTICAL = {"width": 1080, "height": 1920}

# The three filter analyses share one decode (see _analysis_stderr). Their
# stderr lines are unmistakably tagged, so parsing the combined output with
# the same regexes yields the same values as three separate passes.
EBUR128 = "ebur128=peak=true"
BLACKDETECT = "blackdetect=d=0.4:pix_th=0.06"
FREEZEDETECT = "freezedetect=n=-60dB:d=5"


def _ffmpeg_stderr(args):
    r = subprocess.run([config.FFMPEG, "-hide_banner", *args, "-f", "null", "-"],
                       capture_output=True, text=True)
    return r.stderr


def _analysis_stderr(path, has_video, has_audio):
    """One decode for all per-file filter analyses (was three full decodes:
    ebur128, blackdetect, freezedetect each re-read the whole file)."""
    hw = config.hwdecode_args()  # video decode dominates this pass
    if has_video and has_audio:
        return _ffmpeg_stderr(
            [*hw, "-i", path, "-filter_complex",
             f"[0:v]{BLACKDETECT},{FREEZEDETECT}[v];[0:a]{EBUR128}[a]",
             "-map", "[v]", "-map", "[a]"])
    if has_video:
        return _ffmpeg_stderr([*hw, "-i", path, "-vf",
                               f"{BLACKDETECT},{FREEZEDETECT}", "-an"])
    if has_audio:
        return _ffmpeg_stderr(["-i", path, "-filter_complex", EBUR128])
    return ""


def _parse_loudness(err):
    """(integrated LUFS or None, true peak dBTP or None) from ebur128 stderr.
    Tag-tolerant: keys on the summary block lines, which stay contiguous even
    when other filters interleave their per-frame lines earlier."""
    integ = tp = None
    lines = err.splitlines()
    for i, line in enumerate(lines):
        if "Integrated loudness:" in line:
            for l2 in lines[i:i + 4]:
                if "I:" in l2:
                    integ = float(l2.split("I:")[1].split()[0])
        if "True peak:" in line:
            for l2 in lines[i:i + 4]:
                if "Peak:" in l2:
                    tp = float(l2.split("Peak:")[1].split()[0])
    return integ, tp


def _parse_black(err):
    return re.findall(r"black_start:([\d.]+)", err)


def _parse_freeze(err):
    return re.findall(r"freeze_start: ([\d.]+)", err)


def check_file(path, expect_vertical=True, plan_dur=None):
    """Returns {'file', 'results': [(gate, status, detail)]}."""
    R = []
    p = media.probe(path)
    streams = p.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float(p.get("format", {}).get("duration", 0) or 0)

    R.append(("streams", "FAIL" if not (v and a) else "PASS",
              f"video={bool(v)} audio={bool(a)}"))
    if v and expect_vertical:
        ok = v["width"] == VERTICAL["width"] and v["height"] == VERTICAL["height"]
        R.append(("resolution", "PASS" if ok else "FAIL",
                  f"{v['width']}x{v['height']}"))
    if plan_dur:
        drift = abs(dur - plan_dur)
        R.append(("duration", "PASS" if drift <= 1.5 else "FAIL",
                  f"{dur:.1f}s vs plan {plan_dur:.1f}s"))

    err = _analysis_stderr(path, bool(v), bool(a))
    integ, tp = _parse_loudness(err)
    if integ is not None and integ < -45 and a:
        # Sanity re-measure: the combined video+audio single-decode graph can
        # under-read audio on -c copy concat files (seen 2026-08-03: a healthy
        # -14 LUFS deliverable measured -66.8 through the combined graph while
        # an audio-only decode read it correctly). A sub- -45 LUFS integrated
        # value on a real deliverable is implausible, so before FAILing, decode
        # the audio alone and trust that number; the extra pass runs only in
        # the anomaly case.
        r2 = _ffmpeg_stderr(["-i", path, "-map", "0:a", "-af", EBUR128])
        integ2, tp2 = _parse_loudness(r2)
        if integ2 is not None:
            print(f"[judge] {os.path.basename(path)}: combined-graph loudness "
                  f"{integ} LUFS implausible; audio-only re-measure {integ2} "
                  f"LUFS (using it)")
            integ, tp = integ2, (tp2 if tp2 is not None else tp)
    if integ is not None:
        R.append(("loudness", "PASS" if LUFS_LO <= integ <= LUFS_HI else "FAIL",
                  f"{integ} LUFS (window {LUFS_LO}..{LUFS_HI})"))
    if tp is not None:
        R.append(("true_peak", "PASS" if tp <= TP_MAX else "FAIL",
                  f"{tp} dBTP (max {TP_MAX})"))

    # A deliverable carrying HDR transfer metadata looks blown out after
    # platform re-encodes even when it plays fine locally in QuickTime.
    ct = media.color_transfer(path)
    R.append(("sdr_transfer", "FAIL" if ct in media.HDR_TRANSFERS else "PASS",
              f"color_transfer={ct or 'unset'}"))

    blacks = _parse_black(err)
    R.append(("black_frames", "PASS" if not blacks else "FAIL",
              f"{len(blacks)} black runs" + (f" @{blacks[:3]}" if blacks else "")))

    freezes = _parse_freeze(err)
    R.append(("frozen_video", "PASS" if not freezes else "WARN",
              f"{len(freezes)} freezes >5s (screen content can be static)"))
    return {"file": path, "results": R}


def caption_coverage(plan, words):
    """Every planned word must land in a cue (the lost-cool gate).

    Skipped for captions:"none" cuts. Those are picture-planned, their audio is
    music or game VO, and the ASR words were rejected as untrustworthy on
    purpose (P-TRUST) — so "0 of 0 words reached cues" is the correct outcome,
    not a defect. Failing them here would train a human to ignore the gate.
    """
    if plan.get("captions") == "none":
        return ("caption_coverage", "SKIP",
                "captions:none by design (P-TRUST); nothing to cover")
    from . import speech
    from .finalize import _shifted_words
    wtl, _ = _shifted_words(words, plan["segments"])
    cues = speech.group_cue_words(wtl)
    in_cues = sum(len(wl) for _, _, wl in cues)
    ok = in_cues == len(wtl) and len(wtl) > 0
    return ("caption_coverage", "PASS" if ok else "FAIL",
            f"{in_cues}/{len(wtl)} words reach cues")


def _text_layers(plan, words):
    """Every burned text layer as (t0, t1, y0, y1, label), mirroring the
    geometry _burn_final actually renders (same y anchors, same wrap)."""
    from . import captions, speech
    from .finalize import _shifted_words
    comp = plan.get("composition")
    layout = comp.get("cam") if comp else None
    hook_y, cue_y = (40, 820) if layout == "split" else (210, 1430)
    layers = []
    hook = plan.get("hook") or {}
    if hook.get("text"):
        h = captions.block_height(hook["text"], width=980, size=74)
        layers.append((0.0, float(hook.get("show_s", 3.6)),
                       hook_y, hook_y + h, "hook"))
    for o in plan.get("overlay_lines") or []:
        t0 = float(o["t"])
        h = captions.block_height(o["text"], width=980, size=74)
        layers.append((t0, t0 + float(o.get("show_s", 3.0)),
                       hook_y, hook_y + h, f"overlay@{t0:.0f}s"))
    payoff = plan.get("payoff") or {}
    if payoff.get("jump") and payoff.get("local_t") is not None:
        t0 = float(payoff["local_t"])
        chip_y = 970 if layout == "split" else 1310
        h = captions.block_height(">> " + payoff.get("chip", "moments later"),
                                  width=560, size=42, stroke_w=5)
        layers.append((t0, t0 + 1.6, chip_y, chip_y + h, "payoff_chip"))
    if plan.get("captions") != "none":
        wtl, _ = _shifted_words(words, plan["segments"])
        for s, e, wlist in speech.group_cue_words(wtl):
            text = " ".join(w["t"] for w in wlist)
            h = captions.block_height(text, width=960, size=56)
            layers.append((s, e, cue_y, cue_y + h, f"cue@{s:.1f}s"))
    return layers


def caption_collisions(plan, words, t_eps=0.05):
    """No two burned text layers may occupy the same screen space at the same
    time (gap 2026-07-31: a delivered cut rendered two caption layers on top
    of each other into unreadable mush; caption_coverage passed it 62/62
    because every word had a cue). Checks BOTH failure modes: cue windows
    overlapping in time, and any two layers colliding in time AND vertical
    band as actually rendered."""
    layers = _text_layers(plan, words)
    bad = []
    cues = sorted(l for l in layers if l[4].startswith("cue@"))
    for a, b in zip(cues, cues[1:]):
        if b[0] < a[1] - t_eps:
            bad.append(f"cues overlap in time: {a[4]} runs to {a[1]:.2f}s, "
                       f"{b[4]} starts at {b[0]:.2f}s")
    for i in range(len(layers)):
        for j in range(i + 1, len(layers)):
            a, b = layers[i], layers[j]
            if a[4].startswith("cue@") and b[4].startswith("cue@"):
                continue  # cue-vs-cue is the temporal check above
            t_over = min(a[1], b[1]) - max(a[0], b[0])
            y_over = min(a[3], b[3]) - max(a[2], b[2])
            if t_over > t_eps and y_over > 0:
                bad.append(f"{a[4]} and {b[4]} collide on screen for "
                           f"{t_over:.1f}s (bands {a[2]}-{a[3]} / {b[2]}-{b[3]}px)")
    if bad:
        return ("caption_collision", "FAIL", "; ".join(bad[:4]))
    return ("caption_collision", "PASS",
            f"{len(layers)} text layer(s), no temporal or spatial collisions")


# M9 voice/attribution: the managed account's own account never speaks in the first person as a
# third-party creator. First-person-singular in the ASK or the message is the
# tell (paid for: a CTA read "See my managed account prices" — managed account posing as a creator).
_FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'll|i'd|me|my|mine|myself)\b", re.I)


def _voice_violations(plan):
    """First-person-creator voice in any authored field (M9)."""
    hook = plan.get("hook")
    hook = hook.get("text", "") if isinstance(hook, dict) else (hook or "")
    pay = (plan.get("payoff") or {}).get("text", "") if isinstance(plan.get("payoff"), dict) else ""
    fields = {"hook": hook, "payoff": pay, "cta": plan.get("cta") or "",
              "caption": plan.get("caption") or ""}
    bad = []
    for name, text in fields.items():
        m = _FIRST_PERSON.search(text)
        if m:
            bad.append(f"{name} speaks first-person ({m.group(0)!r} in {text!r}); "
                       "the managed account's account is not a third-party creator (M9)")
    return bad


def copy_contract(plan):
    """M8: on brand, fun, one clear CTA, short. M9: the managed account's voice, not a creator's.

    The mechanical half of M8 is checked here (lengths, single ask, caption
    truncation) plus the M9 voice/attribution rule (no first-person-creator
    voice); the rest of voice is the brain's job and the human screen's catch."""
    hook = plan.get("hook")
    if isinstance(hook, dict):
        hook = hook.get("text", "")
    if not hook:
        return ("copy_contract", "SKIP", "no authored copy fields")
    bad = []
    if len(hook.split()) > 7:
        bad.append(f"hook {len(hook.split())} words (max 7)")
    pay = (plan.get("payoff") or {}).get("text", "") if isinstance(plan.get("payoff"), dict) else ""
    if pay and len(pay.split()) > 8:
        bad.append(f"payoff {len(pay.split())} words (max 8)")
    cta = (plan.get("cta") or "").strip()
    if not cta:
        bad.append("no CTA (one clear ask required)")
    elif len(cta.split()) > 4:
        bad.append(f"cta {len(cta.split())} words (max 4)")
    cap = (plan.get("caption") or "").strip()
    if cap and len(cap) > 125:
        bad.append(f"caption {len(cap)} chars (hook must fit first 125)")
    bad += _voice_violations(plan)
    if bad:
        return ("copy_contract", "FAIL", "; ".join(bad))
    return ("copy_contract", "PASS", "hook/payoff/cta/caption within contract, managed account voice")


def hook_from_frame1(root, plan):
    """H-rule for motion posts: the hook is readable on FRAME 1 (which is also
    the cover frame) and holds; entrances that fade in miss the frame."""
    spec_p = os.path.join(root, "edl", "overlay_specs.json")
    if not os.path.exists(spec_p):
        return ("hook_frame1", "SKIP", "no overlay specs")
    events = json.load(open(spec_p)).get(plan["id"], [])
    for ev in events:
        t = ev.get("t", [None])
        if t and t[0] is not None and float(t[0]) <= 0.01 and ev.get("fade_in", True) is False:
            return ("hook_frame1", "PASS", "an overlay starts at t=0 at full strength")
    return ("hook_frame1", "FAIL",
            "no overlay is readable on frame 1 (start one at t=0 with fade_in:false)")


# The closing card must start at least this far after the measured payoff
# anchor (smaller than overlays.ENDCARD_BREATH_S so honest rounding in the
# spec never trips the gate; a card ON the payoff misses it by a lot).
ENDCARD_MIN_AFTER_ANCHOR_S = 0.2


def _spec_data(root, plan_id):
    """(events, meta) for one cut from overlay_specs.json, or (None, None)."""
    p = os.path.join(root, "edl", "overlay_specs.json")
    if not os.path.exists(p):
        return None, None
    specs = json.load(open(p))
    return specs.get(plan_id) or [], (specs.get("_meta") or {}).get(plan_id) or {}


def endcard_timing(root, plan):
    """Screening fix 2026-08-03: the closing card must start AFTER the
    measured payoff anchor, judged from the artifacts + overlay_specs --
    the exact numbers the render used, not the plan's estimates. Degrades
    to WARN when no anchor could be measured (missing analysis): the gate
    never blocks on absent data, it blocks on measured spoilage."""
    events, meta = _spec_data(root, plan["id"])
    if events is None:
        return ("endcard_timing", "SKIP", "no overlay specs")
    cards = [ev for ev in events if ev.get("role") == "endcard"] or \
        [ev for ev in events
         if ev.get("template") in ("kitcard", "badge", "lowerthird")
         and ev.get("fade_out", True) is False]
    if not cards:
        return ("endcard_timing", "SKIP", "no closing card event")
    t0 = float(cards[0]["t"][0])
    anc = (cards[0].get("anchor") or meta.get("payoff_anchor")
           or plan.get("payoff_anchor") or {})
    if not anc.get("resolved") or anc.get("t_anchor") is None:
        return ("endcard_timing", "WARN",
                f"payoff anchor unresolved (missing analysis artifacts); "
                f"card starts {t0:.2f}s on plan-based timing")
    need = float(anc["t_anchor"]) + ENDCARD_MIN_AFTER_ANCHOR_S
    if t0 + 1e-6 < need:
        return ("endcard_timing", "FAIL",
                f"card starts {t0:.2f}s but the measured payoff anchor "
                f"({anc.get('kind', '?')}) is {float(anc['t_anchor']):.2f}s: "
                f"card must start >= {need:.2f}s")
    return ("endcard_timing", "PASS",
            f"card {t0:.2f}s >= anchor {float(anc['t_anchor']):.2f}s "
            f"+ {ENDCARD_MIN_AFTER_ANCHOR_S}s (kind {anc.get('kind', '?')})")


# Designed endings (2026-08-03): the darkened outro card must be the last
# thing on screen. The backdrop is the final content frame at -0.28
# brightness through a heavy blur (or the ink gradient) under a ~60% scrim
# card, so the deliverable's last frames measure DARK; a bright last frame
# means the outro never made it into the file.
OUTRO_MAX_LAST_LUMA = 115.0
OUTRO_DUR_TOL = 0.75


def _frame_luma(path, t):
    """Mean luma (0-255) of the frame at t, or None when unreadable."""
    import tempfile
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        png = os.path.join(td, "f.png")
        r = subprocess.run([config.FFMPEG, "-y", "-v", "error",
                            "-ss", f"{max(0.0, t):.3f}", "-i", path,
                            "-frames:v", "1", png], capture_output=True)
        if r.returncode != 0 or not os.path.exists(png):
            return None
        try:
            im = Image.open(png).convert("L")
            hist = im.histogram()
            total = sum(hist) or 1
            return sum(i * n for i, n in enumerate(hist)) / total
        except Exception:   # noqa: BLE001
            return None


def outro_present(plan, path):
    """The deliverable must END with the appended outro segment when the
    plan carries one: full duration on file AND a dark card-like last
    second. Replaces endcard_timing's role for outro-architecture renders
    (those plans schedule no card events, so endcard_timing SKIPs)."""
    from . import outro as outro_mod
    ob = plan.get("outro")
    if not ob:
        return ("outro_present", "SKIP", "plan has no outro segment (legacy)")
    if not outro_mod.enabled():
        return ("outro_present", "SKIP", "REELLY_OUTRO=off")
    try:
        dur = float(media.probe(path)["format"]["duration"])
    except Exception:   # noqa: BLE001
        return ("outro_present", "FAIL", "file duration unreadable")
    want = float(plan["duration_s"])
    content = outro_mod.content_len(plan)
    if dur < content + float(ob.get("len_s", 0.0)) - OUTRO_DUR_TOL:
        return ("outro_present", "FAIL",
                f"file runs {dur:.1f}s but content+outro plans {want:.1f}s: "
                f"the outro segment is missing or truncated")
    luma = _frame_luma(path, dur - 0.4)
    if luma is None:
        return ("outro_present", "WARN",
                f"duration ok ({dur:.1f}s) but the last-second frame could "
                f"not be read for the card check")
    if luma > OUTRO_MAX_LAST_LUMA:
        return ("outro_present", "FAIL",
                f"last-second frame luma {luma:.0f} > {OUTRO_MAX_LAST_LUMA:.0f}: "
                f"the file does not end on the darkened outro card")
    return ("outro_present", "PASS",
            f"outro on file: {dur:.1f}s vs plan {want:.1f}s, last-second "
            f"luma {luma:.0f}")


# Tail hygiene: the shipped file's CONTENT must
# not end on a clipped sound onset -- silence, then a new loud sound whose
# first milliseconds the content->outro boundary cuts off (loudness gain
# turns it into a blast). The vision ending check cannot catch this: it
# watches video, not audio. Same detector as the render-time trim.
TAIL_GATE_SCAN_S = 0.35


def clean_audio_tail(plan, path):
    """FAIL when a rising onset is being cut at the content end of the
    shipped file; WARN when the content bounds cannot be determined."""
    from . import audiotail
    from . import outro as outro_mod
    try:
        dur = float(media.probe(path)["format"]["duration"])
        end = float(outro_mod.content_len(plan))
    except Exception:   # noqa: BLE001
        return ("clean_audio_tail", "WARN",
                "content bounds could not be determined")
    if not (0.5 < end <= dur + 0.5):
        return ("clean_audio_tail", "WARN",
                f"content end {end:.1f}s outside the file ({dur:.1f}s); "
                f"content bounds could not be determined")
    end = min(end, dur)
    try:
        env = audiotail.envelope(path, max(0.0, end - TAIL_GATE_SCAN_S), end)
    except Exception:   # noqa: BLE001
        env = []
    if not env:
        return ("clean_audio_tail", "WARN",
                "content tail audio could not be decoded")
    hit = audiotail.clipped_onset(env, end)
    if hit:
        return ("clean_audio_tail", "FAIL",
                f"a rising onset is cut at the content end ({end:.2f}s): "
                f"RMS {hit['onset_rms']:.0f} -> {hit['last_rms']:.0f} "
                f"after {hit['silence_s']:.2f}s of silence; content should "
                f"end at {hit['new_end']:.2f}s")
    return ("clean_audio_tail", "PASS",
            f"content tail clean at {end:.2f}s "
            f"(final-window RMS {env[-1][1]:.0f})")


def ending_verdict(root, plan):
    """Surface the semantic ending check (ending_check.py) as a gate. FAIL
    `ending_incomplete` when the vision model said the payoff does not
    complete on screen even after the one allowed adjustment; WARN when a
    verdict could not be obtained; SKIP when the check has not run."""
    from . import outro as outro_mod
    v = (outro_mod.load_verdicts(root).get(plan["id"]) or {}).get("final")
    if not plan.get("outro"):
        return ("ending_complete", "SKIP", "legacy plan (no outro block)")
    if v is None:
        return ("ending_complete", "SKIP",
                "no ending-check verdict recorded (REELLY_ENDING_CHECK=off "
                "or the check has not run)")
    if v.get("complete") is True:
        return ("ending_complete", "PASS",
                f"model: payoff completes on screen ({v.get('reason', '')})")
    if v.get("complete") is False:
        reason = v.get("reason") or (
            "payoff does not complete on screen before the content ends")
        return ("ending_complete", "FAIL", f"ending_incomplete: {reason}")
    return ("ending_complete", "WARN",
            f"ending verdict unavailable ({v.get('reason', '?')})")


def reveal_spoiler(plan):
    """No reveal-role overlay line may start before the moment it describes
    (its resolved anchor). Teases are exempt by definition -- they precede
    their event on purpose -- and lines whose anchor could not be resolved
    WARN rather than block (absent data is not evidence of spoilage)."""
    lines = [o for o in plan.get("overlay_lines") or []
             if o.get("role") == "reveal"]
    if not lines:
        return ("reveal_spoiler", "SKIP", "no reveal-role overlay lines")
    bad, unresolved = [], []
    for o in lines:
        a = o.get("anchor")
        if a is None:
            unresolved.append(o.get("text", "?"))
            continue
        if float(o["t"]) + 0.05 < float(a):
            bad.append(f"{o.get('text', '?')!r} at {float(o['t']):.2f}s "
                       f"precedes its moment at {float(a):.2f}s")
    if bad:
        return ("reveal_spoiler", "FAIL", "; ".join(bad[:3]))
    if unresolved:
        return ("reveal_spoiler", "WARN",
                "reveal anchor unresolved for: "
                + "; ".join(repr(t) for t in unresolved[:3]))
    return ("reveal_spoiler", "PASS",
            f"{len(lines)} reveal line(s) start at or after their moments")


def run(project, tag=None, visual=False):
    from . import speech
    root = direct.resolve_project(project)
    sfx = f"_{tag}" if tag else ""
    plans_p = os.path.join(root, "edl", f"cut_plans{sfx}.json")
    plans = {p["id"]: p for p in json.load(open(plans_p))} if os.path.exists(plans_p) else {}
    words = speech.words_from(os.path.join(root, "analysis", "words.json"))
    blocked = clearance.blocked_ranges(root)
    reports, fails = [], 0

    tpath = timing.timings_path(root)
    fin = os.path.join(root, "deliverables", f"final{sfx}")

    def _final_report(f):
        """Full analysis for one deliverable. The remaining decode passes
        (combined filter analysis, monotony sampling, loopable frames) are
        independent, so they run concurrently inside the file's own pool."""
        from . import outro as outro_mod
        path = os.path.join(fin, f)
        plan = plans.get(f[:6])  # cut_NN
        with timing.stage(f"judge {f} analysis", tpath):
            with ThreadPoolExecutor(max_workers=3) as ex:
                # expected duration honours REELLY_OUTRO=off (content only)
                fu_rep = ex.submit(check_file, path, True,
                                   outro_mod.expected_duration(plan)
                                   if plan else None)
                fu_mono = ex.submit(retention.monotony, path)
                fu_loop = ex.submit(retention.loopable, path)
                rep, mono, loop = (fu_rep.result(), fu_mono.result(),
                                   fu_loop.result())
        if plan:
            rep["results"].append(caption_coverage(plan, words))
            rep["results"].append(caption_collisions(plan, words))
            rep["results"].append(plain.verdict(plan))
            rep["results"].append(retention.bait(plan))
            rep["results"].append(safety.verdict(plan))
            rep["results"].append(clearance.verdict(plan, blocked))
            rep["results"].append(endcard_timing(root, plan))
            rep["results"].append(reveal_spoiler(plan))
            rep["results"].append(outro_present(plan, path))
            rep["results"].append(clean_audio_tail(plan, path))
            rep["results"].append(ending_verdict(root, plan))
            if plan.get("planned_from") == "image":
                rep["results"].append(copy_contract(plan))
                rep["results"].append(hook_from_frame1(root, plan))
        # Retention checks are WARN-level: they inform the human, they do
        # not block a cut that may be working for other reasons.
        rep["results"].append(mono)
        rep["results"].append(loop)
        print(f"[judge] {f}: analyzed")
        return rep

    with timing.stage(f"judge.run{sfx}", tpath):
        if os.path.isdir(fin):
            files = [f for f in sorted(os.listdir(fin)) if f.endswith(".mp4")]
            if files:  # pool.map preserves report order per input order
                with ThreadPoolExecutor(max_workers=3) as pool:
                    reports.extend(pool.map(_final_report, files))

        lf = os.path.join(root, "deliverables", "longform")
        if os.path.isdir(lf):
            lfs = [f for f in sorted(os.listdir(lf)) if f.endswith(".mp4")]
            if lfs:
                with ThreadPoolExecutor(max_workers=3) as pool:
                    reports.extend(pool.map(
                        lambda f: check_file(os.path.join(lf, f),
                                             expect_vertical=False), lfs))

    # perceptual pass (WARN-level: advises the human, never blocks shipping)
    visual_findings = {}
    if visual:
        from . import visual_qc
        visual_findings = visual_qc.review(root, tag)
        for rep in reports:
            for issue in visual_findings.get(os.path.basename(rep["file"]), []):
                rep["results"].append(("visual_join", "WARN", issue))

    qc = os.path.join(root, "qc")
    os.makedirs(qc, exist_ok=True)
    L = ["# QC report", ""]
    for rep in reports:
        bad = [r for r in rep["results"] if r[1] == "FAIL"]
        fails += len(bad)
        L.append(f"## {os.path.basename(rep['file'])}  "
                 f"{'FAIL' if bad else 'PASS'}")
        for gate, status, detail in rep["results"]:
            mark = {"PASS": "ok", "WARN": "warn", "FAIL": "FAIL",
                    "SKIP": "n/a"}[status]
            L.append(f"- {gate}: {mark} ({detail})")
        L.append("")
    open(os.path.join(qc, f"qc_report{sfx}.md"), "w").write("\n".join(L))
    json.dump(reports, open(os.path.join(qc, f"qc_report{sfx}.json"), "w"),
              indent=1, default=str)
    print("\n".join(L))
    print(f"[judge] {len(reports)} files, {fails} failing gates -> {qc}/qc_report{sfx}.md")
    return fails
