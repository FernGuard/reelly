"""Face tracking for the cam insert: keep the face centered whatever the
mask shape is (playbook CO5).

Samples frames from the facecam across a time window, detects the face with
mediapipe FaceMesh (face_landmarker.task, cached in ~/.reelly/models; falls
back to blaze_face_short_range offline), and returns a median face box.
Median over samples beats per-frame tracking here: a webcam face barely
moves, and a static well-centered crop avoids swimming video.

Frames come through faceio (one ffmpeg spawn per window, downscaled) and
results land in the faceio disk cache, so preview/finalize/handoff pay for
detection once per window, not once per run.
"""
import functools
import os
import threading
import urllib.request

import numpy as np

from . import config, faceio

MODEL = os.path.join(config.HOME, "models", "blaze_face_short_range.tflite")
MESH_MODEL = os.path.join(config.HOME, "models", "face_landmarker.task")
MESH_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task")

# Landmark bbox (chin..forehead) is tighter than the old BlazeFace detection
# box; pad it back out so crop framing stays visually equivalent.
BOX_MARGIN = 0.10

# FaceMesh landmark indices: eye corners and outer lips.
_EYES = (33, 133, 362, 263)
_LIPS = (61, 291, 0, 17, 13, 14, 78, 308)

# The FaceLandmarker/BlazeFace singletons are shared across 3-wide render
# pools and MediaPipe Tasks is not documented thread-safe. Detection is a
# tiny fraction of render time, so serialising the actual .detect() calls
# costs nothing and removes the race entirely.
_DETECT_LOCK = threading.Lock()


def _ensure_model():
    """face_landmarker.task on disk, downloading once if missing.

    Atomic (tmp then rename) so a killed download never leaves a corrupt
    model. Returns False on any failure -- caller falls back to BlazeFace.
    """
    if os.path.exists(MESH_MODEL):
        return True
    try:
        os.makedirs(os.path.dirname(MESH_MODEL), exist_ok=True)
        tmp = MESH_MODEL + ".tmp"
        urllib.request.urlretrieve(MESH_URL, tmp)
        os.replace(tmp, MESH_MODEL)
        return True
    except Exception:
        return False


def _detector():
    """BlazeFace detector (fallback path). Kept for compatibility."""
    from mediapipe.tasks.python import BaseOptions, vision
    opts = vision.FaceDetectorOptions(
        base_options=BaseOptions(model_asset_path=MODEL),
        min_detection_confidence=0.5)
    return vision.FaceDetector.create_from_options(opts)


@functools.lru_cache(maxsize=1)
def _landmarker():
    """Singleton FaceLandmarker, or None if the model can't be had."""
    if not _ensure_model():
        return None
    try:
        from mediapipe.tasks.python import BaseOptions, vision
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=MESH_MODEL),
            num_faces=2)
        return vision.FaceLandmarker.create_from_options(opts)
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def _blaze():
    """Singleton BlazeFace detector for the fallback path."""
    try:
        return _detector()
    except Exception:
        return None


def detector_kind():
    """Which detector actually produces face boxes on this machine/process:
    'facemesh', 'blaze' (fallback), or 'none'. Part of the raw-render cache
    key: the two detectors frame the facecam crop differently, so a cached
    raw cut from one must not be reused under the other."""
    try:
        if _landmarker() is not None:
            return "facemesh"
        return "blaze" if _blaze() is not None else "none"
    except Exception:
        return "none"


