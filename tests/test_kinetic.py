"""Kinetic text layer: hook and timed lines rendered as transparent PNG
sequences in headless Chrome, composited where the static PNG used to sit."""
import json
import os
import shutil
import subprocess

import pytest

from reelly import captions, config, finalize, kinetic


# --- pure logic -------------------------------------------------------------------

def test_hot_word_defaults_to_the_last_word():
    assert kinetic.hot_index(["The", "door", "opens."]) == 2


def test_hot_word_by_index_or_by_word():
    words = ["Not", "this", "one", "again."]
    assert kinetic.hot_index(words, 1) == 1
    assert kinetic.hot_index(words, "one") == 2
    assert kinetic.hot_index(words, "AGAIN") == 3
    assert kinetic.hot_index(words, "missing") == 3
    assert kinetic.hot_index([], None) is None


def test_timeline_holds_between_arrival_and_exit():
    settle, exit_t, stagger = kinetic.timeline(3, 3.6, hot=2)
    assert stagger == kinetic.STAGGER
    assert 0 < settle < exit_t
    assert exit_t == pytest.approx(3.6 - kinetic.EXIT)


def test_short_window_squeezes_entrances_so_the_line_arrives_before_leaving():
    settle, exit_t, stagger = kinetic.timeline(8, 1.4, hot=7)
    assert stagger < kinetic.STAGGER
    assert settle <= exit_t


def test_page_marks_the_hot_word_and_keeps_the_given_breaks(monkeypatch):
    monkeypatch.delenv("REELLY_BRAND", raising=False)
    page = kinetic.page_html("The door opens.", 70, 856, 160, 3.6,
                             hot=2, lines=[["The", "door"], ["opens."]],
                             accent="#3366ff", fill="#f4f1ea")
    assert page.count('class="w hot"') == 1
    assert "opens.</span></span>" in page          # hot word wrapped in the pop
    assert page.count("<br>") == 1
    assert "#3366ff" in page and "#f4f1ea" in page
    assert "white-space:nowrap" in page
    assert "animation-play-state:paused!important" in page


def test_page_escapes_text():
    page = kinetic.page_html("<b>hi</b> & bye", 60, 800, 120, 2.0)
    assert "<b>hi</b>" not in page and "&lt;b&gt;hi&lt;/b&gt;" in page


def test_pop_is_a_small_lift_from_the_bottom_centre():
    """A big scale swallows the word spaces; the pop lifts and grows 4%."""
    page = kinetic.page_html("The door opens.", 70, 856, 160, 3.6, hot=2)
    assert "transform-origin:50% 100%" in page
    assert kinetic.POP_SCALE <= 1.05
    assert f"translateY(-{kinetic.POP_LIFT}em)" in page


def test_wrap_lines_matches_the_static_wrap():
    text = "The door opens on a long and winding road again"
    lines = kinetic.wrap_lines(text, 74, 800)
    assert " ".join(w for ln in lines for w in ln) == text
    assert len(lines) >= 2


def test_headless_chrome_never_touches_the_keychain():
    """From ssh or a background job a keychain access raises a dialog on the
    user's screen; every headless launch passes the mock keychain."""
    import inspect
    from reelly import card, overlays
    for src in (inspect.getsource(card._Browser.__aenter__),
                inspect.getsource(overlays)):
        assert "--use-mock-keychain" in src and "--password-store=basic" in src


def test_off_switch(monkeypatch):
    monkeypatch.setenv("REELLY_KINETIC", "off")
    assert kinetic.enabled() is False


# --- finalize wiring ----------------------------------------------------------------

def test_text_layer_falls_back_to_static_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(kinetic, "enabled", lambda: True)

    def boom(*a, **k):
        raise RuntimeError("no chrome")

    monkeypatch.setattr(kinetic, "hook_line", boom)
    path, dy = finalize._text_layer("Open it.", str(tmp_path), "hook", 3.0)
    assert path.endswith("hook.png") and os.path.exists(path) and dy == 0
    assert not isinstance(path, finalize._Seq)


def test_text_layer_uses_the_sequence_and_lifts_by_the_pad(monkeypatch, tmp_path):
    monkeypatch.setattr(kinetic, "enabled", lambda: True)
    monkeypatch.setattr(kinetic, "hook_line",
                        lambda text, out, dur, **kw: (out + "/k%05d.png", 856, 160, 28))
    path, dy = finalize._text_layer("Open it.", str(tmp_path), "hook", 3.0)
    assert isinstance(path, finalize._Seq) and path.endswith("k%05d.png")
    assert dy == -28


def test_chunks_carry_the_sequence_offset():
    seq = finalize._Seq("/x/k%05d.png")
    events = [(seq, 200, 70.0, 80.0), ("/x/still.png", 300, 0.0, 2.0)]
    chunks = finalize._burn_chunks(events, 160.0, chunk_s=75.0)
    first, second = chunks[0][2], chunks[1][2]
    assert first[0][0].offset == 0.0
    s2 = second[0][0]
    assert isinstance(s2, finalize._Seq)
    assert s2.offset == pytest.approx(75.0 - 70.0)
    assert second[0][2] == 0.0


