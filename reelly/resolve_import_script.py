"""Reelly Import: runs INSIDE DaVinci Resolve (Workspace > Scripts > Reelly Import).

Self-contained on purpose: no reelly package imports. Reads the newest
handoff manifest and rebuilds the engine's composition natively:
  V1 blurred background (screen zoomed to fill; Fusion blur comp when the
     API allows, otherwise add Gaussian Blur once by hand)
  V2 screen content, framed to the lower band (with audio)
  V3 facecam, waveform-synced, face-centered in the top band, video only
plus the engine's markers and a subtitle import attempt per timeline.
"""
import glob
import json
import os

def _manifest_glob():
    """Newest Resolve handoff under the user's projects root.

    Override with REELLY_PROJECTS. Defaults to ~/reelly-projects, never a
    hardcoded personal path.
    """
    root = os.environ.get("REELLY_PROJECTS") or os.path.expanduser("~/reelly-projects")
    return os.path.join(os.path.expanduser(root),
                        "*/deliverables/resolve*/manifest.json")


MANIFEST_GLOB = _manifest_glob()


def _get_resolve():
    try:
        return resolve  # injected by Resolve when run from the Scripts menu
    except NameError:
        import DaVinciResolveScript as dvr
        return dvr.scriptapp("Resolve")


def _find_items(mp, ms, paths):
    items = ms.AddItemListToMediaPool([p for p in paths if p]) or []
    by = {}
    for it in items:
        try:
            by[os.path.basename(it.GetClipProperty("File Path"))] = it
        except Exception:
            pass
    missing = [p for p in paths if p and os.path.basename(p) not in by]
    if missing:
        for it in mp.GetRootFolder().GetClipList() or []:
            n = os.path.basename(it.GetClipProperty("File Path") or "")
            for p in missing:
                if n == os.path.basename(p):
                    by[n] = it
    return by


def _unique_name(proj, name):
    existing = {proj.GetTimelineByIndex(i + 1).GetName()
                for i in range(proj.GetTimelineCount() or 0)}
    if name not in existing:
        return name
    n = 2
    while f"{name} v{n}" in existing:
        n += 1
    return f"{name} v{n}"


def _seconds_to_src(sec, fps):
    return int(round(sec * fps))


def _append(mp, item, s, e, fps, track=None, record=None, video_only=False):
    info = {"mediaPoolItem": item,
            "startFrame": _seconds_to_src(s, fps),
            "endFrame": max(_seconds_to_src(s, fps) + 1, _seconds_to_src(e, fps) - 1)}
    if track:
        info["trackIndex"] = track
    if record is not None:
        info["recordFrame"] = record
    if video_only:
        info["mediaType"] = 1
    return mp.AppendToTimeline([info])


def _set_xform(items, zoom=None, pan=None, tilt=None):
    for it in items or []:
        try:
            if zoom is not None:
                it.SetProperty("ZoomX", zoom)
                it.SetProperty("ZoomY", zoom)
            if pan is not None:
                it.SetProperty("Pan", pan)
            if tilt is not None:
                it.SetProperty("Tilt", tilt)
        except Exception as e:
            print("Reelly: transform failed:", e)


