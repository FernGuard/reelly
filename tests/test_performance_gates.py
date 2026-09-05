"""Tests for the neutral public defaults in ``reelly.performance``."""
from reelly import performance as P
from reelly import direct


def _sents(texts, dur_each=2.0):
    return [{"text": t, "s": i * dur_each, "e": (i + 1) * dur_each}
            for i, t in enumerate(texts)]


# --- P-TRUST -----------------------------------------------------------------
# Synthetic transcript fixtures.

def test_trust_accepts_real_narration():
    """A varied synthetic transcript should caption normally."""
    texts = [f"line number {i} about the outbreak" for i in range(40)]
    t = P.transcript_trust(_sents(texts), duration_s=120)
    assert t["trusted"], t["reason"]


def test_trust_rejects_looped_asr():
    """A repeated synthetic phrase should be rejected.

    Captioning this would burn invented dialogue onto the brand.
    """
    texts = ["I'm so proud of you."] * 8 + ["How lovely.", "Me?"]
    t = P.transcript_trust(_sents(texts), duration_s=358)
    assert not t["trusted"]
    assert t["worst_repeat"] == 8


def test_trust_rejects_single_word_loop_despite_high_wpm():
    """A single-word loop is rejected despite high words per minute.

    wpm alone would have passed this; unique-ratio is why it leads.
    """
    texts = ["Time Time Time Time Time Time Time Time Time"] * 24 + ["a", "b", "c"]
    t = P.transcript_trust(_sents(texts), duration_s=141)
    assert not t["trusted"]
    assert t["wpm"] > P.MIN_WPM


def test_trust_rejects_silence():
    """No speech means plan from picture rather than crashing."""
    t = P.transcript_trust([], duration_s=38)
    assert not t["trusted"] and t["cues"] == 0


# --- P-LEN -------------------------------------------------------------------
# The public default template is 20-28s.

def test_length_accepts_template_window():
    for d in (20.0, 24.0, 28.0):
        assert P.length_verdict(d)[0] == "ok"


def test_length_drops_the_48_second_cut():
    """A 48-second fixture is outside the hard short-form window."""
    assert P.length_verdict(48.1)[0] == "drop"


def test_length_flags_but_keeps_near_misses():
    """A 30s cut is worth a human look; it is not auto-binned."""
    assert P.length_verdict(30.0)[0] == "flag"
    assert P.length_verdict(15.0)[0] == "flag"


# --- P-HANDLES ---------------------------------------------------------------
# Synthetic handle-count cases.

def test_handles_requires_two():
    assert P.handles_verdict(["persistent_character"])[0] == "drop"
    assert P.handles_verdict(["persistent_character", "narrative_turn"])[0] == "ok"


def test_handles_ignores_invented_names():
    """The brain may only claim handles from the known set."""
    assert P.clean_handles(["persistent_character", "vibes", "cool_lighting"]) == \
        ["persistent_character"]


def test_handles_normalises_and_dedupes():
    assert P.clean_handles(["Persistent Character", "persistent-character",
                            "narrative_turn"]) == ["persistent_character", "narrative_turn"]


# --- R1 ----------------------------------------------------------------------

def test_outlier_score_uses_channel_median():
    assert P.outlier_score(10000, 100) == 100.0
    assert P.outlier_score(100, 0) is None


# --- planner wiring ----------------------------------------------------------

def test_visual_plan_rejects_when_too_long():
    cand = {"s": 0, "e": 60, "why": "test", "signals": []}
    ref = {"segments": [[0, 45]], "handles": ["persistent_character", "narrative_turn"],
           "hook": "h", "title": "t"}
    out = direct._visual_plan_from(cand, ref, 1, scenes=[], duration=600, reframe=1.5)
    assert out["_rejected"].startswith("P-LEN")


