"""Synthetic sizzle-reel decision tests with no network or renders."""
import json

import pytest

from reelly import sizzle


# ----------------------------------------------------------------- crop_map

def test_crop_map_bare_value_applies_everywhere():
    pick = sizzle.crop_map(["512:892:704:90"])
    assert pick("/any/clip.mp4") == "512:892:704:90"


def test_crop_map_pattern_only_matches_its_paths():
    """The Video pool mixes screen recordings that need the player cropped out
    with finished episodes that the same crop would destroy."""
    pick = sizzle.crop_map(["clip_=512:892:704:90"])
    assert pick("/x/catalogue/clip_01_part2.mp4") == "512:892:704:90"
    assert pick("/x/catalogue/t23_platform.mp4") is None


def test_crop_map_empty_is_always_none():
    assert sizzle.crop_map(None)("/x/a.mp4") is None
    assert sizzle.crop_map([])("/x/a.mp4") is None


# ------------------------------------------------------------------ windows


def test_windows_split_long_recordings(monkeypatch):
    """Six frames across a 32-minute recording describe nothing, so anything
    long enters the pool as consecutive windows."""
    monkeypatch.setattr(sizzle.media, "duration", lambda p: 1932.0)
    w = sizzle.windows("/x/long.mp4", span=120.0)
    assert len(w) == 16
    assert w[0][1] == 0.0
    assert w[-1][2] == pytest.approx(1932.0)
    # windows tile the clip with no gaps
    for a, b in zip(w, w[1:]):
        assert a[2] == pytest.approx(b[1])


def test_windows_leave_short_clips_whole(monkeypatch):
    monkeypatch.setattr(sizzle.media, "duration", lambda p: 90.0)
    assert len(sizzle.windows("/x/short.mp4", span=120.0)) == 1


# --------------------------------------------------------------- title_hint

@pytest.mark.parametrize("path,want", [
    ("/x/sample_scene.mp4", "scene"),
    ("/x/SampleProject.mp4", "Sample Project"),
    ("/x/HarborLight.mp4", "Harbor Light"),
])
def test_title_hint_reads_a_name_off_the_filename(path, want):
    assert sizzle.title_hint(path) == want


# ------------------------------------------------------- descriptive labels

def test_descriptions_are_rejected_as_labels():
    """Burning "noir detective scene" on screen makes eight worlds read as one
    stock library."""
    assert sizzle._is_description("noir detective scene")
    assert sizzle._is_description("anime visual novel game")


def test_real_titles_are_not_descriptions():
    for name in ("Harbor Light", "The Gatehouse", "What the Water Carries"):
        assert not sizzle._is_description(name)


# ----------------------------------------------------------------- _json_from

class _Part:
    def __init__(self, text, thought=False):
        self.text, self.thought = text, thought


class _Resp:
    """Mimics a thinking model: reasoning parts alongside the answer."""
    def __init__(self, parts):
        cand = type("C", (), {"content": type("X", (), {"parts": parts})()})()
        self.candidates = [cand]

    @property
    def text(self):
        raise ValueError("convenience accessor fails on multi-part responses")


def test_json_from_skips_thought_parts():
    r = _Resp([_Part("thinking...", thought=True), _Part('{"a": 1}')])
    assert sizzle._json_from(r) == {"a": 1}


def test_json_from_strips_code_fences():
    assert sizzle._json_from(_Resp([_Part('```json\n{"a": 2}\n```')])) == {"a": 2}


def test_json_from_finds_json_inside_prose():
    r = _Resp([_Part('Here you go: {"a": 3} hope that helps')])
    assert sizzle._json_from(r) == {"a": 3}


def test_json_from_returns_none_when_there_is_no_object():
    assert sizzle._json_from(_Resp([_Part("no json here")])) is None


# ------------------------------------------------------------ music envelope

def _plan(shots):
    return {"shots": shots}


def test_music_envelope_is_not_flat():
    """loudnorm alone flattened the bed to 1.2 dB across 30s; the envelope is
    what gives the cut an arc."""
    plan = _plan([{"role": "platform", "dur": 1.5}, {"role": "title", "dur": 4.6},
                  {"role": "body", "dur": 2.0}, {"role": "body", "dur": 2.0},
                  {"role": "payoff", "dur": 2.0}])
    env = sizzle._music_envelope(plan, 30.0)
    assert env.startswith("volume='")
    assert env.endswith(":eval=frame")
    assert "between(t," in env


def test_music_envelope_survives_a_plan_with_no_title():
    env = sizzle._music_envelope(_plan([{"role": "body", "dur": 5.0}]), 9.0)
    assert "volume='" in env


# ----------------------------------------------------------------- _validate

def _pool(n=10, kind="output", beauty=8):
    return [{"id": f"f{i}#0", "file": f"/x/Name{i}.mp4", "start": 0.0,
             "end": 60.0, "kind": kind, "beauty": beauty, "subject": "S",
             "summary": "sum"} for i in range(n)]


