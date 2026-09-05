"""M3: DaVinci Resolve handoff.

Per-cut FCPXML timelines that reference the ORIGINAL source media (nothing
re-encoded, everything finesse-able), facecam connected on lane 1 synced by
the session offset, engine notes as markers (hook, payoff, reaction peaks),
a re-timed SRT per cut for Resolve's subtitle track, and a full-edit
longform timeline with topic chapter markers.
"""
import json
import os
import subprocess
import urllib.parse
from xml.sax.saxutils import escape

from . import config, direct, media, speech

FPS = 30


def _t(sec):
    """FCPXML rational time, frame-aligned."""
    return f"{round(sec * FPS)}/{FPS}s"


def _url(path):
    return "file://" + urllib.parse.quote(os.path.abspath(path))


def _asset(aid, path, fid, has_audio=True):
    dur = media.duration(path)
    return (f'<asset id="{aid}" name="{escape(os.path.basename(path))}" '
            f'src="{_url(path)}" start="0s" duration="{_t(dur)}" '
            f'hasVideo="1" hasAudio="{1 if has_audio else 0}" format="{fid}"/>')


def _marker(t_src, text):
    return f'<marker start="{_t(t_src)}" duration="1/30s" value="{escape(text)}"/>'


def _fcpxml(project_name, body_events, assets, vertical=True):
    w, h = (1080, 1920) if vertical else (1920, 1080)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE fcpxml>
<fcpxml version="1.8">
 <resources>
  <format id="rSeq" frameDuration="1/{FPS}s" width="{w}" height="{h}"/>
  <format id="rSrc" frameDuration="1/{FPS}s" width="1920" height="1080"/>
  {assets}
 </resources>
 <library>
  <event name="Reelly">
   <project name="{escape(project_name)}">
    <sequence format="rSeq" tcStart="0s">
     <spine>
{body_events}
     </spine>
    </sequence>
   </project>
  </event>
 </library>
