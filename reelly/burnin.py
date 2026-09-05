"""BURNIN: karaoke captions + intro/outro beds on a human-finished master.

The DVR export is the picture lock. This pass transcribes THAT file (so the
words match the final edit, not the raw recording), burns bottom-center
word-highlight captions, and scores the longform intro/outro music beds.

A 12-minute video would need thousands of overlay inputs in one ffmpeg
graph, so the burn runs in chunks split at caption gaps and concatenates.
"""
import json
import os
import subprocess
import tempfile

from . import audio_post, captions, config, media, speech, topics


def _ts(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def _sidecars(out, words, cues):
    """SRT + chapter list beside the master, cut from the burned timeline."""
    srt = os.path.splitext(out)[0] + ".srt"
    rows = []
    for i, (s, e, wl) in enumerate(cues, 1):
        rows.append(f"{i}\n{_ts(s)} --> {_ts(e)}\n"
                    + " ".join(x["t"] for x in wl) + "\n")
    open(srt, "w").write("\n".join(rows))
    ch = os.path.splitext(out)[0] + "_chapters.txt"
    sents = topics.sentences(words)
    clips = topics.topic_clips(sents)
    lines, last = [], -1e9
    for c in clips:
        s = c["s"] if isinstance(c, dict) else c[0]
        text = c.get("text", "") if isinstance(c, dict) else ""
        if s - last >= 55 and len(lines) < 15:
            lines.append(f"{int(s // 60)}:{int(s % 60):02d} "
                         + str(text)[:48].rstrip(" ,."))
            last = s
    if lines:
        lines[0] = "0:00 " + lines[0].split(" ", 1)[1]
        open(ch, "w").write("\n".join(lines) + "\n")
    print(f"[burn ] sidecars: {os.path.basename(srt)}, "
          f"{os.path.basename(ch)} (retitle chapters by hand)")


def _cut_pauses(video, workdir, max_pause=0.9, keep_pause=0.35):
    """Collapse silences longer than max_pause down to keep_pause (playbook
    C1/C3: cut on pauses, keep breathing room in long-form).

    Single-pass trim/concat filter graph on purpose: per-segment files
    concatenated by copy accumulate ~21ms of AAC priming per boundary, which
    audibly desyncs the cam after a few minutes (shipped bug, user-caught).
    One graph = one shared timeline = sync by construction."""
    total = float(media.probe(video)["format"]["duration"])
    sil = speech.get_silences(video, min_d=max_pause)
    cuts = []
    for s, e in sil:
        if e - s > max_pause and s > 0.5 and e < total - 0.5:
            half = keep_pause / 2
            cuts.append((s + half, e - half))
    if not cuts:
        print(f"[burn ] pauses: nothing longer than {max_pause}s, keeping as is")
        return video
    keep, t = [], 0.0
    for s, e in cuts:
        if s - t > 0.05:
            keep.append((t, s))
        t = e
    if total - t > 0.05:
        keep.append((t, total))
    removed = total - sum(e - s for s, e in keep)
    print(f"[burn ] pauses: {len(cuts)} cuts, {removed:.1f}s removed")
    fc = []
    for i, (s, e) in enumerate(keep):
        fc.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]")
        fc.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]")
    pairs = "".join(f"[v{i}][a{i}]" for i in range(len(keep)))
    fc.append(f"{pairs}concat=n={len(keep)}:v=1:a=1[v][a]")
    out = os.path.join(workdir, "trimmed.mp4")
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", video,
                    "-filter_complex", ";".join(fc),
                    "-map", "[v]", "-map", "[a]",
                    "-r", "30", "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "18", "-c:a", "aac", "-b:a", "192k",
                    "-ar", "48000", out], check=True)
    return out


CUE_Y = 940          # bottom-center for 1080p, clear of platform UI
CHUNK_S = 75.0       # target chunk length; cuts only happen between cues
MIN_SPEECH_WORDS = 3  # below this a clip has no real speech -> skip captioning


def _chunks(cues, total):
    """[(start, end, [cue, ...])] split at caption gaps near CHUNK_S."""
    out, cur, t0 = [], [], 0.0
    for c in cues:
        cur.append(c)
        if c[1] - t0 >= CHUNK_S:
            out.append((t0, c[1], cur))
            t0, cur = c[1], []
    out.append((t0, total, cur))
    return [(s, e, cs) for s, e, cs in out if e - s > 0.05]


