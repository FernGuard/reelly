"""Motion card tests. Each one is named after the failure it protects against.

These are all regressions that shipped or nearly shipped, so they are written
against the generated HTML rather than against a rendered video: the render is
slow and the defects were all decided at template time.
"""
import re

import pytest

from reelly import card


def _page(beats, **kw):
    plan, total = card._plan(beats)
    return card._html(plan, total, kw.get("logo"), 90, 42, kw.get("bg"), kw.get("pal"))


ONE = [{"text": "Only beat", "sub": "sub"}]
TWO = [{"text": "First beat", "sub": "one"}, {"text": "Second beat", "sub": "two"}]


# --- timeline -------------------------------------------------------------

def test_beats_do_not_overlap_and_cover_the_timeline():
    plan, total = card._plan(TWO)
    assert plan[0]["start"] == 0.0
    assert plan[1]["start"] == pytest.approx(plan[0]["dur"])
    assert total == pytest.approx(sum(b["dur"] for b in plan))


def test_long_beat_gets_longer_than_short_beat():
    """Pacing is set by reading time. A fixed slot per beat rushes the long
    ones and strands the short ones."""
    plan, _ = card._plan([{"text": "Go."},
                          {"text": "A considerably longer line of copy here."}])
    assert plan[1]["dur"] > plan[0]["dur"]


def test_last_beat_never_exits():
    """The card has to end ON its message. An exit on the final beat leaves the
    viewer looking at an empty frame at the exact moment the CTA should land."""
    page = _page(TWO)
    assert "wordout" in page          # the first beat still leaves
    outs = re.findall(r"wordout", page)
    ins = re.findall(r"wordin", page)
    assert len(outs) < len(ins)       # but not every word has one


def test_single_beat_card_has_no_exit_at_all():
    # The keyframes are always defined; what matters is that nothing uses them.
    assert ",wordout" not in _page(ONE)


# --- the fill-mode regression ---------------------------------------------

def test_entry_holds_and_exit_does_not_fill_backwards():
    """An element with an entry and an exit must be `both, forwards`.

    `both, both` lets the exit's start state fill BACKWARDS over the entry, so
    every beat's accent rule was drawn at full width from frame zero.
    `backwards, forwards` is the opposite failure: nothing applies between the
    entry ending and the exit starting, so the word snaps back to its off-screen
    base and then flies past when the exit runs.
    """
    page = _page(TWO)
    assert "animation-fill-mode:both,forwards" in page
    assert "animation-fill-mode:backwards,forwards" not in page
    assert "animation-fill-mode:both!important" not in page


def test_animations_are_paused_so_a_screenshot_is_a_seek():
    """Every frame is captured by seeking a paused timeline. If anything is
    allowed to run, frames race the screenshot and the video judders."""
    assert "animation-play-state:paused!important" in _page(ONE)


# --- layout ---------------------------------------------------------------

def test_headline_shrinks_as_the_line_grows():
    short = card._size("Go.", 96)
    long = card._size("A considerably longer line of copy that has to fit.", 96)
    assert short > long >= card.FLOOR


def test_head_size_never_exceeds_the_caller_cap():
    assert card._size("Go.", 60) == 60


def test_consecutive_beats_are_not_in_the_same_place():
    """If every beat lands in the same spot the sequence reads as one slide with
    the words being swapped out."""
    plan, _ = card._plan([{"text": "one"}, {"text": "two"}, {"text": "three"},
                          {"text": "four"}])
    assert plan[0]["pos"] != plan[1]["pos"]


def test_last_beat_is_centred_wherever_it_falls():
    for n in (3, 4, 5):
        plan, _ = card._plan([{"text": f"b{i}"} for i in range(n)])
        assert plan[-1]["pos"] == "mid"


def test_logo_is_centred_without_a_transform():
    """The `rise` keyframe ends on transform:translateY(0), which replaces the
    whole transform. Centering with translateX(-50%) is silently dropped and the
    logo lands half its width right of centre."""
    page = _page(ONE, logo="/nonexistent.png")
    css = page[page.index(".logo{"):page.index(".logo{") + 260]
    assert "margin:0 auto" in css
    assert "translateX(-50%)" not in css


