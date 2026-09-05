"""Preview renderer: rough watchable 9:16 cuts from cut plans.

Deliberately unpolished (no music, no SFX, no titles): just the planned
segments, blurred-fill reframe, burned captions, hook text, and a one-pass
loudness normalize, so a human can verdict each cut in seconds. The real
render with sound design is M4.
"""
import json
import os
import subprocess
import tempfile

from . import captions, config, direct, speech

MAX_OFFSET_S = 30.0  # beyond this, waveform sync failed rather than found a real lag


def run_parallel(items, fn, max_workers=3):
    """Run fn over items on a small thread pool; results in input order.

    Returns [(item, result, error)], one row per item. Errors are captured per
    item so one failing cut cannot kill its siblings; the caller decides how to
    report and whether to raise. The heavy work under fn is subprocess/HTTP
    bound, so threads (not processes) are the right tool.
    """
    items = list(items)
    if len(items) <= 1 or max_workers <= 1:
        out = []
        for it in items:
            try:
                out.append((it, fn(it), None))
            except Exception as e:  # noqa: BLE001 — isolate per item
                out.append((it, None, e))
        return out
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(fn, it) for it in items]
        out = []
        for it, fu in zip(items, futs):
            try:
                out.append((it, fu.result(), None))
            except Exception as e:  # noqa: BLE001
                out.append((it, None, e))
    return out


def _se(seg):
    """Normalize a segment: (start, end, speed)."""
    return seg[0], seg[1], (seg[2] if len(seg) > 2 else 1.0)


def _atempo_chain(speed):
    """atempo is happiest in factors of 2; chain to the target."""
    parts = []
    while speed > 2.0:
        parts.append("atempo=2.0")
        speed /= 2.0
    parts.append(f"atempo={speed:.4f}")
    return ",".join(parts)


