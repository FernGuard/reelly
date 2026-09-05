"""Deterministic gacha-style reveal: ordered cards composited over a cosmic
background with escalating size + fast punchy transitions + SFX, climaxing on the
last card. The repeatable path for a DESIGNED-CARD SEQUENCE (winners / standings
/ theme reveals) -- which the generative `motion` path cannot do (it reinterprets
a designed card into an invented scene, and won't honour an exact N-card order).
Cards are composited, so their text stays razor-sharp; the order is guaranteed.
The order is guaranteed so a designed card sequence stays intact."""
import os
import subprocess
import tempfile

from . import config

FPS = 30
SFX = os.path.expanduser("~/.reelly/sfx")
XF = ["zoomin", "circleopen", "diagtl", "zoomin", "squeezeh", "zoomin"]
XD = 0.18  # transition duration -- fast


def _sh(*a):
    subprocess.run(a, check=True)


def _dur(p):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", p], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _nebula_still(path, w=1080, h=1920):
    """A procedural starfield+nebula still, the seed for a generated cosmic bg."""
    import hashlib
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGB", (w, h), (4, 5, 12))
    d = ImageDraw.Draw(img)
    rnd = lambda i, m: int(hashlib.md5(str(i).encode()).hexdigest(), 16) % m
    for i in range(320):
        x, y, r = rnd(i * 3, w), rnd(i * 3 + 1, h), rnd(i * 3 + 2, 3) + 1
        c = 170 + rnd(i, 80)
        d.ellipse([x, y, x + r, y + r], fill=(c, c, min(255, c + 30)))
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-200, 500, 650, 1500], fill=(45, 25, 80))
    gd.ellipse([500, 150, 1350, 1050], fill=(15, 40, 75))
    gd.ellipse([200, 1100, 1000, 1900], fill=(60, 20, 60))
    Image.blend(img, glow.filter(ImageFilter.GaussianBlur(180)), 0.55).save(path)
    return path


def _cosmic_bg(out, project, seconds):
    """Generate a card-free cinematic cosmic warp bg (no audio -> no copyright
    filter), or fall back to a slow drift over the procedural nebula still."""
    from . import motion
    td = os.path.dirname(out)
    still = os.path.join(td, "_nebula.png")
    _nebula_still(still)
    prompt = ("Cinematic warp-speed flight through a vast deep-space nebula: "
              "luminous purple-blue-magenta clouds, dense starfield streaking "
              "past, drifting stardust, volumetric god-rays and lens flares, "
              "gentle camera roll and forward push. NO text, NO cards, NO people, "
              "NO UI, NO watermark. Smooth continuous motion, no cuts, no white "
              "flashes.")
    try:
        endpoint, payload, est = motion._video_request(
            "seedance", [motion._data_uri(still)], prompt, seconds)
        payload["generate_audio"] = False   # bg plate: avoid the audio copyright gate
        from . import audio_post
        url = audio_post._fal(endpoint, payload, est, "reveal cosmic-bg", project,
                              service="fal-video", find=motion._find_video_url, tries=900)
        tmp = out + ".dl.mp4"
        audio_post._download(url, tmp)
        _sh(config.FFMPEG, "-y", "-v", "error", "-i", tmp, "-vf",
            "scale=1080:1920:flags=lanczos", "-an", "-c:v", "libx264", "-crf", "17",
            "-preset", "medium", out)
        os.remove(tmp)
        return out
    except Exception as e:  # noqa: BLE001 -- bg generation is best-effort
        print(f"[reveal] cosmic bg generation failed ({e}); "
              f"drifting the nebula still instead")
        _sh(config.FFMPEG, "-y", "-v", "error", "-loop", "1", "-t", f"{seconds}",
            "-i", still, "-vf",
            "scale=2160:3840,zoompan=z='min(pzoom+0.0006,1.15)':d=1:"
            f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={FPS},setsar=1",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium", out)
        return out


def _glow(card, out):
    """Bake a soft outer glow around a card so its punch-in reads on the bg."""
    from PIL import Image, ImageDraw, ImageFilter
    c = Image.open(card).convert("RGBA")
    pad = 120
    W, H = c.width + pad * 2, c.height + pad * 2
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(g).rounded_rectangle(
        [pad, pad, pad + c.width, pad + c.height], radius=40, fill=(120, 180, 255, 200))
    canvas = g.filter(ImageFilter.GaussianBlur(60))
    canvas.alpha_composite(c, (pad, pad))
    canvas.save(out)
    return out


