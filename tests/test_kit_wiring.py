"""Wiring pass: the brand kit feeds the pipeline.

- overlays.autoplan uses the kit's pre-built endcard PNG (full-frame, zero
  Gemini placement + zero Chrome renders) when the kit has the cut's studio;
  falls back to the legacy plan_endcard/badge path when it does not, and
  REELLY_ENDCARD=legacy forces the old path even with a kit present.
- audio_post._music_generate serves beds from the kit's self-building music
  library ($0) when genre + duration fit, else generates via FAL and
  REGISTERS the fresh bed back into the library.
- captions' new thin-stroke + soft-shadow preset respects explicit stroke_w.
"""
import json
import os

import pytest
from PIL import Image

from reelly import audio_post, brandkit, captions, overlays


# --------------------------------------------------------------- fixtures

@pytest.fixture
def kit(tmp_path, monkeypatch):
    """Isolated kit with a video-studio endcard only."""
    kdir = tmp_path / "kit"
    (kdir / "endcards").mkdir(parents=True)
    Image.new("RGB", (1080, 1920), (9, 12, 10)).save(
        kdir / "endcards" / "video.png")
    monkeypatch.setenv("REELLY_BRANDKIT", str(kdir))
    monkeypatch.delenv("REELLY_ENDCARD", raising=False)
    return kdir


@pytest.fixture
def project(tmp_path):
    """Minimal project autoplan can walk: one plan, one (stub) final mp4."""
    root = tmp_path / "proj"
    (root / "edl").mkdir(parents=True)
    (root / "deliverables" / "final").mkdir(parents=True)
    plan = {"id": "cut_01", "title": "demo", "duration_s": 12.0,
            "segments": [[0.0, 12.0]], "hook": {"text": "h", "show_s": 3.6},
            "cta": "made with Video Project on example.invalid", "caption": ""}
    (root / "edl" / "cut_plans.json").write_text(json.dumps([plan]))
    (root / "deliverables" / "final" / "cut_01.mp4").write_bytes(b"stub")
    return root


def _autoplan(root, product, monkeypatch):
    """Run autoplan with the frame-sampling planner stubbed out (the stub
    records whether the legacy path was taken)."""
    from reelly import placement
    sampled = []

    def fake_endcard(video, t, plan, aspect, text=""):
        sampled.append("plan_endcard")
        return {"y": 1200, "h": 110, "w": 900, "size": 34,
                "backdrop_detail": 0, "backdrop_luma": 0}

    def fake_mark(video, t, plan, text, register, avoid=None):
        sampled.append("plan_mark")
        return {"x": 60, "y": 610, "size": 34, "color": "#FCFCFB", "w": 900,
                "h": 80, "stroke": 5, "scrim": 0.6,
                "backdrop_detail": 0, "backdrop_luma": 0}

    monkeypatch.setattr(placement, "plan_endcard", fake_endcard)
    monkeypatch.setattr(placement, "plan_mark", fake_mark)
    specs = overlays.autoplan(str(root), meme=False, product=product)
    return specs, sampled


# --------------------------------------------------- kit endcard consumption

def test_autoplan_uses_the_kit_endcard_when_the_kit_has_the_studio(
        kit, project, monkeypatch):
    specs, sampled = _autoplan(project, "video", monkeypatch)
    evs = specs["cut_01"]
    assert len(evs) == 1
    ev = evs[0]
    assert ev["template"] == overlays.KITCARD
    assert ev["args"] == [str(kit / "endcards" / "video.png")]
    assert ev["fade_out"] is False          # full strength on the last frame
    # zero placement sampling, zero Chrome: the whole point of the kit path
    assert sampled == []
    # respects the payoff-anchored endcard window (gap-13)
    plan = json.load(open(project / "edl" / "cut_plans.json"))[0]
    assert ev["t"] == list(overlays.endcard_window(plan))


def test_autoplan_falls_back_to_legacy_when_the_kit_lacks_the_studio(
        kit, project, monkeypatch):
    # kit has video.png only; a games cut takes the old lowerthird path
    # (no logo registered in this test env -> the lowerthird branch)
    monkeypatch.setattr("reelly.products.brand_logo", lambda k: None)
    specs, sampled = _autoplan(project, "games", monkeypatch)
    evs = specs["cut_01"]
    # legacy card + its companion scrim beneath it (a logo may NEVER render
    # without the dark pass under it; kit cards carry theirs baked in)
    assert len(evs) == 2
    assert evs[0]["template"] == overlays.KITCARD
    assert evs[0]["args"][0].endswith("endcard_scrim.png")
    assert evs[0]["t"] == evs[1]["t"]
    assert evs[1]["template"] in ("badge", "lowerthird")
    assert sampled            # legacy path sampled the frame


