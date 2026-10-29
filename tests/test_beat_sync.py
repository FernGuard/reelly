"""Text timed to the music: stated tempo, beat grid, hot-word pops on beats."""
import subprocess

import pytest

from reelly import audio_post, config, kinetic


def test_track_bpm_from_the_file_name(monkeypatch):
    from reelly import brandkit
    monkeypatch.setattr(brandkit, "brand", lambda name=None: {})
    assert audio_post.track_bpm("/k/Board_125_bpm.wav") == 125
    assert audio_post.track_bpm("/k/Cabin 80bpm.mp3") == 80
    assert audio_post.track_bpm("/k/Menu.wav") is None
    assert audio_post.track_bpm(None) is None


def test_brand_kit_tempo_beats_the_file_name(monkeypatch):
    from reelly import brandkit
    monkeypatch.setattr(brandkit, "brand",
                        lambda name=None: {"music": {"bpm": {"boss": 140}}})
    assert audio_post.track_bpm("/k/Boss_Board_70_bpm.wav", "boss") == 140


def test_pop_waits_for_the_next_beat():
    # 4 words entering, hot word 3: lands at 3*0.07+0.46 = 0.67 s
    default = kinetic.pop_time(4, 3.0, 3)
    assert default == pytest.approx(0.67 + kinetic.POP_AT)
    t = kinetic.pop_time(4, 3.0, 3, beat=(0.1, 0.48))
    assert t == pytest.approx(1.06)                 # first beat after 0.67+0.12
    assert 0.67 + kinetic.POP_MIN <= t < 0.67 + kinetic.POP_MIN + 0.48


def test_pop_never_runs_past_the_exit():
    # a 1.2 s line exits at 0.94; the next beat (1.0) would be too late
    t = kinetic.pop_time(1, 1.2, 0, enter=False, beat=(1.0, 2.0))
    assert t == kinetic.POP_AT


def test_pop_without_a_grid_keeps_its_timing():
    assert kinetic.pop_time(2, 3.0, 0, beat=None) == kinetic.pop_time(2, 3.0, 0)
    assert kinetic.pop_time(2, 3.0, 0, beat=(0.3, 0.0)) == kinetic.pop_time(2, 3.0, 0)
    assert kinetic.pop_time(2, 3.0, None, beat=(0.3, 0.5)) is None


def test_page_uses_the_beat_timed_pop():
    html = kinetic.page_html("Wishlist on Steam", 80, 900, 200, 3.0, hot=0, pop=1.25)
    assert "kin-pop" in html and "1.250s both" in html


def test_local_beat_is_the_grid_seen_from_a_layer():
    assert kinetic.local_beat((3.6, 0.48), 0.0)[0] == pytest.approx(3.6 % 0.48)
    assert kinetic.local_beat((3.6, 0.48), 4.0)[0] == pytest.approx((3.6 - 4.0) % 0.48)
    assert kinetic.local_beat(None, 1.0) is None


def test_beat_grid_uses_the_stated_tempo(tmp_path):
    """A click track at 125 bpm: the stated tempo gives the exact period and
    a click lands on the target."""
    p = str(tmp_path / "click.wav")
    expr = "if(lt(mod(t-0.2,0.48),0.01),sin(2*PI*1000*t),0)"
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"aevalsrc='{expr}':d=12:s=22050", p], check=True)
    off, period = audio_post.beat_grid(p, 3.0, bpm=125)
    assert period == pytest.approx(0.48)
    # a click at 0.2 + k*0.48 in the track lands on 3.0 after trimming `off`
    assert ((3.0 + off - 0.2) / 0.48) % 1 == pytest.approx(0, abs=0.06) or \
        ((3.0 + off - 0.2) / 0.48) % 1 == pytest.approx(1, abs=0.06)
