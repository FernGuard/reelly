"""MAR-108: a sizzle montage must never come out empty because a generated
title swept its own text off screen. When the readability gate fails, the title
beat falls back to a deterministic push-in on the correctly-typeset still.
"""
import os
from PIL import Image

from reelly import sizzle, media, config


def test_still_push_makes_a_held_readable_clip(tmp_path):
    still = tmp_path / "title.png"
    Image.new("RGB", (1920, 1080), (18, 20, 30)).save(still)
    out = str(tmp_path / "beat.mp4")
    sizzle._still_push(str(still), out, 1080, 1920, 4.0)
    assert os.path.exists(out) and os.path.getsize(out) > 0
    # holds for the requested beat length (within an encode tolerance)
    assert abs(media.duration(out) - 4.0) < 0.6


def _clip(path, seconds, w=180, h=320):
    """A tiny solid-colour clip of exactly `seconds`, standing in for a
    rendered segment so render()'s wiring can be tested without FAL/Chrome."""
    sizzle.sh(config.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
              "-i", f"color=c=navy:s={w}x{h}:r={sizzle.FPS}",
              "-t", f"{seconds:.2f}", "-frames:v",
              str(int(round(seconds * sizzle.FPS))),
              "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))
    return str(path)


def test_render_with_no_generated_title_still_ships_a_title_beat(
        tmp_path, monkeypatch):
    """MAR-108: --no-cinecard (or a failed generation) leaves title_clip=None.
    The reel must NOT come out empty -- render() builds a deterministic title
    beat from the film instead of raising, and it must NOT be the generated
    _title_seq path.
    """
    calls = {"title_seq": 0, "brand_beat": 0}

    def fake_seg(s, w, h, d):
        _clip(d, float(s["dur"]), w, h)

    def fake_brand_beat(line, sub, plan, w, h, dur, workdir, name,
                        bedsrc, bed_at, crop=None, cine=None, cine_at=0.0):
        calls["brand_beat"] += 1
        return _clip(os.path.join(workdir, name + ".mp4"), dur, w, h)

    def fake_title_seq(*a, **k):
        calls["title_seq"] += 1
        raise AssertionError("must not build a generated title when none exists")

    monkeypatch.setattr(sizzle, "_seg_source", fake_seg)
    monkeypatch.setattr(sizzle, "_brand_beat", fake_brand_beat)
    monkeypatch.setattr(sizzle, "_title_seq", fake_title_seq)

    plan = {
        "product": "video", "hook": "Your ideas get a play button.",
        "payoff": "Play it now.", "cta": "example.invalid",
        "shots": [
            {"role": "title", "dur": 2.5},
            {"role": "platform", "file": "/x/a.mp4", "at": 0.0, "dur": 1.2},
            {"role": "body", "file": "/x/b.mp4", "at": 0.0, "dur": 2.0},
            {"role": "payoff", "file": "/x/c.mp4", "at": 0.0, "dur": 2.0,
             "label": ""},
        ],
    }
    out = str(tmp_path / "reel.mp4")
    sizzle.render(plan, out, size="180x320", title_clip=None, end_clip=None)

    assert os.path.exists(out) and os.path.getsize(out) > 0   # never empty
    assert calls["brand_beat"] >= 1        # title beat came from the fallback
    assert calls["title_seq"] == 0         # and NOT from the generated path
