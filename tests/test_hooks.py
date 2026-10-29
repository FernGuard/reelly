"""Item 6: brand copy brief, fixed CTA, and the hook chosen together with the
opening frame from five distinct paths."""
import json
import os

import pytest

from reelly import brandkit, direct, hooks


@pytest.fixture
def brand(tmp_path, monkeypatch):
    root = tmp_path / "kit"
    d = root / "brands" / "game-a"
    d.mkdir(parents=True)
    (d / "brand.json").write_text(json.dumps({"name": "game-a", "copy": {
        "audience": "puzzle fans", "promise": "a puzzle game about doors",
        "voice": "dry and warm", "cta": "Wishlist on Steam", "cta_hot": "Wishlist",
        "avoid": ["comment", "cozy"]}}))
    monkeypatch.setenv("REELLY_BRANDKIT", str(root))
    monkeypatch.setenv("REELLY_BRAND", "game-a")
    return d


def test_prompts_no_longer_hardcode_engagement_bait():
    for prompt in (direct.VISUAL_REFINE_PROMPT, direct.REFINE_PROMPT):
        assert "a comment or a save on Instagram" not in prompt
        assert "creative AI tool" not in prompt


def test_brief_block_carries_the_brand(brand):
    b = direct.brief_block()
    assert "BRAND BRIEF" in b and "puzzle fans" in b and "dry and warm" in b
    assert '"Wishlist on Steam"' in b and '"comment"' in b


def test_no_brand_no_brief(monkeypatch):
    monkeypatch.delenv("REELLY_BRAND", raising=False)
    assert direct.brief_block() == ""


def test_brand_cta_overrides_the_model(brand):
    plan = direct.apply_brand_cta({"cta": "Comment your first spell", "because": []})
    assert plan["cta"] == "Wishlist on Steam"
    assert plan["cta_hot"] == "Wishlist"
    assert "hand-authored" in plan["cta_source"]
    assert any("model wrote" in b for b in plan["because"])


FRAMES = [{"t": 4.0, "opening_score": 9, "what_happens": "a knock"},
          {"t": 6.0, "opening_score": 8, "what_happens": "the door opens"},
          {"t": 8.0, "opening_score": 5, "what_happens": "a visitor"}]


def test_clean_drops_lookalikes_long_hooks_and_dashes():
    v = [{"path": "world", "frame": "A", "hook": "Someone knocks — again", "stop_score": 8, "typicality": 0.05},
         {"path": "emotion", "frame": "B", "hook": "Someone knocks twice", "stop_score": 9, "typicality": 0.5},
         {"path": "format", "frame": "C", "hook": "one two three four five six seven eight", "stop_score": 9},
         {"path": "audience", "frame": "Z", "hook": "Never open this door", "stop_score": 6, "typicality": 0.3}]
    out = hooks.clean(v, FRAMES)
    texts = [x["hook"] for x in out]
    assert "Someone knocks, again" in texts            # em dash replaced
    assert "Someone knocks twice" not in texts         # same opener as a kept one
    assert all(len(t.split()) <= hooks.MAX_HOOK_WORDS for t in texts)
    never = next(x for x in out if x["hook"].startswith("Never"))
    assert never["frame"] == "A"                       # unknown letter -> first frame


def test_scoring_rewards_frame_and_novelty_and_punishes_repeats():
    v = [{"path": "world", "frame": "A", "hook": "Open the door", "stop_score": 7, "typicality": 0.05},
         {"path": "emotion", "frame": "C", "hook": "Dread knocks politely", "stop_score": 7, "typicality": 0.5}]
    out = hooks.clean(v, FRAMES)
    assert out[0]["hook"] == "Open the door"           # better frame + unlikely bonus
    out2 = hooks.clean(v, FRAMES, used_openers={"open"})
    assert out2[0]["hook"] == "Dread knocks politely"  # repeated opener loses 3
    assert out2[1]["repeats_batch_opener"] is True


