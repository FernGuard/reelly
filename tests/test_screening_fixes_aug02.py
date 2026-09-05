"""Fixes from a 2026-08-02 screening of sample cuts.

- Endcard = translucent scrim over the STILL-PLAYING video, only after the
  payoff has fully played (kit PNGs are RGBA now; timing anchored to
  payoff-end + a small breath).
- Overlay events can never stretch a render past its source: the four
  duration-gate FAILs were stale card windows past clip end that ffmpeg's
  looped-PNG/SFX graph extended the _gfx file to cover.
- gfx-only accounts ship ONE file per cut: the base burn master leaves
  deliverables/final for deliverables/.cache once the gfx variants exist.
- ingest.link_source resolves its input and never re-links a project's own
  source onto itself (the 'Too many levels of symbolic links' failure).
"""
import json
import os
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

import pytest
from PIL import Image

from reelly import brandkit, finalize, ingest, overlays, products


# --- endcard timing: after the payoff, never on it ---------------------------

def test_endcard_window_starts_after_payoff_plus_small_breath():
    plan = {"duration_s": 26.0, "delivery_end_s": 24.5}
    t0, t1 = overlays.endcard_window(plan)
    assert t0 == pytest.approx(24.5 + overlays.ENDCARD_BREATH_S)
    assert 0.2 <= overlays.ENDCARD_BREATH_S <= 0.3
    assert t1 == 26.0


def test_endcard_window_is_never_pulled_back_onto_the_payoff():
    # a plan so tight the old MIN_ENDCARD_S clamp would start the card at
    # 20.2s -- ON the payoff. Now the card is skipped outright: 0.25s after
    # the delivery is a glitch-flash, and the payoff is never covered.
    plan = {"duration_s": 22.0, "delivery_end_s": 21.5}
    assert overlays.endcard_window(plan) is None
    # with real room, the card starts after the delivery, never on it
    plan = {"duration_s": 24.0, "delivery_end_s": 21.5}
    t0, t1 = overlays.endcard_window(plan)
    assert t0 == pytest.approx(max(24.0 - overlays.ENDCARD_S,
                                   21.5 + overlays.ENDCARD_BREATH_S))
    assert t0 > 21.5 and t1 == 24.0


# --- overlay events can never stretch a render --------------------------------

def _ev(t0, t1, template="chip"):
    return {"template": template, "args": ["x"], "t": [t0, t1],
            "sfx": ["pop.mp3", -14]}


def test_clamp_events_drops_past_end_and_clips_spanning(capsys):
    events = [_ev(1.0, 3.0), _ev(20.0, 24.9), _ev(25.2, 28.6, "badge")]
    kept = overlays.clamp_events(events, 23.6)
    assert [e["t"] for e in kept] == [[1.0, 3.0], [20.0, 23.6]]
    out = capsys.readouterr().out
    assert "DROPPED" in out and "stale" in out


def test_clamp_events_without_a_duration_changes_nothing():
    events = [_ev(1.0, 3.0)]
    assert overlays.clamp_events(events, None) == events


def test_composite_pins_output_to_source_duration(tmp_path):
    """-t <source duration> on the encode: looped PNGs and SFX tails must
    never push a _gfx file past the plan (the duration-gate root cause)."""
    cmds = []
    events = [{"template": overlays.KITCARD, "args": ["card.png"],
               "t": [18.0, 21.0], "sfx": ["ding.mp3", -18]}]
    with mock.patch.object(overlays, "_src_duration", return_value=21.0), \
         mock.patch.object(overlays.subprocess, "run",
                           side_effect=lambda a, **k: cmds.append(a)), \
         mock.patch("reelly.audio_post.enforce_true_peak"), \
         mock.patch("reelly.audio_post.enforce_loudness"):
        overlays._composite("in.mp4", "out.mp4", events, str(tmp_path))
        overlays._composite_variant("gfx.mp4", "trend.mp4", "tg.mp4",
                                    events, str(tmp_path))
    encodes = [c for c in cmds if "-frames:v" not in c]  # skip the freeze-frame grab
    assert encodes
    for cmd in encodes:
        i = cmd.index("-t")
        assert cmd[i + 1] == "21.000"