def test_burn_pass_reads_a_sequence_at_30fps_and_shifts_it(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **k: seen.setdefault("cmd", cmd))
    seq = finalize._Seq(str(tmp_path / "k%05d.png"), offset=0.5)
    finalize._burn_pass("in.mp4", "out.mp4",
                        [(seq, 182, 4.4, 7.4), (str(tmp_path / "a.png"), 300, 0, 2)])
    cmd = seen["cmd"]
    i = cmd.index(str(seq))
    assert cmd[i - 3:i - 1] == ["-framerate", "30"]
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "trim=start=0.500,setpts=PTS-STARTPTS+4.400/TB" in fc
    assert "eof_action=pass" in fc


# --- a real render (needs Chrome) ---------------------------------------------------------

@pytest.mark.skipif(not kinetic.enabled() or not kinetic.probe_ok(),
                    reason="needs headless Chrome")
def test_real_render_is_transparent_and_complete(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.delenv("REELLY_BRAND", raising=False)
    for v in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(v, raising=False)
    pattern, w, h, pad = kinetic.render_line("Open it.", str(tmp_path), 1.0,
                                             size=60, width=600, fps=10)
    frames = sorted(f for f in os.listdir(tmp_path) if f.startswith("k"))
    assert len(frames) == 10
    first = Image.open(tmp_path / frames[0])
    mid = Image.open(tmp_path / frames[6])
    assert first.mode == "RGBA" and first.size == (w, h)
    assert first.getchannel("A").getextrema()[1] < 40      # nothing has arrived yet
    lo, hi = mid.getchannel("A").getextrema()
    assert lo == 0 and hi == 255                            # transparent canvas, solid type


# --- which word is hot, and why ---------------------------------------------------------

@pytest.mark.parametrize("text, kind, want", [
    ("Of course it is.", "line", "course"),         # function words never take it
    ("The door opens.", "line", "opens."),          # end focus
    ("It is what it is.", "line", "is."),           # no content word: last word
    ("Follow for more", "cta", "Follow"),           # a CTA: the action
    ("Play it now", "cta", "Play"),
    ("Now on sale", "cta", "Now"),
])
def test_choose_hot_rules(text, kind, want):
    words = text.split()
    i, reason = kinetic.choose_hot(words, kind=kind)
    assert words[i] == want
    assert reason


def test_plan_hot_wins_with_its_reason():
    i, reason = kinetic.choose_hot(["Not", "this", "one", "again."], hot="this",
                                   reason="contrast with the last one")
    assert i == 1 and reason == "contrast with the last one"


def test_bool_is_not_an_index():
    i, _ = kinetic.choose_hot(["The", "door", "opens."], hot=True)
    assert i == 2


# --- frame 0 and the CTA ----------------------------------------------------------------

def test_hook_without_entrance_is_readable_at_zero():
    page = kinetic.page_html("The door opens.", 70, 856, 160, 3.6, enter=False)
    assert "kin-in" not in page.split("@keyframes")[0]       # no word entrance
    assert "kin-pop" in page and f"{kinetic.POP_AT:.3f}s both" in page
    settle, exit_t, stagger = kinetic.timeline(3, 3.6, hot=2, enter=False)
    assert stagger == 0.0 and settle == pytest.approx(kinetic.POP_AT + kinetic.POP)


def test_cta_holds_to_the_end():
    page = kinetic.page_html("Follow for more", 70, 856, 160, 2.8, leave=False)
    assert "animation:kin-out" not in page
    _, exit_t, _ = kinetic.timeline(3, 2.8, hot=0, leave=False)
    assert exit_t == 2.8


def test_text_layer_skips_the_entrance_only_at_t0(monkeypatch, tmp_path):
    monkeypatch.setattr(kinetic, "enabled", lambda: True)
    seen = []
    monkeypatch.setattr(kinetic, "hook_line",
                        lambda text, out, dur, hot=None, enter=True, hot_reason=None,
                        label=None: (seen.append(enter), (out + "/k%05d.png", 1, 1, 28))[1])
    finalize._text_layer("Open it.", str(tmp_path), "hook", 3.0, t0=0.0)
    finalize._text_layer("Later.", str(tmp_path), "ov0", 3.0, t0=4.4)
    assert seen == [False, True]


def test_outro_segment_composites_the_cta_sequence(monkeypatch, tmp_path):
    from reelly import outro
    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: seen.setdefault("cmd", cmd))
    outro.build_segment("b.png", "c.png", 2.8, "o.mp4", seq=("/x/k%05d.png", 870))
    cmd = seen["cmd"]
    assert "/x/k%05d.png" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[bc][2:v]overlay=(W-w)/2:870:eof_action=repeat" in fc


def test_outro_keeps_the_static_card_without_kinetic(monkeypatch, tmp_path):
    from reelly import outro
    monkeypatch.setattr(kinetic, "enabled", lambda: False)
    assert outro._kinetic_cta({"cta": "Follow for more"}, None, str(tmp_path), 2.8) is None


@pytest.mark.skipif(not kinetic.enabled() or not kinetic.probe_ok(),
                    reason="needs headless Chrome")
def test_real_hook_frame_zero_is_fully_drawn(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.delenv("REELLY_BRAND", raising=False)
    for v in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(v, raising=False)
    kinetic.render_line("Open it.", str(tmp_path), 1.0, size=60, width=600,
                        fps=10, enter=False)
    first = Image.open(tmp_path / "k00000.png")
    assert first.getchannel("A").getextrema()[1] == 255     # solid type on frame 0