# --- emphasis -------------------------------------------------------------

def test_emphasis_markers_are_stripped_from_the_visible_word():
    assert card._words("make it **count**") == [("make", False), ("it", False),
                                                ("count", True)]


def test_emphasised_word_is_rendered_in_the_accent_colour():
    page = _page([{"text": "make it **count**"}])
    assert 'class="hot"' in page
    assert "**" not in page


# --- palette --------------------------------------------------------------

def test_palette_falls_back_to_brand_when_the_image_is_unreadable():
    assert card._palette("/nope/not-an-image.png") == card.BRAND


def test_palette_differs_between_differently_coloured_images():
    """A run of cards sharing one hardcoded accent reads as a single template
    with the words swapped, which is the opposite of what a feed should look
    like."""
    from PIL import Image
    import tempfile, os
    made = []
    for rgb in ((200, 40, 40), (40, 80, 200)):
        f = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
        Image.new("RGB", (64, 64), rgb).save(f)
        made.append(f)
    try:
        assert card._palette(made[0])["accent"] != card._palette(made[1])["accent"]
    finally:
        for f in made:
            os.unlink(f)


def test_palette_accent_is_forced_legible_on_a_dark_frame():
    """The accent is taken from the picture for its HUE only. A muddy source
    colour used as-is disappears against the scrim."""
    from PIL import Image
    import tempfile, os, colorsys
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    Image.new("RGB", (64, 64), (30, 26, 22)).save(f)   # near-black, low sat
    try:
        acc = card._palette(f)["accent"].lstrip("#")
    finally:
        os.unlink(f)
    r, g, b = (int(acc[i:i + 2], 16) / 255 for i in (0, 2, 4))
    _, light, sat = colorsys.rgb_to_hls(r, g, b)
    assert light > 0.5 and sat > 0.6


# --- media ----------------------------------------------------------------

def test_clip_beats_carry_their_own_start_so_footage_tracks_the_card():
    page = _page([{"text": "one", "clip": "/tmp/a.mp4"},
                  {"text": "two", "clip": "/tmp/b.mp4"}])
    starts = re.findall(r'data-start="([0-9.]+)"', page)
    assert len(starts) == 2 and float(starts[1]) > float(starts[0])


def test_shots_wipe_rather_than_dissolve_and_alternate_direction():
    page = _page([{"text": "a", "img": "/tmp/1.png"}, {"text": "b", "img": "/tmp/2.png"},
                  {"text": "c", "img": "/tmp/3.png"}])
    used = [w for w in ("wipeL", "wipeU", "wipeR", "wipeD")
            if re.search(rf"animation:{w} ", page)]
    assert len(used) >= 2


def test_every_shot_is_moving():
    """A still held static in a vertical feed reads as a stalled video."""
    page = _page([{"text": "a", "img": "/tmp/1.png"}])
    assert "@keyframes kb0" in page and "animation:kb0" in page


def test_beats_without_media_still_render():
    page = _page([{"text": "a", "img": "/tmp/1.png"}, {"text": "b"}])
    assert page.count('class="media"') == 1


def test_shots_declare_fill_mode_inline_so_they_stay_hidden_until_their_beat():
    """The inline `animation` shorthand resets fill-mode and beats the class
    rule. Without an inline fill-mode every shot renders from frame zero with no
    clip-path, and the last beat's shot paints over the entire card."""
    page = _page([{"text": "a", "img": "/tmp/1.png"}, {"text": "b", "img": "/tmp/2.png"}])
    layers = re.findall(r'<div class="media" style="([^"]+)"', page)
    assert layers and all("animation-fill-mode:both" in s for s in layers)