def test_composite_with_only_stale_events_ships_the_source_untouched(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"video")
    out = tmp_path / "out.mp4"
    with mock.patch.object(overlays, "_src_duration", return_value=10.0), \
         redirect_stdout(StringIO()):
        overlays._composite(str(src), str(out), [_ev(12.0, 15.0)],
                            str(tmp_path))
    assert out.read_bytes() == b"video"


# --- kit endcard is a translucent layer, not an opaque slide ------------------

@pytest.fixture
def kit(tmp_path, monkeypatch):
    monkeypatch.setenv("REELLY_BRANDKIT", str(tmp_path / "kit"))
    logo = tmp_path / "video-studio-logo.png"
    img = Image.new("RGBA", (400, 160), (0, 0, 0, 0))
    for x in range(400):
        for y in range(60, 100):
            img.putpixel((x, y), (23, 205, 255, 255))
    img.save(logo)
    monkeypatch.setattr(brandkit, "_studio_logos", lambda: {"video": str(logo)})
    return tmp_path / "kit"


def test_kit_endcard_is_rgba_with_translucent_scrim_and_solid_logo(kit):
    with redirect_stdout(StringIO()):
        brandkit.build_defaults()
    im = Image.open(brandkit.endcard("video"))
    assert im.mode == "RGBA"
    # away from the lockup: the scrim layer, 55-65% black over live video
    a = im.getpixel((10, 10))[3]
    assert 0.55 * 255 <= a <= 0.65 * 255
    # inside the wordmark band: fully opaque brand pixels on top
    W, H = im.size
    assert im.getpixel((W // 2, int(H * 0.46)))[3] == 255


# --- gfx-only ships ONE file ---------------------------------------------------

def _final_project(tmp_path, files):
    root = tmp_path / "proj"
    fin = root / "deliverables" / "final"
    fin.mkdir(parents=True)
    for f in files:
        (fin / f).write_bytes(b"v")
    return str(root), fin


def test_retire_moves_base_masters_once_all_gfx_exist(tmp_path):
    root, fin = _final_project(tmp_path, ["cut_01.mp4", "cut_01_gfx.mp4",
                                          "cut_02.mp4"])   # cut_02: gfx pending
    plans = [{"id": "cut_01"}, {"id": "cut_02"}]
    with redirect_stdout(StringIO()):
        moved = finalize.retire_unshipped_bases(root, ["gfx"], plans)
    assert [os.path.basename(m) for m in moved] == ["cut_01.mp4"]
    assert sorted(os.listdir(fin)) == ["cut_01_gfx.mp4", "cut_02.mp4"]
    assert os.path.exists(os.path.join(root, "deliverables", ".cache",
                                       "cut_01.mp4"))


def test_retire_keeps_bases_the_variant_set_actually_ships(tmp_path):
    root, fin = _final_project(tmp_path, ["cut_01.mp4", "cut_01_gfx.mp4"])
    assert finalize.retire_unshipped_bases(root, ["plain", "gfx"],
                                           [{"id": "cut_01"}]) == []
    assert sorted(os.listdir(fin)) == ["cut_01.mp4", "cut_01_gfx.mp4"]


def test_retire_handles_trending_pairs_and_waits_for_all_gfx(tmp_path):
    root, fin = _final_project(
        tmp_path, ["cut_01.mp4", "cut_01_gfx.mp4", "cut_01_trending.mp4"])
    plans = [{"id": "cut_01"}]
    # trending_gfx not composited yet: nothing moves
    assert finalize.retire_unshipped_bases(
        root, ["gfx", "trending_gfx"], plans) == []
    (fin / "cut_01_trending_gfx.mp4").write_bytes(b"v")
    with redirect_stdout(StringIO()):
        moved = finalize.retire_unshipped_bases(
            root, ["gfx", "trending_gfx"], plans)
    assert sorted(os.path.basename(m) for m in moved) == [
        "cut_01.mp4", "cut_01_trending.mp4"]
    assert sorted(os.listdir(fin)) == ["cut_01_gfx.mp4",
                                       "cut_01_trending_gfx.mp4"]


def test_description_md_routes_only_files_that_ship(tmp_path):
    plan = {"id": "cut_01", "title": "t", "duration_s": 24.0,
            "hook": {"text": "hi"}, "cta": "play it on example.invalid",
            "caption": "cap"}
    run_acct = {"name": "managed", "trending_audio": False}
    p = tmp_path / "d.md"
    products.description_md("video", plan, str(p), targets=["tiktok", "x"],
                            account=run_acct, variants=["gfx"])
    text = p.read_text()
    assert "cut_01_gfx.mp4" in text
    assert "`cut_01.mp4`" not in text       # the base master does not ship
    # a plain-shipping set keeps the classic routing
    products.description_md("video", plan, str(p), targets=["x"],
                            account=run_acct, variants=["plain", "gfx"])
    assert "`cut_01.mp4`" in p.read_text()


# --- ingest: realpath + self-link guard ---------------------------------------

def _source_project(tmp_path):
    root = tmp_path / "proj"
    (root / "source").mkdir(parents=True)
    real = tmp_path / "captures" / "session.mov"
    real.parent.mkdir()
    real.write_bytes(b"media")
    return str(root), str(real)


def test_link_source_resolves_and_links_the_real_file(tmp_path):
    root, real = _source_project(tmp_path)
    dst = ingest.link_source(root, real, "screen")
    assert os.path.islink(dst) and os.path.realpath(dst) == real


def test_relinking_the_projects_own_source_never_builds_a_self_loop(tmp_path):
    """The 2026-08-01 failure: `reelly analyze <project>/source/screen.mov`
    replaced the symlink with a link to itself and every ffmpeg open died
    with 'Too many levels of symbolic links'."""
    root, real = _source_project(tmp_path)
    dst = ingest.link_source(root, real, "screen")
    again = ingest.link_source(root, dst, "screen")     # analyze on the link
    assert again == dst
    assert os.path.realpath(dst) == real                # no loop
    assert open(dst, "rb").read() == b"media"           # still openable


def test_linking_a_chain_collapses_to_the_real_file(tmp_path):
    root, real = _source_project(tmp_path)
    alias = os.path.join(tmp_path, "alias.mov")
    os.symlink(real, alias)
    dst = ingest.link_source(root, alias, "screen")
    assert os.readlink(dst) == real         # realpath'd, not the alias


def test_composite_never_freezes_or_grabs_frames(tmp_path):
    """Designed endings (2026-08-03): the freeze-hold is GONE. The card for
    new plans lives in the appended outro segment (outro.py); _composite
    must never grab a freeze frame or alter the base video under an event,
    even for a legacy kitcard."""
    cmds = []
    events = [{"template": overlays.KITCARD, "args": ["card.png"],
               "t": [18.0, 21.0]}]
    with mock.patch.object(overlays, "_src_duration", return_value=21.0), \
         mock.patch.object(overlays.subprocess, "run",
                           side_effect=lambda a, **k: cmds.append(a)), \
         mock.patch("reelly.audio_post.enforce_true_peak"), \
         mock.patch("reelly.audio_post.enforce_loudness"):
        overlays._composite("in.mp4", "out.mp4", events, str(tmp_path))
    assert not [c for c in cmds if "-frames:v" in c]
    enc = [c for c in cmds if "-filter_complex" in c][0]
    graph = enc[enc.index("-filter_complex") + 1]
    assert "[bfrz]" not in graph and "freeze" not in graph