def _beat(bg, glow, dur, cw, zr, shake, gold, bg_start, out):
    ox = f"(W-w)/2 + {shake}*sin(46*t)" if shake else "(W-w)/2"
    oy = f"(H-h)/2 + {shake}*cos(43*t)" if shake else "(H-h)/2"
    zmax = 1.0 + zr * dur
    push = (f"scale=2160:3840,zoompan=z='min(pzoom+{zr / FPS:.5f},{zmax:.3f})':"
            f"d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps={FPS}")
    gold_f = ",eq=gamma_r=1.06:gamma=1.02:saturation=1.08" if gold else ""
    fc = (f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
          f"crop=1080:1920,setsar=1,fps={FPS}[bg];[1:v]scale={cw}:-2[c];"
          f"[bg][c]overlay=x='{ox}':y='{oy}'[cp];"
          f"[cp]{push},setsar=1,fade=in:st=0:d=0.10{gold_f},format=yuv420p[v]")
    _sh(config.FFMPEG, "-y", "-v", "error", "-ss", f"{bg_start:.2f}", "-t", f"{dur:.2f}",
        "-i", bg, "-loop", "1", "-t", f"{dur:.2f}", "-i", glow,
        "-filter_complex", fc, "-map", "[v]", "-r", str(FPS),
        "-c:v", "libx264", "-crf", "18", "-preset", "medium", out)
    return out, dur


def build(cards, out, bg=None, project="", seconds=None):
    """Ordered card images (last = climax) -> a gacha reveal mp4.

    `bg` = a cosmic background mp4; generated if omitted. Card size and push-in
    escalate toward the last card, which also gets a gold tint + shake. Fast
    xfade transitions + whoosh/ding SFX."""
    n = len(cards)
    if n < 2:
        raise ValueError("reveal needs at least 2 cards")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        if not bg:
            bg = _cosmic_bg(os.path.join(td, "cosmic_bg.mp4"), project, 12)
        bd = _dur(bg) or 12.0
        # escalating params: size 620..930, duration ramps, champion is last
        beats = []
        start = 0.0
        for i, card in enumerate(cards):
            f = i / (n - 1)
            cw = int(round(620 + (930 - 620) * f))
            dur = round(1.0 + 1.9 * (f ** 1.7), 2)          # ramps toward the climax
            zr = round(0.06 + 0.09 * f, 3)
            champ = (i == n - 1)
            glow = _glow(card, os.path.join(td, f"g_{i}.png"))
            bs = start % max(1.0, bd - dur - 0.1)
            b, d = _beat(bg, glow, dur, cw, zr, 7 if champ else 0, champ, bs,
                         os.path.join(td, f"beat_{i}.mp4"))
            beats.append((b, d))
            start += dur
        # fast xfade chain
        inputs, fc, prev, off, times = [], [], "[0:v]", 0.0, []
        for b, _ in beats:
            inputs += ["-i", b]
        for k in range(1, n):
            off += beats[k - 1][1] - XD
            times.append(off)
            fc.append(f"{prev}[{k}:v]xfade=transition={XF[(k - 1) % len(XF)]}:"
                      f"duration={XD}:offset={off:.2f}[x{k}]")
            prev = f"[x{k}]"
        vid = os.path.join(td, "vid.mp4")
        _sh(config.FFMPEG, "-y", "-v", "error", *inputs, "-filter_complex", ";".join(fc),
            "-map", prev, "-r", str(FPS), "-c:v", "libx264", "-crf", "18",
            "-preset", "medium", vid)
        # SFX: whoosh at each cut, ding+pop on the champion reveal
        a_in, a_fc, ai = [], [], 0
        def add(path, t, vol):
            nonlocal ai
            if not os.path.exists(path):
                return
            a_in.extend(["-i", path])
            a_fc.append(f"[{ai + 1}:a]adelay={int(max(0, t) * 1000)}|{int(max(0, t) * 1000)},"
                        f"volume={vol}[a{ai}]")
            ai += 1
        for t in times:
            add(f"{SFX}/whoosh.mp3", t - 0.05, "-8dB")
        champ_t = sum(b[1] - XD for b in beats[:-1])
        add(f"{SFX}/ding.mp3", champ_t + 0.1, "-6dB")
        add(f"{SFX}/pop.mp3", champ_t + 0.05, "-10dB")
        if ai:
            mix = "".join(f"[a{i}]" for i in range(ai)) + \
                  f"amix=inputs={ai}:normalize=0,alimiter=limit=0.9[aout]"
            _sh(config.FFMPEG, "-y", "-v", "error", "-i", vid, *a_in,
                "-filter_complex", ";".join(a_fc) + ";" + mix,
                "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
                "-b:a", "192k", "-shortest", out)
        else:
            _sh(config.FFMPEG, "-y", "-v", "error", "-i", vid, "-c", "copy", out)
    print(f"[reveal] {n} cards -> {out}")
    return out
