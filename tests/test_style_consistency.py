"""Art-style consistency (reviewer 2026-08-13): the reference-first pipeline held
character identity but drifted STYLE (a claymation source rendered flat anime,
shot 1 disagreeing with shot 2) and nothing measured it. These lock the fix:
- the prompts stop hardcoding 'anime' and trust the source image as the style ref
- a MULTI-FRAME style gate (never one keyframe) compares each frame DIRECTLY to
  the source image and catches shot-to-shot drift.
"""
from PIL import Image

from reelly import design, motion


AI = {
    "character": "a lone knight",
    "shots": [{"prompt": "push in", "seconds": 4, "setting": "a throne room"},
              {"prompt": "pan across", "seconds": 4}],
    "hook": {"text": "H"}, "payoff": {"text": "P"}, "cta": "PLAY",
}


# --- design.style_match compares frame to the SOURCE image ---------------------

def test_style_match_no_reference_is_a_pass(monkeypatch):
    """With no source there is nothing to enforce -- never block on it."""
    called = {"n": 0}
    monkeypatch.setattr(design, "_gemini",
                        lambda *a, **k: called.__setitem__("n", 1))
    assert design.style_match(Image.new("RGB", (8, 8)), None)["match"] is True
    assert called["n"] == 0


def test_style_match_reports_drift(monkeypatch):
    monkeypatch.setattr(design, "_gemini",
                        lambda *a, **k: {"match": False, "drift": "flat anime, not clay"})
    v = design.style_match(Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8)))
    assert v["match"] is False and "anime" in v["drift"]


def test_style_match_passes_two_images_to_the_critic(monkeypatch):
    """The comparison is image-to-image: the frame AND the source reach vision."""
    seen = {}
    monkeypatch.setattr(design, "_gemini",
                        lambda parts, *a, **k: seen.update(n=len(parts))
                        or {"match": True, "drift": ""})
    design.style_match(Image.new("RGB", (8, 8)), Image.new("RGB", (8, 8)))
    assert seen["n"] == 3          # frame, source, prompt


def test_style_match_garbled_reply_does_not_block(monkeypatch):
    monkeypatch.setattr(design, "_gemini", lambda *a, **k: "junk")
    assert design.style_match(Image.new("RGB", (8, 8)),
                              Image.new("RGB", (8, 8)))["match"] is True


# --- prompts no longer force 'anime' ------------------------------------------

def test_character_frame_no_longer_hardcodes_anime(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr("reelly.audio_post._fal",
                        lambda ep, payload, *a, **k: seen.update(prompt=payload["prompt"],
                                                                 refs=payload["image_urls"])
                        or "http://x/c.png")
    monkeypatch.setattr("reelly.audio_post._download",
                        lambda url, path: open(path, "wb").close())
    src = tmp_path / "s.png"; src.write_bytes(b"S")
    motion._character_frame(str(src), "a knight", "push in", "proj",
                            str(tmp_path / "c.png"))
    assert "anime" not in seen["prompt"].lower()
    assert "art style" in seen["prompt"].lower()
    assert len(seen["refs"]) == 1          # the source is the visual style ref


def test_generate_holds_source_style_without_anime(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr("reelly.audio_post._fal",
                        lambda ep, payload, *a, **k: seen.update(prompt=payload["prompt"])
                        or "http://x/v.mp4")
    monkeypatch.setattr("reelly.audio_post._download",
                        lambda url, path: open(path, "wb").close())
    monkeypatch.setattr(motion.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(motion.os, "remove", lambda *a, **k: None)
    char = tmp_path / "c.png"; char.write_bytes(b"C")
    bg = tmp_path / "b.png"; bg.write_bytes(b"B")
    motion._generate(str(char), str(bg), AI, "draft", "proj", str(tmp_path / "o.mp4"))
    # 'anime' only survives inside the explicit guard, never as the imposed style
    assert "no anime-fication of non-anime art" in seen["prompt"].lower()
    assert "cinematic anime" not in seen["prompt"].lower()
    assert "art style" in seen["prompt"].lower()


# --- the multi-frame style gate -----------------------------------------------

def test_style_gate_samples_multiple_frames_not_one(tmp_path, monkeypatch):
    """The whole point: more than one frame, spanning both shots, so shot-to-shot
    drift is visible."""
    src = tmp_path / "src.png"; Image.new("RGB", (8, 8)).save(src)
    seen_ts = []
    monkeypatch.setattr(motion, "_frame_at",
                        lambda vid, t: seen_ts.append(t) or Image.new("RGB", (8, 8)))
    monkeypatch.setattr(design, "style_match",
                        lambda frame, ref, project="": {"match": True, "drift": ""})
    g = motion._style_gate("v.mp4", str(src), cut=4, total=8,
                           project="p", root=str(tmp_path))
    assert g["pass"] is True
    assert len(seen_ts) >= 3, "multi-frame, never a single keyframe"
    assert min(seen_ts) < 4 <= max(seen_ts), "samples span BOTH shots"


def test_style_gate_fails_and_reports_on_drift(tmp_path, monkeypatch):
    src = tmp_path / "src.png"; Image.new("RGB", (8, 8)).save(src)
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: Image.new("RGB", (8, 8)))
    drift = iter([{"match": True, "drift": ""},
                  {"match": False, "drift": "flat anime not clay"},
                  {"match": True, "drift": ""}, {"match": True, "drift": ""}])
    monkeypatch.setattr(design, "style_match",
                        lambda frame, ref, project="": next(drift))
    g = motion._style_gate("v.mp4", str(src), cut=4, total=8,
                           project="p", root=str(tmp_path))
    assert g["pass"] is False
    report = (tmp_path / "qc" / "style_report.md").read_text()
    assert "FAIL" in report and "flat anime not clay" in report


def test_style_gate_noop_without_a_source(tmp_path):
    g = motion._style_gate("v.mp4", "", cut=4, total=8, project="p", root=str(tmp_path))
    assert g == {"pass": True, "frames": []}