</fcpxml>
"""


def _cut_timeline(plan, screen, facecam, offset, comp):
    """Spine: screen segments back to back, facecam connected on lane 1."""
    rows = []
    tl = 0.0
    for i, seg in enumerate(plan["segments"]):
        s, e = seg[0], seg[1]
        dur = e - s
        markers = []
        if i == 0:
            markers.append(_marker(s, f"HOOK: {plan['hook']['text']} "
                                      f"(hold {plan['hook'].get('show_s', 3.6)}s)"))
        payoff = plan.get("payoff")
        if payoff and payoff.get("jump") and i == len(plan["segments"]) - 1:
            markers.append(_marker(s, f"PAYOFF: {payoff.get('event', '')} "
                                      f"(add 'moments later' marker here)"))
        if comp:
            for f in comp.get("features", []):
                if s <= f["t_src"] <= e:
                    markers.append(_marker(f["t_src"], f"REACTION: {f['why']} "
                                                       f"(feature the cam)"))
        cam = ""
        if facecam:
            # nested clip offset lives in the parent's source-time coordinates
            cam = (f'      <asset-clip ref="aCam" lane="1" offset="{_t(s)}" '
                   f'start="{_t(max(0, s - offset))}" duration="{_t(dur)}" '
                   f'name="facecam">'
                   f'<adjust-transform scale="0.5 0.5" position="0 25"/>'
                   f'</asset-clip>\n')
        rows.append(
            f'      <asset-clip ref="aScr" offset="{_t(tl)}" start="{_t(s)}" '
            f'duration="{_t(dur)}" name="{escape(plan["id"])}_seg{i + 1}" format="rSrc">\n'
            f'{cam}'
            + "".join(f"       {m}\n" for m in markers) +
            f'      </asset-clip>')
        tl += dur
    return "\n".join(rows)


def _retimed_srt(words, segments, path):
    shifted, off = [], 0.0
    for seg in segments:
        s, e = seg[0], seg[1]
        for w in words:
            if s <= (w["s"] + w["e"]) / 2 <= e:  # midpoint: keep boundary words
                shifted.append({"t": w["t"], "s": max(0.0, w["s"] - s) + off,
                                "e": max(0.05, min(w["e"], e) - s) + off})
        off += e - s
    return speech.clean_srt(shifted, path)


def _xf(sec):
    return round(sec * FPS)


def _xmeml_file(fid, path, w, h, define):
    if not define:
        return f'<file id="{fid}"/>'
    dur = _xf(media.duration(path))
    return (f'<file id="{fid}"><name>{escape(os.path.basename(path))}</name>'
            f'<pathurl>{_url(path)}</pathurl>'
            f'<rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>'
            f'<duration>{dur}</duration>'
            f'<media><video><samplecharacteristics><width>{w}</width>'
            f'<height>{h}</height></samplecharacteristics></video>'
            f'<audio><channelcount>2</channelcount></audio></media></file>')


def _xmeml_clip(cid, name, fid, path, tl0, dur, src0, define_file, audio=False):
    track = ('<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex>'
             '</sourcetrack>') if audio else ''
    return (f'<clipitem id="{cid}"><name>{escape(name)}</name><enabled>TRUE</enabled>'
            f'<rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>'
            f'<duration>{dur}</duration>'
            f'<start>{tl0}</start><end>{tl0 + dur}</end>'
            f'<in>{src0}</in><out>{src0 + dur}</out>'
            f'{_xmeml_file(fid, path, 1920, 1080, define_file)}{track}</clipitem>')


def _xmeml(name, segments, screen, facecam, offset, markers, vertical=True):
    """FCP7 XML: the timeline format Resolve digests most reliably."""
    w, h = (1080, 1920) if vertical else (1920, 1080)
    scr_v, cam_v, aud, tl = [], [], [], 0
    n = 0
    for seg in segments:
        s, e = seg[0], seg[1]
        dur = _xf(e - s)
        src0 = _xf(s)
        n += 1
        scr_v.append(_xmeml_clip(f"sv{n}", f"seg{n}", "f1", screen,
                                 tl, dur, src0, define_file=(n == 1)))
        aud.append(_xmeml_clip(f"sa{n}", f"seg{n}", "f1", screen,
                               tl, dur, src0, define_file=False, audio=True))
        if facecam:
            cam_v.append(_xmeml_clip(f"cv{n}", f"cam{n}", "f2", facecam,
                                     tl, dur, _xf(max(0, s - offset)),
                                     define_file=(n == 1)))
        tl += dur
    mk = "".join(f'<marker><name>{escape(m_name)}</name><comment></comment>'
                 f'<in>{m_frame}</in><out>-1</out></marker>'
                 for m_frame, m_name in markers)
    cam_track = f'<track>{"".join(cam_v)}</track>' if cam_v else ''
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
 <sequence>
  <name>{escape(name)}</name>
  <duration>{tl}</duration>
  <rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>
  <media>
   <video>
    <format><samplecharacteristics>
     <rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>
     <width>{w}</width><height>{h}</height>
    </samplecharacteristics></format>
    <track>{"".join(scr_v)}</track>
    {cam_track}
   </video>
   <audio><track>{"".join(aud)}</track></audio>
  </media>
  <timecode><rate><timebase>{FPS}</timebase><ntsc>FALSE</ntsc></rate>
   <string>00:00:00:00</string><frame>0</frame></timecode>
  {mk}
 </sequence>
</xmeml>
"""


def _timeline_markers(plan, comp):
    """(timeline_frame, text) markers for a cut plan."""
    out, tl = [], 0.0
    for i, seg in enumerate(plan["segments"]):
        s, e = seg[0], seg[1]
        if i == 0:
            out.append((_xf(tl), f"HOOK: {plan['hook']['text']} "
                                 f"(hold {plan['hook'].get('show_s', 3.6)}s)"))
        payoff = plan.get("payoff")
        if payoff and payoff.get("jump") and i == len(plan["segments"]) - 1:
            out.append((_xf(tl), f"PAYOFF: {payoff.get('event', '')}"))
        if comp:
            for f in comp.get("features", []):
                if s <= f["t_src"] <= e:
                    out.append((_xf(tl + f["t_src"] - s), f"REACTION: {f['why']}"))
        tl += e - s
    return out