def detect_faces(frame):
    """Faces in an RGB frame, largest first.

    Each face is a dict in FRAME pixel coords:
      cx, cy, h   -- box center and height (h approximates the old
                     BlazeFace detection-box height)
      w           -- box width
      eye_y       -- eye-line y from landmarks, or None (BlazeFace fallback)
      mouth       -- (x0, y0, x1, y1) lip box from landmarks, or None
    """
    import mediapipe as mp
    if frame is None:
        return []
    H, W = frame.shape[:2]
    img = mp.Image(image_format=mp.ImageFormat.SRGB,
                   data=np.ascontiguousarray(frame))
    lm = _landmarker()
    faces = []
    if lm is not None:
        with _DETECT_LOCK:
            result = lm.detect(img)
        for pts in result.face_landmarks:
            xs = np.array([p.x for p in pts]) * W
            ys = np.array([p.y for p in pts]) * H
            w = float(xs.max() - xs.min()) * (1 + BOX_MARGIN)
            h = float(ys.max() - ys.min()) * (1 + BOX_MARGIN)
            mx0 = min(xs[i] for i in _LIPS); mx1 = max(xs[i] for i in _LIPS)
            my0 = min(ys[i] for i in _LIPS); my1 = max(ys[i] for i in _LIPS)
            pad = 0.08 * h
            faces.append({
                "cx": float((xs.min() + xs.max()) / 2),
                "cy": float((ys.min() + ys.max()) / 2),
                "w": w, "h": h,
                "eye_y": float(np.mean([ys[i] for i in _EYES])),
                "mouth": (float(mx0 - pad), float(my0 - pad),
                          float(mx1 + pad), float(my1 + pad)),
            })
    else:
        det = _blaze()
        if det is None:
            return []
        with _DETECT_LOCK:
            result = det.detect(img)
        for d in result.detections:
            bb = d.bounding_box
            faces.append({
                "cx": bb.origin_x + bb.width / 2,
                "cy": bb.origin_y + bb.height / 2,
                "w": float(bb.width), "h": float(bb.height),
                "eye_y": None, "mouth": None,
            })
    faces.sort(key=lambda f: -(f["w"] * f["h"]))
    return faces


def face_box(facecam, start, end, samples=7):
    """Median face box in a window: (cx, cy, size[, eye_y]) in pixels of the
    source.

    size is the face height. Returns None if no face is found (fall back to
    center crop). eye_y is present when FaceMesh landmarks were available;
    existing callers indexing [0..2] are unaffected.
    """
    key = f"box:{start:.2f}:{end:.2f}:{samples}"
    cached = faceio.cache_get(facecam, key)
    if cached is not None:
        return tuple(cached["box"]) if cached.get("box") else None

    step = max(0.5, (end - start) / (samples + 1))
    times = [start + (i + 1) * step for i in range(samples)]
    boxes, eyes = [], []
    for frame, scale in faceio.extract_frames(facecam, times):
        faces = detect_faces(frame)
        if not faces:
            continue
        f = faces[0]
        boxes.append((f["cx"] * scale, f["cy"] * scale, f["h"] * scale))
        if f["eye_y"] is not None:
            eyes.append(f["eye_y"] * scale)

    if not boxes:
        faceio.cache_put(facecam, key, {"box": None})
        return None
    a = np.array(boxes)
    box = (float(np.median(a[:, 0])), float(np.median(a[:, 1])),
           float(np.median(a[:, 2])))
    if eyes:
        box = box + (float(np.median(eyes)),)
    faceio.cache_put(facecam, key, {"box": list(box)})
    return box


def region_crop(facecam_wh, box, aspect):
    """Face-centered crop of a given aspect (w/h): (w, h, x, y) for ffmpeg.

    Uses the full source height (or width) and slides the window onto the
    face; the split layout's top band uses this with aspect 1080/768.
    """
    W, H = facecam_wh
    if H * aspect <= W:
        w, h = int(H * aspect), H
    else:
        w, h = W, int(W / aspect)
    cx = box[0] if box else W / 2
    cy = box[1] if box else H / 2
    x = int(min(max(0, cx - w / 2), W - w))
    y = int(min(max(0, cy - h / 2), H - h))
    return w, h, x, y


def crop_for(facecam_wh, box, zoom=2.6, eye_bias=0.12):
    """Square crop centered on the face: (w, x, y) for ffmpeg crop=w:w:x:y.

    zoom: crop side = zoom * face height (face fills ~1/zoom of the insert).
    eye_bias: face sits slightly above center, where eyes read naturally.
    When box carries a true eye line (4th element, from FaceMesh) the
    vertical placement anchors on it instead of assuming where the eyes
    sit inside the box.
    """
    W, H = facecam_wh
    if box is None:
        side = min(W, H)
        return side, (W - side) // 2, (H - side) // 2
    cx, cy, fh = box[0], box[1], box[2]
    side = min(min(W, H), int(fh * zoom))
    x = int(min(max(0, cx - side / 2), W - side))
    if len(box) > 3 and box[3] is not None:
        # eyes sit ~0.2*fh above box center; same formula, true anchor
        cy_eff = box[3] + 0.2 * fh
        y = int(min(max(0, cy_eff - side / 2 - side * eye_bias), H - side))
    else:
        y = int(min(max(0, cy - side / 2 - side * eye_bias), H - side))
    return side, x, y
