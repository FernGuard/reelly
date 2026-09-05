"""M4 finalize: post-ready renders with zero manual steps.

Takes a cut plan and produces deliverables/final/<id>.mp4: composed video
(same layout as previews), S6 voice stem, generated music bed ducked under
the voice, sparse SFX on the moments that earn them (S3), word-highlight
captions, hook, payoff chip, product end tag — plus DESCRIPTION.md with the
channel-correct tracked links and the posting checklist. Stems land in
deliverables/audio/ so everything stays editable.
"""
import hashlib
import json
import os
import subprocess
import tempfile
import threading

from . import audio_post, audiotail, captions, config, direct, face, media, products, speech, timing
from .preview import _cut_segments, _facecam_source, _se, run_parallel


def _shifted_words(words, segments):
    shifted, off = [], 0.0
    for seg in segments:
        s, e, speed = _se(seg)
        for w in words:
            # midpoint inclusion: whisper word ends can drift past a snapped
            # cut even when the word is audibly inside (lost "cool." verdict)
            if s <= (w["s"] + w["e"]) / 2 <= e:
                shifted.append({"t": w["t"],
                                "s": max(0.0, w["s"] - s) / speed + off,
                                "e": (min(w["e"], e) - s) / speed + off})
        off += (e - s) / speed
    return shifted, off


# A 45s karaoke cut is ~120 overlay inputs; one serial filtergraph that size
# is the slow path. Past this input count the burn splits into time chunks
# (burnin.py's longform approach) and concatenates with -c copy.
BURN_CHUNK_INPUTS = 80
BURN_CHUNK_S = 75.0     # target chunk length, seconds


def _pointer_events(plan, words_tl, workdir, video):
    """Opt-in transcript-driven POINTER overlays (circle/brackets) as full-frame
    PNG burn rows (reviewer 2026-08-13). For a voiced/tutorial cut, points at the
    thing the narrator names, located by vision. Off unless plan['pointers'] or
    REELLY_POINTERS is set. Fully guarded: any failure yields no rows, never
    breaks the burn."""
    if not (video and words_tl
            and (plan.get("pointers") or os.environ.get("REELLY_POINTERS", "").strip())):
        return []
    try:
        from PIL import Image
        from . import overlays

        def frame_at(t):
            fp = os.path.join(workdir, "ptr_probe.png")
            subprocess.run([config.FFMPEG, "-y", "-v", "error",
                            "-ss", f"{max(0.0, t):.2f}", "-i", video, "-frames:v", "1",
                            "-vf", "scale=1080:1920", fp], check=True)
            return Image.open(fp)

        rows = []
        for i, pe in enumerate(overlays.plan_pointers(
                video, words_tl, project=plan.get("id", ""), frame_at=frame_at)):
            body = overlays.TEMPLATES[pe["template"]](*pe["args"])
            png = overlays._render_png(workdir, f"ptr{i}", body, size=(1080, 1920))
            rows.append((png, 0, pe["t"][0], pe["t"][1]))
        if rows:
            print(f"        {plan.get('id', '?')}: {len(rows)} pointer overlay(s)")
        return rows
    except Exception as e:                              # never break the burn
        print(f"[finalize] pointer overlays skipped ({e})")
        return []


