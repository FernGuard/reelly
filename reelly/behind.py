"""Text behind the subject: the person is composited back over the burned
text, so captions pass behind their head (the "text behind subject" look).

WHY
On a talking head the strongest frame position for a hook overlaps the
person. Behind them the type reads as part of the shot instead of a sticker
on top, and the face is never covered.

HOW
- Mask: MediaPipe's selfie segmenter (Apache-2.0, selfie_segmenter.tflite,
  244 KB) in VIDEO mode on the pre-caption cut, at half width; the
  confidence is smoothed over time (an exponential average, so the edge
  does not flicker) and feathered, then written as greyscale frames.
- Composite: the burned cut, then the raw cut with the mask as its alpha
  on top: the person covers the text, everything else keeps it.
- Only where a brand asks for it (fx.text_behind) AND the content profile
  protects faces (talking_head, podcast): never on gameplay or demos.

REELLY_SEGMENTER = path to selfie_segmenter.tflite (else the usual places).
"""
import os
import subprocess

from . import config

CANDIDATES = (
    r"C:\Patolli\tools\models\selfie-segmenter\selfie_segmenter.tflite",
    os.path.expanduser("~/.reelly/models/selfie-segmenter/selfie_segmenter.tflite"),
)
EMA = 0.55               # weight of the new frame in the temporal smoothing


def find_model():
    p = os.environ.get("REELLY_SEGMENTER", "").strip()
    if p and os.path.exists(p):
        return p
    return next((c for c in CANDIDATES if os.path.exists(c)), None)


def enabled(plan=None):
    """Brand fx.text_behind AND a content profile that protects faces."""
    from . import shaderfx
    try:
        from . import brandkit
        fx = brandkit.brand().get("fx")
        on = bool(isinstance(fx, dict) and fx.get("text_behind"))
    except Exception:   # noqa: BLE001
        on = False
    if os.environ.get("REELLY_TEXT_BEHIND", "").strip().lower() in ("1", "on", "yes"):
        on = True
    if not on:
        return False
    try:
        prof = shaderfx.load_profile(shaderfx.profile_name(plan))
    except Exception:   # noqa: BLE001
        return False
    return "faces" in (prof.get("protect") or [])


def masks(video, out_dir, fps=30, width=540):
    """Person masks for `video` as out_dir/m%05d.png (white = person).
    Returns (pattern, fps, n)."""
    import numpy as np
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision
    from PIL import Image
    from scipy.ndimage import gaussian_filter
    model = find_model()
    if not model:
        raise FileNotFoundError("selfie_segmenter.tflite not installed")
    os.makedirs(out_dir, exist_ok=True)
    from . import media
    v = next(s for s in media.probe(video)["streams"] if s.get("codec_type") == "video")
    h = int(round(width * int(v["height"]) / int(v["width"]) / 2)) * 2
    r = subprocess.run([config.FFMPEG, "-v", "error", "-i", video, "-vf",
                        f"fps={fps},scale={width}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, check=True)
    n = len(r.stdout) // (width * h * 3)
    frames = np.frombuffer(r.stdout[: n * width * h * 3], np.uint8).reshape(n, h, width, 3)
    opts = vision.ImageSegmenterOptions(base_options=BaseOptions(model_asset_path=model),
                                        running_mode=vision.RunningMode.VIDEO,
                                        output_confidence_masks=True)
    ema = None
    with vision.ImageSegmenter.create_from_options(opts) as seg:
        for i in range(n):
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frames[i]))
            res = seg.segment_for_video(img, int(i * 1000 / fps))
            conf = np.squeeze(res.confidence_masks[-1].numpy_view()).astype(np.float32)
            ema = conf if ema is None else EMA * conf + (1 - EMA) * ema
            m = np.clip((ema - 0.35) / 0.3, 0.0, 1.0)            # firm core, soft edge
            m = gaussian_filter(m, 1.2)
            Image.fromarray((m * 255 + 0.5).astype(np.uint8), "L").save(
                os.path.join(out_dir, f"m{i:05d}.png"), compress_level=1)
    return os.path.join(out_dir, "m%05d.png"), fps, n


def apply(burned, raw, mask_pattern, mask_fps, dst):
    """The raw cut, masked to the person, over the burned cut."""
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", burned, "-i", raw,
                    "-framerate", str(mask_fps), "-i", mask_pattern, "-filter_complex",
                    "[2:v]scale=1080:1920:flags=bicubic,format=gray[m];"
                    "[1:v]scale=1080:1920,format=rgba[r];[r][m]alphamerge[cut];"
                    "[0:v][cut]overlay=0:0:shortest=1,format=yuv420p[v]",
                    "-map", "[v]", "-an", "-r", str(config.output_fps()),
                    *config.final_encode_args(), dst], check=True)
    return dst


MAX_COVER = 0.25         # a layer the person covers more than this stays on top


def guard(mask_pattern, n, fps, boxes, max_cover=MAX_COVER):
    """Readability first: for each text layer box (t0, t1, x0, y0, x1, y1 in
    the 1080x1920 frame), the share of it the person covers over its
    window; a layer covered more than `max_cover` keeps its text ON TOP
    (the mask is cleared inside its box for its window). Rewrites the mask
    frames in place. Returns [(box, cover, behind?)]."""
    import numpy as np
    from PIL import Image
    if not boxes or n < 1:
        return []
    probe = Image.open(mask_pattern % 0)
    mw, mh = probe.size
    sx, sy = mw / 1080.0, mh / 1920.0
    out = []
    for (t0, t1, x0, y0, x1, y1) in boxes:
        i0, i1 = max(0, int(t0 * fps)), min(n - 1, int(t1 * fps))
        bx = (int(x0 * sx), int(y0 * sy), max(int(x0 * sx) + 1, int(x1 * sx)),
              max(int(y0 * sy) + 1, int(y1 * sy)))
        covers = [float((np.asarray(Image.open(mask_pattern % i))[bx[1]:bx[3], bx[0]:bx[2]] > 127).mean())
                  for i in range(i0, i1 + 1)]
        cover = float(np.mean(covers)) if covers else 0.0
        behind = cover <= max_cover
        if not behind:
            for i in range(i0, i1 + 1):
                m = np.array(Image.open(mask_pattern % i))
                m[bx[1]:bx[3], bx[0]:bx[2]] = 0
                Image.fromarray(m, "L").save(mask_pattern % i, compress_level=1)
        out.append(((t0, t1, x0, y0, x1, y1), round(cover, 3), behind))
    return out
