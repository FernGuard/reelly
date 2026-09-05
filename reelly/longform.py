"""M4.5 longform: the full landscape video, clearly edited for engagement.

Renders the full-edit plan (dead air jump-cut out) at 16:9 with a circular
corner cam, S6 voice chain, platform loudness, product end tag, SRT sidecar,
and a paste-ready YouTube chapter list from the topic map.
"""
import json
import os
import subprocess
import tempfile

from . import audio_post, captions, config, direct, face, media, products, speech
from .handoff import _retimed_srt
from .preview import _facecam_source

CAM_D = 300  # corner cam diameter, 16:9 longform


def _block(video, facecam, cam_crop, mask, s, e, dst, offset=0.0,
           endtag_png=None, tag_at=None, cam_grade=""):
    args = [config.FFMPEG, "-y", "-v", "error",
            "-ss", str(s), "-to", str(e), "-i", video]
    fc = []
    last = "0:v"
    sdr_v = media.sdr_chain(video)
    if sdr_v:
        fc.append(f"[0:v]{sdr_v}[v0sdr]")
        last = "v0sdr"
    if facecam and mask:
        cw, cx, cy = cam_crop
        cs = max(0.0, s - offset)  # screen_time = facecam_time + offset
        args += ["-ss", str(cs), "-to", str(cs + (e - s)), "-i", facecam,
                 "-loop", "1", "-i", mask]
        cam_src = "1:v"
        sdr_cam = media.sdr_chain(facecam)
        if sdr_cam:
            fc.append(f"[1:v]{sdr_cam}[v1sdr]")
            cam_src = "v1sdr"
        fc.append(f"[{cam_src}]crop={cw}:{cw}:{cx}:{cy},"
                  f"{cam_grade + ',' if cam_grade else ''}scale=512:512[cs];"
                  f"[cs][2:v]alphamerge,scale={CAM_D}:{CAM_D}[cam];"
                  f"[{last}][cam]overlay=W-w-42:H-h-42[vc]")
        last = "vc"
    if endtag_png is not None:
        idx = 3 if (facecam and mask) else 1
        args += ["-i", endtag_png]
        fc.append(f"[{last}][{idx}:v]overlay=(W-w)/2:940:"
                  f"enable='gte(t,{tag_at:.2f})'[vt]")
        last = "vt"
    if fc:
        args += ["-filter_complex", ";".join(fc), "-map", f"[{last}]", "-map", "0:a"]
    # Pin both streams to exactly the block length. CFR conversion left each
    # block's video a shade longer than its audio, and concatenating by copy
    # stacks the streams independently, so the voice crept ahead of the picture
    # block by block (5.5s adrift by t=280, 23s of mute video at the end).
    args += ["-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-af", ("adeclick,deesser=i=0.4,highpass=f=70,"
                     f"afade=t=in:st=0:d=0.03,"
                     f"afade=t=out:st={max(0.0, e - s - 0.03):.3f}:d=0.03,apad"),
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-t", f"{e - s:.3f}", dst]
    subprocess.run(args, check=True)


def _chapters(topics, keep, min_gap=60.0, max_n=15):
    """YouTube chapter lines mapped onto the edited timeline."""
    rows, tl, last = [], 0.0, -1e9
    for s, e in keep:
        for c in topics:
            if s <= c["s"] < e:
                t = tl + (c["s"] - s)
                if t - last >= min_gap and len(rows) < max_n:
                    m, sec = int(t // 60), int(t % 60)
                    title = c["text"][:48].rstrip(" ,.")
                    rows.append(f"{m}:{sec:02d} {title}")
                    last = t
        tl += e - s
    if rows and not rows[0].startswith("0:00"):
        rows[0] = "0:00 " + rows[0].split(" ", 1)[1]
    return rows


def run(project, product="video", music=True):
    root = direct.resolve_project(project)
    full = json.load(open(os.path.join(root, "edl", "full_edit.json")))
    words = speech.words_from(os.path.join(root, "analysis", "words.json"))
    tp = os.path.join(root, "analysis", "topics.json")
    topics = json.load(open(tp)) if os.path.exists(tp) else []
    lp = os.path.join(root, "analysis", "loudness.json")
    loud = json.load(open(lp)) if os.path.exists(lp) else {}
    sess_p = os.path.join(root, "analysis", "session.json")
    offset = (json.load(open(sess_p)).get("facecam_offset_s", 0.0)
              if os.path.exists(sess_p) else 0.0)
    video = direct._source_video(root)
    facecam = _facecam_source(root)
    out = os.path.join(root, "deliverables", "longform")
    os.makedirs(out, exist_ok=True)
    keep = full["keep"]
    name = os.path.basename(root)

    cam_crop, mask, cam_grade = None, None, ""
    tmp_holder = tempfile.TemporaryDirectory()
    td = tmp_holder.name
    if facecam:
        box = face.face_box(facecam, keep[0][0], keep[0][1], samples=5)
        v = next(x for x in media.probe(facecam)["streams"]
                 if x.get("codec_type") == "video")
        cam_crop = face.crop_for((v["width"], v["height"]), box)
        mask = captions.circle_mask(os.path.join(td, "mask.png"))
        from . import grade
        cam_grade, _ = grade.auto_grade(facecam, keep[0][0],
                                        max(1.0, keep[-1][1] - keep[0][0]))
        if cam_grade:
            print(f"[long ] cam grade: {cam_grade}")

    endtag = captions.text_png(products.PRODUCTS[product]["end_tag"],
                               os.path.join(td, "endtag.png"),
                               width=900, size=40, fill="#FCFCFB", stroke_w=5)

    parts = []
    print(f"[long ] {len(keep)} blocks, {media.fmt(full['edited_s'])} target ...")
    for i, (s, e) in enumerate(keep):
        p = os.path.join(td, f"b{i:03d}.mp4")
        is_last = i == len(keep) - 1
        _block(video, facecam, cam_crop, mask, s, e, p, offset=offset,
               endtag_png=endtag if is_last else None,
               tag_at=max(0.0, (e - s) - 2.5) if is_last else None,
               cam_grade=cam_grade if facecam else "")
        parts.append(p)
        if (i + 1) % 10 == 0:
            print(f"[long ] {i + 1}/{len(keep)} blocks")
    lst = os.path.join(td, "list.txt")
    open(lst, "w").write("\n".join(f"file '{p}'" for p in parts))
    joined = os.path.join(td, "joined.mp4")
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", joined], check=True)
    final = os.path.join(out, f"{name}_longform.mp4")
    # two-pass loudness (single-pass overshoots true peak; judge-caught)
    pre = os.path.join(td, "a_pre.wav")
    normed = os.path.join(td, "a_norm.wav")
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", joined, "-vn",
                    "-ar", "48000", pre], check=True)
    # score the way in and the way out, then normalize the mix (not just the
    # voice) so the delivered loudness is the loudness of what people hear.
    # Measure the voice track itself: the joined container reports a longer
    # duration than its audio, which put the outro past the end of the episode.
    total_s = float(media.probe(pre)["format"]["duration"])
    if music:
        audir = os.path.join(root, "deliverables", "audio")
        os.makedirs(audir, exist_ok=True)
        bed = audio_post.episode_bed(loud)
        intro = audio_post.longform_music(
            bed, audio_post.INTRO_S, os.path.join(audir, "longform_intro.mp3"),
            "intro", name)
        outro = audio_post.longform_music(
            bed, audio_post.OUTRO_S, os.path.join(audir, "longform_outro.mp3"),
            "outro", name)
        scored = os.path.join(td, "a_scored.wav")
        print(f"[long ] scoring {total_s:.1f}s: intro 0-{audio_post.INTRO_S:.0f}s, "
              f"outro {total_s - audio_post.OUTRO_S:.1f}-{total_s:.1f}s")
        audio_post.score_longform(pre, intro, outro, total_s, scored)
        pre = scored
    audio_post._loudnorm_2pass(pre, normed)
    # -shortest: padding the block audio leaves it a beat past the last frame,
    # and a stream that outruns the picture is the same class of bug as the mute
    # tail it replaced. End the file where both streams still exist.
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", joined, "-i", normed,
                    "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", final], check=True)

    ncues = _retimed_srt(words, keep, os.path.join(out, f"{name}_longform.srt"))
    chapters = _chapters(topics, keep)
    open(os.path.join(out, "chapters.txt"), "w").write("\n".join(chapters) + "\n")
    p = products.PRODUCTS[product]
    open(os.path.join(out, "DESCRIPTION.md"), "w").write("\n".join([
        f"# {name} longform - posting block", "",
        f"Built live with {p['name']}: {products.link(product, 'youtube')}", "",
        "## Chapters (paste into the YouTube description)", "", *chapters, "",
        f"Subtitles: upload `{name}_longform.srt` (brand-corrected).", ""]))
    print(f"[long ] -> {final} ({ncues} srt cues, {len(chapters)} chapters)")
    tmp_holder.cleanup()
    return final