def test_env_override_restores_the_legacy_endcard_path(
        kit, project, monkeypatch):
    monkeypatch.setenv("REELLY_ENDCARD", "legacy")
    monkeypatch.setattr("reelly.products.brand_logo", lambda k: None)
    specs, sampled = _autoplan(project, "video", monkeypatch)
    evs = specs["cut_01"]
    assert evs[0]["template"] == overlays.KITCARD    # the companion scrim
    assert evs[0]["args"][0].endswith("endcard_scrim.png")
    assert evs[1]["template"] in ("badge", "lowerthird")
    assert sampled


def test_kit_endcard_accessor_gates(kit, monkeypatch):
    assert overlays.kit_endcard("video") == str(kit / "endcards" / "video.png")
    assert overlays.kit_endcard("games") is None      # not in the kit
    assert overlays.kit_endcard(None) is None
    monkeypatch.setenv("REELLY_ENDCARD", "legacy")
    assert overlays.kit_endcard("video") is None


def test_composite_skips_chrome_for_kitcard_events(kit, monkeypatch, tmp_path):
    """_composite must feed the kit PNG straight to ffmpeg — no HTML, no
    Chrome subprocess for a kitcard event."""
    ran = {}

    def fake_run(cmd, **k):
        ran["cmd"] = cmd

        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(overlays.subprocess, "run", fake_run)
    monkeypatch.setattr(overlays, "_render_png",
                        lambda *a, **k: pytest.fail("Chrome rendered a kitcard"))
    monkeypatch.setattr("reelly.audio_post.enforce_true_peak", lambda *a, **k: None)
    monkeypatch.setattr("reelly.audio_post.enforce_loudness", lambda *a, **k: None)
    png = str(kit / "endcards" / "video.png")
    ev = {"template": overlays.KITCARD, "args": [png], "t": [8.0, 12.0],
          "ent": "none", "sfx": ["ding.mp3", -18], "fade_out": False}
    overlays._composite("in.mp4", str(tmp_path / "out.mp4"), [ev],
                        str(tmp_path / "wd"))
    assert png in ran["cmd"]                       # the kit PNG is an input
    fc = ran["cmd"][ran["cmd"].index("-filter_complex") + 1]
    assert "fade=t=in" in fc                       # short opacity fade-in
    assert "fade=t=out" not in fc                  # holds to the last frame


# ------------------------------------------------- self-building music library

@pytest.fixture
def music_kit(tmp_path, monkeypatch):
    kdir = tmp_path / "mkit"
    (kdir / "music").mkdir(parents=True)
    monkeypatch.setenv("REELLY_BRANDKIT", str(kdir))
    return kdir


def test_register_bed_then_find_bed_roundtrip(music_kit, tmp_path):
    src = tmp_path / "bed.mp3"
    src.write_bytes(b"lofi-bytes")
    dst = brandkit.register_bed(str(src), "lofi", 42.5)
    assert dst and os.path.exists(dst) and "/music/lofi/" in dst
    man = brandkit.music_manifest()
    assert man["beds"][0]["genre"] == "lofi"
    assert man["beds"][0]["duration_s"] == 42.5
    assert man["beds"][0]["source"] == "fal-ai/elevenlabs/music"
    assert man["beds"][0]["date"]
    # long enough -> served; too short for a longer cut -> not served
    assert brandkit.find_bed("lofi", 40) == dst
    assert brandkit.find_bed("lofi", 50) is None
    assert brandkit.find_bed("house", 10) is None
    # registering the same audio twice does not duplicate the entry
    assert brandkit.register_bed(str(src), "lofi", 42.5) == dst
    assert len(brandkit.music_manifest()["beds"]) == 1


def test_find_bed_prefers_the_shortest_fit(music_kit, tmp_path):
    for name, dur in (("long.mp3", 120.0), ("short.mp3", 30.0)):
        p = tmp_path / name
        p.write_bytes(name.encode())
        brandkit.register_bed(str(p), "house", dur)
    got = brandkit.find_bed("house", 25)
    assert open(got, "rb").read() == b"short.mp3"