def run(project, tag=None):
    root = direct.resolve_project(project)
    sfx = f"_{tag}" if tag else ""
    plans = json.load(open(os.path.join(root, "edl", f"cut_plans{sfx}.json")))
    full = json.load(open(os.path.join(root, "edl", "full_edit.json")))
    words = speech.words_from(os.path.join(root, "analysis", "words.json"))
    sess_p = os.path.join(root, "analysis", "session.json")
    sess = json.load(open(sess_p)) if os.path.exists(sess_p) else None
    topics_p = os.path.join(root, "analysis", "topics.json")
    topics = json.load(open(topics_p)) if os.path.exists(topics_p) else []
    screen = direct._source_video(root)
    from .preview import _facecam_source
    facecam = _facecam_source(root)
    offset = sess["facecam_offset_s"] if sess else 0.0

    out = os.path.join(root, "deliverables", f"resolve{sfx}")
    os.makedirs(out, exist_ok=True)

    assets = _asset("aScr", screen, "rSrc", has_audio=True)
    if facecam:
        # audio disabled: the voice lives in the screen recording
        assets += "\n  " + _asset("aCam", facecam, "rSrc", has_audio=False)

    made = []
    for p in plans:
        comp = p.get("composition")
        body = _cut_timeline(p, screen, facecam, offset, comp)
        name = f"{p['id']} {p['title']}"
        fx = os.path.join(out, f"{p['id']}.fcpxml")
        open(fx, "w").write(_fcpxml(name, body, assets, vertical=True))
        open(os.path.join(out, f"{p['id']}.xml"), "w").write(
            _xmeml(name, p["segments"], screen, facecam, offset,
                   _timeline_markers(p, comp), vertical=True))
        _retimed_srt(words, p["segments"], os.path.join(out, f"{p['id']}.srt"))
        made.append(p["id"])
        print(f"[handoff] {p['id']}: xml + fcpxml + srt")

    # longform: keep blocks on one 16:9 timeline, chapter markers from topics
    rows = []
    tl = 0.0
    chapters = [(c["s"], c["text"][:60]) for c in topics]
    for i, (s, e) in enumerate(full["keep"]):
        dur = e - s
        marks = "".join(f"       {_marker(cs, 'CHAPTER: ' + escape(txt))}\n"
                        for cs, txt in chapters if s <= cs < e)
        cam = ""
        if facecam:
            cam = (f'      <asset-clip ref="aCam" lane="1" offset="{_t(s)}" '
                   f'start="{_t(max(0, s - offset))}" duration="{_t(dur)}" '
                   f'name="facecam">'
                   f'<adjust-transform scale="0.25 0.25" position="35 -35"/>'
                   f'</asset-clip>\n')
        rows.append(f'      <asset-clip ref="aScr" offset="{_t(tl)}" start="{_t(s)}" '
                    f'duration="{_t(dur)}" name="keep{i + 1}" format="rSrc">\n'
                    f'{cam}{marks}      </asset-clip>')
        tl += dur
    fx = os.path.join(out, "full_edit.fcpxml")
    open(fx, "w").write(_fcpxml(f"{os.path.basename(root)} full edit",
                                "\n".join(rows), assets, vertical=False))
    ch_marks, tl = [], 0.0
    for s, e in full["keep"]:
        for cs, txt in chapters:
            if s <= cs < e:
                ch_marks.append((_xf(tl + cs - s), f"CHAPTER: {txt}"))
        tl += e - s
    open(os.path.join(out, "full_edit.xml"), "w").write(
        _xmeml(f"{os.path.basename(root)} full edit", full["keep"],
               screen, facecam, offset, ch_marks, vertical=False))
    _retimed_srt(words, full["keep"], os.path.join(out, "full_edit.srt"))
    print(f"[handoff] full_edit: {media.fmt(full['original_s'])} -> "
          f"{media.fmt(full['edited_s'])}, {len(full['keep'])} blocks, "
          f"{len(chapters)} chapter markers")

    _manifest(out, root, plans, full, screen, facecam, offset, topics, words)
    _blur_comp(out)
    _install_resolve_script(out)
    _write_md(out, made, facecam is not None)
    return out


def _fps_of(path):
    v = next(s for s in media.probe(path)["streams"] if s.get("codec_type") == "video")
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    return float(num) / float(den or 1)


def _alpha_clip(png, mov, dur):
    """Transparent ProRes 4444 clip of exact duration: Resolve ignores
    still-image durations on append (measured), video clips it obeys."""
    import subprocess
    from . import config
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-loop", "1", "-i", png,
                    "-t", f"{max(dur, 0.2):.2f}", "-r", str(FPS),
                    "-c:v", "prores_ks", "-profile:v", "4444",
                    "-pix_fmt", "yuva444p10le", mov], check=True)
    return mov