def test_visual_plan_rejects_when_underhandled():
    cand = {"s": 0, "e": 60, "why": "test", "signals": []}
    ref = {"segments": [[0, 24]], "handles": [], "hook": "h", "title": "t"}
    out = direct._visual_plan_from(cand, ref, 1, scenes=[], duration=600, reframe=1.5)
    assert out["_rejected"].startswith("P-HANDLES")


def test_visual_plan_is_never_captioned():
    """A picture-planned cut must never inherit a transcript."""
    cand = {"s": 0, "e": 60, "why": "test", "signals": []}
    ref = {"segments": [[10, 34]], "handles": ["persistent_character", "readable_detail"],
           "hook": "do not turn around", "title": "The Corridor",
           "overlay_lines": [{"t": 8, "text": "it moved"}], "caption": "which door"}
    p = direct._visual_plan_from(cand, ref, 3, scenes=[], duration=600, reframe=1.5)
    assert p["captions"] == "none"
    assert p["transcript"] == ""
    assert p["planned_from"] == "visual"
    assert p["id"] == "cut_03"
    assert p["overlay_lines"][0]["text"] == "it moved"
    assert p["reframe"] == 1.5


def test_visual_plan_snaps_edits_to_scene_cuts():
    """Never land an edit mid-shot when a real boundary is close."""
    cand = {"s": 0, "e": 60, "why": "test", "signals": []}
    ref = {"segments": [[10.4, 34.6]], "handles": ["persistent_character", "narrative_turn"],
           "hook": "h", "title": "t"}
    p = direct._visual_plan_from(cand, ref, 1, scenes=[10.0, 35.0], duration=600, reframe=1.0)
    assert p["segments"] == [[10.0, 35.0]]


def test_overlay_lines_drop_out_of_range_entries():
    assert direct._overlay_lines({"overlay_lines": [{"t": 99, "text": "late"}]}, 24) == []
    assert direct._overlay_lines({"overlay_lines": [{"t": 6, "text": "ok"}]}, 24)[0]["t"] == 6


def test_length_rechecked_after_payoff_append():
    """A payoff event lengthens the cut; the gate must run on the final length.

    Regression: a 33.0s cut walked past the 32s hard limit because the check
    ran before _apply_payoff extended the segments.
    """
    cand = {"s": 0, "e": 40, "why": "t", "signals": [], "score": 1}
    sents = [{"text": f"s{i}", "s": i * 2.0, "e": i * 2.0 + 1.8} for i in range(16)]
    ref = {"ranges": [[0, 12]], "handles": ["persistent_character", "narrative_turn"],
           "hook": "h", "title": "t", "payoff_event": 0,
           "payoff_why": "completes it"}
    vr = [{"start_s": 60.0, "end_s": 75.0, "label": "payoff", "what_happens": "x",
           "trailer_score": 9, "short_score": 9}]
    out = direct._plan_from(cand, ref, sents, [], 1, vr=vr)
    assert out is None or "_rejected" not in out or "after payoff" in out["_rejected"]


def test_caption_gate_skips_captionless_cuts():
    """A picture-planned cut has no words by design; failing it trains people
    to ignore the QC report."""
    from reelly import judge
    name, status, msg = judge.caption_coverage({"captions": "none", "segments": []}, [])
    assert name == "caption_coverage" and status == "SKIP"


# --- closing beat: one message, one place ------------------------------------

def test_meme_window_clears_before_the_end_card():
    """A callout still on screen when the ask lands splits the attention the
    whole cut just spent earning it."""
    from reelly import overlays
    assert overlays.clear_for_endcard((16.0, 24.0), 26.0, True) == (16.0, 22.3)


def test_short_leftover_window_is_dropped_not_squeezed():
    from reelly import overlays
    assert overlays.clear_for_endcard((21.5, 24.0), 26.0, True) is None


def test_no_cta_means_no_clamp():
    from reelly import overlays
    assert overlays.clear_for_endcard((16.0, 24.0), 26.0, False) == (16.0, 24.0)