def _burn_chunk(video, s, e, cs, workdir, idx):
    dst = os.path.join(workdir, f"part{idx:03d}.mp4")
    args = [config.FFMPEG, "-y", "-v", "error",
            "-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-i", video]
    filters, last, n = [], "0:v", 1
    for ci, (_cs, _ce, wlist) in enumerate(cs):
        for wi, w in enumerate(wlist):
            png = os.path.join(workdir, f"k{idx}_{ci}_{wi}.png")
            captions.karaoke_png([x["t"] for x in wlist], wi, png)
            w_end = wlist[wi + 1]["s"] if wi + 1 < len(wlist) else max(_ce, w["e"])
            args += ["-i", png]
            filters.append(
                f"[{last}][{n}:v]overlay=(W-w)/2:{CUE_Y}:"
                f"enable='between(t,{w['s'] - s:.2f},{w_end - s:.2f})'[v{n}]")
            last = f"v{n}"
            n += 1
    if filters:
        args += ["-filter_complex", ";".join(filters), "-map", f"[{last}]"]
    else:
        args += ["-map", "0:v"]
    args += ["-an", "-r", "30", "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "18", dst]
    subprocess.run(args, check=True)
    return dst


def run(video, project=None, out=None, music=True, cut_pauses=False,
        max_pause=0.9):
    out = out or os.path.splitext(video)[0] + "_captioned.mp4"
    with tempfile.TemporaryDirectory() as td:
        src = video
        if cut_pauses:
            src = _cut_pauses(video, td, max_pause=max_pause)
        total = float(media.probe(src)["format"]["duration"])
        # cache the transcript beside the ORIGINAL, keyed against staleness:
        # a re-exported master with the same name must retranscribe
        words_json = os.path.splitext(video)[0] + ".words.json"
        if src != video:
            words_json = os.path.join(td, "words.json")
        if (not os.path.exists(words_json)
                or os.path.getmtime(words_json) < os.path.getmtime(video)):
            from . import transcribe as tr
            print(f"[burn ] transcribing {os.path.basename(src)} ...")
            tr.transcribe(src, words_json)
        words = speech.words_from(words_json)
        # Auto-safe (reviewer 2026-08-13): only caption when there is REAL speech.
        # A clip with ambient/music audio transcribes to a word or two of noise;
        # captioning it invents lines. Below the floor, hand back the source
        # unchanged so auto-running burnin on any post never fabricates captions.
        if len(words) < MIN_SPEECH_WORDS:
            print(f"[burn ] only {len(words)} word(s) -- no real speech in "
                  f"{os.path.basename(video)}; skipping karaoke (returned unchanged)")
            return video
        cues = speech.group_cue_words(words)
        print(f"[burn ] {len(words)} words, {len(cues)} cues, {total:.1f}s")
        video = src
        parts = []
        for i, (s, e, cs) in enumerate(_chunks(cues, total)):
            parts.append(_burn_chunk(video, s, e, cs, td, i))
            print(f"[burn ] chunk {i + 1}: {s:.1f}-{e:.1f}s, {len(cs)} cues")
        lst = os.path.join(td, "parts.txt")
        open(lst, "w").write("".join(f"file '{p}'\n" for p in parts))
        joined = os.path.join(td, "joined.mp4")
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-f", "concat",
                        "-safe", "0", "-i", lst, "-c", "copy", joined],
                       check=True)

        pre = os.path.join(td, "pre.wav")
        media.extract_wav(video, pre)
        audio = pre
        if music and project:
            from . import direct
            audir = os.path.join(direct.resolve_project(project),
                                 "deliverables", "audio")
            intro = os.path.join(audir, "longform_intro.mp3")
            outro = os.path.join(audir, "longform_outro.mp3")
            if os.path.exists(intro) and os.path.exists(outro):
                scored = os.path.join(td, "scored.wav")
                audio_post.score_longform(pre, intro, outro, total, scored)
                audio = scored
                print(f"[burn ] scored: intro 0-{audio_post.INTRO_S:.0f}s, "
                      f"outro {total - audio_post.OUTRO_S:.1f}-{total:.1f}s")
            else:
                print("[burn ] no beds found, voice only")

        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", joined,
                        "-i", audio, "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-ar", "48000", "-shortest", out], check=True)
    _sidecars(out, words, cues)
    print(f"[burn ] -> {out}")
    return out