def test_music_generate_serves_bed_from_kit_with_zero_api_calls(
        music_kit, tmp_path, monkeypatch):
    src = tmp_path / "bed.mp3"
    src.write_bytes(b"lofi-bed")
    brandkit.register_bed(str(src), "lofi", 60.0)
    monkeypatch.setattr(audio_post, "_fal",
                        lambda *a, **k: pytest.fail("FAL called with a kit bed available"))
    ledger_rows = []
    monkeypatch.setattr(audio_post.ledger, "add",
                        lambda *a, **k: ledger_rows.append(a))
    out = str(tmp_path / "cut_01_music.mp3")
    plan = {"id": "cut_01", "duration_s": 20.0, "segments": [[0, 20]],
            "title": "demo"}          # pick_bed -> lofi
    assert audio_post.pick_bed(plan) == "lofi"
    assert audio_post._music_generate(plan, out) == out
    assert open(out, "rb").read() == b"lofi-bed"
    assert ledger_rows and ledger_rows[0][0] == "brandkit-music"
    assert ledger_rows[0][2] == 0.0


def test_music_generate_falls_back_to_fal_and_registers_the_new_bed(
        music_kit, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(audio_post, "_fal",
                        lambda *a, **k: calls.append(a) or "http://fal/bed.mp3")
    monkeypatch.setattr(audio_post, "_download",
                        lambda url, path: open(path, "wb").write(b"fresh") or path)
    monkeypatch.setattr(audio_post.media, "probe",
                        lambda p: {"format": {"duration": "22.0"}})
    out = str(tmp_path / "cut_02_music.mp3")
    plan = {"id": "cut_02", "duration_s": 20.0, "segments": [[0, 20]],
            "title": "demo"}
    assert audio_post._music_generate(plan, out) == out
    assert calls                                   # FAL generated (library empty)
    beds = brandkit.music_manifest()["beds"]
    assert len(beds) == 1 and beds[0]["genre"] == "lofi"
    assert beds[0]["duration_s"] == 22.0
    # the NEXT cut of the same genre is now served from the library
    monkeypatch.setattr(audio_post, "_fal",
                        lambda *a, **k: pytest.fail("library bed not reused"))
    monkeypatch.setattr(audio_post.ledger, "add", lambda *a, **k: None)
    out2 = str(tmp_path / "cut_03_music.mp3")
    assert audio_post._music_generate(dict(plan, id="cut_03"), out2) == out2
    assert open(out2, "rb").read() == b"fresh"


def test_registration_failure_never_fails_the_render(
        music_kit, tmp_path, monkeypatch):
    monkeypatch.setattr(audio_post, "_fal", lambda *a, **k: "http://fal/x.mp3")
    monkeypatch.setattr(audio_post, "_download",
                        lambda url, path: open(path, "wb").write(b"ok") or path)
    monkeypatch.setattr(audio_post.media, "probe",
                        lambda p: (_ for _ in ()).throw(RuntimeError("no probe")))
    out = str(tmp_path / "m.mp3")
    plan = {"id": "x", "duration_s": 15.0, "segments": [[0, 15]], "title": ""}
    assert audio_post._music_generate(plan, out) == out


# --------------------------------------------------------- caption stroke

def test_default_stroke_is_the_thin_preset():
    assert captions.DEFAULT_STYLE["stroke_px"] == 2        # ~40% of the old 6
    assert captions._stroke_px() == 2
    assert captions._stroke_px(None) == 2


def test_explicit_stroke_w_is_respected(tmp_path):
    assert captions._stroke_px(6) == 6
    a, b = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    captions.karaoke_png(["hey", "there"], 0, a)               # thin preset
    captions.karaoke_png(["hey", "there"], 0, b, stroke_w=6)   # old slab
    assert Image.open(a).tobytes() != Image.open(b).tobytes()
    # heavier stroke -> taller canvas (2*stroke_w in the height formula)
    assert Image.open(b).height == Image.open(a).height + 8


def test_soft_shadow_is_present_and_padded(tmp_path, monkeypatch):
    """The shadow layer adds semi-transparent dark pixels under the text and
    the canvas grows by the shadow pad so nothing is clipped."""
    p = str(tmp_path / "s.png")
    captions.text_png("shadowed", p, size=56)
    img = Image.open(p)
    alphas = set(img.convert("RGBA").getchannel("A").tobytes())
    assert any(0 < a < 200 for a in alphas)        # blurred low-alpha shadow
    h_with = captions.block_height("shadowed", size=56)
    shadow_pad = captions._shadow_pad()
    monkeypatch.setitem(captions.DEFAULT_STYLE, "shadow", None)
    q = str(tmp_path / "ns.png")
    captions.text_png("shadowed", q, size=56)
    assert captions.block_height("shadowed", size=56) == \
        h_with - shadow_pad
    assert Image.open(q).height == img.height - shadow_pad
