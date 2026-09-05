"""speaker._detect face floor: exactly MIN_FACE_PX (90) SOURCE pixels at any
source resolution.

Detection runs on 640px-wide downscaled frames; the floor must be applied
after mapping face heights back through the scale factor. A frame-relative
floor drifts with resolution: 4K would silently demand 180 source px (flips
has_faces() to blurred fill), 720p only 60 (background faces hijack shots()).
"""
import numpy as np
import pytest

from reelly import face, faceio, speaker


def _rig(monkeypatch, src_wh, face_src_px):
    """Stub faceio/face so _detect sees one face of face_src_px source-pixel
    height on a 640-wide downscaled frame of the given source."""
    W, H = src_wh
    scale = W / 640
    frame = np.zeros((int(round(H / scale)), 640, 3), dtype=np.uint8)
    monkeypatch.setattr(faceio, "extract_frames",
                        lambda video, times, max_width=640: [(frame, scale)])
    monkeypatch.setattr(face, "detect_faces", lambda fr: [
        {"cx": 320.0, "cy": fr.shape[0] / 2, "w": face_src_px / scale,
         "h": face_src_px / scale, "eye_y": None, "mouth": None}])
    return scale


CASES = [  # (label, source dims)
    ("4k", (3840, 2160)),
    ("1080p", (1920, 1080)),
    ("720p", (1280, 720)),
]


@pytest.mark.parametrize("label,src", CASES)
def test_face_at_91_source_px_accepted_everywhere(monkeypatch, label, src):
    scale = _rig(monkeypatch, src, 91)
    (faces,) = speaker._detect("v.mp4", [1.0])
    assert len(faces) == 1
    cx, cy, size, mouth = faces[0]
    assert size == pytest.approx(91)        # reported in source pixels
    assert cx == pytest.approx(320 * scale)


@pytest.mark.parametrize("label,src", CASES)
def test_face_at_89_source_px_rejected_everywhere(monkeypatch, label, src):
    _rig(monkeypatch, src, 89)
    (faces,) = speaker._detect("v.mp4", [1.0])
    assert faces == []


def test_4k_does_not_demand_a_180px_face(monkeypatch):
    # the old frame-relative floor rejected a 120 source-px face on 4K
    _rig(monkeypatch, (3840, 2160), 120)
    (faces,) = speaker._detect("v.mp4", [1.0])
    assert len(faces) == 1


def test_720p_background_face_below_90_is_still_rejected(monkeypatch):
    # the old frame-relative floor let a 70 source-px face through on 720p
    _rig(monkeypatch, (1280, 720), 70)
    (faces,) = speaker._detect("v.mp4", [1.0])
    assert faces == []