def _burn_events(plan, words_tl, dur, workdir, layout, product_key, video=None):
    """Every overlay the final burn composites, as (png, y, t0, t1) rows.

    Building the list first (instead of appending straight into a filtergraph)
    lets the burn run either as one pass or chunked, and lets identical word
    PNGs render once.
    """
    # Split layout stacks the facecam on top and the screen recording below,
    # with a blurred SEAM gap between them (preview._split_filter). Captions
    # must live in that gap -- never on the face at the top (reviewer
    # 2026-08-16) and never over the screen content: hook at the top of the
    # gap, karaoke at the bottom of it.
    # SINGLE LAYOUT AUTHORITY (layout.py): the burn layer and the gfx layer
    # (overlays) both read text positions from here, so they cannot disagree
    # and collide (reviewer 2026-08-16 root-cause fix). No local band math.
    from . import layout as _layout
    L = _layout.plan_text(plan, layout)
    hook_y = L.get("hook", {}).get("y", 210)
    cue_y = L.get("karaoke", {}).get("y", 1430)
    gname_y = L.get("game_name", {}).get("y", 300)
    tag_y = L.get("endtag", {}).get("y", 940)
    chip_y = cue_y - 4
    events = []

    # On-screen game/show attribution (reviewer 2026-08-16: name the game ON
    # the video, not only in the post text). Small persistent label at the
    # bottom of the seam gap -- clear of the hook (gap top) and the karaoke
    # (below the screen).
    gname = str(plan.get("game_name") or "").strip()
    if gname:
        gp = captions.text_png(gname, os.path.join(workdir, "gamename.png"),
                               width=760, size=40, fill="#FCFCFB", stroke_w=6)
        events.append((gp, gname_y, 0.0, dur))

    # NO BURN-TIME END-CARD SCRIM (2026-08-02). The closing card's dark pass
    # now lives in the OVERLAY layer, in one place: kit endcards are RGBA
    # (translucent ~60% scrim + wordmark + CTA baked at kit-build time) and
    # the legacy badge/lowerthird paths get a companion scrim event
    # (overlays.card_scrim_event). Burning a second 80% scrim here stacked to
    # near-black under the new translucent cards -- the exact opposite of the
    # decided look, a dark translucent scrim over the STILL-PLAYING video --
    # and on cuts where the burn window and the card window disagreed
    # (stale specs), logos rendered with no darkening at all. One layer, one
    # owner. The non-gfx variant ships with no card and now no scrim either:
    # a dimmed tail with nothing on it was serving nobody.

    hook = plan.get("hook") or {}
    if hook.get("text"):
        hp = captions.hook_png(hook["text"], os.path.join(workdir, "hook.png"))
        events.append((hp, hook_y, 0.0, float(hook.get("show_s", 3.6))))

    # P-TEXT: timed lines beyond the hook; on captions:"none" cuts they carry
    # all of the writing.
    for oi, o in enumerate(plan.get("overlay_lines") or []):
        op = captions.hook_png(o["text"], os.path.join(workdir, f"ov{oi}.png"))
        t0 = float(o["t"])
        events.append((op, hook_y, t0, t0 + float(o.get("show_s", 3.0))))

    payoff = plan.get("payoff") or {}
    if payoff.get("jump") and payoff.get("local_t") is not None:
        chip = captions.text_png(">> " + payoff.get("chip", "moments later"),
                                 os.path.join(workdir, "chip.png"),
                                 width=560, size=42, fill="#17cdff", stroke_w=5)
        t0 = float(payoff["local_t"])
        events.append((chip, chip_y, t0, t0 + 1.6))

    # captions:"none" — see preview._render. Non-narration audio (game VO,
    # music) makes ASR invent lines; those must never reach a deliverable.
    cue_groups = ([] if plan.get("captions") == "none"
                  else speech.group_cue_words(words_tl))
    kcache = {}   # (word texts, highlight index) -> png; repeats render once
    for ci, (s, e, wlist) in enumerate(cue_groups):
        texts = tuple(x["t"] for x in wlist)
        for wi, w in enumerate(wlist):
            png = kcache.get((texts, wi))
            if png is None:
                png = captions.karaoke_png(list(texts), wi,
                                           os.path.join(workdir, f"k{ci}_{wi}.png"))
                kcache[(texts, wi)] = png
            w_end = wlist[wi + 1]["s"] if wi + 1 < len(wlist) else max(e, w["e"])
            events.append((png, cue_y, w["s"], w_end))

    # One closing message. When the plan carries a CTA, the overlay layer draws
    # a single end card (logo + the ask) and this attribution line is suppressed:
    # two blocks both naming example.invalid, fired at the same second, compete for the
    # one decision we actually want (G1).
    if product_key and not (plan.get("cta") or "").strip():
        tag = captions.text_png(products.PRODUCTS[product_key]["end_tag"],
                                os.path.join(workdir, "endtag.png"),
                                width=900, size=44, fill="#FCFCFB", stroke_w=5)
        events.append((tag, tag_y, max(0, dur - 2.2), dur))

    # Opt-in pointer overlays (circle/brackets) pointing at what the narrator
    # names -- transcript-driven, vision-located, guarded.
    events += _pointer_events(plan, words_tl, workdir, video)
    return events