def test_end_card_zone_is_the_lower_third():
    """CTAs go where a thumb already is, not wherever the frame is calmest."""
    from reelly import placement as P
    g = [[(60.0, 5.0)] * P.COLS for _ in range(P.ROWS)]
    x, y, _ = P.best_box(g, 700, 180, bands=[], avoid=(),
                         band=(int(P.H * 0.60), P.SAFE["bottom"]))
    assert y >= P.H * 0.60 and y + 180 <= P.SAFE["bottom"]


def test_end_card_never_lands_on_the_caption_band():
    """Regression: a 1020px card found no legal x, best_box fell through to its
    corner fallback, and the fallback ignored the zone — putting the closing
    card on top of a karaoke cue."""
    from reelly import placement as P
    g = [[(60.0, 5.0)] * P.COLS for _ in range(P.ROWS)]
    zone = (int(P.H * 0.52), P.CAPTION_BAND[0] - 24)
    x, y, _ = P.best_row(g, 1020, 216, bands=[P.CAPTION_BAND], band=zone)
    assert y + 216 <= P.CAPTION_BAND[0], "card must clear the caption band"
    assert x == int((P.W - 1020) / 2), "closing card is centred by rule"


def test_end_card_fits_the_frame():
    """Regression: logo + sentence laid out in a row measured ~1490px on a
    1080px frame, so the centred card was clipped at both ends."""
    from reelly import placement as P
    assert P.CARD_W <= P.W - 2 * P.SAFE["left"], "card must fit with margin"


def test_wide_wordmark_is_scaled_to_the_card_not_past_it():
    from reelly import overlays as O
    html = O.badge(__file__, text="make yours on example.invalid", x=None, y=1000,
                   h=120, w=900, stack=True)
    assert "flex-direction:column" in html and "width:900px" in html


def test_scrim_is_a_full_frame_dim_pass():
    """The closing scrim covers the frame and is composited before any text, so
    captions sit above it rather than being dimmed with the footage."""
    import os
    import tempfile
    from PIL import Image
    from reelly import captions
    p = captions.scrim_png(os.path.join(tempfile.mkdtemp(), "s.png"), alpha=0.80)
    im = Image.open(p)
    assert im.size == (1080, 1920)
    assert im.getpixel((540, 960))[3] == int(0.80 * 255)


def test_end_card_has_no_panel_of_its_own():
    """A box on top of an already-dimmed screen is a box on a box."""
    from reelly import overlays as O
    html = O.badge(__file__, text="x", x=None, y=1000, h=120, w=900,
                   stack=True, scrim=0.0)
    assert "background:#090c0a" not in html


def test_end_card_does_not_fade_out():
    """The last thing a viewer sees must be the ask at full strength, not the
    ask half faded."""
    import inspect
    from reelly import overlays as O
    src = inspect.getsource(O.autoplan)
    # three branches since the kit wiring: kitcard, badge, lowerthird
    assert src.count('"fade_out": False') == 3, "every end-card branch holds to the last frame"


def test_true_peak_guard_targets_the_delivered_file():
    """Normalising the mix is not enough: a mix correctly at -2.0 dBFS arrived
    at +1.6 dBFS in the mp4 because the AAC encode overshot by 3.6 dB. The
    guard must measure what was written, not what was intended."""
    import inspect
    from reelly import audio_post, finalize, overlays
    assert "ebur128=peak=true" in inspect.getsource(audio_post.true_peak)
    assert "enforce_true_peak" in inspect.getsource(finalize.run)
    assert "enforce_true_peak" in inspect.getsource(overlays._composite)


def test_true_peak_guard_limits_rather_than_attenuates():
    """Turning the whole mix down to fix peaks trades a true-peak failure for a
    loudness failure: a -3.6 dB attenuation took one file to -3.7 dBTP and
    -15.5 LUFS, at the edge of the loudness gate. Peaks get limited."""
    import inspect
    from reelly import audio_post
    src = inspect.getsource(audio_post.enforce_true_peak)
    assert "alimiter" in src
    assert "volume=" not in src, "must not attenuate the whole mix"
