"""MAR-106: the self-letterbox detector finds the sharp content band vs the
blurred top/bottom bed, and no-ops on a full-bleed render."""
import numpy as np
from reelly import motion


def test_full_bleed_profile_returns_full_span():
    prof = np.ones(100) * 5.0                     # uniform energy = full picture
    assert motion._content_band(prof) == (0, 100)


def test_letterboxed_profile_finds_the_centre_band():
    prof = np.concatenate([np.full(20, 0.1), np.full(60, 8.0), np.full(20, 0.1)])
    top, bot = motion._content_band(prof)
    assert 15 <= top <= 25 and 75 <= bot <= 85    # sharp band ~ rows 20..80


def test_too_little_signal_does_not_crop():
    prof = np.zeros(100); prof[50] = 9.0          # one hot row only
    assert motion._content_band(prof) == (0, 100)