def _burn_chunks(events, dur, chunk_s=BURN_CHUNK_S):
    """[(start, end, [local events])] time chunks for a large burn.

    Overlays spanning a boundary appear in both chunks with clipped, chunk-local
    times: the same PNG at the same position on both sides of a -c copy concat
    is visually continuous. A tail shorter than 5s merges into the last chunk
    so no chunk is a sliver.
    """
    out, s = [], 0.0
    while s < dur:
        e = min(dur, s + chunk_s)
        if dur - e < 5.0:
            e = dur
        local = [(png, y, max(0.0, t0 - s), min(e, t1) - s)
                 for png, y, t0, t1 in events if t1 > s and t0 < e]
        out.append((s, e, local))
        s = e
    return out


def _burn_pass(src, dst, events, seek=None):
    """One encode: overlay each event PNG over its window. seek=(s,e) burns
    only that slice of src (chunked mode), with event times already local."""
    inputs = [config.FFMPEG, "-y", "-v", "error"]
    if seek:
        inputs += ["-ss", f"{seek[0]:.3f}", "-to", f"{seek[1]:.3f}"]
    inputs += ["-i", src]
    filters, last, idx = [], "0:v", 1
    for png, y, t0, t1 in events:
        inputs.extend(["-i", png])
        filters.append(f"[{last}][{idx}:v]overlay=(W-w)/2:{y}:"
                       f"enable='between(t,{t0:.2f},{t1:.2f})'[v{idx}]")
        last = f"v{idx}"
        idx += 1
    if filters:
        inputs += ["-filter_complex", ";".join(filters), "-map", f"[{last}]"]
    else:
        inputs += ["-map", "0:v"]
    inputs += ["-an", "-r", "30",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", dst]
    subprocess.run(inputs, check=True)
    return dst


def _burn_final(src, dst, plan, words_tl, dur, workdir, layout, product_key):
    """Video-only pass: hook, karaoke cues, payoff chip, end tag."""
    events = _burn_events(plan, words_tl, dur, workdir, layout, product_key, video=src)
    if len(events) <= BURN_CHUNK_INPUTS:
        return _burn_pass(src, dst, events)
    chunks = _burn_chunks(events, dur)
    print(f"        {plan['id']}: {len(events)} overlays, "
          f"burning in {len(chunks)} chunks")
    parts = []
    for i, (s, e, local) in enumerate(chunks):
        part = os.path.join(workdir, f"burnpart{i:03d}.mp4")
        parts.append(_burn_pass(src, part, local, seek=(s, e)))
    lst = os.path.join(workdir, "burnparts.txt")
    open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-f", "concat",
                    "-safe", "0", "-i", lst, "-c", "copy", dst], check=True)
    return dst


# Bump whenever crop/framing code changes in a way that alters rendered raw
# pixels without moving any other key input (e.g. face-crop math, reframe
# geometry). Old sidecars simply stop matching -- that's the point.
RAW_CACHE_VERSION = 2


def _raw_cache_key(plan, video, facecam, follow):
    """Cache key for a cut's concatenated pre-caption raw.mp4: everything the
    segment render depends on. Captions/music/overlay changes do NOT move it."""
    st = os.stat(video)
    parts = {"version": RAW_CACHE_VERSION,
             "segments": plan.get("segments"),
             "comp": plan.get("composition"),
             "reframe": plan.get("reframe", 1.0),
             "follow": bool(follow),
             # facemesh vs blaze frame the facecam crop differently
             "detector": face.detector_kind(),
             # intermediate encoder selection (incl. REELLY_SW/HW_ENCODE state)
             "encode": config.intermediate_encode_args(),
             "src": [os.path.basename(video), st.st_mtime, st.st_size]}
    if facecam:
        fs = os.stat(facecam)
        parts["facecam"] = [os.path.basename(facecam), fs.st_mtime, fs.st_size]
    return hashlib.sha1(
        json.dumps(parts, sort_keys=True).encode()).hexdigest()