def _legal_plan(pool):
    shots = [{"role": "platform", "id": pool[0]["id"], "at": 1, "dur": 1.0},
             {"role": "platform", "id": pool[1]["id"], "at": 1, "dur": 1.0},
             {"role": "title", "dur": 2.5}]
    durs = [3.0, 2.5, 3.4, 2.0, 3.1]
    shots += [{"role": "body", "id": pool[2 + i]["id"], "at": 2, "dur": d,
               "label": f"Name{2 + i}"} for i, d in enumerate(durs)]
    shots.append({"role": "payoff", "id": pool[7]["id"], "at": 2, "dur": 2.5,
                  "label": "Name7"})
    return {"shots": shots, "hook": "Worlds you can walk into",
            "payoff": "Every one of them playable", "cta": "play free",
            "studio": "Adventure Project"}


def test_a_legal_plan_passes():
    pool = _pool()
    p = _legal_plan(pool)
    total = sum(s["dur"] for s in p["shots"])
    assert sizzle._validate(p, pool, total) == []


def test_flat_rhythm_is_rejected():
    """Every body shot the same length is the clearest tell of an automated
    edit."""
    pool = _pool()
    p = _legal_plan(pool)
    for s in p["shots"]:
        if s["role"] == "body":
            s["dur"] = 3.0
    errs = sizzle._validate(p, pool, sum(s["dur"] for s in p["shots"]))
    assert any("distinct lengths" in e for e in errs)


def test_platform_beat_must_come_from_the_catalogue():
    """Beat 1 is evidence a platform exists; the studio's own worlds are
    beat 3."""
    pool = _pool()
    pool[0]["file"] = "/x/catalogue/t23_gallery.mp4"
    pool[1]["file"] = "/x/catalogue/t24_gallery.mp4"
    p = _legal_plan(pool)
    p["shots"][0]["id"] = pool[5]["id"]          # a non-catalogue entry
    errs = sizzle._validate(p, pool, sum(s["dur"] for s in p["shots"]))
    assert any("catalogue" in e for e in errs)


def test_payoff_may_not_be_an_interface_shot():
    pool = _pool()
    pool[7]["kind"] = "interface"
    p = _legal_plan(pool)
    errs = sizzle._validate(p, pool, sum(s["dur"] for s in p["shots"]))
    assert any("open and close on the work" in e for e in errs)


def test_shot_running_past_its_source_is_rejected():
    pool = _pool()
    p = _legal_plan(pool)
    p["shots"][3]["at"] = 59.0                   # 59 + 3.0 > end 60.0
    errs = sizzle._validate(p, pool, sum(s["dur"] for s in p["shots"]))
    assert any("outside the usable" in e for e in errs)


def test_configured_banned_branding_in_copy_is_rejected(monkeypatch):
    pool = _pool()
    p = _legal_plan(pool)
    p["hook"] = "Made with OldBrand"
    monkeypatch.setattr(sizzle.brandkit, "copy_bank", lambda: {
        "limits": {"hook": 7, "payoff": 8, "cta": 4},
        "one_cta": True, "banned": ["OldBrand"], "ctas": {}})
    errs = sizzle._validate(p, pool, sum(s["dur"] for s in p["shots"]))
    assert any("OldBrand" in e for e in errs)


# ------------------------------------------------------------------ cleared

def test_retired_branding_is_never_scopable():
    """--allow-weapons scopes a REACH rule off. Branding is untrue on every
    surface, so it stays a hard drop."""
    surveyed = [{"file": "/x/a.mp4", "retired_branding": True,
                 "retired_branding_note": "OldBrand tab"}]
    for allow in (False, True):
        keep, drops = sizzle.cleared(surveyed, allow_weapons=allow)
        assert keep == [] and len(drops) == 1


def test_weapons_are_scopable_per_run():
    surveyed = [{"file": "/x/a.mp4", "weapons_or_blood": True,
                 "weapons_or_blood_note": "handgun"}]
    assert sizzle.cleared(surveyed)[0] == []
    assert len(sizzle.cleared(surveyed, allow_weapons=True)[0]) == 1


def test_a_failed_survey_is_dropped_not_trusted():
    surveyed = [{"file": "/x/a.mp4", "error": "unparseable survey"}]
    keep, drops = sizzle.cleared(surveyed)
    assert keep == [] and "unparseable" in drops[0][1]


# -------------------------------------------------------------- cache key

def test_crop_is_part_of_the_survey_cache_key():
    """A clearance verdict is about the frame that was looked at; changing the
    crop must not reuse an answer about pixels no longer in shot."""
    a = sizzle._cache_key("/x/a.mp4", 0.0, None)
    b = sizzle._cache_key("/x/a.mp4", 0.0, "100:100:0:0")
    assert a != b