def _plan():
    return {"id": "cut_01", "title": "The Knock", "segments": [[3.0, 29.0]],
            "source_range": [3.0, 29.0], "duration_s": 28.8, "content_s": 26.0,
            "payoff_complete_by": 25.0, "hook": {"text": "old hook", "show_s": 3.6},
            "overlay_lines": [{"t": 5.0, "text": "a"}, {"t": 12.0, "text": "b"}],
            "because": []}


def test_shift_start_keeps_relative_times_consistent():
    p = _plan()
    d = hooks._shift_start(p, 4.0)
    assert d == 1.0 and p["segments"][0][0] == 4.0
    assert p["duration_s"] == 27.8 and p["content_s"] == 25.0
    assert p["payoff_complete_by"] == 24.0
    assert [o["t"] for o in p["overlay_lines"]] == [4.0, 11.0]


def test_shift_start_refuses_big_or_backward_moves():
    p = _plan()
    assert hooks._shift_start(p, 2.0) == 0.0
    assert hooks._shift_start(p, 3.0 + hooks.MAX_SHIFT_S + 1) == 0.0
    assert p["segments"][0][0] == 3.0


def test_compose_applies_the_best_pair(tmp_path, monkeypatch, brand):
    an = tmp_path / "analysis"
    an.mkdir()
    (an / "frames.json").write_text(json.dumps({"frames": FRAMES}))
    seen = {}

    def ask(prompt, images):
        if "judging hook" in prompt:
            return None                    # no judge: the writer's ranking stands
        seen["prompt"] = prompt
        return {"variants": [
            # A is the cut's current first frame (3.0s), then the sight frames
            {"path": "world", "frame": "B", "hook": "Someone is knocking", "hot": "knocking",
             "hot_reason": "the event", "stop_score": 8, "typicality": 0.4},
            {"path": "inverted", "frame": "C", "hook": "Never answer the door", "hot": "Never",
             "hot_reason": "the warning", "stop_score": 9, "typicality": 0.05}]}

    p = _plan()
    out = hooks.compose(p, str(an), "v.mp4", "gemini", "proj", ask=ask)
    assert len(out) == 2
    assert p["hook"]["text"] == "Never answer the door"
    assert p["hook"]["hot"] == "Never" and p["hook"]["hot_reason"] == "the warning"
    assert p["opening"]["t"] == 6.0 and p["segments"][0][0] == 6.0
    assert p["hook_variants"] == out
    assert "BRAND BRIEF" in seen["prompt"]               # the brief reaches the hook writer
    for key, _ in hooks.PATHS:
        assert key in seen["prompt"]


def test_compose_keeps_the_plan_when_the_brain_fails(tmp_path):
    p = _plan()
    assert hooks.compose(p, str(tmp_path), "v.mp4", "gemini", "proj",
                         ask=lambda prompt, images: None) == []
    assert p["hook"]["text"] == "old hook" and p["segments"][0][0] == 3.0


def test_frames_without_sight_are_spaced_and_include_the_current_start(tmp_path):
    fr = hooks._frames_near(str(tmp_path), 10.0, 40.0)
    assert fr[0]["t"] == 10.0 and len(fr) == hooks.N_FRAMES


def test_lines_never_start_under_a_longer_hook():
    p = {"hook": {"text": "x", "show_s": 4.75},
         "overlay_lines": [{"t": 3.8, "text": "early"}, {"t": 10.3, "text": "later"}]}
    hooks._clear_hook_window(p)
    assert [o["t"] for o in p["overlay_lines"]] == [5.05, 10.3]


def test_an_early_line_with_no_room_is_dropped():
    p = {"hook": {"text": "x", "show_s": 4.75}, "because": [],
         "overlay_lines": [{"t": 3.8, "text": "early"}, {"t": 6.0, "text": "next"}]}
    hooks._clear_hook_window(p)
    assert [o["text"] for o in p["overlay_lines"]] == ["next"]