def _overlays(out, plan, words):
    """Styled hook + caption cues as transparent alpha clips at exact frames,
    so the Resolve timeline reads like the preview and stays editable."""
    from . import captions
    from .preview import _preview_cues
    odir = os.path.join(out, "overlays")
    os.makedirs(odir, exist_ok=True)
    items = []
    hook = plan.get("hook") or {}
    if hook.get("text"):
        png = os.path.join(odir, f"{plan['id']}_hook.png")
        captions.full_frame_overlay(hook["text"], png, kind="hook")
        dur = float(hook.get("show_s", 3.6))
        mov = _alpha_clip(png, png.replace(".png", ".mov"), dur)
        items.append({"png": png, "mov": mov, "record_s": 0.0, "dur_s": dur})
    for i, (s, e, text) in enumerate(_preview_cues(words, plan["segments"])):
        png = os.path.join(odir, f"{plan['id']}_cue{i:02d}.png")
        captions.full_frame_overlay(text, png, kind="cue")
        dur = round(max(e - s, 0.4), 2)
        mov = _alpha_clip(png, png.replace(".png", ".mov"), dur)
        items.append({"png": png, "mov": mov, "record_s": round(s, 2), "dur_s": dur})
    return items


def _co7_comp(ax, ay, aw, ah, sw, sh, band_center_y=1344, tw=1080, th=1920):
    """Fusion comp: crop source to the active box, scale to band width,
    place on a transparent timeline-size canvas (ffmpeg-equivalent)."""
    yoff = sh - (ay + ah)                # fusion measures from the bottom
    size = tw / aw
    cy = 1 - (band_center_y / th)
    return f"""Composition {{
	CurrentTime = 0,
	Tools = ordered() {{
		MediaIn1 = MediaIn {{ }},
		Crop1 = Crop {{
			Inputs = {{
				XOffset = Input {{ Value = {ax}, }}, YOffset = Input {{ Value = {yoff}, }},
				XSize = Input {{ Value = {aw}, }}, YSize = Input {{ Value = {ah}, }},
				Input = Input {{ SourceOp = "MediaIn1", Source = "Output", }},
			}},
		}},
		BG1 = Background {{
			Inputs = {{
				UseFrameFormatSettings = Input {{ Value = 0, }},
				Width = Input {{ Value = {tw}, }}, Height = Input {{ Value = {th}, }},
				TopLeftAlpha = Input {{ Value = 0, }},
			}},
		}},
		Merge1 = Merge {{
			Inputs = {{
				Background = Input {{ SourceOp = "BG1", Source = "Output", }},
				Foreground = Input {{ SourceOp = "Crop1", Source = "Output", }},
				Center = Input {{ Value = {{ 0.5, {cy:.4f} }}, }},
				Size = Input {{ Value = {size:.4f}, }},
			}},
		}},
		MediaOut1 = MediaOut {{
			Inputs = {{ Input = Input {{ SourceOp = "Merge1", Source = "Output", }}, }},
		}},
	}},
}}
"""


