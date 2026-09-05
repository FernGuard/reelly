"""Tests for the 13 pipeline gaps logged from the Friday live sessions
(2026-07-31). Each test names the gap it protects; the silent-skip class
(gaps 1/3 and the per-chunk cache of gap 2) is tested as a class: work can
be dropped only loudly, and repair must not reroll what succeeded."""
import json
import os

import pytest

from reelly import (accounts, captions, clearance, deliver, direct, judge,
                    learn, ledger, overlays, plain, products, visual)


# --- gaps 1 + 2 + 3: visual review silent skip / per-chunk cache / ledger ----

def _wire_visual(monkeypatch, tmp_path, analyze_fn, duration=900):
    monkeypatch.setattr(visual.media, "duration", lambda v: duration)
    monkeypatch.setattr(visual, "_compress_segment", lambda *a, **k: None)
    monkeypatch.setattr(visual, "_client", lambda: object())
    monkeypatch.setattr(visual, "_sleep", lambda s: None)
    monkeypatch.setattr(visual, "_analyze_chunk", analyze_fn)
    monkeypatch.setattr(ledger, "LEDGER", str(tmp_path / "ledger.json"))


def _fake_video(tmp_path):
    v = tmp_path / "session.mp4"
    v.write_bytes(b"x" * 128)
    return str(v)


def test_visual_review_writes_complete_artifact_and_chunk_cache(monkeypatch, tmp_path):
    """Gap 2: every chunk's result is cached keyed on (video, range, model)."""
    calls = []

    def ok(client, model, prox, mins):
        calls.append(mins)
        return [{"label": "x", "start": "00:10", "end": "00:20", "short_score": 8}]

    _wire_visual(monkeypatch, tmp_path, ok)
    video = _fake_video(tmp_path)
    oj, om = str(tmp_path / "vr.json"), str(tmp_path / "vr.md")
    seqs = visual.review(video, oj, om, model="test-model")
    assert len(seqs) == 2 and len(calls) == 2  # 15 min -> 10 + 5 min chunks
    art = json.load(open(oj))
    assert art["complete"] is True and art["missing"] == []
    assert len(art["sequences"]) == 2
    cache = os.listdir(tmp_path / "visual_chunks")
    assert len(cache) == 2 and all("test-model" in c for c in cache)
    assert len(json.load(open(ledger.LEDGER))["entries"]) == 2


def test_visual_rerun_uses_cache_and_bills_nothing(monkeypatch, tmp_path):
    """Gap 2 (MANDATORY): a re-run must NOT reroll or re-bill cached chunks,
    so the analysis a cut plan was keyed to survives a repair."""
    def ok(client, model, prox, mins):
        return [{"label": "kept", "start": "00:05", "end": "00:15", "short_score": 9}]

    _wire_visual(monkeypatch, tmp_path, ok)
    video = _fake_video(tmp_path)
    oj, om = str(tmp_path / "vr.json"), str(tmp_path / "vr.md")
    visual.review(video, oj, om, model="m")

    def boom(client, model, prox, mins):
        raise AssertionError("cached chunk was re-analyzed")

    monkeypatch.setattr(visual, "_analyze_chunk", boom)
    seqs = visual.review(video, oj, om, model="m")
    assert [s["label"] for s in seqs] == ["kept", "kept"]
    assert len(json.load(open(ledger.LEDGER))["entries"]) == 2  # unchanged


def test_visual_failed_chunk_is_retried_recorded_and_raises(monkeypatch, tmp_path):
    """Gaps 1 + 3: a chunk that fails all retries is recorded IN the artifact,
    the ledger delta is explained, and the stage exits non-zero."""
    attempts = []

    def half(client, model, prox, mins):
        attempts.append(mins)
        if mins == 5:  # second (partial) chunk always dies
            raise RuntimeError("upload FAILED")
        return [{"label": "ok", "start": "00:01", "end": "00:09", "short_score": 7}]

    _wire_visual(monkeypatch, tmp_path, half)
    video = _fake_video(tmp_path)
    oj, om = str(tmp_path / "vr.json"), str(tmp_path / "vr.md")
    with pytest.raises(RuntimeError, match="INCOMPLETE"):
        visual.review(video, oj, om, model="m")
    assert attempts.count(5) == visual.RETRIES  # retried, not skipped
    art = json.load(open(oj))
    assert art["complete"] is False
    assert art["missing"][0]["start_s"] == 600 and art["missing"][0]["end_s"] == 900
    assert visual.needs_rerun(oj)  # analyze must not treat this as a cache hit
    assert "MISSING" in open(om).read()
    # only the successful chunk was charged; the hole explains the underspend
    assert len(json.load(open(ledger.LEDGER))["entries"]) == 1

    # the repair: only the hole is fresh, the good chunk comes from cache
    def heal(client, model, prox, mins):
        assert mins == 5, "cached chunk must not re-run during repair"
        return [{"label": "healed", "start": "00:00", "end": "00:04", "short_score": 6}]

    monkeypatch.setattr(visual, "_analyze_chunk", heal)
    visual.review(video, oj, om, model="m")
    art = json.load(open(oj))
    assert art["complete"] is True and not visual.needs_rerun(oj)
    assert sorted(s["label"] for s in art["sequences"]) == ["healed", "ok"]


