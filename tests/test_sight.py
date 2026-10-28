"""Zero-paid visual pass: contact sheets read by a subscription or local
vision model, written in the same visual_review.json shape direct reads."""
import json
import os

import pytest
from PIL import Image, ImageDraw

from reelly import captions, direct, media, sight


def test_step_scales_with_length():
    assert sight.step_for(56) == 2.0
    assert sight.step_for(900) == 3.0
    assert sight.step_for(3600) == 5.0


def test_backend_resolution(monkeypatch):
    monkeypatch.delenv("REELLY_SIGHT", raising=False)
    monkeypatch.delenv("REELLY_VISION_URL", raising=False)
    monkeypatch.setattr(sight, "_codex_ready", lambda: True)
    assert sight.resolve_backend() == "codex"
    monkeypatch.setattr(sight, "_codex_ready", lambda: False)
    assert sight.resolve_backend() is None
    monkeypatch.setenv("REELLY_VISION_URL", "http://127.0.0.1:11435/v1")
    assert sight.resolve_backend() == "local"
    monkeypatch.setenv("REELLY_SIGHT", "off")
    assert sight.resolve_backend() is None
    monkeypatch.setenv("REELLY_SIGHT", "codex")
    assert sight.resolve_backend() is None          # asked for codex, not signed in


def _fake_reply(t0, step):
    return {"frames": [{"t": t0 + step * k, "what_happens": f"frame {k}",
                        "on_screen_text": "KNOCK" if k == 2 else "",
                        "opening_score": 9 if k == 2 else 3} for k in range(9)]
                      + [{"t": 999, "what_happens": "out of range"}],
            "sequences": [{"label": "The Knock", "start_t": t0 + 4, "end_t": t0 + 8,
                           "trailer_score": 9, "short_score": "9",
                           "what_happens": "a knock", "why": "mystery",
                           "strong_hook": True}]}


def test_review_writes_the_visual_review_shape(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v" * 100)
    monkeypatch.setattr(media, "duration", lambda p: 30.0)
    monkeypatch.setattr(sight, "_sheet", lambda v, t0, step, n, out, crop=None:
                        Image.new("RGB", (8, 8)).save(out) or out)
    calls = []

    def fake_ask(backend, prompt, image, label, project):
        t0 = float(prompt.split("t = ")[1].split(" +")[0])
        calls.append(t0)
        return _fake_reply(t0, 2.0)

    monkeypatch.setattr(sight, "_ask", fake_ask)
    an = tmp_path / "analysis"
    an.mkdir()
    seqs = sight.review(str(video), str(an / "visual_review.json"),
                        str(an / "visual_review.md"), backend="codex")
    assert sorted(calls) == [0.0, 18.0]            # 2 sheets of 9 frames, 2 s apart
    art = json.load(open(an / "visual_review.json"))
    assert art["complete"] and art["engine"] == "sight"
    s0 = art["sequences"][0]
    for k in ("start_s", "end_s", "start_abs", "end_abs", "trailer_score",
              "short_score", "label", "what_happens", "strong_hook"):
        assert k in s0
    assert s0["short_score"] == 9                   # string scores are normalised
    frames = json.load(open(an / "frames.json"))["frames"]
    assert all(f["t"] <= 30.5 for f in frames)      # out-of-range frames dropped
    assert "Strongest opening frames" in open(an / "visual_review.md").read()
    # second run: every sheet cached, no calls
    calls.clear()
    sight.review(str(video), str(an / "visual_review.json"),
                 str(an / "visual_review.md"), backend="codex")
    assert calls == []
    # direct reads it like the Gemini artifact
    from reelly import visual
    assert visual.sequences(art) == art["sequences"]


def test_a_failed_sheet_is_recorded_as_missing(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"v")
    monkeypatch.setattr(media, "duration", lambda p: 10.0)
    monkeypatch.setattr(sight, "_sheet", lambda v, t0, step, n, out, crop=None:
                        Image.new("RGB", (8, 8)).save(out) or out)
    monkeypatch.setattr(sight, "_ask", lambda *a, **k: None)
    an = tmp_path / "analysis"
    an.mkdir()
    sight.review(str(video), str(an / "v.json"), str(an / "v.md"), backend="codex")
    art = json.load(open(an / "v.json"))
    assert art["complete"] is False and art["missing"][0]["error"]


def test_no_backend_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(sight, "resolve_backend", lambda: None)
    assert sight.review("x.mp4", str(tmp_path / "v.json"), str(tmp_path / "v.md")) is None


def test_codex_images_go_before_a_double_dash(monkeypatch, tmp_path):
    import shutil
    import subprocess
    monkeypatch.setattr(shutil, "which", lambda t: f"/usr/bin/{t}")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        open(cmd[cmd.index("-o") + 1], "w").write('{"ok": true}')
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(direct.ledger, "add", lambda *a, **k: None)
    img = tmp_path / "s.jpg"
    img.write_bytes(b"j")
    assert direct._ask_cli("codex", "look", "p", "l", images=[str(img)]) == {"ok": True}
    cmd = seen["cmd"]
    assert cmd[cmd.index("-i") + 1] == str(img)
    assert cmd[-2] == "--" and cmd[-1].startswith("look")


def test_claude_brain_refuses_images():
    with pytest.raises(ValueError):
        direct._ask_cli("claude", "look", "p", "l", images=["x.jpg"])


# --- balanced headline wrap ------------------------------------------------------------

def test_balanced_wrap_leaves_no_widow():
    font = captions._font(74)
    d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    text = "Now you have to clear it"
    w = int(d.textlength("Now you have to clear", font=font)) + 4
    greedy = captions._wrap(d, text, font, w)
    balanced = captions.wrap_balanced(d, text, font, w)
    assert len(greedy) == len(balanced) == 2
    assert greedy[-1] == "it"                          # the widow
    assert len(balanced[-1].split()) >= 2
    assert " ".join(balanced) == text


def test_balanced_wrap_keeps_one_liners_and_long_text():
    font = captions._font(40)
    d = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    assert captions.wrap_balanced(d, "Open it.", font, 800) == ["Open it."]
    long = " ".join(["word"] * 30)
    assert captions.wrap_balanced(d, long, font, 300) == captions._wrap(d, long, font, 300)
