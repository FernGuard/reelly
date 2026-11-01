"""`reelly fx list` and `reelly fx preview`: see an effect before using it.

A preview renders one preset over a stand-in for a cut: a 16:9 "your footage"
panel in the brand's accent, with a blurred, dimmed copy of it around it
(where a real cut has its blurred fill).
A scripted control track drives it (a beat every half second, two hot-word
pops, two real impacts), so every effect shows how it reacts. Colours come
from the active brand (--brand); --set tries parameter values without
touching brand.json.
"""
import json
import os
import subprocess
import tempfile
import textwrap

from . import config

W, H = 1080, 1920
PIC_H = 608                            # a 16:9 picture shown full width
PIC_Y = (H - PIC_H) // 2
SAMPLE = "Every frame counts."


def _ctrl(seconds, fps=60):
    import math
    n = int(round(seconds * fps))
    return {"beats": [0.25, 0.5], "pops": [0.8, min(2.6, seconds * 0.7)],
            "onsets": [0.6, 1.2, 2.0, 2.8], "fps": fps,
            "energy": [0.25 + 0.55 * abs(math.sin(i / 11.0)) for i in range(n)],
            "events": [{"t": 1.2, "x": 0.35, "y": 0.35, "s": 1.0},
                       {"t": min(2.8, seconds * 0.8), "x": 0.7, "y": 0.6, "s": 0.9}]}


def _pic_clear():
    pic = (0.0, 1 - (PIC_Y + PIC_H) / H, 1.0, 1 - PIC_Y / H)
    clear = (1 - PIC_Y / H, 1 - (PIC_Y - 220) / H)
    return pic, clear


def _backdrop(td):
    """The stand-in frame (PNG, 1080x1920) laid out like a real cut: a 16:9
    "your footage" panel, and around it a blurred, dimmed copy of it (Reelly's
    blurred fill), which ambient layers and the finish draw on."""
    from PIL import Image, ImageDraw, ImageFilter
    from . import brandkit, captions
    acc = brandkit.accent()
    a = tuple(int(acc[i:i + 2], 16) for i in (1, 3, 5))
    panel = Image.new("RGB", (W, PIC_H))
    d = ImageDraw.Draw(panel)
    for x in range(W):
        k = x / (W - 1)
        d.line([(x, 0), (x, PIC_H)], fill=tuple(int(24 + (v * 0.5 - 24) * k) for v in a))
    blobs = Image.new("RGB", (W, PIC_H))
    bd = ImageDraw.Draw(blobs)
    for cx, cy, r, c in ((260, 200, 170, a), (760, 380, 210, (230, 150, 60)),
                         (520, 470, 120, (90, 200, 220))):
        bd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    panel = Image.blend(panel, Image.composite(blobs, panel, blobs.convert("L").point(
        lambda v: 255 if v else 0)).filter(ImageFilter.GaussianBlur(40)), 0.6)
    fill = panel.resize((int(W * H / PIC_H), H)).crop(
        (int((W * H / PIC_H - W) / 2), 0, int((W * H / PIC_H - W) / 2) + W, H))
    fill = fill.filter(ImageFilter.GaussianBlur(36)).point(lambda v: int(v * 0.55))
    im = fill.copy()
    im.paste(panel, (0, PIC_Y))
    d = ImageDraw.Draw(im)
    f = captions._font(40, "caption")
    t = "your footage"
    d.text(((W - d.textlength(t, font=f)) / 2, PIC_Y + PIC_H / 2 - 24), t, font=f,
           fill=(240, 240, 244))
    p = os.path.join(td, "backdrop.png")
    im.save(p)
    return p


def _encode_overlay(backdrop, seq, fps, seconds, out, y=0, scale_w=W):
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-loop", "1", "-framerate", str(fps),
                    "-i", backdrop, "-framerate", str(fps), "-i", seq, "-filter_complex",
                    f"[1:v]scale={scale_w}:-2:flags=bicubic,format=rgba[f];"
                    f"[0:v][f]overlay=(W-w)/2:{y}:eof_action=pass,format=yuv420p[v]",
                    "-map", "[v]", "-t", f"{seconds:.3f}", "-r", "30",
                    *config.final_encode_args(), out], check=True)


def _encode_frames(seq, fps, seconds, out):
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-framerate", str(fps), "-i", seq,
                    "-vf", f"scale={W}:{H}:flags=bicubic,format=yuv420p", "-t", f"{seconds:.3f}",
                    "-r", "30", *config.final_encode_args(), out], check=True)


