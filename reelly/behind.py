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
- Only where a brand asks for it (text.behind_subject) AND the content type
  protects faces (talking-head, podcast): never on gameplay or demos.

Install the model with `reelly models fetch segmenter` (or REELLY_SEGMENTER).
"""
import os
import subprocess

from . import config

EMA = 0.55               # weight of the new frame in the temporal smoothing


def find_model():
    from . import models
    return models.path("segmenter")


def enabled(plan=None):
    """The brand's text.behind_subject (or REELLY_TEXT_BEHIND) AND a content
    type that protects faces."""
    from . import config, shaderfx
    try:
        from . import brandkit
        on = brandkit.text_style("behind_subject") is True
    except Exception:   # noqa: BLE001
        on = False
    if not config.env_bool("REELLY_TEXT_BEHIND", on):
        return False
    try:
        return shaderfx.content_flag("faces", shaderfx.content_type(plan))
    except Exception:   # noqa: BLE001
        return False


GUIDE_R, GUIDE_EPS = 8, 1e-4     # chosen on a 3x hair-edge comparison of 4 settings


def guided_filter(rgb, p, r=GUIDE_R, eps=GUIDE_EPS):
    """Edge-aware refinement of a soft mask `p` (float, 0..1) guided by the
    frame's luminance (He, Sun & Tang, Guided Image Filtering): the output
    follows the guide's edges where the mask changes, and stays smooth
    elsewhere. Box filters only (scipy), O(1) in the radius."""
    import numpy as np
    from scipy.ndimage import uniform_filter
    I = (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114).astype(np.float32) / 255.0
    k = 2 * r + 1
    mI, mp_ = uniform_filter(I, k), uniform_filter(p, k)
    cI, cIp = uniform_filter(I * I, k), uniform_filter(I * p, k)
    a = (cIp - mI * mp_) / (cI - mI * mI + eps)
    b = mp_ - a * mI
    return np.clip(uniform_filter(a, k) * I + uniform_filter(b, k), 0.0, 1.0)


def masks(video, out_dir, fps=30, width=540):
    """Person masks for `video` as out_dir/m%05d.png at the video's FULL
    size (white = person). Returns (pattern, fps, n).

    The segmenter sees a half-size frame (its own input is 256 px), so its
    edge is blocky once scaled up: the soft confidence is upsampled and
    refined with a GUIDED FILTER against the full-size frame (guided_filter),
    which snaps the matte to the real edges (hair, shoulders, the face
    outline); then smoothed over time and given a gentle curve, not a hard
    threshold.
    Frames stream one at a time (long cuts do not fill memory)."""
    import numpy as np
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision
    from PIL import Image
    model = find_model()
    if not model:
        from . import models
        raise FileNotFoundError(models.missing_message("segmenter"))
    # (OpenCV's contrib guidedFilter crashed natively next to MediaPipe in
    # one process: the filter is a few box filters, done here)
    os.makedirs(out_dir, exist_ok=True)
    from . import media
    v = next(s for s in media.probe(video)["streams"] if s.get("codec_type") == "video")
    W, H = int(v["width"]), int(v["height"])
    h = int(round(width * H / W / 2)) * 2
    proc = subprocess.Popen([config.FFMPEG, "-v", "error", "-i", video, "-vf", f"fps={fps}",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    opts = vision.ImageSegmenterOptions(base_options=BaseOptions(model_asset_path=model),
                                        running_mode=vision.RunningMode.VIDEO,
                                        output_confidence_masks=True)
    ema, n, size = None, 0, W * H * 3
    try:
        with vision.ImageSegmenter.create_from_options(opts) as seg:
            while True:
                buf = proc.stdout.read(size)
                if len(buf) < size:
                    break
                full = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
                small = np.asarray(Image.fromarray(full).resize((width, h), Image.BILINEAR))
                res = seg.segment_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,
                                                     data=np.ascontiguousarray(small)),
                                            int(n * 1000 / fps))
                conf = np.squeeze(res.confidence_masks[-1].numpy_view()).astype(np.float32)
                up = np.asarray(Image.fromarray(conf, "F").resize((W, H), Image.BILINEAR),
                                dtype=np.float32)
                up = guided_filter(full, up, GUIDE_R, GUIDE_EPS)
                ema = up if ema is None else EMA * up + (1 - EMA) * ema
                m = np.clip((ema - 0.2) / 0.6, 0.0, 1.0)
                m = m * m * (3.0 - 2.0 * m)                      # gentle S-curve, no hard edge
                Image.fromarray((m * 255 + 0.5).astype(np.uint8), "L").save(
                    os.path.join(out_dir, f"m{n:05d}.png"), compress_level=1)
                n += 1
    finally:
        proc.stdout.close()
        proc.wait()
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


def guard(mask_pattern, n, fps, boxes, max_cover=MAX_COVER, on_top=(), grow=(0, 0, 0, 0.0)):
    """Readability first. For each text layer box (t0, t1, x0, y0, x1, y1 in
    the 1080x1920 frame): the share of it the person covers over its window;
    a layer covered more than `max_cover` keeps its text ON TOP. The mask is
    then LIMITED to the boxes allowed behind, during their windows, and
    cleared inside every `on_top` box (captions: always readable), so the
    subject only ever steps in front of the text chosen for it and nothing
    else in the frame (effects, plates, end tag) is replaced by the raw cut.
    grow = (top px, side px, bottom px, extra seconds): how far the text's
    motion reaches beyond its layout box (a block-lock drop from above, the
    aura, petals drifting after the line), so the subject's edge never cuts
    a hard seam through moving type. Coverage is measured on the box itself.
    Rewrites the mask frames in place. Returns [(box, cover, behind?)]."""
    import numpy as np
    from PIL import Image
    if n < 1:
        return []
    probe = Image.open(mask_pattern % 0)
    mw, mh = probe.size
    sx, sy = mw / 1080.0, mh / 1920.0

    def frame_box(b):
        t0, t1, x0, y0, x1, y1 = b
        i0, i1 = max(0, int(t0 * fps)), min(n - 1, int(t1 * fps))
        x0, y0 = max(0, int(x0 * sx)), max(0, int(y0 * sy))
        return i0, i1, (x0, y0, max(x0 + 1, int(x1 * sx)), max(y0 + 1, int(y1 * sy)))

    out, allow = [], []
    for b in boxes:
        i0, i1, bx = frame_box(b)
        covers = [float((np.asarray(Image.open(mask_pattern % i))[bx[1]:bx[3], bx[0]:bx[2]] > 127).mean())
                  for i in range(i0, i1 + 1)]
        cover = float(np.mean(covers)) if covers else 0.0
        behind = cover <= max_cover
        if behind:
            gt, gs, gb, gl = grow
            t0, t1, x0, y0, x1, y1 = b
            allow.append(frame_box((t0, t1 + gl, x0 - gs, y0 - gt, x1 + gs, y1 + gb))[0:3])
        out.append((tuple(b), round(cover, 3), behind))
    block = [frame_box(b) for b in on_top]
    for i in range(n):
        m = np.asarray(Image.open(mask_pattern % i))
        keep = np.zeros(m.shape, bool)
        for i0, i1, bx in allow:
            if i0 <= i <= i1:
                keep[bx[1]:bx[3], bx[0]:bx[2]] = True
        for i0, i1, bx in block:
            if i0 <= i <= i1:
                keep[bx[1]:bx[3], bx[0]:bx[2]] = False
        Image.fromarray(np.where(keep, m, 0).astype(np.uint8), "L").save(
            mask_pattern % i, compress_level=1)
    return out
