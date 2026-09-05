"""Follow the speaker when reframing a podcast to vertical.

A recorded call is not one fixed layout. This one cuts between a single speaker
full-frame and both speakers side by side, and a 9:16 window cannot be parked in
one place across that: centred on a two-up frame it lands on the seam between the
two people, and pinned to one pane it spends half the cut on whoever is listening.

So the window follows the faces. One face on screen, it centres on that face. Two
faces, it centres on whichever mouth is moving, because a listener nods and blinks
but a talker's mouth moves a great deal more. Shots are held long enough to read as
an edit rather than a flicker.

Detection runs through faceio (batched, downscaled frames; disk-cached shot
lists) and the shared FaceMesh singleton in face.py; when landmarks are
available the mouth region comes from the actual lips instead of the lower
third of the box.
"""
import subprocess

import numpy as np

from . import config, faceio

SAMPLE_FPS = 10       # motion sampling
DETECT_EVERY = 1.0    # face detection cadence, in seconds
MIN_SHOT_S = 1.8      # hold a face at least this long before cutting away
MIN_FACE_PX = 90      # accept/reject floor, in SOURCE pixels at any resolution
MOVE_PX = 220         # re-centre only when the target really moved


def _detect(video, times):
    """Faces at each time: [[(cx, cy, size, mouth), ...], ...], largest first.

    Coordinates are SOURCE pixels. mouth is a lip box (x0, y0, x1, y1) from
    FaceMesh landmarks, or None on the BlazeFace fallback.
    """
    from . import face as face_mod
    out = []
    for frame, scale in faceio.extract_frames(video, times):
        if frame is None:
            out.append([])
            continue
        fs = []
        for f in face_mod.detect_faces(frame):
            # Compare in SOURCE pixels: detection ran on a downscaled frame,
            # and a frame-relative floor would drift with source resolution
            # (4K would demand 180 source px, 720p only 60).
            if f["h"] * scale < MIN_FACE_PX:
                continue        # ignore faces in the posters on the wall
            mouth = None
            if f["mouth"] is not None:
                mouth = tuple(v * scale for v in f["mouth"])
            fs.append((f["cx"] * scale, f["cy"] * scale,
                       f["h"] * scale, mouth))
        fs.sort(key=lambda f: -f[2])
        out.append(fs[:2])
    return out


def _gray_stack(video, s, e, src_wh):
    """Small grayscale frames at SAMPLE_FPS, for mouth motion."""
    W, H = src_wh
    dw = 480
    dh = int(round(480 * H / W / 2) * 2)
    r = subprocess.run([config.FFMPEG, "-v", "error", "-ss", str(s), "-to", str(e),
                        "-i", video, "-vf", f"fps={SAMPLE_FPS},scale={dw}:{dh},format=gray",
                        "-f", "rawvideo", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, dtype=np.uint8)
    n = len(x) // (dw * dh)
    if n < 3:
        return None, dw / W
    return x[:n * dw * dh].reshape(n, dh, dw).astype(np.float32), dw / W


def _mouth_energy(stack, k, box, i0, i1):
    """Motion in a face's mouth region over a slice of frames."""
    cx, cy, size = box[0], box[1], box[2]
    mouth = box[3] if len(box) > 3 else None
    n, dh, dw = stack.shape
    if mouth is not None:       # true lip box from landmarks
        x0 = int(max(0, mouth[0] * k)); x1 = int(min(dw, mouth[2] * k))
        y0 = int(max(0, mouth[1] * k)); y1 = int(min(dh, mouth[3] * k))
    else:                       # lower part of the detection box
        x0 = int(max(0, (cx - size * 0.45) * k)); x1 = int(min(dw, (cx + size * 0.45) * k))
        y0 = int(max(0, (cy + size * 0.05) * k)); y1 = int(min(dh, (cy + size * 0.55) * k))
    i0, i1 = max(0, i0), min(n, i1)
    if x1 - x0 < 2 or y1 - y0 < 2 or i1 - i0 < 2:
        return 0.0
    roi = stack[i0:i1, y0:y1, x0:x1]
    return float(np.abs(np.diff(roi, axis=0)).mean())


def crop_for(cx, src_wh, aspect=9 / 16):
    """Full-height 9:16 window centred on a face: (w, h, x, y) for ffmpeg."""
    W, H = src_wh
    cw = int(round(H * aspect / 2) * 2)
    x = int(round((cx - cw / 2) / 2) * 2)
    return (cw, H, max(0, min(x, W - cw)), 0)


def has_faces(video, duration_s, samples=6):
    """Is this a camera source (people) rather than a screen recording?

    Screen content gets the blurred-fill reframe: you want to see the whole
    screen. A face wants the window on the face.
    """
    step = duration_s / (samples + 1)
    hits = _detect(video, [step * (i + 1) for i in range(samples)])
    return sum(1 for f in hits if f) >= max(2, samples // 2)


def shots(video, s, e, src_wh):
    """Reframing shots over [s, e]: [(t0, t1, (w, h, x, y)), ...].

    Falls back to a single centred window when no face is ever found, which is
    what the old fixed reframe did anyway. Results are disk-cached per video.
    """
    W, H = src_wh
    key = f"shots:{s:.2f}:{e:.2f}:{W}x{H}"
    cached = faceio.cache_get(video, key)
    if cached is not None:
        return [(t0, t1, tuple(c)) for t0, t1, c in cached]

    times = list(np.arange(s, e, DETECT_EVERY))
    if not times:
        return [(s, e, crop_for(W / 2, src_wh))]
    faces = _detect(video, times)
    stack, k = _gray_stack(video, s, e, src_wh)

    targets, last = [], None
    for i, t in enumerate(times):
        fs = faces[i]
        if not fs:
            pick = last
        elif len(fs) == 1 or stack is None:
            pick = fs[0][0]
        else:                       # two on screen: cut to the one talking
            i0 = int((t - s) * SAMPLE_FPS)
            i1 = int((t + DETECT_EVERY - s) * SAMPLE_FPS)
            energies = [_mouth_energy(stack, k, f, i0, i1) for f in fs]
            pick = fs[int(np.argmax(energies))][0]
        if pick is None:
            pick = W / 2
        targets.append(pick)
        last = pick

    # merge into shots: hold a face until the target genuinely moves elsewhere
    out, start, cur = [], s, targets[0]
    for i in range(1, len(targets)):
        t = times[i]
        if abs(targets[i] - cur) > MOVE_PX and (t - start) >= MIN_SHOT_S \
                and (e - t) >= MIN_SHOT_S:
            out.append((start, t, crop_for(cur, src_wh)))
            start, cur = t, targets[i]
        else:
            cur = cur * 0.7 + targets[i] * 0.3      # settle onto the face
    out.append((start, e, crop_for(cur, src_wh)))
    faceio.cache_put(video, key,
                     [[float(t0), float(t1), [int(v) for v in c]]
                      for t0, t1, c in out])
    return out