def preview(name, out=None, seconds=4.0, params=None):
    """Render a preview MP4 of preset `name` ("ambient/embers" or "embers").
    Returns the output path."""
    from . import brandkit, gputext, shaderfx
    pid = shaderfx.resolve(name)
    placement, short = pid.split("/", 1)
    out = out or f"fx-{placement}-{short}.mp4"
    seconds = max(1.0, min(15.0, float(seconds)))
    if params:
        shaderfx.uniforms(pid, params)               # refuse unknown names up front
        shaderfx._TRY[pid] = dict(params)
    fps = 30
    w, h = W // 2, H // 2
    ctrl = _ctrl(seconds)
    pic, clear = _pic_clear()
    try:
        with tempfile.TemporaryDirectory(prefix="reelly-fx-") as td:
            bg = _backdrop(td)
            frames = os.path.join(td, "f")
            if placement == "ambient":
                html = shaderfx.ambient_html(pid, w, h, brandkit.accent(), pic, clear,
                                             ctrl["pops"], end=seconds, ctrl=ctrl)
                seq = shaderfx.render_frames(html, frames, w, h, seconds, fps)
                _encode_overlay(bg, seq, fps, seconds, out)
            elif placement == "endcard":
                html = shaderfx.page_html(pid, bg, w, h, brandkit.accent(), pulse_t=1.2,
                                          ctrl=ctrl, ctrl_t0=0.0, from_png=bg)
                _encode_frames(shaderfx.render_frames(html, frames, w, h, seconds, fps),
                               fps, seconds, out)
            elif placement == "text":
                lines = [SAMPLE.split()]
                html, _, _, off = gputext.page(SAMPLE, 72, 860, 220, seconds, "headline", 1,
                                               lines, True, True, 0.9,
                                               brandkit.ink() or "#FCFCFB", brandkit.accent(),
                                               aura=pid)
                tw, th = 860 + 2 * off, 220 + 2 * off
                seq = shaderfx.render_frames(html, frames, tw, th, seconds, fps)
                _encode_overlay(bg, seq, fps, seconds, out, y=PIC_Y - th - 20, scale_w=tw)
            elif placement == "finish":
                src = os.path.join(td, "src")
                os.makedirs(src)
                subprocess.run([config.FFMPEG, "-y", "-v", "error", "-loop", "1", "-i", bg,
                                "-vf", f"fps={fps},scale={w}:{h}", "-t", f"{seconds:.3f}",
                                "-q:v", "3", "-start_number", "0",
                                os.path.join(src, "s%05d.jpg")], check=True)
                n = len(os.listdir(src))
                seq, ffps = shaderfx.render_finish(pid, src, n, fps, frames, pic, clear)
                _encode_overlay(bg, seq, ffps, seconds, out)
            elif placement == "still":
                from PIL import Image
                dep = os.path.join(td, "depth.png")
                dm = Image.new("L", (w, h))
                for y in range(h):
                    dm.paste(int(255 * y / (h - 1)), (0, y, w, y + 1))
                dm.save(dep)
                html = shaderfx.parallax_html(pid, bg, dep, w, h, seconds, params)
                _encode_frames(shaderfx.render_frames(html, frames, w, h, seconds, fps),
                               fps, seconds, out)
            else:
                raise shaderfx.EffectError(f"no preview for placement {placement!r}")
    finally:
        shaderfx._TRY.pop(pid, None)
    return out


def listing():
    """The text `reelly fx list` prints."""
    from . import shaderfx
    L = []
    for placement in shaderfx.PLACEMENTS:
        ids = [p for p in shaderfx.presets() if p.startswith(placement + "/")]
        if not ids:
            continue
        L.append(f"{placement}")
        for pid in ids:
            p = shaderfx.load_preset(pid)
            header, _ = shaderfx.load_effect(p["effect"])
            L.append(f"  {pid.split('/', 1)[1]}")
            for line in textwrap.wrap(p.get("description", ""), 72):
                L.append(f"      {line}")
            for inp in header.get("INPUTS", []):
                if inp.get("HOST"):
                    continue
                v = p.get("params", {}).get(inp["NAME"], inp.get("DEFAULT"))
                rng = (f" {inp['MIN']}..{inp['MAX']}" if "MIN" in inp and "MAX" in inp else "")
                slot = f" (brand {inp['SLOT']})" if inp.get("SLOT") else ""
                L.append(f"      {inp['NAME']:<12} {inp['TYPE']:<6} {json.dumps(v)}{rng}{slot}")
        L.append("")
    L.append("Preview one: reelly fx preview <name> [--brand NAME] [--set u_name=value]")
    return "\n".join(L)
