"""P1 #6 (reference-first character pipeline) and #7 (persistent managed account brand bug).

#6: build a character reference AND a clean background plate before the video,
then drive reference-to-video with BOTH -- never one busy keyframe that makes
the model invent character and scene at once (MAR-37).

#7: the managed account wordmark rides the whole clip as a stamped (not generated) corner
bug, so its colour/form carry through the video without violating the
no-logos-in-generation rule (M7).
"""
import base64

import pytest

from reelly import motion


AI = {
    "character": "a lone knight",
    "shots": [{"prompt": "push in on the hero", "seconds": 4,
               "setting": "a ruined throne room"},
              {"prompt": "pan across the hall", "seconds": 4}],
    "hook": {"text": "H"}, "payoff": {"text": "P"}, "cta": "PLAY",
}


# --- #6 reference-first --------------------------------------------------------

def test_background_frame_asks_for_an_empty_scene(tmp_path, monkeypatch):
    """The background plate must be generated with NO people, from the source as
    a style reference."""
    seen = {}

    def fake_fal(endpoint, payload, *a, **k):
        seen["prompt"] = payload["prompt"]
        seen["refs"] = payload["image_urls"]
        return "http://x/bg.png"

    monkeypatch.setattr("reelly.audio_post._fal", fake_fal)
    monkeypatch.setattr("reelly.audio_post._download",
                        lambda url, path: open(path, "wb").close())
    src = tmp_path / "src.png"
    src.write_bytes(b"\x89PNG\r\n")
    out = tmp_path / "background_frame.png"
    motion._background_frame(str(src), AI, "proj", str(out))
    assert "NO people" in seen["prompt"] or "no people" in seen["prompt"].lower()
    assert "ruined throne room" in seen["prompt"]
    assert len(seen["refs"]) == 1                      # the source as style ref