def _cam_circle_clips(out, facecam, keep, offset):
    """CO6: bake the longform corner cam as 300x300 ProRes 4444 alpha clips,
    one per keep block, using the SAME ffmpeg chain as the render. The
    Fusion-comp route was abandoned: mask normalization and gamma semantics
    drifted from ffmpeg and the import stopped being 1:1."""
    import tempfile
    from . import face, grade, captions, media
    v = next(x for x in media.probe(facecam)["streams"]
             if x.get("codec_type") == "video")
    box = face.face_box(facecam, keep[0][0], keep[0][1], samples=5)
    cw, cx, cy = face.crop_for((v["width"], v["height"]), box)
    eq, _ = grade.auto_grade(facecam, keep[0][0],
                             max(1.0, keep[-1][1] - keep[0][0]))
    paths = [os.path.join(out, f"co6_cam_b{i:02d}.mov")
             for i in range(len(keep))]
    if all(os.path.exists(p) and os.path.getsize(p) > 1e6 for p in paths):
        print(f"[handoff] co6 circle cam: {len(paths)} clips (cached)")
        return paths
    paths = []
    with tempfile.TemporaryDirectory() as td:
        mask = captions.circle_mask(os.path.join(td, "mask.png"))
        for i, (s, e) in enumerate(keep):
            cs = max(0.0, s - offset)
            dst = os.path.join(out, f"co6_cam_b{i:02d}.mov")
            sdr = media.sdr_chain(facecam)
            head = f"[0:v]{sdr}[v0];[v0]" if sdr else "[0:v]"
            fc = (f"{head}crop={cw}:{cw}:{cx}:{cy},"
                  f"{eq + ',' if eq else ''}scale=512:512[cs];"
                  f"[cs][1:v]alphamerge,scale=300:300[cam]")
            subprocess.run([config.FFMPEG, "-y", "-v", "error",
                            "-ss", str(cs), "-to", str(cs + (e - s)), "-i", facecam,
                            "-loop", "1", "-i", mask,
                            "-filter_complex", fc, "-map", "[cam]",
                            "-r", "30", "-c:v", "prores_ks", "-profile:v", "4444",
                            "-pix_fmt", "yuva444p10le", "-an",
                            "-t", f"{e - s:.3f}", dst], check=True)
            paths.append(dst)
    print(f"[handoff] co6 circle cam: {len(paths)} clips (prores 4444 alpha)")
    return paths


def _blur_comp(out):
    """Minimal Fusion comp (MediaIn -> Blur -> MediaOut) the script tries to
    attach to the background track; harmless if the API refuses."""
    comp = """Composition {
	CurrentTime = 0,
	Tools = ordered() {
		MediaIn1 = MediaIn { },
		Blur1 = Blur {
			Inputs = {
				XBlurSize = Input { Value = 40, },
				Input = Input { SourceOp = "MediaIn1", Source = "Output", },
			},
		},
		MediaOut1 = MediaOut {
			Inputs = { Input = Input { SourceOp = "Blur1", Source = "Output", }, },
		},
	},
}
"""
    open(os.path.join(out, "reelly_blur.comp"), "w").write(comp)


def _manifest(out, root, plans, full, screen, facecam, offset, topics, words):
    """Everything the in-Resolve import script needs, in one JSON."""
    cam_wh = None
    if facecam:
        v = next(s for s in media.probe(facecam)["streams"]
                 if s.get("codec_type") == "video")
        cam_wh = (v["width"], v["height"])
    timelines = []
    for p in plans:
        entry = {
            "name": f"{p['id']} {p['title']}",
            "vertical": True,
            "segments": [[seg[0], seg[1]] for seg in p["segments"]],
            "markers": _timeline_markers(p, p.get("composition")),
            "srt": f"{p['id']}.srt",
        }
        if facecam:
            # Native split-layout transforms, CALIBRATED against Resolve by
            # live renders (2026-07-09): with a src clip fit into the timeline,
            # canvas_shift = value * k where k_pan = src_w*fit/tl_w and
            # k_tilt = src_h*fit/tl_h; positive tilt moves UP. For 1920x1080
            # in 1080x1920: k_pan = 1.0, k_tilt = 607.5/1920 = 0.3164.
            from . import face
            box = face.face_box(facecam, p["segments"][0][0] - offset,
                                p["segments"][-1][1] - offset, samples=5)
            tw, th = 1080, 1920
            sw, sh = cam_wh
            fit = min(tw / sw, th / sh)
            k_pan = sw * fit / tw
            k_tilt = sh * fit / th
            zoom = round(768 / (sh * fit), 3)          # cam fills the 768px top band
            fx = box[0] if box else sw / 2
            pan = round(-(fx - sw / 2) * fit * zoom / k_pan, 1)
            entry["xform"] = {
                "cam_zoom": zoom,
                "cam_pan": pan,
                "cam_tilt": round((th / 2 - 384) / k_tilt, 1),      # band center 384
                "screen_tilt": round((th / 2 - 1344) / k_tilt, 1),  # band center 1344
            }
            # content-aware zoom (CO7) via a generated Fusion comp: Inspector
            # crop+zoom semantics proved unreliable, Fusion coordinates are
            # deterministic and verified pixel-equivalent to the previews
            from . import activity
            sv = next(x for x in media.probe(screen)["streams"]
                      if x.get("codec_type") == "video")
            act = activity.active_box(screen, p["segments"], (sv["width"], sv["height"]))
            if act:
                ax, ay, aw, ah = act
                cpath = os.path.join(out, f"co7_{p['id']}.comp")
                open(cpath, "w").write(_co7_comp(ax, ay, aw, ah,
                                                 sv["width"], sv["height"]))
                entry["co7_comp"] = cpath
        entry["overlays"] = _overlays(out, p, words)
        # polished stems from finalize, if that ran: voice -> A2, music -> A3
        audir = os.path.join(root, "deliverables", "audio")
        stems = {}
        for kind, fname in (("voice", f"{p['id']}_voice.wav"),
                            ("music", f"{p['id']}_music.mp3")):
            sp = os.path.join(audir, fname)
            if os.path.exists(sp):
                stems[kind] = sp
        if stems:
            entry["stems"] = stems
        timelines.append(entry)
    ch_marks, tl = [], 0.0
    for s, e in full["keep"]:
        for c in topics:
            if s <= c["s"] < e:
                ch_marks.append([_xf(tl + c["s"] - s), "CHAPTER: " + c["text"][:60]])
        tl += e - s
    full_entry = {"name": f"{os.path.basename(root)} full edit",
                  "vertical": False, "segments": full["keep"],
                  "markers": ch_marks, "srt": "full_edit.srt"}
    if facecam:
        try:
            full_entry["cam_circle"] = _cam_circle_clips(out, facecam,
                                                         full["keep"], offset)
        except Exception as e:
            print("[handoff] co6 circle cam skipped:", e)
    timelines.append(full_entry)
    man = {
        "project": os.path.basename(root),
        "screen": screen, "screen_fps": _fps_of(screen),
        "facecam": facecam, "facecam_fps": _fps_of(facecam) if facecam else None,
        "offset_s": offset,
        "timelines": timelines,
    }
    json.dump(man, open(os.path.join(out, "manifest.json"), "w"), indent=1)
    print(f"[handoff] manifest.json ({len(timelines)} timelines)")