def _cached_raw(root, plan, sfx, key, force, render_fn):
    """Reuse deliverables/.cache/<id>_raw.mp4 when its key still matches.

    A re-run that only changed captions/music skips the whole re-cut +
    re-encode. One file per cut id (overwritten), so the cache cannot grow.
    """
    cdir = os.path.join(root, "deliverables", ".cache")
    os.makedirs(cdir, exist_ok=True)
    raw = os.path.join(cdir, f"{plan['id']}{sfx}_raw.mp4")
    meta = raw + ".json"
    if not force and os.path.exists(raw) and os.path.exists(meta):
        try:
            if json.load(open(meta)).get("key") == key:
                print(f"[final] {plan['id']}: raw cut unchanged, reusing cached "
                      "segments+concat")
                return raw
        except (ValueError, OSError):
            pass
    tmp = raw + ".part.mp4"
    # cache misses do the expensive cut+encode: gate them on the machine-wide
    # render pool so parallel projects share one budget (cache hits above
    # never queue)
    from . import slots
    with slots.hold("render", plan["id"]):
        render_fn(tmp)
    os.replace(tmp, raw)
    json.dump({"key": key, "id": plan["id"]}, open(meta, "w"))
    return raw


# finalize workers run on a thread pool; cut_plans.json is read-modify-write
_PLANS_LOCK = threading.Lock()


def _apply_tail_trim(root, sfx, plan, trim_s):
    """Move a cut's whole accounting with a render-time tail trim: the last
    segment's end, content_s, duration_s -- and persist the plan back to
    edl/cut_plans<sfx>.json so re-runs, the duration QC gate and the outro
    accounting all see the same numbers the render actually used."""
    from . import outro as outro_mod
    seg = plan["segments"][-1]
    speed = float(seg[2]) if len(seg) > 2 else 1.0
    seg[1] = round(float(seg[1]) - trim_s * speed, 2)
    content = round(outro_mod.content_len(plan) - trim_s, 2)
    plan["content_s"] = content
    ob = plan.get("outro") or {}
    try:
        o_len = float(ob.get("len_s", 0.0))
    except (TypeError, ValueError):
        o_len = 0.0
    plan["duration_s"] = round(content + o_len, 1)
    p = os.path.join(root, "edl", f"cut_plans{sfx}.json")
    with _PLANS_LOCK:
        try:
            plans = json.load(open(p))
            plans = [plan if q.get("id") == plan["id"] else q for q in plans]
            json.dump(plans, open(p, "w"), indent=1)
        except (OSError, ValueError) as e:
            print(f"[tail ] {plan['id']}: could not persist the trimmed "
                  f"plan ({e})")
    return plan