def _reframe_vf(zoom=1.0):
    """16:9 source into a 9:16 frame over a blurred bed.

    zoom=1.0 fits the full width, which leaves a 16:9 source occupying only
    about a third of the phone screen. Cinematic footage with no UI to protect
    can afford to lose the sides: zoom scales up and center-crops back to 1080,
    trading horizontal framing for a taller, more legible content band.
    zoom=1.78 makes it square; ~3.16 fills the frame. Cap at 2x per CO7 so the
    content stays legible."""
    zoom = max(1.0, min(float(zoom or 1.0), 2.0))
    fg = ("[fg]scale=1080:-2[fgs];" if zoom == 1.0 else
          f"[fg]scale=w={round(1080 * zoom)}:h=-2,"
          f"crop=1080:ih:(iw-1080)/2:0[fgs];")
    return ("split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=24:2,eq=brightness=-0.05:saturation=1.15[bgb];"
            + fg +
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2")


def _cam_filter(comp, seg_start, seg_dur, mask_idx=2, cam_idx=1):
    """Facecam branch: center-square crop, circle alpha, small + feature sizes.

    Returns the filter tail that expects [base] as the composed screen video
    and produces [vout]. Feature windows are in local segment time.
    """
    d, fd = comp["d"], comp["feature_d"]
    x, y_small, y_big = 64, 1420 - d, 1420 - fd
    wins = []
    for f in comp["features"]:
        t0 = f["t_src"] - f["dur"] / 2 - seg_start
        t1 = t0 + f["dur"]
        if t1 > 0 and t0 < seg_dur:
            wins.append((max(0, t0), min(seg_dur, t1)))
    cw, cx_, cy_ = comp.get("crop", (None, None, None))
    crop = f"crop={cw}:{cw}:{cx_}:{cy_}" if cw else "crop='min(iw,ih)':'min(iw,ih)'"
    g = comp.get("cam_grade")
    cam_sq = (f"[{cam_idx}:v]{crop},{g + ',' if g else ''}scale=512:512[camsq];"
              f"[camsq][{mask_idx}:v]alphamerge")
    if not wins:  # single persistent size; never create an unconsumed branch
        return (f"{cam_sq},scale={d}:{d}[cams];"
                f"[base][cams]overlay={x}:{y_small}[vout]")
    feat_en = "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in wins)
    return (f"{cam_sq},split=2[camA][camB];"
            f"[camA]scale={d}:{d}[cams];"
            f"[camB]scale={fd}:{fd}[camf];"
            f"[base][cams]overlay={x}:{y_small}:enable='not({feat_en})'[wcam];"
            f"[wcam][camf]overlay={x}:{y_big}:enable='{feat_en}'[vout]")


def _split_filter(comp):
    """Researched winner layout (CO1): face-centered cam top band, screen below,
    content band zoomed to the active screen region when one exists (CO7)."""
    ch = comp.get("cam_h", 768)
    w, h, x, y = comp["crop"]
    act = comp.get("screen_crop")
    if act:
        ax, ay, aw, ah = act
        out_h = int(1080 * ah / aw) // 2 * 2
        fg = f"[fg]crop={aw}:{ah}:{ax}:{ay},scale=1080:-2[fgs];"
    else:
        out_h = 608
        fg = "[fg]scale=1080:-2[fgs];"
    scr_y = ch + max(0, (1920 - ch - out_h) // 2)
    return (f"[0:v]split=2[bg][fg];"
            f"[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,boxblur=24:2,eq=brightness=-0.05:saturation=1.15[bgb];"
            + fg +
            f"[bgb][fgs]overlay=(W-w)/2:{scr_y}[scr];"
            f"[1:v]crop={w}:{h}:{x}:{y},"
            f"{comp['cam_grade'] + ',' if comp.get('cam_grade') else ''}"
            # clone the first cam frame backward: after a seek the cam stream
            # can start 1-2 frames later than the screen, and overlay then
            # emits cam-less frames (a blurred-bg flash) at every join
            f"scale=1080:{ch},tpad=start_mode=clone:start_duration=0.12[cam];"
            f"[scr][cam]overlay=0:0[vout]")


def _safe_offset(comp):
    """Facecam offset, rejecting values the sync search could not legitimately produce.

    OBS source-record starts the cam with the recording, so a real offset is
    small. A large one means waveform sync failed (silent cam track pins the
    lag to the search-window edge) and the garbage got frozen into a cut plan.
    """
    off = comp.get("offset_s", 0.0) or 0.0
    if abs(off) > MAX_OFFSET_S:
        print(f"[sync] implausible facecam offset {off:+.3f}s in cut plan, using 0.0")
        return 0.0
    return off


def _face_shots(video, s, e, td, i):
    """Vertical parts that follow whoever is on screen (and, of two, whoever talks)."""
    from . import media, speaker
    st = media.probe(video)["streams"]
    v0 = next(x for x in st if x.get("codec_type") == "video")
    wh = (v0["width"], v0["height"])
    parts = []
    for j, (a, b, (cw, ch, cx, cy)) in enumerate(speaker.shots(video, s, e, wh)):
        p = os.path.join(td, f"seg{i}_{j}.mp4")
        subprocess.run([config.FFMPEG, "-y", "-v", "error", *config.hwdecode_args(),
                        "-ss", str(a), "-to", str(b),
                        "-i", video,
                        "-vf", f"crop={cw}:{ch}:{cx}:{cy},scale=1080:1920,setsar=1",
                        "-r", "30", *config.intermediate_encode_args(),
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", p], check=True)
        parts.append(p)
    return parts


def _cut_segments(video, segments, dst, comp=None, facecam=None, workdir=None,
                  follow_faces=False, reframe=1.0):
    """Extract + reframe each segment (cam composited if present), then concat."""
    from . import media as _media
    sdr_v = _media.sdr_chain(video)
    sdr_cam = _media.sdr_chain(facecam) if facecam else ""

    def _tonemapped(fc):
        # rewrite raw input taps through a tonemap stage when the source is HDR
        pre = ""
        if sdr_v and "[0:v]" in fc:
            pre += f"[0:v]{sdr_v}[v0sdr];"
            fc = fc.replace("[0:v]", "[v0sdr]")
        if sdr_cam and "[1:v]" in fc:
            pre += f"[1:v]{sdr_cam}[v1sdr];"
            fc = fc.replace("[1:v]", "[v1sdr]")
        return pre + fc

    with tempfile.TemporaryDirectory() as td:
        layout = comp.get("cam") if comp and facecam else None
        if comp:
            comp = {**comp, "offset_s": _safe_offset(comp)}
        mask = (captions.cam_mask(layout, os.path.join(td, "mask.png"))
                if layout in ("circle", "rounded") else None)
        if layout:  # face-centered framing, measured across the whole cut (CO5)
            from . import face, media
            box = face.face_box(facecam,
                                segments[0][0] - comp["offset_s"],
                                segments[-1][1] - comp["offset_s"])
            st = media.probe(facecam)["streams"]
            v = next(x for x in st if x.get("codec_type") == "video")
            wh = (v["width"], v["height"])
            if layout == "split":
                comp = {**comp, "crop": face.region_crop(wh, box, 1080 / comp.get("cam_h", 768))}
                if not comp.get("screen_crop"):
                    # plan-authored crop wins: some cuts (UI walkthroughs) must
                    # keep HUD chrome the activity heatmap would zoom past
                    from . import activity
                    sv = media.probe(video)["streams"]
                    s0 = next(x for x in sv if x.get("codec_type") == "video")
                    comp["screen_crop"] = activity.active_box(
                        video, segments, (s0["width"], s0["height"]))
            else:
                comp = {**comp, "crop": face.crop_for(wh, box)}
            print(f"         face box: {'found' if box else 'NOT found, center fallback'}"
                  f" layout={layout} crop={comp['crop']}"
                  f" screen_zoom={comp.get('screen_crop')}")
            # bounded corrective grade on the cam band only: screen content is
            # already clean and eq on UI pixels would shift brand colors
            from . import grade
            g0 = max(0.0, segments[0][0] - comp["offset_s"])
            g1 = max(g0 + 1.0, segments[-1][1] - comp["offset_s"])
            gflt, _ = grade.auto_grade(facecam, g0, g1 - g0)
            if gflt:
                comp["cam_grade"] = gflt
                print(f"         cam grade: {gflt}")
        parts = []
        for i, seg in enumerate(segments):
            s, e, speed = _se(seg)
            if follow_faces and not layout and speed == 1.0:
                parts += _face_shots(video, s, e, td, i)
                continue
            p = os.path.join(td, f"seg{i}.mp4")
            # hwaccel is a per-input option (before the -i it accelerates);
            # never on the mask (-loop image) input or the concat pass below
            args = [config.FFMPEG, "-y", "-v", "error", *config.hwdecode_args(),
                    "-ss", str(s), "-to", str(e), "-i", video]
            remap_v = f";[vout]setpts=PTS/{speed}[vsp]" if speed != 1.0 else ""
            vmap = "[vsp]" if speed != 1.0 else "[vout]"
            # sample-timing disguise: every segment after the first opens on a
            # small punch-in that settles over ~8 frames, so the join reads
            # as an intentional beat instead of a raw jump cut
            if i > 0 and layout == "split":
                # zoompan at the segment's NATIVE fps: forcing another fps
                # restamps frames into slow motion, and the scale/crop
                # alternative drifts off-center (crop geometry locks at init
                # while per-frame scale sizes change under it). zoompan zooms
                # about the true center and settles over ~0.27s; 'on' clamps
                # both ends so a post-seek negative timestamp cannot spike it.
                from . import media as _m
                _vs = next(x for x in _m.probe(video)["streams"]
                           if x.get("codec_type") == "video")
                _num, _den = (_vs.get("r_frame_rate") or "30/1").split("/")
                _fps = max(1, round(int(_num) / max(1, int(_den))))
                _settle = max(1, round(0.33 * _fps))
                remap_v += (f";{vmap}zoompan="
                            f"z='1+0.02*pow(1-min(on/{_settle},1),2)'"
                            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                            f":d=1:fps={_fps}:s=1080x1920[vpunch]")
                vmap = "[vpunch]"
            if layout:
                cam_s = s - comp["offset_s"]  # screen_time = facecam_time + offset
                args += [*config.hwdecode_args(),
                         "-ss", str(max(0, cam_s)), "-to", str(e - comp["offset_s"]),
                         "-i", facecam]
                if layout == "split":
                    fc = _split_filter(comp) + remap_v
                else:
                    args += ["-loop", "1", "-i", mask]
                    fc = (f"[0:v]{_reframe_vf()}[base];"
                          + _cam_filter(comp, s, (e - s) / speed) + remap_v)
                args += ["-filter_complex", _tonemapped(fc), "-map", vmap, "-map", "0:a"]
            elif speed != 1.0:
                args += ["-filter_complex",
                         _tonemapped(f"[0:v]{_reframe_vf(reframe)},setpts=PTS/{speed}[vsp]"),
                         "-map", "[vsp]", "-map", "0:a"]
            elif sdr_v:
                args += ["-vf", sdr_v + "," + _reframe_vf(reframe)]
            else:
                args += ["-vf", _reframe_vf(reframe)]
            # 30ms boundary fades: a cut landing mid-waveform clicks, and the
            # downstream adeclick pass only partially rescues it
            adur = (e - s) / speed
            fades = (f"afade=t=in:st=0:d=0.03,"
                     f"afade=t=out:st={max(0.0, adur - 0.03):.3f}:d=0.03")
            if speed != 1.0:  # time-remapped wait (C6): audio keeps pace
                args += ["-filter:a", _atempo_chain(speed) + "," + fades]
            else:
                args += ["-filter:a", fades]
            args += ["-r", "30", *config.intermediate_encode_args(),
                     "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", p]
            subprocess.run(args, check=True)
            parts.append(p)
        lst = os.path.join(td, "list.txt")
        open(lst, "w").write("\n".join(f"file '{p}'" for p in parts))
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-f", "concat",
                        "-safe", "0", "-i", lst, "-c", "copy", dst], check=True)


def _preview_cues(words, segments):
    """Words inside the kept segments, re-timed onto the preview timeline
    (speed-remapped segments compress their word timings with them)."""
    shifted, off = [], 0.0
    for seg in segments:
        s, e, speed = _se(seg)
        for w in words:
            if s <= (w["s"] + w["e"]) / 2 <= e:  # midpoint: keep boundary words
                shifted.append({"t": w["t"],
                                "s": max(0.0, w["s"] - s) / speed + off,
                                "e": max(0.05, min(w["e"], e) - s) / speed + off})
        off += (e - s) / speed
    return speech.group_cues(shifted)


def _burn(src, dst, cues, hook, workdir, layout=None, payoff=None, overlays=None):
    """One ffmpeg pass: hook + caption cues + payoff chip + loudness.

    Split layout (CO2): captions sit at the cam/content seam; hook rides the
    top of the cam band. Full-frame layouts keep the lower-third positions.
    """
    hook_y, cue_y = (40, 820) if layout == "split" else (210, 1430)
    inputs = [config.FFMPEG, "-y", "-v", "error", "-i", src]
    filters, last, idx = [], "0:v", 1
    if payoff and payoff.get("jump"):  # C6: mark the jump so it reads as intent
        chip = captions.text_png(">> " + payoff.get("chip", "moments later"),
                                 os.path.join(workdir, "chip.png"),
                                 width=560, size=42, fill="#17cdff", stroke_w=5)
        t0 = payoff["local_t"]
        inputs += ["-i", chip]
        filters.append(f"[{last}][{idx}:v]overlay=(W-w)/2:"
                       f"{970 if layout == 'split' else 1310}:"
                       f"enable='between(t,{t0:.2f},{t0 + 1.6:.2f})'[v{idx}]")
        last = f"v{idx}"
        idx += 1
    if hook:
        hp = captions.hook_png(hook["text"], os.path.join(workdir, "hook.png"))
        inputs += ["-i", hp]
        filters.append(f"[{last}][{idx}:v]overlay=(W-w)/2:{hook_y}:"
                       f"enable='between(t,0,{hook.get('show_s', 2.6)})'[v{idx}]")
        last = f"v{idx}"
        idx += 1
    # P-TEXT: the best writing belongs on screen, not only in the post caption.
    # On captions:"none" cuts these lines are the only text after the hook.
    for oi, o in enumerate(overlays or []):
        op = captions.hook_png(o["text"], os.path.join(workdir, f"ov{oi}.png"))
        t0 = float(o["t"])
        inputs += ["-i", op]
        filters.append(f"[{last}][{idx}:v]overlay=(W-w)/2:{hook_y}:"
                       f"enable='between(t,{t0:.2f},{t0 + float(o.get('show_s', 3.0)):.2f})'[v{idx}]")
        last = f"v{idx}"
        idx += 1
    for ci, (s, e, text) in enumerate(cues):
        cp = captions.cue_png(text, os.path.join(workdir, f"cue{ci}.png"))
        inputs += ["-i", cp]
        filters.append(f"[{last}][{idx}:v]overlay=(W-w)/2:{cue_y}:"
                       f"enable='between(t,{s:.2f},{max(e, s + 0.4):.2f})'[v{idx}]")
        last = f"v{idx}"
        idx += 1
    fc = ";".join(filters) if filters else f"[{last}]null[vout]"
    if filters:
        inputs += ["-filter_complex", fc, "-map", f"[{last}]"]
    else:
        inputs += ["-map", "0:v"]
    # S6 audio polish: de-click, de-ess the long s, gentle voice EQ, then target loudness
    inputs += ["-map", "0:a",
               "-af", "adeclick,deesser=i=0.4,highpass=f=70,loudnorm=I=-14:TP=-1.5:LRA=11",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
               "-c:a", "aac", "-b:a", "192k", dst]
    subprocess.run(inputs, check=True)


def _facecam_source(root):
    src = os.path.join(root, "source")
    for f in sorted(os.listdir(src)):
        if f.startswith("facecam"):
            return os.path.realpath(os.path.join(src, f))
    return None


def render(project, cut_id=None, tag=None):
    root = direct.resolve_project(project)
    sfx = f"_{tag}" if tag else ""
    plans = json.load(open(os.path.join(root, "edl", f"cut_plans{sfx}.json")))
    words = speech.words_from(os.path.join(root, "analysis", "words.json"))
    video = direct._source_video(root)
    facecam = _facecam_source(root)
    outdir = os.path.join(root, "deliverables", "cuts", f"previews{sfx}")
    os.makedirs(outdir, exist_ok=True)
    def _render_one(p):
        comp = p.get("composition")
        out = os.path.join(outdir, f"{p['id']}.mp4")
        cam_note = f" cam:{comp['cam']}" if comp and facecam else ""
        print(f"[render] {p['id']}: {p['title']} ({p['duration_s']}s){cam_note} ...")
        with tempfile.TemporaryDirectory() as td:
            raw = os.path.join(td, "raw.mp4")
            from . import slots
            with slots.hold("render", p["id"]):
                _cut_segments(video, p["segments"], raw, comp=comp,
                              facecam=facecam, reframe=p.get("reframe", 1.0))
            # captions:"none" is for footage whose audio is not narration
            # (game VO, music beds). ASR invents lines on non-speech audio, and
            # a burned-in hallucination is a shipped typo on the brand.
            cues = ([] if p.get("captions") == "none"
                    else _preview_cues(words, p["segments"]))
            _burn(raw, out, cues, p.get("hook"), td, overlays=p.get("overlay_lines"),
                  layout=comp.get("cam") if comp and facecam else None,
                  payoff=p.get("payoff"))
        print(f"[render] {p['id']} -> {out}")
        return out

    todo = [p for p in plans if not cut_id or p["id"] == cut_id]
    done, failed = [], []
    for p, out, err in run_parallel(todo, _render_one, max_workers=3):
        if err:
            print(f"[render] {p['id']} FAILED: {err}")
            failed.append(p["id"])
        else:
            done.append(out)
    if failed:
        raise RuntimeError("preview render failed on: " + ", ".join(failed))
    return done
