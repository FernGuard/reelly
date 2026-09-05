"""Geometry contracts for face.crop_for / face.region_crop.

Pure math, no model, no ffmpeg. These pin the framing so the FaceMesh switch
(and any future detector swap) cannot silently move the crop.
"""
from reelly import face

WH = (1920, 1080)


# ------------------------------------------------------------------ crop_for

def test_crop_for_no_box_is_centered_square():
    side, x, y = face.crop_for(WH, None)
    assert side == 1080
    assert x == (1920 - 1080) // 2
    assert y == 0


def test_crop_for_side_is_zoom_times_face_height():
    side, x, y = face.crop_for(WH, (960, 540, 200))
    assert side == int(200 * 2.6)  # 520
    assert x == 960 - side // 2


def test_crop_for_side_never_exceeds_frame():
    side, _, _ = face.crop_for(WH, (960, 540, 900))  # 900*2.6 > 1080
    assert side == 1080


def test_crop_for_eye_bias_raises_the_crop():
    _, _, y_biased = face.crop_for(WH, (960, 540, 200), eye_bias=0.12)
    _, _, y_flat = face.crop_for(WH, (960, 540, 200), eye_bias=0.0)
    assert y_flat == 540 - 260            # cy - side/2
    assert y_biased == int(540 - 260 - 520 * 0.12)


def test_crop_for_clamps_to_frame_edges():
    side, x, y = face.crop_for(WH, (10, 10, 200))
    assert (x, y) == (0, 0)
    side, x, y = face.crop_for(WH, (1910, 1070, 200))
    assert x == 1920 - side
    assert y == 1080 - side


def test_crop_for_eye_line_matches_assumed_offset():
    """A 4-tuple box whose eye line sits exactly 0.2*fh above center must
    reproduce the 3-tuple framing bit for bit."""
    plain = face.crop_for(WH, (960, 540, 200))
    with_eyes = face.crop_for(WH, (960, 540, 200, 540 - 0.2 * 200))
    assert with_eyes == plain


def test_crop_for_eye_line_shifts_vertical_only():
    base = face.crop_for(WH, (960, 540, 200, 500))
    lower = face.crop_for(WH, (960, 540, 200, 560))
    assert base[0] == lower[0] and base[1] == lower[1]
    assert lower[2] == base[2] + 60


# --------------------------------------------------------------- region_crop

def test_region_crop_wide_aspect_uses_full_height():
    w, h, x, y = face.region_crop(WH, (960, 540, 200), aspect=1.0)
    assert (w, h) == (1080, 1080)
    assert y == 0
    assert x == 960 - 540


def test_region_crop_tall_aspect_uses_full_width():
    w, h, x, y = face.region_crop((1080, 1920), (540, 960, 200), aspect=1080 / 768)
    assert w == 1080
    assert h == int(1080 / (1080 / 768))
    assert x == 0


def test_region_crop_no_box_centers():
    w, h, x, y = face.region_crop(WH, None, aspect=1.0)
    assert x == (1920 - 1080) // 2 and y == 0


def test_region_crop_clamps():
    w, h, x, y = face.region_crop(WH, (5, 5, 100), aspect=1.0)
    assert (x, y) == (0, 0)
    w, h, x, y = face.region_crop(WH, (1919, 1079, 100), aspect=1.0)
    assert x == 1920 - w and y == 0