def test_logo_is_still_on_screen_at_the_last_frame():
    """`animation:` resets fill-mode, so a shared earlier rule cannot supply it.
    Without fill-mode in the same rule the logo shows only while its 0.8s
    animation runs and is gone by the final frame, which is the frame it is for.
    """
    page = _page(ONE, logo="/nonexistent.png")
    css = page[page.index(".logo{"):page.index("@keyframes rise")]
    assert "animation-fill-mode:both" in css


def test_emphasis_spans_multiple_words():
    """`**August 5, noon PT.**` is one emphasised phrase. Testing each word on
    its own highlights only the first and last and leaves the middle plain."""
    got = card._words("Publish before **August 5, noon PT.**")
    assert [w for w, hot in got if hot] == ["August", "5,", "noon", "PT."]


def test_offscreen_offsets_clear_the_clip_box_padding():
    """The clip box carries padding so descenders are not sliced, which makes it
    taller than the word inside it. An offset of ~110% therefore leaves the tops
    of unentered words showing as dashes and specks around the line."""
    page = _page(ONE)
    # Only the clipped elements; .stage centres itself with translateY(-50%),
    # which is a legitimate use of a small offset.
    for frag in (".head .w i{", "@keyframes wordout{", ".sub i{", "@keyframes subout{"):
        chunk = page[page.index(frag):page.index(frag) + 220]
        for pct in re.findall(r"translateY\((-?\d+)%\)", chunk):
            assert abs(int(pct)) >= 140, f"{frag} offset {pct}% does not clear padding"


def test_accent_is_complementary_not_matching():
    """Matching the picture's dominant hue is the obvious move and it is wrong:
    blue art gives blue type, the card reads as one flat colour, and a run of
    cards looks identical however different the imagery was."""
    import colorsys, tempfile, os
    from PIL import Image
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    Image.new("RGB", (64, 64), (30, 90, 200)).save(f)      # solidly blue
    try:
        pal = card._palette(f)
    finally:
        os.unlink(f)
    acc = pal["accent"].lstrip("#")
    r, g, b = (int(acc[i:i + 2], 16) / 255 for i in (0, 2, 4))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    # Blue sits near 0.6; its complement is warm, near 0.1.
    assert hue < 0.2 or hue > 0.9, f"accent hue {hue:.2f} is not warm"
    assert light > 0.5 and sat > 0.6, "accent must stay legible on a dark frame"


# --- render-cost wins: still-hold dedupe + JPEG frames ----------------------

def test_still_start_lands_inside_the_final_hold():
    """_still_start marks the moment every entry animation of the last beat
    (words, pop, rule, sub, logo rise) has finished: after it the page is
    settled and make() duplicates ONE screenshot instead of re-capturing the
    whole LAST_HOLD."""
    plan, total = card._plan(TWO)
    still = card._still_start(plan, total)
    b = plan[-1]
    # after the last word has landed and the logo rise has completed
    assert still >= b["start"] + 0.30 + (len(b["words"]) - 1) * 0.065 + 0.82
    assert still >= max(0.3, total - card.LAST_HOLD - 0.8) + 0.8
    assert still <= total
    # and it actually saves a meaningful share of the hold (~LAST_HOLD*fps)
    saved = int(total * card.FPS) - int(still * card.FPS) - 1
    assert saved >= int((card.LAST_HOLD - 1.5) * card.FPS)


def test_still_start_accounts_for_a_hot_word_pop():
    plan, total = card._plan([{"text": "The **hot** word pops"}])
    still = card._still_start(plan, total)
    n = len(plan[-1]["words"])
    assert still >= plan[-1]["start"] + 0.30 + (n - 1) * 0.065 + 0.42 + 0.52


def test_progress_bar_completes_at_the_still_point():
    """The dedupe must never freeze the progress bar mid-track: prog is timed
    to finish exactly when frame capture stops advancing."""
    plan, total = card._plan(ONE)
    still = card._still_start(plan, total)
    page = card._html(plan, total, None, 90, 42, None, None, still_at=still)
    assert f"animation:prog {still:.2f}s linear" in page
    # without a still point the old behavior stands
    page = card._html(plan, total, None, 90, 42, None, None)
    assert f"animation:prog {total:.2f}s linear" in page