def test_visual_sequences_helper_accepts_both_artifact_shapes():
    legacy = [{"label": "a"}]
    assert visual.sequences(legacy) == legacy
    assert visual.sequences({"sequences": legacy, "missing": []}) == legacy
    assert visual.sequences(None) == []
    assert visual.missing_ranges(legacy) == []
    assert visual.missing_ranges({"missing": [{"start_s": 0}]}) == [{"start_s": 0}]


# --- gap 4: verdict parser must not silently drop lines ----------------------

def test_parser_accepts_tokens_already_in_use(tmp_path):
    p = tmp_path / "V.md"
    p.write_text("\n".join([
        "2026-07-27 placement/fallbacks LEARNED because a constrained search lied",
        "2026-07-27 audio/true-peak FIXED because ten files clipped",
        "2026-07-27 sample-campaign/cuts_01-30 KEEP(all 30) because approved first pass",
        "2026-07-31 sample/cut_02 KILL for the scheduled profile because scheduler has no audio library",
        "2026-07-23 sample-project/cut_09 AUTHORED because the set skipped layer 1",
    ]))
    rows, unparsed = learn.parse_verdicts_full(str(p))
    assert [r["verdict"] for r in rows] == ["LEARNED", "FIXED", "KEEP", "KILL", "AUTHORED"]
    assert rows[2]["qualifier"] == "all 30"          # space in parens broke \S*
    assert rows[3]["scope"] == "for the scheduled profile"  # scope words before 'because'
    assert unparsed == []


def test_parser_reports_unparsed_dated_lines_instead_of_skipping(tmp_path):
    p = tmp_path / "V.md"
    p.write_text("2026-07-09 sample-exp blind round: 6/6 clips SHIP with no because\n"
                 "2026-07-10 x/y KEEP because fine\n")
    rows, unparsed = learn.parse_verdicts_full(str(p))
    assert len(rows) == 1
    assert len(unparsed) == 1 and unparsed[0]["line"] == 1


# --- gap 10: constraint verdicts bypass the outlier gate ---------------------

def test_constraint_verdict_reaches_proposals_without_outliers(tmp_path, monkeypatch):
    v = tmp_path / "V.md"
    v.write_text("2026-07-31 sample/cut_05 CONSTRAINT because scheduled profile cannot use trending "
                 "audio: scheduler publishes third-party and rights forbid it\n")
    monkeypatch.setattr(learn, "VERDICTS", str(v))
    monkeypatch.setattr(learn, "PROPOSALS", str(tmp_path / "proposals"))
    report = learn.run()
    assert "CONSTRAINT" in report and "playbook proposals" in report
    files = os.listdir(tmp_path / "proposals")
    assert files and "CONSTRAINT" in open(tmp_path / "proposals" / files[0]).read()


def test_unparsed_lines_are_loud_in_the_learn_report(tmp_path, monkeypatch):
    v = tmp_path / "V.md"
    v.write_text("2026-07-09 broken line with no verdict token at all\n")
    monkeypatch.setattr(learn, "VERDICTS", str(v))
    monkeypatch.setattr(learn, "PROPOSALS", str(tmp_path / "proposals"))
    assert "DID NOT PARSE" in learn.run()


# --- gaps 5 + 6: account scoping of P5 + variant selection -------------------

@pytest.fixture
def clean_home(tmp_path, monkeypatch):
    """Isolate from any real ~/.reelly/accounts.json."""
    from reelly import config
    monkeypatch.setattr(config, "HOME", str(tmp_path / "reelly-home"))
    return tmp_path