def run(project, cut_id=None, tag=None, product="video", targets_for=None,
        review=False, force=False, account=None, variants=None, on_cut=None):
    """on_cut(plan, made_paths), when given, fires on the WORKER thread the
    moment a cut's deliverables exist — the seam `cut` uses to start that
    cut's graphics layer while the remaining cuts are still rendering. It must
    be cheap (hand the work to another pool); an exception in it fails the
    cut like any other error."""
    from . import accounts
    root = direct.resolve_project(project)
    profile = accounts.for_project(root, account)
    wanted = accounts.variants_for(root, profile, variants)
    targets = products.delivery_targets(root, targets_for)
    # A human cannot judge a cut that is missing its music, captions and SFX;
    # reviewing a rough pass and then reviewing the finished file is two reviews
    # for one decision. The review copy is the music-baked master, so "master"
    # is forced into the target set even when only clean-mix platforms were
    # asked for. The clean _trending file is still what ships to TikTok/Reels.
    if review and "master" not in targets:
        targets = targets + ["master"]
    # P5 is scoped per account here: platform_spec downgrades "clean" to
    # "music" for accounts that cannot add trending audio at post time.
    needed_mixes = {products.platform_spec(t, profile)["mix"] for t in targets}
    if "clean" in needed_mixes and not any(v.startswith("trending") for v in wanted):
        print(f"[plan ] WARNING: targets expect a _trending file but variants "
              f"({', '.join(wanted)}) exclude it; skipping the clean mix")
        needed_mixes.discard("clean")
        if not needed_mixes:
            needed_mixes.add("music")
    print(f"[plan ] account: {profile['name']} | targets: {', '.join(targets)} "
          f"-> mixes: {', '.join(sorted(needed_mixes))} | "
          f"variants: {', '.join(wanted)}"
          + ("" if "music" in needed_mixes else " (no music generation needed)"))
    sfx = f"_{tag}" if tag else ""
    plans = json.load(open(os.path.join(root, "edl", f"cut_plans{sfx}.json")))
    words = speech.words_from(os.path.join(root, "analysis", "words.json"))
    video = direct._source_video(root)
    facecam = _facecam_source(root)
    outdir = os.path.join(root, "deliverables", f"final{sfx}")
    audir = os.path.join(root, "deliverables", "audio")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(audir, exist_ok=True)
    name = os.path.basename(root)
    # A camera source with no separate facecam (a recorded call) is reframed by
    # following the faces. Screen recordings keep the blurred-fill reframe: there
    # the whole screen is the content.
    follow = False
    if not facecam:
        from . import speaker
        dur_s = float(media.probe(video)["format"]["duration"])
        follow = speaker.has_faces(video, dur_s)
        print(f"[plan ] reframe: {'follow the faces' if follow else 'blurred fill'}")
    tpath = timing.timings_path(root)

    def _finalize_one(p):
        from . import outro as outro_mod
        comp = p.get("composition")
        layout = comp.get("cam") if comp and facecam else None
        print(f"[final] {p['id']}: {p['title']} ...")
        # Designed endings: plans carrying an outro block get the brand
        # outro APPENDED after the content (never overlaid on it). The mix
        # runs the full length (music bed continues, voice is padded), the
        # burn and captions stay content-only by construction.
        ob = p.get("outro") if outro_mod.enabled() else None
        o_len = float(ob.get("len_s", 0.0)) if ob else 0.0
        if p.get("outro") and not ob:
            print(f"[final] {p['id']}: REELLY_OUTRO=off -- outro skipped, "
                  f"deliverable is content-only")
        with tempfile.TemporaryDirectory() as td:
            def _render_raw(dst):
                with timing.stage(f"{p['id']} segments+concat", tpath):
                    _cut_segments(video, p["segments"], dst, comp=comp,
                                  facecam=facecam, follow_faces=follow,
                                  reframe=p.get("reframe", 1.0))
            raw = _cached_raw(root, p, sfx,
                              _raw_cache_key(p, video, facecam, follow),
                              force, _render_raw)
            # Tail hygiene safety net (cut_04 screening bug): a legacy plan
            # whose frozen content end sits on a rising sound onset ships a
            # clipped blast at the content->outro boundary. Plan-time trims
            # cover new plans; here the rendered cut is measured and the
            # burn window + all duration accounting move together.
            trim = audiotail.raw_tail_trim(raw, p.get("payoff_complete_by"),
                                           p["id"])
            if trim > 0:
                raw = audiotail.trim_file(raw, trim,
                                          os.path.join(td, "raw_tailclean.mp4"))
                _apply_tail_trim(root, sfx, p, trim)
                print(f"[tail ] {p['id']}: trimmed {trim:.2f}s clipped onset "
                      f"off the content tail")
            words_tl, dur = _shifted_words(words, p["segments"])
            full_dur = dur + o_len
            with timing.stage(f"{p['id']} voice stem", tpath):
                voice = audio_post.voice_stem(raw, os.path.join(audir, f"{p['id']}_voice.wav"))
            events = audio_post.sfx_events(p)
            with timing.stage(f"{p['id']} caption burn", tpath):
                vid = _burn_final(raw, os.path.join(td, "burn.mp4"), p, words_tl,
                                  dur, td, layout, product)
            if ob:
                with timing.stage(f"{p['id']} outro", tpath):
                    # backdrop from the PRE-CAPTION raw so no burned text
                    # bleeds into the darkened card background
                    vid = outro_mod.append(vid, raw, p, product, td)
            # delivery plan: build only the mixes the requested targets need;
            # music is generated only when a music platform asked for it
            mixes = {}
            if "music" in needed_mixes:
                with timing.stage(f"{p['id']} music/sfx", tpath):
                    mus = audio_post.music(p, os.path.join(audir, f"{p['id']}_music.mp3"), name)
                    payoff = p.get("payoff") or {}
                    beat_target = (float(payoff["local_t"]) if payoff.get("jump")
                                   else float(p["hook"].get("show_s", 3.6)))
                    off = audio_post.beat_offset(mus, beat_target)
            with timing.stage(f"{p['id']} mix", tpath):
                # pad_to keeps the outro alive through amix(duration=first)
                # and the mux's -shortest: the voice is padded with silence
                # to the full length and the ducked bed opens back up across
                # the outro (music continues, voice never does).
                pad = full_dur if ob else None
                if "music" in needed_mixes:
                    mixes[""] = audio_post.final_mix(
                        voice, mus, events, full_dur,
                        os.path.join(audir, f"{p['id']}_mix.wav"), name,
                        music_offset=off, pad_to=pad)
                if "clean" in needed_mixes:
                    mixes["_trending"] = audio_post.final_mix(
                        voice, None, events, full_dur,
                        os.path.join(audir, f"{p['id']}_mix_clean.wav"), name,
                        pad_to=pad)
            made = []
            with timing.stage(f"{p['id']} mux/peak", tpath):
                for suffix, mix in mixes.items():
                    dst = os.path.join(outdir, f"{p['id']}{suffix}.mp4")
                    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", vid, "-i", mix,
                                    "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                                    "-c:a", "aac", "-b:a", "192k", "-shortest", dst],
                                   check=True)
                    # The mix was normalised, but the AAC encode just added its own
                    # inter-sample overshoot. Verify what was actually written.
                    audio_post.enforce_true_peak(dst, source_audio=mix)
                    made.append(dst)
        products.description_md(product, p, os.path.join(outdir, f"{p['id']}_DESCRIPTION.md"),
                                targets=targets, account=profile, variants=wanted)
        print(f"[final] {p['id']} -> " + ", ".join(os.path.basename(m) for m in made)
              + " (+ stems, + DESCRIPTION.md)")
        if on_cut:
            on_cut(p, made)
        return made

    todo = [p for p in plans if not cut_id or p["id"] == cut_id]
    # Cuts are independent (own tempdir, own stems, own outputs) and the heavy
    # work is ffmpeg subprocesses + FAL HTTP, so a small thread pool wins.
    # 5 workers (was 3): the chain is dominated by subprocess/HTTP waits (music
    # queue+poll alone is 10-13s of pure wait per cut), so wider overlap beats
    # CPU contention on typical 5-8 cut sessions.
    done, failed = [], []
    for p, made, err in run_parallel(todo, _finalize_one, max_workers=5):
        if err:
            print(f"[final] {p['id']} FAILED: {err}")
            failed.append(p["id"])
        else:
            done += made
    if failed:
        raise RuntimeError("finalize failed on: " + ", ".join(failed))
    if review:
        rp = _write_review_md(root, plans, targets, sfx, cut_id)
        print(f"\nOne review, everything on: {rp}")
    return done


