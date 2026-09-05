"""title_hint() burns the game NAME, never the screen-capture timestamp."""
from reelly import sizzle


def test_strips_obs_screen_capture_suffix():
    assert sizzle.title_hint("/x/10 Days to Home - screen_2026-08-17_15-54-33.mov") \
        == "10 Days to Home"
    assert sizzle.title_hint("/x/Ballhalla - screen_2026-08-17_14-40-40.mov") == "Ballhalla"
    assert sizzle.title_hint("/x/Rusko's Raiders - screen_2026-08-17_16-11-49.mov") \
        == "Rusko's Raiders"


def test_keeps_a_real_name_and_leading_number():
    assert sizzle.title_hint("/x/Rain on Neon.mp4") == "Rain on Neon"
    assert sizzle.title_hint("/x/Blue Screen.mp4") == "Blue Screen"   # no trailing digits -> kept