def main():
    manifests = sorted(glob.glob(MANIFEST_GLOB), key=os.path.getmtime)
    if not manifests:
        print("Reelly: no manifest found under", MANIFEST_GLOB)
        return
    man = json.load(open(manifests[-1]))
    base = os.path.dirname(manifests[-1])
    print("Reelly: importing", man["project"], "from", manifests[-1])

    r = _get_resolve()
    pm = r.GetProjectManager()
    pname = "Reelly - " + man["project"]
    proj = pm.LoadProject(pname) or pm.CreateProject(pname)
    if not proj:
        print("Reelly: could not create/load project", pname)
        return
    proj.SetSetting("timelineFrameRate", "30")

    mp = proj.GetMediaPool()
    by = _find_items(mp, r.GetMediaStorage(), [man["screen"], man.get("facecam")])
    scr = by.get(os.path.basename(man["screen"]))
    cam = by.get(os.path.basename(man["facecam"])) if man.get("facecam") else None
    if not scr:
        print("Reelly: screen media not found, aborting")
        return
    sfps = float(man.get("screen_fps", 30))
    cfps = float(man.get("facecam_fps", sfps))
    off = float(man.get("offset_s", 0))

    for t in man["timelines"]:
        name = _unique_name(proj, t["name"])
        tl = mp.CreateEmptyTimeline(name)
        if not tl:
            print("Reelly: could not create timeline", name)
            continue
        proj.SetCurrentTimeline(tl)
        vertical = t.get("vertical")
        if vertical:
            try:
                tl.SetSetting("useCustomSettings", "1")
                tl.SetSetting("timelineResolutionWidth", "1080")
                tl.SetSetting("timelineResolutionHeight", "1920")
                tl.SetSetting("timelineFrameRate", "30")
            except Exception as e:
                print("Reelly: vertical settings failed (set manually):", e)

        # tracks: V1 bg, V2 screen, V3 cam, V4 text overlays
        overlays = t.get("overlays", [])
        want = 1
        if vertical:
            # V4 cues, V5 hook: the hook plays OVER the early cues, so they
            # cannot share a track (concurrent clips trim each other; measured)
            want = 5 if overlays else 3
        elif cam:
            want = 2
        try:
            while tl.GetTrackCount("video") < want:
                tl.AddTrack("video")
        except Exception as e:
            print("Reelly: AddTrack failed:", e)

        segs = [(seg[0], seg[1]) for seg in t["segments"]]
        start = tl.GetStartFrame()
        pos = 0.0
        for s, e in segs:
            rec = start + int(round(pos * 30))
            if vertical:  # V1 blurred-fill background (video only)
                _append(mp, scr, s, e, sfps, track=1, record=rec, video_only=True)
                _append(mp, scr, s, e, sfps, track=2, record=rec, video_only=True)
                # API quirk: a video trackIndex override silently drops audio,
                # so the voice gets its own audio-only append onto A1
                mp.AppendToTimeline([{
                    "mediaPoolItem": scr,
                    "startFrame": _seconds_to_src(s, sfps),
                    "endFrame": max(_seconds_to_src(s, sfps) + 1,
                                    _seconds_to_src(e, sfps) - 1),
                    "mediaType": 2, "trackIndex": 1, "recordFrame": rec}])
            else:
                _append(mp, scr, s, e, sfps, track=1, record=rec)
            if cam and not (not vertical and t.get("cam_circle")):
                cs = max(0.0, s - off)
                _append(mp, cam, cs, cs + (e - s), cfps,
                        track=3 if vertical else 2, record=rec, video_only=True)
            pos += e - s

        if vertical:
            xf = t.get("xform", {})
            bg_items = tl.GetItemListInTrack("video", 1)
            _set_xform(bg_items, zoom=3.17)
            comp_path = os.path.join(base, "reelly_blur.comp")
            blurred = False
            if os.path.exists(comp_path):
                for it in bg_items or []:
                    try:
                        fn = getattr(it, "ImportFusionComp", None)
                        if fn and fn(comp_path):
                            blurred = True
                    except Exception:
                        pass
            if not blurred:
                print("Reelly:", name, "- V1 blur: select V1 clips, add "
                      "Gaussian Blur once (API refused the comp)")
            # screen band: CO7 content zoom via generated Fusion comp when
            # available (deterministic; Inspector crop+zoom is not), else
            # plain fit framing with the calibrated tilt
            scr_items = tl.GetItemListInTrack("video", 2)
            co7 = t.get("co7_comp")
            if co7 and os.path.exists(co7):
                for it in scr_items or []:
                    try:
                        if it.GetFusionCompCount() == 0:
                            it.ImportFusionComp(co7)
                    except Exception as e:
                        print("Reelly: co7 comp failed:", e)
            else:
                _set_xform(scr_items, tilt=xf.get("screen_tilt", -1213.6))
            if cam:
                _set_xform(tl.GetItemListInTrack("video", 3),
                           zoom=xf.get("cam_zoom", 1.264),
                           pan=xf.get("cam_pan", 0.0),
                           tilt=xf.get("cam_tilt", 1820.4))
        elif cam:
            circles = [p for p in (t.get("cam_circle") or []) if os.path.exists(p)]
            if circles:
                # CO6: baked circle-cam clips (same ffmpeg chain as the render);
                # zoom/pan/tilt place the 300px circle at overlay=W-w-42:H-h-42
                try:
                    mp.ImportMedia(circles)
                except Exception as e:
                    print("Reelly: circle import failed:", e)
                cby = _find_items(mp, r.GetMediaStorage(), circles)
                start = tl.GetStartFrame()
                placed = []
                rec_t = 0.0
                for k, (s, e) in enumerate(segs):
                    it = cby.get(os.path.basename(circles[k])) if k < len(circles) else None
                    if it:
                        try:
                            mp.AppendToTimeline([{
                                "mediaPoolItem": it,
                                "startFrame": 0,
                                "endFrame": max(1, int(round((e - s) * 30)) - 1),
                                "trackIndex": 2, "mediaType": 1,
                                "recordFrame": start + int(round(rec_t * 30))}])
                            placed.append(it)
                        except Exception as ex:
                            print("Reelly: circle append failed:", ex)
                    rec_t += e - s
                _set_xform(tl.GetItemListInTrack("video", 2),
                           zoom=0.2778, pan=1365.3, tilt=-348.0)
                print("Reelly: circle cam placed on", len(placed), "blocks")
            else:  # no baked clips: square corner cam via edit transforms
                _set_xform(tl.GetItemListInTrack("video", 2),
                           zoom=0.25, pan=600, tilt=-330)

        # V4: styled hook + caption overlays as alpha clips at exact frames
        # (stills get Resolve's default 5s duration no matter what; measured)
        if overlays:
            start = tl.GetStartFrame()
            srcs = [o.get("mov") or o["png"] for o in overlays]
            obys = _find_items(mp, r.GetMediaStorage(), srcs)
            placed = 0
            for o in overlays:
                base = os.path.basename(o.get("mov") or o["png"])
                it = obys.get(base)
                if not it:
                    continue
                dur = max(2, int(round(o["dur_s"] * 30)))
                try:
                    mp.AppendToTimeline([{
                        "mediaPoolItem": it,
                        "startFrame": 0, "endFrame": dur - 1,
                        "trackIndex": 5 if "_hook" in base else 4,
                        "mediaType": 1,
                        "recordFrame": start + int(round(o["record_s"] * 30))}])
                    placed += 1
                except Exception as e:
                    print("Reelly: overlay failed:", e)
            print("Reelly:", name, "- overlays placed:", placed, "/", len(overlays))

        for frame, label in t.get("markers", []):
            color = ("Red" if label.startswith("HOOK") else
                     "Green" if label.startswith("PAYOFF") else
                     "Yellow" if label.startswith("REACTION") else "Blue")
            try:
                tl.AddMarker(int(frame), color, label[:80], label, 1)
            except Exception:
                pass

        # polished stems: voice on A2 (raw A1 disabled), music bed on A3
        stems = t.get("stems") or {}
        if stems:
            try:
                while tl.GetTrackCount("audio") < (3 if "music" in stems else 2):
                    tl.AddTrack("audio")
            except Exception as e:
                print("Reelly: audio AddTrack failed:", e)
            sby = _find_items(mp, r.GetMediaStorage(), list(stems.values()))
            start = tl.GetStartFrame()
            for kind, track in (("voice", 2), ("music", 3)):
                it = sby.get(os.path.basename(stems.get(kind, "")))
                if not it:
                    continue
                try:
                    mp.AppendToTimeline([{
                        "mediaPoolItem": it, "startFrame": 0,
                        "endFrame": int(float(it.GetClipProperty("Frames") or 2)) - 1,
                        "mediaType": 2, "trackIndex": track,
                        "recordFrame": start}])
                except Exception as e:
                    print("Reelly: stem append failed:", kind, e)
            try:
                tl.SetTrackEnable("audio", 1, False)  # raw stays, one toggle away
            except Exception:
                pass

        srt = os.path.join(base, t.get("srt", ""))
        imported = False
        if os.path.exists(srt):
            for meth in ("ImportSubtitles", "ImportSubtitle", "ImportIntoTimeline"):
                try:
                    fn = getattr(tl, meth, None)
                    if fn and fn(srt):
                        imported = True
                        break
                except Exception:
                    pass
        print("Reelly: built", name, "|", len(segs), "segments |",
              "subtitles OK" if imported else f"subtitles: Timeline > Import Subtitle > {os.path.basename(srt)}")

    print("Reelly: done. If V1 background is not blurred: select V1 clips once, "
          "Effects > Gaussian Blur (the API cannot always attach OFX).")


main()