def retire_unshipped_bases(root, wanted, plans, sfx=""):
    """gfx-only shipping means ONE file per cut in deliverables/final.

    With variants=["gfx"] (the default) the base burned master cut_XX.mp4 --
    the file the gfx composite reads from -- used to stay next to
    cut_XX_gfx.mp4, and both looked deliverable. Once EVERY wanted gfx
    variant of a cut exists, each base whose own variant ("plain",
    "trending") was not asked for moves to deliverables/.cache/: still on
    disk for re-composites and the raw-cache economy, no longer in the ship
    directory, so REVIEW/QC/delivery only ever see files that ship.
    Returns the moved paths."""
    from . import accounts
    fin = os.path.join(root, "deliverables", f"final{sfx}")
    gfx_wanted = [v for v in wanted if v.endswith("gfx")]
    if not gfx_wanted or not os.path.isdir(fin):
        return []
    cache = os.path.join(root, "deliverables", ".cache")
    moved = []
    for p in plans:
        # only after ALL wanted gfx variants for this cut are composited:
        # the base is the composite's video/audio source until then
        if not all(os.path.exists(os.path.join(
                fin, f"{p['id']}{accounts.suffix(v)}.mp4")) for v in gfx_wanted):
            continue
        for base_v, gfx_v in (("plain", "gfx"), ("trending", "trending_gfx")):
            if base_v in wanted or gfx_v not in gfx_wanted:
                continue
            f = os.path.join(fin, f"{p['id']}{accounts.suffix(base_v)}.mp4")
            if os.path.exists(f):
                os.makedirs(cache, exist_ok=True)
                dst = os.path.join(cache, os.path.basename(f))
                os.replace(f, dst)
                moved.append(dst)
    if moved:
        print(f"[final] moved {len(moved)} base burn master(s) to "
              f"deliverables/.cache: variants ({', '.join(wanted)}) ship the "
              f"gfx file only, and the ship directory must hold only what ships")
    return moved