def test_generate_drives_seedance_with_character_and_background_refs(tmp_path, monkeypatch):
    """Two references reach reference-to-video: the character frame AND the clean
    background plate -- not the raw busy source."""
    seen = {}

    def fake_fal(endpoint, payload, *a, **k):
        seen["endpoint"] = endpoint
        seen["refs"] = payload["image_urls"]
        seen["prompt"] = payload["prompt"]
        return "http://x/vid.mp4"

    monkeypatch.setattr("reelly.audio_post._fal", fake_fal)
    monkeypatch.setattr("reelly.audio_post._download",
                        lambda url, path: open(path, "wb").close())
    monkeypatch.setattr(motion.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(motion.os, "remove", lambda *a, **k: None)
    char = tmp_path / "char.png"; char.write_bytes(b"C")
    bg = tmp_path / "bg.png"; bg.write_bytes(b"B")
    out = tmp_path / "base.mp4"
    motion._generate(str(char), str(bg), AI, "draft", "proj", str(out),
                     video_model="seedance")
    assert seen["endpoint"] == motion.VIDEO_ENDPOINT
    assert len(seen["refs"]) == 2, "character ref + background ref, reference-first"
    assert "CLEAN BACKGROUND PLATE" in seen["prompt"]
    assert "do NOT add any other people" in seen["prompt"]
    # seedance keeps the @Image tags
    assert "@Image1" in seen["prompt"] and "@Image2" in seen["prompt"]


def _cap_fal(monkeypatch, seen):
    def fake_fal(endpoint, payload, *a, **k):
        seen["endpoint"] = endpoint
        seen["payload"] = payload
        return "http://x/vid.mp4"
    monkeypatch.setattr("reelly.audio_post._fal", fake_fal)
    monkeypatch.setattr("reelly.audio_post._download",
                        lambda url, path: open(path, "wb").close())
    monkeypatch.setattr(motion.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(motion.os, "remove", lambda *a, **k: None)


def test_generate_default_is_h3max_single_keyframe(tmp_path, monkeypatch):
    """The DEFAULT video model is H3 Max image-to-video: ONE keyframe reaches it
    under `image_url`, and the prompt is a single-keyframe motion prompt with no
    @Image reference tags (h3max is the fast single-frame path)."""
    seen = {}
    _cap_fal(monkeypatch, seen)
    char = tmp_path / "char.png"; char.write_bytes(b"C")
    bg = tmp_path / "bg.png"; bg.write_bytes(b"B")
    out = tmp_path / "base.mp4"
    motion._generate(str(char), str(bg), AI, "draft", "proj", str(out))  # default model
    assert seen["endpoint"] == "minimax/h3-max/image-to-video"
    assert "image_url" in seen["payload"] and "reference_image_urls" not in seen["payload"]
    assert "@Image" not in seen["payload"]["prompt"]


def test_generate_minimax_is_two_ref_reference_to_video(tmp_path, monkeypatch):
    """Explicit minimax (H3 reference-to-video) still feeds TWO refs under
    reference_image_urls with @Image tags rewritten to 'Image 1'/'Image 2'."""
    seen = {}
    _cap_fal(monkeypatch, seen)
    char = tmp_path / "char.png"; char.write_bytes(b"C")
    bg = tmp_path / "bg.png"; bg.write_bytes(b"B")
    out = tmp_path / "base.mp4"
    motion._generate(str(char), str(bg), AI, "draft", "proj", str(out), video_model="minimax")
    assert seen["endpoint"] == "minimax/h3/reference-to-video"
    assert len(seen["payload"]["reference_image_urls"]) == 2
    p = seen["payload"]["prompt"]
    assert "Image 1" in p and "Image 2" in p and "@Image" not in p


# --- reserved text zones + composition contract -------------------------------

def test_text_zones_split_the_frame_into_three_bands():
    assert 0 < motion.TEXT_ZONE_TOP_Y < motion.TEXT_ZONE_BOTTOM_Y < motion.FRAME_H
    # the middle (subject) band is the largest
    top = motion.TEXT_ZONE_TOP_Y
    bottom = motion.FRAME_H - motion.TEXT_ZONE_BOTTOM_Y
    middle = motion.TEXT_ZONE_BOTTOM_Y - motion.TEXT_ZONE_TOP_Y
    assert middle > top and middle > bottom


def test_composition_contract_reaches_the_generation_prompts(tmp_path, monkeypatch):
    """The character frame, background plate and video prompt all tell the model
    to reserve the text bands and keep the subject out of them."""
    seen = {}
    monkeypatch.setattr("reelly.audio_post._fal",
                        lambda ep, payload, *a, **k: seen.setdefault("prompts", []).append(payload["prompt"])
                        or "http://x/o")
    monkeypatch.setattr("reelly.audio_post._download",
                        lambda url, path: open(path, "wb").close())
    monkeypatch.setattr(motion.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(motion.os, "remove", lambda *a, **k: None)
    src = tmp_path / "s.png"; src.write_bytes(b"S")
    motion._character_frame(str(src), "a knight", "push in", "p", str(tmp_path / "c.png"))
    motion._background_frame(str(src), AI, "p", str(tmp_path / "b.png"))
    char = tmp_path / "c.png"; char.write_bytes(b"C")
    bg = tmp_path / "b.png"; bg.write_bytes(b"B")
    motion._generate(str(char), str(bg), AI, "draft", "p", str(tmp_path / "o.mp4"))
    for p in seen["prompts"]:
        pl = p.lower()
        assert "top" in pl and "bottom" in pl and "text" in pl
        assert "middle" in pl and "calm" in pl
        # one continuous full-frame scene -- never the letterboxed blurred bands
        assert "full-frame" in pl or "edge to edge" in pl
        assert "blurred bands" in pl  # the explicit prohibition


# --- #7 persistent brand bug --------------------------------------------------

def _mark(tmp_path):
    from PIL import Image
    p = tmp_path / "run-wordmark.png"
    Image.new("RGBA", (240, 100)).save(p)          # a real image so the box helper reads size
    return str(p)


def test_corner_bug_ends_before_the_cta_card(monkeypatch, tmp_path):
    """The bug rides [0, t_end] and fades out -- it ends as the end-card (which
    has its own managed account mark) rises, so the last beat shows ONE mark, not two."""
    monkeypatch.setattr(motion, "_brand_wordmark", lambda: _mark(tmp_path))
    ev = motion._corner_bug_event(6.2)
    assert ev is not None
    assert ev["t"] == [0.0, 6.2]
    assert ev["fade_out"] is True
    assert f"opacity:{motion.BUG_OPACITY}" in ev["args"][0]


def test_corner_bug_absent_when_no_wordmark_registered(monkeypatch):
    monkeypatch.setattr(motion, "_brand_wordmark", lambda: None)
    assert motion._corner_bug_event(8.0) is None
    assert motion._corner_bug_box() is None


def test_corner_bug_box_clears_the_safe_insets(monkeypatch, tmp_path):
    """The bug box (used to draw it AND to keep lettering off it) sits inside the
    safe insets for the configured corner."""
    monkeypatch.setattr(motion, "_brand_wordmark", lambda: _mark(tmp_path))
    monkeypatch.setattr(motion, "BUG_CORNER", "bottom-right")
    x, y, w, h = motion._corner_bug_box()
    assert h == motion.BUG_HEIGHT and w > 0
    assert x + w == motion.FRAME_W - motion.SAFE_RIGHT
    assert y + h == motion.FRAME_H - motion.SAFE_BOTTOM
    ev = motion._corner_bug_event(6.0)
    assert f"bottom:{motion.SAFE_BOTTOM}px" in ev["args"][0]
    assert f"right:{motion.SAFE_RIGHT}px" in ev["args"][0]