def _install_resolve_script(out):
    """Copy the in-Resolve import script where Workspace > Scripts finds it."""
    import shutil
    src = os.path.join(os.path.dirname(__file__), "resolve_import_script.py")
    dst_dir = os.path.expanduser(
        "~/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
        "Fusion/Scripts/Utility")
    targets = [os.path.join(out, "Reelly Import.py")]
    if os.path.isdir(dst_dir):
        targets.append(os.path.join(dst_dir, "Reelly Import.py"))
    for t in targets:
        shutil.copy2(src, t)
    print(f"[handoff] Reelly Import.py -> {', '.join(targets)}")


def _write_md(out, cut_ids, has_cam):
    L = ["# Resolve handoff", "",
         "THE ONE-CLICK WAY (recommended): open DaVinci Resolve, then "
         "Workspace > Scripts > Reelly Import. The script builds the whole "
         "project through Resolve's own API: media pool, one vertical timeline "
         "per cut, the 16:9 full edit, and all engine markers. No import "
         "dialogs. Console output appears in Workspace > Console.", "",
         "Fallback (file-based): File > Import > Timeline > Import AAF, EDL, "
         "XML... (NOT File > Import Project, which only lists .drp files and "
         "hides everything else) and pick a .xml (FCP7 XML). The .fcpxml twins "
         "exist for FCPX compatibility. Media relinks to the ORIGINAL "
         "recordings; nothing is re-encoded.", "",
         "Per timeline:",
         "- V1: screen segments, cut and silence-snapped by the engine",
         "- V2 (lane 1): facecam, waveform-synced, audio disabled "
         "(voice lives in the screen track)" if has_cam else
         "- single-source timeline (no facecam in this session)",
         "- Markers carry the engine's notes: HOOK text and hold time, PAYOFF "
         "jump points, REACTION moments worth featuring the cam, CHAPTER points "
         "on the full edit",
         "- Subtitles: import the matching .srt onto a subtitle track "
         "(Timeline > Import Subtitle). Cues are brand-vocabulary corrected.", "",
         "Layout: transforms are rough placeholders. For the split look use the "
         "Circle_Insert power-bin macro or your own; the engine's preview MP4s "
         "show the intended composition.", "",
         f"Timelines: {', '.join(cut_ids)}, full_edit", ""]
    open(os.path.join(out, "HANDOFF.md"), "w").write("\n".join(L))