def test_the_judge_reorders_awkward_winners():
    v = hooks.clean([
        {"path": "inverted", "frame": "A", "hook": "The cursed are invited inside here",
         "stop_score": 9, "typicality": 0.07},
        {"path": "world", "frame": "A", "hook": "Here, every curse makes a house call",
         "stop_score": 9, "typicality": 0.14}], FRAMES)
    assert v[0]["hook"].startswith("The cursed")          # novelty bonus won
    judged = {"scores": [{"i": 0, "natural": 4, "stop": 6, "on_brief": 7},
                         {"i": 1, "natural": 9, "stop": 9, "on_brief": 9, "note": "sharp"}]}
    out = hooks.rescore(v, judged)
    assert out[0]["hook"].startswith("Here, every curse")
    assert out[0]["judge_score"] == 9.0 and out[0]["judge_note"] == "sharp"


def test_a_broken_judge_keeps_the_writer_ranking():
    v = hooks.clean([{"path": "world", "frame": "A", "hook": "Open the door", "stop_score": 7}], FRAMES)
    assert hooks.rescore(list(v), None) == v
    assert hooks.rescore(list(v), {"scores": [{"i": "x"}]}) == v


def test_hooks_that_need_three_lines_are_dropped(monkeypatch):
    monkeypatch.setattr(hooks, "hook_lines", lambda t: 3 if "smoke" in t else 1)
    out = hooks.clean([{"path": "a", "frame": "A", "hook": "The curse brought smoke"},
                       {"path": "b", "frame": "A", "hook": "Open the door"}], FRAMES)
    assert [v["hook"] for v in out] == ["Open the door"]


def test_hook_lines_measures_the_real_render():
    assert hooks.hook_lines("Open it.") == 1
    assert hooks.hook_lines("You brought combos and the curse brought smoke and more") >= 2


def test_brief_carries_approved_and_rejected_lines(tmp_path, monkeypatch):
    d = tmp_path / "kit" / "brands" / "g"
    d.mkdir(parents=True)
    (d / "brand.json").write_text(json.dumps({"copy": {
        "approved_lines": ["Flattering, really."], "rejected_lines": ["Green crosses the grid"]}}))
    monkeypatch.setenv("REELLY_BRANDKIT", str(tmp_path / "kit"))
    monkeypatch.setenv("REELLY_BRAND", "g")
    b = direct.brief_block()
    assert "LINES THE OWNER APPROVED" in b and '"Flattering, really."' in b
    assert "LINES THE OWNER REJECTED" in b and '"Green crosses the grid"' in b


def test_record_verdict_dedupes_and_keeps_the_newest(tmp_path, monkeypatch):
    d = tmp_path / "kit" / "brands" / "g"
    d.mkdir(parents=True)
    (d / "brand.json").write_text(json.dumps({"copy": {"approved_lines": ["a"]}}))
    monkeypatch.setenv("REELLY_BRANDKIT", str(tmp_path / "kit"))
    c = brandkit.record_verdict("g", approve=["b", "a"], reject=["x"])
    assert c["approved_lines"] == ["b", "a"] and c["rejected_lines"] == ["x"]
    c = brandkit.record_verdict("g", approve=[f"l{i}" for i in range(50)])
    assert len(c["approved_lines"]) == 40 and c["approved_lines"][-1] == "l49"
    with pytest.raises(ValueError):
        brandkit.record_verdict("missing", approve=["a"])


def test_unshift_undoes_shift_start_exactly():
    p = _plan()
    before = json.loads(json.dumps(p))
    d = hooks._shift_start(p, 4.0)
    p["opening"] = {"shifted_s": d}
    hooks._unshift(p)
    for k in ("segments", "duration_s", "content_s", "payoff_complete_by", "source_range"):
        assert p[k] == before[k], k
    assert [o["t"] for o in p["overlay_lines"]] == [o["t"] for o in before["overlay_lines"]]


def test_cli_brand_verdict(tmp_path, monkeypatch, capsys):
    from reelly import cli
    d = tmp_path / "kit" / "brands" / "g"
    d.mkdir(parents=True)
    (d / "brand.json").write_text("{}")
    monkeypatch.setenv("REELLY_BRANDKIT", str(tmp_path / "kit"))
    cli.main(["brand", "verdict", "g", "--approve", "Mop optional.", "--reject", "meh"])
    assert "1 approved, 1 rejected" in capsys.readouterr().out