def test_p5_scoped_off_for_accounts_without_trending_audio(clean_home):
    run_acct = accounts.load("managed")
    tek = accounts.load("creator")
    assert products.platform_spec("tiktok", tek)["mix"] == "clean"
    spec = products.platform_spec("tiktok", run_acct)
    assert spec["mix"] == "music" and spec["file"] == ""
    assert "does not use in-app trending audio" in spec["note"]
    # platforms without a trending ecosystem are untouched
    assert products.platform_spec("youtube", run_acct)["mix"] == "music"


def test_account_variant_selection_and_precedence(clean_home, tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    prof = accounts.load("managed")
    assert accounts.variants_for(str(root), prof) == ["gfx"]
    # trending variants are dropped LOUDLY for a no-trending-audio account
    assert accounts.variants_for(str(root), prof, "gfx,trending") == ["gfx"]
    with pytest.raises(SystemExit):
        accounts.variants_for(str(root), prof, "trending")
    # delivery.json binds account + variants per project
    (root / "delivery.json").write_text(json.dumps(
        {"account": "managed", "variants": ["gfx"]}))
    assert accounts.for_project(str(root))["name"] == "managed"
    tek = accounts.load("creator")
    assert accounts.variants_for(str(root), tek) == ["gfx"]  # delivery.json wins
    assert set(accounts.variants_for(str(root), tek, "plain,gfx")) == {"plain", "gfx"}


def test_description_routing_is_account_aware(clean_home, tmp_path):
    plan = {"id": "cut_01", "title": "t", "duration_s": 22.0,
            "hook": {"text": "hello"}, "cta": "play on example.invalid",
            "caption": "which door would you pick"}
    p = str(tmp_path / "D.md")
    products.description_md("video", plan, p, targets=["tiktok"],
                            account=accounts.load("managed"))
    body = open(p).read()
    assert "`cut_01.mp4`" in body and "_trending" not in body
    assert "in-app audio disabled" in body
    products.description_md("video", plan, p, targets=["tiktok"],
                            account=accounts.load("creator"))
    assert "`cut_01_trending.mp4`" in open(p).read()


# --- gap 7: P-PLAIN brand-voice rules govern branded copy, never a ----------
# --- description of recorded content (provenance axis, reviewer 2026-08-01) --

def test_narrative_strangers_passes_with_nothing_recorded():
    """the reviewer's acceptance case, verbatim: text describing the footage
    (fictional characters, on-screen events) is a record of what was shot,
    not a copy decision -- it must pass CLEAN, no exemption, nothing logged."""
    plan = {"hook": {"text": "Four strangers. One luxury hall."},
            "caption": "Four strangers. One luxury hall. A world taking shape."}
    name, status, detail = plain.verdict(plan)
    assert status == "PASS"
    assert "exempt" not in detail and "strangers" not in detail


def test_audience_use_of_strangers_still_fails():
    for text in ("strangers will find your game",
                 "Then strangers can play it",
                 "made so strangers scrolling past stop"):
        hits = plain.find(text)
        assert any(t == "strangers" for t, _ in hits), text
    assert plain.verdict({"caption": "show your world to strangers"})[1] == "FAIL"


def test_mixed_caption_judges_each_sentence_on_its_own():
    """A lore tease plus a self-insertion ask: the narrative sentence carries
    the word, the audience sentence carries 'you', and neither combination is
    a violation because they are different sentences."""
    plan = {"caption": "Four strangers. One luxury hall. Which one are you?"}
    assert plain.verdict(plan)[1] == "PASS"


def test_tribe_is_scoped_the_same_way_and_jargon_stays_strict():
    assert any(t == "tribe" for t, _ in plain.find("find your tribe"))
    assert not plain.find("a nomad tribe crosses the desert")
    # jargon entries fire everywhere, context or not
    assert plain.verdict({"caption": "everyone lands in the same catalog row"})[1] == "FAIL"
    assert plain.verdict({"caption": "our cohort"})[1] == "FAIL"


def test_transcript_text_is_never_gated_by_p_plain():
    """The provenance boundary, pinned: the transcript (and the burned-in
    cues derived from it) is a RECORD of what was said on camera, not a copy
    decision. Even a transcript full of listed terms -- brand-voice words in
    branded context, jargon, retired names -- must never fail the gate."""
    plan = {"hook": {"text": "watch the reveal"},
            "caption": "A world taking shape.",
            "cta": "play it on example.invalid",
            "transcript": "so strangers can play your game, it lands in the "
                          "catalog row of our funnel, back when it was video"}
    name, status, detail = plain.verdict(plan)
    assert status == "PASS", detail
    assert "strangers" not in detail and "catalog" not in detail


def test_plain_exemption_backstop_still_works_for_audience_terms():
    plan = {"caption": "show your world to strangers",
            "plain_exempt": {"strangers": "approved wording for this campaign"}}
    name, status, detail = plain.verdict(plan)
    assert status == "PASS" and "exempted" in detail
    plan2 = {"caption": "show your world to strangers",
             "plain_exempt": {"strangers": "  "}}
    assert plain.verdict(plan2)[1] == "FAIL"  # empty reason is no exemption


# --- gap 8: voice clearance (declared map, diarizer flagged) -----------------

def _root_with(tmp_path, voices=None, guests=None):
    an = tmp_path / "proj" / "analysis"
    an.mkdir(parents=True)
    if voices is not None:
        (an / "voices.json").write_text(json.dumps(voices))
    if guests is not None:
        (an / "guest_blocks.json").write_text(json.dumps(guests))
    return str(tmp_path / "proj")


def test_uncleared_speaker_ranges_block_cuts(tmp_path):
    root = _root_with(tmp_path, voices={"speakers": [
        {"id": "host", "cleared": True, "ranges": []},
        {"id": "guest-1", "cleared": False, "note": "no release",
         "ranges": [[100.0, 160.0]]}]})
    blocked = clearance.blocked_ranges(root)
    assert len(blocked) == 1 and "guest-1" in blocked[0][2]
    name, status, detail = clearance.verdict({"segments": [[120.0, 140.0]]}, blocked)
    assert status == "FAIL" and "guest-1" in detail
    assert clearance.verdict({"segments": [[10.0, 30.0]]}, blocked)[1] == "PASS"


def test_no_artifacts_means_no_blocks(tmp_path):
    root = _root_with(tmp_path)
    assert clearance.blocked_ranges(root) == []
    assert clearance.verdict({"segments": [[0, 20]]}, [])[1] == "SKIP"


# --- gap 8 follow-up: local diarizer behind the voices.json interface --------

class _Seg:
    def __init__(self, s, e):
        self.start, self.end = s, e


class _FakeAnnotation:
    """Duck-typed pyannote Annotation: two speakers taking turns."""
    def itertracks(self, yield_label=True):
        yield _Seg(0.0, 10.0), None, "SPEAKER_00"
        yield _Seg(10.5, 20.0), None, "SPEAKER_01"
        yield _Seg(20.4, 30.0), None, "SPEAKER_00"


def test_diarize_two_speaker_fixture_produces_two_labelled_speakers(monkeypatch, tmp_path):
    from reelly import diarize
    monkeypatch.setattr(diarize, "_pipeline", lambda: (lambda wav: _FakeAnnotation()))
    monkeypatch.setattr(diarize, "_extract_audio", lambda v, d: None)
    out = str(tmp_path / "speaker_turns.json")
    art = diarize.run("session.mp4", out)
    assert art["status"] == "ok" and len(art["speakers"]) == 2
    assert art["speakers"]["SPEAKER_00"]["ranges"] == [[0.0, 10.0], [20.4, 30.0]]
    assert art["speakers"]["SPEAKER_01"]["ranges"] == [[10.5, 20.0]]
    assert not diarize.needs_rerun(out)


def test_diarized_turns_map_onto_word_timings():
    from reelly import diarize
    turns = [{"s": 0.0, "e": 10.0, "speaker": "SPEAKER_00"},
             {"s": 10.5, "e": 20.0, "speaker": "SPEAKER_01"}]
    words = [{"t": "hello", "s": 1.0, "e": 1.4},
             {"t": "there", "s": 12.0, "e": 12.3},
             {"t": "gap", "s": 10.1, "e": 10.3}]  # between turns
    lw = diarize.label_words(words, turns)
    assert [w["speaker"] for w in lw] == ["SPEAKER_00", "SPEAKER_01", None]


def test_uncleared_diarized_speaker_excludes_the_cut(tmp_path):
    """The end-to-end contract: diarizer says WHEN, voices.json says WHO is
    allowed, and a cut overlapping the uncleared speaker fails."""
    root = _root_with(tmp_path, voices={"speakers": [
        {"id": "SPEAKER_00", "cleared": True},
        {"id": "SPEAKER_01", "cleared": False, "note": "guest, no release"}]})
    (os.path.join(root, "analysis", "speaker_turns.json"))
    json.dump({"engine": "test", "status": "ok", "turns": [],
               "speakers": {"SPEAKER_00": {"ranges": [[0.0, 10.0]]},
                            "SPEAKER_01": {"ranges": [[10.5, 20.0]]}}},
              open(os.path.join(root, "analysis", "speaker_turns.json"), "w"))
    blocked = clearance.blocked_ranges(root)
    assert len(blocked) == 1 and "SPEAKER_01" in blocked[0][2]
    assert clearance.verdict({"segments": [[12.0, 18.0]]}, blocked)[1] == "FAIL"
    assert clearance.verdict({"segments": [[2.0, 9.0]]}, blocked)[1] == "PASS"
    assert clearance.diarization_status(root)[0] == "ok"


def test_blocking_an_unresolvable_speaker_is_a_hard_actionable_error(tmp_path):
    # diarization unavailable + no explicit ranges: refuse to guess
    root = _root_with(tmp_path, voices={"speakers": [
        {"id": "guest", "cleared": False}]})
    json.dump({"status": "unavailable", "error": "no token", "speakers": {}},
              open(os.path.join(root, "analysis", "speaker_turns.json"), "w"))
    with pytest.raises(SystemExit, match="diarization is unavailable"):
        clearance.blocked_ranges(root)
    # diarization ran but the id does not exist: name the known ids
    root2 = _root_with((tmp_path / "b"), voices={"speakers": [
        {"id": "typo", "cleared": False}]})
    os.makedirs(os.path.join(root2, "analysis"), exist_ok=True)
    json.dump({"status": "ok", "speakers": {"SPEAKER_00": {"ranges": [[0, 5]]}}},
              open(os.path.join(root2, "analysis", "speaker_turns.json"), "w"))
    with pytest.raises(SystemExit, match="SPEAKER_00"):
        clearance.blocked_ranges(root2)


def test_diarize_fails_actionably_when_dependency_or_token_missing(monkeypatch, tmp_path):
    from reelly import config, diarize

    def no_dep():
        raise ImportError("No module named 'pyannote'")

    monkeypatch.setattr(diarize, "_import_pipeline", no_dep)
    with pytest.raises(RuntimeError, match="diarize"):
        diarize._pipeline()
    # dependency present, token absent
    monkeypatch.setattr(diarize, "_import_pipeline", lambda: object)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(config, "HOME", str(tmp_path / "empty"))
    with pytest.raises(RuntimeError, match="HuggingFace token"):
        diarize._pipeline()


def test_unavailable_artifact_is_marked_unverified_and_retried(tmp_path, monkeypatch):
    from reelly import config, diarize
    p = str(tmp_path / "speaker_turns.json")
    art = diarize.unavailable_artifact("no token")
    json.dump(art, open(p, "w"))
    assert art["unverified"] is True and art["status"] == "unavailable"
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(config, "HOME", str(tmp_path / "empty"))
    # no token: skip retry (would fail identically)
    assert diarize.needs_rerun(p) is False
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_test_not_a_real_token")
    assert diarize.needs_rerun(p) is True


# --- gap 11: third-party content heuristic -----------------------------------

def test_ownership_cues_produce_suspected_guest_blocks():
    sents = [
        {"text": "welcome back to the stream", "s": 10.0, "e": 12.0},
        {"text": "did you make this one?", "s": 300.0, "e": 302.0},
        {"text": "I don't know how to video edit", "s": 320.0, "e": 323.0},
        {"text": "back to my own build now", "s": 900.0, "e": 903.0},
    ]
    blocks = clearance.guest_blocks(sents)
    assert len(blocks) == 1  # the two nearby cues merge into one window
    b = blocks[0]
    assert b["confidence"] == "suspected"      # flagged, never asserted
    assert b["s"] <= 300.0 and b["e"] >= 320.0
    assert len(b["evidence"]) == 2


def test_host_ownership_is_counter_evidence_not_silent_resolution():
    sents = [{"text": "did you make that?", "s": 100.0, "e": 102.0},
             {"text": "when I made this one it took a week", "s": 110.0, "e": 113.0}]
    b = clearance.guest_blocks(sents)[0]
    assert b["counter_evidence"] and "when i made this" in b["counter_evidence"][0]["cue"]


def test_guest_blocks_feed_blocked_ranges(tmp_path):
    root = _root_with(tmp_path, guests={"blocks": [
        {"s": 500.0, "e": 580.0, "confidence": "suspected",
         "evidence": [{"t": 540.0, "text": "did you make this", "cue": "did you make"}],
         "counter_evidence": []}]})
    blocked = clearance.blocked_ranges(root)
    assert len(blocked) == 1 and "third-party" in blocked[0][2]
    assert clearance.verdict({"segments": [[560, 575]]}, blocked)[1] == "FAIL"


# --- gap 9: caption collision gate -------------------------------------------

def test_hook_and_overlay_colliding_in_time_and_band_fail():
    plan = {"hook": {"text": "watch this door", "show_s": 4.0},
            "overlay_lines": [{"t": 2.0, "text": "it opens both ways", "show_s": 3.0}],
            "captions": "none", "segments": [[0, 20]], "duration_s": 20.0}
    name, status, detail = judge.caption_collisions(plan, [])
    assert status == "FAIL" and "hook" in detail and "overlay" in detail


def test_spaced_layers_pass():
    plan = {"hook": {"text": "watch this door", "show_s": 3.6},
            "overlay_lines": [{"t": 6.0, "text": "it opens both ways", "show_s": 3.0}],
            "captions": "none", "segments": [[0, 20]], "duration_s": 20.0}
    assert judge.caption_collisions(plan, [])[1] == "PASS"


def test_temporally_overlapping_cues_fail():
    """The shipped mush: every word had a cue (62/62 coverage PASSED) while
    two cues were on screen at once."""
    words = [{"t": "Dad", "s": 0.2, "e": 0.5}, {"t": "said.", "s": 0.6, "e": 2.4},
             {"t": "Would", "s": 1.0, "e": 1.3}, {"t": "you?", "s": 1.4, "e": 1.8}]
    plan = {"hook": {}, "captions": "burned", "segments": [[0.0, 10.0]],
            "duration_s": 10.0}
    name, status, detail = judge.caption_collisions(plan, words)
    assert status == "FAIL" and "overlap in time" in detail


# --- gap 13: endcard anchored to payoff-end ----------------------------------

def test_apply_payoff_always_records_local_t_and_local_e():
    # Single-scene rule (2026-08-15, reviewer): a DISTANT payoff is NOT
    # jump-cut in -- cross-gap stitching is the failure this blocks. It is
    # dropped entirely and the clip lands on its own footage; merged is left
    # single-segment.
    vr = [{"start_s": 30.0, "end_s": 40.0, "trailer_score": 8, "label": "reveal"}]
    cand = {"s": 0.0, "e": 20.0}
    merged = [[0.0, 20.0]]
    p = direct._apply_payoff(merged, cand, {"payoff_event": 0}, vr)
    assert p is None and merged == [[0.0, 20.0]]
    # contiguous case still records local_t/local_e (gap-13 intent preserved):
    # without it local_t was None on all 8 cuts of a session
    vr2 = [{"start_s": 20.2, "end_s": 28.0, "trailer_score": 8, "label": "r"}]
    merged2 = [[0.0, 20.0]]
    p2 = direct._apply_payoff(merged2, {"s": 0.0, "e": 20.0}, {"payoff_event": 0}, vr2)
    assert p2["jump"] is False and p2["local_t"] == 20.0
    assert p2["local_e"] == pytest.approx(28.0, abs=0.01)


def test_endcard_window_anchors_to_payoff_end_not_clip_end():
    # payoff runs into the old card window: card is pushed later. The old
    # MIN_ENDCARD_S pull-back is gone: the card starts payoff_end + breath,
    # NEVER earlier (it must not obscure the delivery), and holds to the end.
    plan = {"duration_s": 24.0, "segments": [[0, 24]],
            "payoff": {"jump": True, "local_t": 18.0, "local_e": 21.8}}
    t0, t1 = overlays.endcard_window(plan)
    assert t0 == pytest.approx(21.8 + overlays.ENDCARD_BREATH_S, abs=0.01)
    assert t0 >= 21.8 and t1 == 24.0
    # payoff well clear of the tail: unchanged classic window
    plan2 = {"duration_s": 24.0, "segments": [[0, 24]],
             "payoff": {"jump": True, "local_t": 8.0, "local_e": 12.0}}
    assert overlays.endcard_window(plan2)[0] == pytest.approx(24.0 - overlays.ENDCARD_S)
    # no payoff data at all: legacy behaviour, never a crash
    assert overlays.endcard_window({"duration_s": 20.0})[0] == pytest.approx(16.6)


def test_endcard_window_prefers_recorded_delivery_end():
    # delivery_end_s overrides the payoff dict; a late delivery pushes the card
    plan = {"duration_s": 26.0, "delivery_end_s": 22.5,
            "payoff": {"jump": True, "local_t": 5.0, "local_e": 9.0}}
    t0, _ = overlays.endcard_window(plan)
    assert t0 == pytest.approx(22.5 + overlays.ENDCARD_BREATH_S)
    # a delivery already clear of the tail leaves the classic window alone
    plan2 = {"duration_s": 26.0, "delivery_end_s": 21.0}
    assert overlays.endcard_window(plan2)[0] == pytest.approx(26.0 - overlays.ENDCARD_S)


def test_breathe_tail_extends_only_to_the_breath():
    """Designed endings: _breathe_tail reserves NO card room any more (the
    outro is appended after the content); it only grows the tail so the
    delivery lands plus a natural breath, into the trailing pause."""
    from reelly import outro
    merged = [[10.0, 32.0]]
    m, t, note = direct._breathe_tail(merged, 22.0, 22.0, room=10.0)
    assert t == pytest.approx(22.0 + outro.TAIL_BREATH_S)
    assert m[-1][1] == pytest.approx(32.0 + outro.TAIL_BREATH_S)
    assert "ENDING-breathe" in note


def test_breathe_tail_never_extends_past_the_trailing_pause():
    from reelly import outro
    # no pause at all: nothing grows, the note says why
    m, t, note = direct._breathe_tail([[0.0, 22.0]], 22.0, 22.0, room=0.0)
    assert t == 22.0 and m[-1][1] == 22.0
    assert "ENDING-breathe" in note
    # a short pause caps the breath: room is a hard bound now, not a
    # preference (no minimum card room is forced past it)
    m2, t2, _ = direct._breathe_tail([[0.0, 22.0]], 22.0, 22.0, room=0.3)
    assert t2 == pytest.approx(22.3)
    assert t2 < 22.0 + outro.TAIL_BREATH_S


def test_breathe_tail_respects_room_and_hard_length():
    # hard P-LEN cap wins over the full breathing ask AND the minimum
    m2, t2, _ = direct._breathe_tail([[0.0, 31.0]], 31.0, 31.0, room=10.0)
    assert t2 <= 32.0 + 1e-6
    # already at the hard cap: nothing can grow, the note says so
    m3, t3, note3 = direct._breathe_tail([[0.0, 32.0]], 32.0, 32.0, room=0.0)
    assert t3 == 32.0 and "hard length cap" in note3


def test_trailing_pause_room_reads_the_silence_map():
    sil = [(21.8, 27.0)]
    assert direct._trailing_pause_room(22.0, sil) == pytest.approx(5.0)
    assert direct._trailing_pause_room(10.0, sil) == 0.0


def test_clear_for_endcard_uses_the_payoff_aware_card_start():
    # card pushed to 22.2 by the payoff: the meme window may run later than
    # the old clip-end-anchored limit would have allowed
    win = overlays.clear_for_endcard((10.0, 21.0), 24.0, True, card_t0=22.2)
    assert win == (10.0, 21.0)
    # and is still trimmed to finish before the card actually arrives
    win2 = overlays.clear_for_endcard((10.0, 23.5), 24.0, True, card_t0=22.2)
    assert win2[1] == pytest.approx(21.9)


# --- gap 12: post-verdict delivery stage -------------------------------------

def _delivery_project(tmp_path, screened):
    root = tmp_path / "proj"
    (root / "edl").mkdir(parents=True)
    fin = root / "deliverables" / "final"
    fin.mkdir(parents=True)
    plans = []
    for i, s in enumerate(screened, 1):
        p = {"id": f"cut_{i:02d}", "title": f"t{i}", "duration_s": 22.0,
             "hook": {"text": "h"}, "cta": "play on example.invalid",
             "caption": "cap", "segments": [[0, 22]]}
        if s is not None:
            p["screened"] = s
        plans.append(p)
        (fin / f"cut_{i:02d}_gfx.mp4").write_bytes(b"v")
    (root / "edl" / "cut_plans.json").write_text(json.dumps(plans))
    return str(root)


def test_deliver_renumbers_keeps_and_writes_the_mapping(tmp_path, clean_home):
    root = _delivery_project(tmp_path, [
        {"by": "reviewer", "on": "2026-07-31", "verdict": "clean"},
        {"by": "reviewer", "on": "2026-07-31", "verdict": "guest footage on screen"},
        {"by": "reviewer", "on": "2026-07-31", "verdict": "clean"},
    ])
    mapping = deliver.run(root, account="managed")
    out = os.path.join(root, "deliverables", "delivery")
    # survivors renumbered contiguously; cut_03 became cut_02
    assert mapping["delivered"]["cut_01"]["source_cut"] == "cut_01"
    assert mapping["delivered"]["cut_02"]["source_cut"] == "cut_03"
    assert mapping["killed"][0]["cut"] == "cut_02"
    assert sorted(f for f in os.listdir(out) if f.endswith(".mp4")) == \
        ["cut_01_gfx.mp4", "cut_02_gfx.mp4"]
    assert os.path.exists(os.path.join(out, "cut_02_DESCRIPTION.md"))
    assert os.path.exists(os.path.join(out, "mapping.json"))
    assert "was cut_03" in open(os.path.join(out, "DELIVERY.md")).read()
    # killed cut is archived in final/, not destroyed
    assert os.path.exists(os.path.join(root, "deliverables", "final", "cut_02_gfx.mp4"))


def test_deliver_refuses_unscreened_cuts(tmp_path, clean_home):
    root = _delivery_project(tmp_path, [
        {"by": "f", "on": "d", "verdict": "clean"}, None])
    with pytest.raises(SystemExit, match="not screened"):
        deliver.run(root, account="managed")


def test_deliver_refuses_missing_variant_files(tmp_path, clean_home):
    root = _delivery_project(tmp_path, [{"by": "f", "on": "d", "verdict": "clean"}])
    os.remove(os.path.join(root, "deliverables", "final", "cut_01_gfx.mp4"))
    with pytest.raises(SystemExit, match="missing variant"):
        deliver.run(root, account="managed")


# --- caption block heights back the collision gate ---------------------------

def test_block_height_grows_with_wrapping():
    one = captions.block_height("short", width=980, size=74)
    many = captions.block_height("a much longer line that will certainly wrap "
                                 "across several rendered rows on screen",
                                 width=980, size=74)
    assert many > one


# --- pyannote 4.x compatibility -------------------------------------------
# The first real run against pyannote.audio 4.0.7 broke twice on API drift that
# the duck-typed fakes above sail straight past: `use_auth_token` was renamed to
# `token`, and the pipeline stopped returning a bare Annotation. These pin the
# unwrapping so a version bump surfaces here instead of 40 minutes into a run.

def test_annotation_unwraps_pyannote_4_diarize_output():
    """4.x wraps the Annotation in DiarizeOutput.speaker_diarization."""
    from reelly import diarize

    class _Ann:
        def itertracks(self, yield_label=False):
            return iter([])

    class _DiarizeOutput:            # shape of pyannote.audio 4.x
        speaker_diarization = _Ann()

    got = diarize._annotation(_DiarizeOutput())
    assert isinstance(got, _Ann), "must unwrap .speaker_diarization"


def test_annotation_accepts_bare_annotation_from_pyannote_3():
    """3.x returns the Annotation directly; both shapes must work."""
    from reelly import diarize

    class _Ann:
        def itertracks(self, yield_label=False):
            return iter([])

    ann = _Ann()
    assert diarize._annotation(ann) is ann


def test_annotation_names_a_compat_bug_not_a_setup_problem():
    """An unusable return is OUR bug. It must not blame the operator's setup."""
    from reelly import diarize

    class _Useless:
        pass

    try:
        diarize._annotation(_Useless())
    except RuntimeError as e:
        msg = str(e)
        assert "compatibility bug" in msg
        assert "not a setup problem" in msg
        assert "accept" not in msg.lower(), "must not send them to the terms page"
    else:
        raise AssertionError("expected a RuntimeError")
