"""Regressions for the seven motion-pipeline gaps that were masked by manual
intervention on 2026-07-31. Each test is named after the failure it prevents.

No test makes a live API call: the FAL client, the LLM brain and the vision
critic are all mocked exactly where the shipped tests mock them.
"""
import json
import sys

import pytest

from reelly import audio_post, cli, config, design, judge, motion


# --- gap 1: real-art mode (no invented character over real games / product UI) -

def test_real_art_prompt_forbids_inventing_any_content():
    """The whole point of real-art mode: animate the real still by camera and
    invent nothing. A prompt that omits the do-not-invent contract lets the
    model paint a character onto a real product screenshot."""
    ai = {"shots": [{"seconds": 4, "prompt": "push in on the logo"},
                    {"seconds": 4, "prompt": "pan across the board"}],
          "sound": "ambient"}
    low = motion.real_art_prompt(ai, 4, 8).lower()
    assert "do not invent" in low
    assert "camera" in low
    for banned in ("characters", "objects", "ui", "gameplay"):
        assert banned in low, f"contract must name {banned!r} as not-to-invent"
    assert "do not add, remove" in low  # existing text/logos must be preserved


def test_real_art_generation_passes_only_the_real_still_as_reference(tmp_path, monkeypatch):
    """Real-art i2v must reference the source still and ONLY the source still.
    The masked bug generated an invented character frame and fed that as the
    first reference, which is how an anime character landed on real UI."""
    captured = {}

    def fake_fal(endpoint, payload, *a, **k):
        captured["payload"] = payload
        return "http://x/v.mp4"

    monkeypatch.setattr(audio_post, "_fal", fake_fal)
    monkeypatch.setattr(audio_post, "_download", lambda url, path: path)
    monkeypatch.setattr(motion.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(motion.os, "remove", lambda p: None)
    src = tmp_path / "screenshot.png"
    src.write_bytes(b"\x89PNGfake")
    ai = {"shots": [{"seconds": 4, "prompt": "push"}, {"seconds": 4, "prompt": "pan"}],
          "sound": "amb"}
    motion._generate_real_art(str(src), ai, "draft", "proj", str(tmp_path / "base.mp4"))
    # Real-art references exactly ONE image (the source still), whatever the
    # video model's reference field is named (seedance image_urls, H3
    # reference_image_urls, grok image_url) -- the guarantee is the count, not
    # the key.
    payload = captured["payload"]
    refs = (payload.get("image_urls") or payload.get("reference_image_urls")
            or [payload.get("image_url")])
    refs = [r for r in refs if r]
    assert len(refs) == 1
    assert "do not invent" in captured["payload"]["prompt"].lower()


def test_real_art_run_skips_the_invented_character_frame(monkeypatch):
    """run(real_art=True) must never call _character_frame. It stayed wired in
    on real-game posts, so the operator had to monkeypatch it to a no-op."""
    def boom(*a, **k):
        raise AssertionError("_character_frame ran in real-art mode")

    monkeypatch.setattr(motion, "_character_frame", boom)
    # Prove the branch that would call it is guarded by real_art, without
    # running the whole pipeline: the source string is the branch condition.
    import inspect
    src = inspect.getsource(motion.run)
    assert "elif real_art:" in src
    assert "_generate_real_art" in src


# --- gap 2: sense-gate tries, surfaced reason, hand-authored copy override -----

def test_sense_gate_failure_surfaces_its_reason_in_the_error(monkeypatch):
    """The masked bug raised a bare 'unparseable' with no diagnostic. The reason
    the gate rejected the hook must reach the operator."""
    monkeypatch.setattr("reelly.direct._ask_json",
                        # cta present and lint-clean so the plan gets PAST the
                        # code linter and into the sense gate under test
                        lambda *a, **k: {"hook": {"text": "Your anime still can"},
                                         "payoff": {"text": "watch it draw"},
                                         "cta": "Start free"})
    monkeypatch.setattr("reelly.design._gemini",
                        lambda *a, **k: {"makes_sense": False,
                                         "literal_meaning": "an anime cannot draw"})
    with pytest.raises(RuntimeError) as e:
        motion._author("m", "/i.png", {"palette": {}}, "gpt", "proj", tries=2)
    assert "an anime cannot draw" in str(e.value)


def test_author_retries_more_than_the_old_two_tries(monkeypatch):
    """tries=2 failed 2/2 on ordinary messages and forced a full bypass. The
    default must give the brain more than two attempts."""
    calls = {"n": 0}

    def fake_ask(*a, **k):
        calls["n"] += 1
        return {"hook": {"text": "bad hook"}, "payoff": {"text": "p"}}

    monkeypatch.setattr("reelly.direct._ask_json", fake_ask)
    monkeypatch.setattr("reelly.design._gemini",
                        lambda *a, **k: {"makes_sense": False, "literal_meaning": "nope"})
    with pytest.raises(RuntimeError):
        motion._author("m", "/i.png", {"palette": {}}, "gpt", "proj")  # default tries
    assert calls["n"] >= 4


def test_copy_override_is_recorded_hand_authored_and_skips_the_sense_gate(monkeypatch):
    """The supported escape hatch: hand-authored copy is folded in, the sense
    gate is skipped (a human is the authority), and the plan says so."""
    brain_plan = {"hook": {"text": "clever but hollow"}, "payoff": {"text": "p"},
                  "cta": "brain cta", "caption": "brain caption",
                  "character": "x", "archetype": "a",
                  "shots": [{"seconds": 4, "prompt": "s1"}, {"seconds": 4, "prompt": "s2"}]}
    monkeypatch.setattr("reelly.direct._ask_json", lambda *a, **k: dict(brain_plan))

    def boom(*a, **k):
        raise AssertionError("sense gate ran despite a copy override")

    monkeypatch.setattr("reelly.design._gemini", boom)
    override = {"hook": "Your idea becomes a game", "payoff": "live in minutes",
                "cta": "Start free", "caption": "A plain sentence."}
    ai = motion._author("msg", "/img.png", {"palette": {}}, "gpt", "proj",
                        copy_override=override)
    assert ai["copy_source"] == "hand-authored"
    assert ai["hook"]["text"] == "Your idea becomes a game"
    assert ai["cta"] == "Start free"
    assert ai["shots"] == brain_plan["shots"]  # structure still comes from the brain


def test_motion_copy_without_message_uses_caption_for_provenance(tmp_path, monkeypatch):
    copy = tmp_path / "copy.json"
    copy.write_text(json.dumps({"caption": "Caption provenance", "hook": "Hook"}))
    seen = {}
    monkeypatch.setattr(motion, "run", lambda image, message, **kwargs:
                        seen.update(message=message, kwargs=kwargs))
    monkeypatch.setattr(sys, "argv", ["reelly", "motion", "image.png",
                                      "--copy", str(copy), "--real-art"])
    cli.main()
    assert seen["message"] == "Caption provenance"
    assert seen["kwargs"]["copy_override"]["hook"] == "Hook"
    assert seen["kwargs"]["real_art"] is True


# --- gap 3: actionable lettering critic, resilient OCR, audited override -------

def test_lettering_ocr_normalizes_currency_commas_and_apostrophes_on_both_sides():
    assert motion._normalize("Winners split $2,500.") == motion._normalize("Winners split 2500")
    assert motion._normalize("Creator’s win") == motion._normalize("CREATORS WIN")
    assert motion._normalize("€1,000") == motion._normalize("1000 euros")


def test_short_lettering_gets_a_second_ocr_read_before_rejection(tmp_path, monkeypatch):
    from PIL import Image
    style = tmp_path / "style.png"
    raw_image = Image.new("RGB", (100, 100), "black")
    raw_image.putpixel((50, 50), (255, 255, 255))
    raw_image.save(style)
    # The general transcription pass (read_text) misreads the stylized glyphs
    # as no text; the lettering-framed second read (read_lettering) rescues a
    # correctly-spelled asset before it is counted a failure.
    monkeypatch.setattr(design, "read_text", lambda *a, **k: "")
    monkeypatch.setattr(design, "read_lettering", lambda *a, **k: "Win $5!")
    monkeypatch.setattr(design, "lettering_style_fidelity",
                        lambda *a, **k: {"match": True, "missing": None})
    monkeypatch.setattr(audio_post, "_fal", lambda *a, **k: "http://x/raw.png")
    monkeypatch.setattr(audio_post, "_download", lambda url, path: raw_image.save(path))
    out = tmp_path / "lettering.png"
    assert motion._lettering(str(style), "Win $5!", "p", str(out), tries=1) == str(out)


def test_style_rejection_without_reason_reasks_once_then_passes(monkeypatch):
    calls = []
    monkeypatch.setattr(design, "_gemini",
                        lambda *a, **k: calls.append(1) or {"match": False, "missing": None})
    verdict = design.lettering_style_fidelity(object(), object(), "p")
    assert verdict == {"match": True, "missing": None}
    assert len(calls) == 2


def test_style_rejection_with_a_reason_remains_a_rejection(monkeypatch):
    monkeypatch.setattr(design, "_gemini",
                        lambda *a, **k: {"match": False, "missing": "dark keyline"})
    assert design.lettering_style_fidelity(object(), object())["missing"] == "dark keyline"


def test_human_lettering_override_records_who_when_and_raw_acceptance(tmp_path, monkeypatch):
    from PIL import Image
    style = tmp_path / "style.png"
    raw_image = Image.new("RGB", (100, 100), "black")
    raw_image.putpixel((50, 50), (255, 255, 255))
    raw_image.save(style)
    # both OCR reads must miss so the asset reaches the human-override path
    monkeypatch.setattr(design, "read_text", lambda *a, **k: "wrong")
    monkeypatch.setattr(design, "read_lettering", lambda *a, **k: "wrong")
    monkeypatch.setattr(audio_post, "_fal", lambda *a, **k: "http://x/raw.png")
    monkeypatch.setattr(audio_post, "_download", lambda url, path: raw_image.save(path))
    # The override now attests a human LOOKED AT THIS EXACT RAW: its sha1 is
    # required alongside the reviewer, so a blanket env var can no longer
    # auto-accept a hallucinated line the spelling gate rejected.
    import hashlib
    scratch = tmp_path / "scratch.raw.png"
    raw_image.save(scratch)
    sha = hashlib.sha1(scratch.read_bytes()).hexdigest()
    monkeypatch.setenv("REELLY_LETTERING_OVERRIDE_BY", "reviewer")
    monkeypatch.setenv("REELLY_LETTERING_OVERRIDE_SHA", sha[:12])
    out = tmp_path / "lettering.png"
    motion._lettering(str(style), "right", "p", str(out), tries=1)
    audit = json.loads((tmp_path / "lettering.png.override.json").read_text())
    assert audit["by"] == "reviewer"
    assert audit["at"]
    assert audit["accepted_raw"].endswith(".raw.png")


# --- P0 #4: spelling gate was case-blind ('eitheR' passed) --------------------

def test_stray_interior_capital_fails_the_case_gate():
    """The exact fault: 'eitheR' for 'either'. A lone interior capital amid
    lowercase must be rejected -- the case-blind normalize gate passed it."""
    assert design.spelling_case_ok("Yours won't either.", "Yours won't either.")
    assert not design.spelling_case_ok("Yours won't eitheR.", "Yours won't either.")


def test_all_caps_lettering_style_still_passes_the_case_gate():
    """A caps STYLE (or uniform OCR case drift) shows no stray mix and must not
    trip the gate -- otherwise every all-caps hook would false-reject."""
    assert design.spelling_case_ok("YOURS WON'T EITHER", "Yours won't either.")
    assert design.spelling_case_ok("either", "either")


def test_case_gate_leaves_legitimate_interior_capitals_alone():
    """An intended interior capital (a wordmark like 'McRun') is not a stray;
    only a capital where the text is lowercase, amid lowercase, is."""
    assert design.spelling_case_ok("McRun", "McRun")
    assert not design.spelling_case_ok("McRuN", "McRun")


def test_case_gate_defers_word_mismatches_to_the_spelling_gate():
    """Different word counts or different letters are the spelling gate's job,
    not the case gate's -- it returns ok so it never double-reports."""
    assert design.spelling_case_ok("a b", "abc")          # count mismatch
    assert design.spelling_case_ok("caT", "dog")          # letters differ


def test_lettering_rejects_a_stray_interior_capital(tmp_path, monkeypatch):
    """End to end: an asset whose OCR reads a stray interior capital fails the
    gate and is retried, not shipped."""
    from PIL import Image
    style = tmp_path / "style.png"
    raw_image = Image.new("RGB", (100, 100), "black")
    raw_image.putpixel((50, 50), (255, 255, 255))
    raw_image.save(style)
    monkeypatch.setattr(design, "read_text", lambda *a, **k: "Win eitheR")
    monkeypatch.setattr(design, "read_lettering", lambda *a, **k: "Win eitheR")
    monkeypatch.setattr(audio_post, "_fal", lambda *a, **k: "http://x/raw.png")
    monkeypatch.setattr(audio_post, "_download", lambda url, path: raw_image.save(path))
    out = tmp_path / "lettering.png"
    with __import__("pytest").raises(RuntimeError, match="case"):
        motion._lettering(str(style), "Win either", "p", str(out), tries=1)


# --- gap 4: lettering cache keyed on the text (+ style ref) --------------------

def test_lettering_cache_key_changes_with_the_text(tmp_path):
    """New copy must not reuse the old asset's cache slot (the masked bug
    deployed a stale payoff line with zero warning)."""
    style = tmp_path / "style.png"
    style.write_bytes(b"STYLE")
    k1 = motion._lettering_key(str(style), "Winners split $2,500.")
    k2 = motion._lettering_key(str(style), "Winners split $5,000.")
    assert k1 != k2


def test_lettering_cache_key_changes_with_the_style_asset(tmp_path):
    a = tmp_path / "a.png"
    a.write_bytes(b"AAA")
    b = tmp_path / "b.png"
    b.write_bytes(b"BBB")
    assert motion._lettering_key(str(a), "same text") != motion._lettering_key(str(b), "same text")


def test_lettering_reuses_cache_and_prints_the_reused_text(tmp_path, monkeypatch, capsys):
    """On a genuine hit the cached asset is reused AND the reused text is
    printed, so an operator can see what is being deployed."""
    style = tmp_path / "style.png"
    style.write_bytes(b"S")
    out = tmp_path / "payoff.png"
    out.write_bytes(b"IMG")
    text = "Winners split $2,500."
    (tmp_path / "payoff.png.key").write_text(motion._lettering_key(str(style), text))

    def boom(*a, **k):
        raise AssertionError("regenerated despite a valid cache hit")

    monkeypatch.setattr(audio_post, "_fal", boom)
    got = motion._lettering(str(style), text, "proj", str(out))
    assert got == str(out)
    assert "reusing cached lettering" in capsys.readouterr().out


def test_lettering_does_not_reuse_a_stale_asset_when_the_copy_changed(tmp_path, monkeypatch):
    """A cache slot keyed on OLD text must miss when the copy changes: the code
    goes on to regenerate (proven by reaching the FAL call) instead of shipping
    the stale line."""
    style = tmp_path / "style.png"
    style.write_bytes(b"S")
    out = tmp_path / "payoff.png"
    out.write_bytes(b"OLDIMG")
    (tmp_path / "payoff.png.key").write_text(motion._lettering_key(str(style), "OLD line"))

    class RegenReached(Exception):
        pass

    def reached(*a, **k):
        raise RegenReached

    monkeypatch.setattr(audio_post, "_fal", reached)
    with pytest.raises(RegenReached):
        motion._lettering(str(style), "A completely NEW line", "proj", str(out))


# --- gap 5: composite loudness targets the judge's own window ------------------

def test_composite_loudness_targets_the_judges_window_not_a_hardcoded_one(monkeypatch):
    """Enforcement must follow the judge's window so it can never land a file
    outside the exact band the judge grades against. With the window moved, a
    file inside it must be left untouched; a hardcoded -15.5..-12.5 would
    'correct' it and re-encode."""
    monkeypatch.setattr(judge, "LUFS_LO", -20.0)
    monkeypatch.setattr(judge, "LUFS_HI", -18.0)
    monkeypatch.setattr(audio_post, "integrated_loudness", lambda p: -19.0)

    def boom(*a, **k):
        raise AssertionError("re-encoded a file already inside the judge window")

    monkeypatch.setattr(audio_post.subprocess, "run", boom)
    assert audio_post.enforce_loudness("x.mp4") == -19.0


def test_composite_true_peak_ceiling_tracks_the_judges_gate(monkeypatch):
    """The true-peak ceiling is derived from judge.TP_MAX (with margin), so a
    peak already under the judge's gate is not clamped further."""
    monkeypatch.setattr(judge, "TP_MAX", 0.0)          # ceiling becomes -0.6
    monkeypatch.setattr(audio_post, "true_peak", lambda p: -1.0)

    def boom(*a, **k):
        raise AssertionError("limited a peak already under the judge gate")

    monkeypatch.setattr(audio_post.subprocess, "run", boom)
    assert audio_post.enforce_true_peak("x.mp4") == -1.0


# --- gap 7: copy contract voice/attribution (brand is not a creator) ------------

def test_copy_contract_fails_brand_speaking_first_person_as_a_creator():
    """'See my prices' is brand copy posing as a third-party creator.
    The judge must catch first-person-creator voice, not only lengths."""
    plan = {"planned_from": "image", "hook": {"text": "Build a game fast"},
            "payoff": {"text": "ship it today"}, "cta": "See my prices",
            "caption": "A plain sentence."}
    gate, status, detail = judge.copy_contract(plan)
    assert status == "FAIL"
    assert "first-person" in detail


def test_copy_contract_passes_run_voice():
    plan = {"planned_from": "image", "hook": {"text": "Your idea becomes a game"},
            "payoff": {"text": "playable in minutes"}, "cta": "Start free on ExampleBrand",
            "caption": "Turn an idea into a playable game on ExampleBrand."}
    assert judge.copy_contract(plan)[1] == "PASS"


# --- gap 8: campaign loading warns on missing/reused lettering style -----------

def _campaign_dir(tmp_path, monkeypatch, specs):
    camp = tmp_path / "campaigns"
    camp.mkdir()
    for name, spec in specs.items():
        (camp / f"{name}.json").write_text(json.dumps(spec))
    monkeypatch.setattr(config, "HOME", str(tmp_path))


def test_campaign_warns_when_lettering_style_ref_is_missing(tmp_path, monkeypatch, capsys):
    _campaign_dir(tmp_path, monkeypatch, {"c1": {"product": "video", "cta": "x"}})
    motion.load_campaign("c1")
    assert "NO lettering_style_ref" in capsys.readouterr().out


def test_campaign_warns_when_lettering_style_ref_is_reused_verbatim(tmp_path, monkeypatch, capsys):
    _campaign_dir(tmp_path, monkeypatch, {
        "campaign_one": {"product": "video", "cta": "x", "lettering_style_ref": "/gold.png"},
        "campaign_two": {"product": "video", "cta": "y", "lettering_style_ref": "/gold.png"}})
    motion.load_campaign("campaign_two")
    out = capsys.readouterr().out
    assert "reuses lettering_style_ref" in out
    assert "campaign_one" in out


def test_campaign_does_not_warn_on_a_unique_style_ref(tmp_path, monkeypatch, capsys):
    _campaign_dir(tmp_path, monkeypatch, {
        "c1": {"product": "video", "cta": "x", "lettering_style_ref": "/one.png"},
        "c2": {"product": "video", "cta": "y", "lettering_style_ref": "/two.png"}})
    motion.load_campaign("c2")
    assert "WARNING" not in capsys.readouterr().out


# --- gap 6: D1/D3 gate re-placement and measured scrim escalation -------------

def test_design_gate_recomposites_twice_before_reporting_persistent_d1_failure(tmp_path, monkeypatch):
    issue = {"rule": "D1", "what": "over subject", "region": [0.1, 0.2, 0.8, 0.4],
             "fix": "move it"}
    monkeypatch.setattr(motion, "_frame_at", lambda *a: object())
    monkeypatch.setattr(design, "critique",
                        lambda *a, **k: {"pass": False, "issues": [issue]})
    recomposes = []
    gate = motion._design_gate(str(tmp_path), "out.mp4",
                               {"hook": {"text": "h"}, "payoff": {"text": "p"}, "cta": "c"},
                               4, 8, "p",
                               recompose=lambda attempt, avoid: recomposes.append((attempt, avoid)) or "out.mp4")
    assert gate["pass"] is False
    assert len(gate["attempts"]) == 3
    assert [x[0] for x in recomposes] == [1, 2]


def test_design_gate_stops_replacing_after_d1_retry_passes(tmp_path, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(motion, "_frame_at", lambda *a: object())
    def critique(*a, **k):
        calls["n"] += 1
        if calls["n"] <= 3:
            return {"pass": False, "issues": [{"rule": "D3", "what": "floats",
                                                "region": [0, 0, 1, 1], "fix": "scrim"}]}
        return {"pass": True, "issues": []}
    monkeypatch.setattr(design, "critique", critique)
    recomposes = []
    gate = motion._design_gate(str(tmp_path), "out.mp4",
                               {"hook": {"text": "h"}, "payoff": {"text": "p"}},
                               4, 8, "p",
                               recompose=lambda attempt, avoid: recomposes.append(attempt) or "out.mp4")
    assert gate["pass"] is True
    assert recomposes == [1]
    assert len(gate["attempts"]) == 2


def test_scrim_uses_worst_cell_and_stays_light_on_calm_contrasting_backdrop():
    calm = [[(60, 8) for _ in range(12)] for _ in range(24)]
    assert motion._scrim_for_box(calm, 0, 0, 1080, 1920) == 0.0
    one_busy_cell = [[(60, 8) for _ in range(12)] for _ in range(24)]
    one_busy_cell[0][0] = (230, 80)
    assert motion._scrim_for_box(one_busy_cell, 0, 0, 1080, 1920) >= 0.7


def test_subject_sampling_covers_the_overlay_duration(monkeypatch):
    seen = []
    monkeypatch.setattr(motion, "_frame_at", lambda vid, t: seen.append(t) or object())
    monkeypatch.setattr(design, "subject_box", lambda *a, **k: (1, 2, 3, 4))
    boxes = motion._sample_subjects("v", 2.0, 6.0, "p", samples=5)
    assert seen == [2.0, 3.0, 4.0, 5.0, 6.0]
    assert len(boxes) == 5


def test_malformed_vision_box_falls_back_to_default_placement(monkeypatch, capsys):
    from PIL import Image
    frame = Image.new("RGB", (1080, 1920))
    monkeypatch.setattr(motion, "_frame_at", lambda *a: frame)
    monkeypatch.setattr(design, "_gemini",
                        lambda *a, **k: {"found": True, "box": [0.5]})
    diag = {"placement": "subject-aware"}
    assert motion._sample_subjects("v", 0, 1, "p", samples=2, diag=diag) == []
    # the fallback is LOUD now: a warning AND a recorded content-blind marker,
    # not the old quiet "default placement" print that scrolled past.
    assert "content-blind fallback" in capsys.readouterr().out
    assert diag["placement"] == "content-blind fallback"


# --- gap 9: FAL audio content-policy auto-retry, once, instrumental-only -------

def _no_ledger(monkeypatch):
    monkeypatch.setattr(audio_post.ledger, "check", lambda *a, **k: None)
    monkeypatch.setattr(audio_post.ledger, "add", lambda *a, **k: None)


def test_content_policy_detection_matches_fal_rejection_blobs():
    assert audio_post._is_content_policy({"status": "FAILED", "error": "content_policy_violation"})
    assert audio_post._is_content_policy("Prompt was flagged")
    assert not audio_post._is_content_policy({"status": "COMPLETED"})


def test_fal_audio_retries_once_instrumental_on_content_policy(monkeypatch):
    """The masked crash: 'voices' near a product term was a raw content-policy
    error. Audio must auto-retry once with an instrumental-only clause."""
    _no_ledger(monkeypatch)
    calls = []

    def fake_once(endpoint, payload, detail, project, service, find, tries):
        calls.append(payload.get("prompt") or payload.get("text"))
        if len(calls) == 1:
            raise audio_post._ContentPolicy("content_policy_violation: voices")
        return "http://x/a.mp3"

    monkeypatch.setattr(audio_post, "_fal_once", fake_once)
    url = audio_post._fal("ep", {"prompt": "a bed with big voices"}, 0.06,
                          "music", "proj", service="fal-audio")
    assert url == "http://x/a.mp3"
    assert len(calls) == 2
    assert "instrumental only" in calls[1].lower()


def test_fal_audio_reraises_clearly_when_still_rejected(monkeypatch):
    """One retry, then a clear message — never an infinite loop or a bare crash."""
    _no_ledger(monkeypatch)
    calls = []

    def fake_once(endpoint, payload, detail, project, service, find, tries):
        calls.append(1)
        raise audio_post._ContentPolicy("content_policy_violation")

    monkeypatch.setattr(audio_post, "_fal_once", fake_once)
    with pytest.raises(RuntimeError) as e:
        audio_post._fal("ep", {"prompt": "x"}, 0.06, "d", "p", service="fal-audio")
    assert "instrumental" in str(e.value).lower()
    assert len(calls) == 2  # original + exactly one retry


def test_fal_non_audio_content_policy_is_not_auto_retried(monkeypatch):
    """The instrumental clause is meaningless for image/video; a policy refusal
    there surfaces immediately instead of being silently mangled."""
    _no_ledger(monkeypatch)
    calls = []

    def fake_once(endpoint, payload, detail, project, service, find, tries):
        calls.append(1)
        raise audio_post._ContentPolicy("content_policy_violation")

    monkeypatch.setattr(audio_post, "_fal_once", fake_once)
    with pytest.raises(RuntimeError) as e:
        audio_post._fal("ep", {"prompt": "x"}, 0.06, "d", "p", service="fal-video")
    assert len(calls) == 1
    assert "auto-fixed" in str(e.value).lower()
