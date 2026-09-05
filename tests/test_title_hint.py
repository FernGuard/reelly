"""title_hint() burns the game NAME, never the screen-capture timestamp."""
from reelly import sizzle


def test_strips_obs_screen_capture_suffix():
    assert sizzle.title_hint("/x/Sample Title - screen_2026-01-01_00-00-00.mov") \
        == "Sample Title"
    assert sizzle.title_hint("/x/North Harbor - screen_2026-01-01_12-00-00.mov") \
        == "North Harbor"
    assert sizzle.title_hint("/x/Cedar Point Run - screen_2026-01-01_16-00-00.mov") \
        == "Cedar Point Run"


def test_keeps_a_real_name_and_leading_number():
    assert sizzle.title_hint("/x/Harbor Light.mp4") == "Harbor Light"
    assert sizzle.title_hint("/x/Blue Screen.mp4") == "Blue Screen"   # no trailing digits -> kept
    assert sizzle.title_hint("/x/7 Nights Out - screen_2026-01-01_09-00-00.mov") \
        == "7 Nights Out"