def _write_review_md(root, plans, targets, sfx, cut_id=None):
    """The single review surface: fully treated files, what to watch, what ships."""
    outdir = f"deliverables/final{sfx}"
    shown = [p for p in plans if not cut_id or p["id"] == cut_id]
    L = [f"# Review: {os.path.basename(root)}", "",
         "Every file below is **fully treated**: music bed, karaoke captions, "
         "hook and on-screen lines, SFX, end tag, loudness. What you watch is what ships.",
         "", "**Watch** the plain `<cut>.mp4` (music baked in). **Post** the file named "
         "per platform in each cut's `_DESCRIPTION.md`: TikTok, Reels and Shorts take the "
         "`_trending` file with no bed so in-app trending audio can ride under the voice (P5).",
         "", "Verdict each cut KEEP or KILL with one line why. KEEPs are cleared to schedule; "
         "there is no second render.", ""]
    for p in shown:
        # Always name the most-treated file that exists. Overlays composite a
        # _gfx copy after this runs, and reviewing the version without its
        # graphics layer is the same two-review mistake in a smaller costume.
        watch = f"{p['id']}_gfx.mp4"
        if not os.path.exists(os.path.join(root, outdir, watch)):
            watch = f"{p['id']}.mp4"
        L += [f"## {p['id']} — {p['title']}  ({p['duration_s']}s)", "",
              f"- **Watch:** `{outdir}/{watch}`"
              + ("  (music + captions + SFX + VFX)" if watch.endswith("_gfx.mp4")
                 else "  (music + captions + SFX; no graphics layer)"),
              f"- Hook: **{p['hook']['text']}**"]
        for o in p.get("overlay_lines") or []:
            L.append(f"- On screen at {o['t']:.0f}s: {o['text']}")
        if p.get("caption"):
            L.append(f"- Caption: {p['caption']}")
        L += [f"- Handles: {', '.join(p.get('handles') or ['NONE'])} | "
              f"captions: {p.get('captions', 'burned')} | "
              f"planned from: {p.get('planned_from', 'speech')}",
              f"- Posting block: `{outdir}/{p['id']}_DESCRIPTION.md`",
              "- **Verdict:** _pending_", ""]
    L += ["---", "", f"{len(shown)} cuts. Delivery targets: {', '.join(targets)}.", ""]
    path = os.path.join(root, "deliverables", f"REVIEW{sfx}.md")
    open(path, "w").write("\n".join(L))
    return path
