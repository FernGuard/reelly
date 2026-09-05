"""Synthetic tests for the learn-loop promotion pipeline."""
from reelly import learn


def test_outlier_promotes_without_plan_evidence():
    metrics = [
        {"clip": "a", "platform": "tiktok", "views": 14000, "channel_median": 100},
        {"clip": "b", "platform": "tiktok", "views": 2200, "channel_median": 100},
    ]
    props = learn.metric_promotions(metrics)
    assert len(props) == 2
    assert all(p.startswith("- PROMOTE (score") for p in props)
    assert "140.0" in props[0]


def test_below_views_floor_does_not_promote():
    metrics = [{"clip": "tiny", "platform": "tiktok", "views": 50,
                "channel_median": 1}]
    assert learn.metric_promotions(metrics) == []
    assert 50 < learn.PLATFORM_MIN_VIEWS["tiktok"]


def test_high_er_keep_promotes_at_modest_reach():
    metrics = [{"clip": "sample-project/cut-03", "platform": "tiktok",
                "views": 500, "engagements": 30, "channel_median": 500}]
    verdicts = [{"verdict": "KEEP", "item": "sample-project/cut-03"}]
    props = learn.metric_promotions(metrics, verdicts)
    assert len(props) == 1
    assert "PROMOTE (ER 6.00%" in props[0]


def test_high_er_without_keep_does_not_promote():
    metrics = [{"clip": "sample-project/cut-03", "platform": "tiktok",
                "views": 500, "engagements": 30, "channel_median": 500}]
    assert learn.metric_promotions(metrics, verdicts=[]) == []


def test_keep_matches_human_label_against_slug_item():
    metrics = [{"clip": "Sample Clip", "platform": "tiktok",
                "views": 500, "engagements": 30, "channel_median": 500}]
    verdicts = [{"verdict": "KEEP", "item": "sample-clip/formula"}]
    props = learn.metric_promotions(metrics, verdicts)
    assert len(props) == 1
    assert "PROMOTE (ER 6.00%" in props[0]


def test_unrelated_label_does_not_match_slug_item():
    metrics = [{"clip": "Unrelated Clip", "platform": "tiktok",
                "views": 500, "engagements": 30, "channel_median": 500}]
    verdicts = [{"verdict": "KEEP", "item": "sample-clip/formula"}]
    assert learn.metric_promotions(metrics, verdicts) == []


def test_low_engagement_impression_spike_does_not_promote():
    metrics = [{"clip": "xpost", "platform": "x", "views": 10000,
                "engagements": 1, "channel_median": 100}]
    verdicts = [{"verdict": "KEEP", "item": "xpost"}]
    assert learn.metric_promotions(metrics, verdicts) == []
    assert (1 / 10000) < learn.ER_FLOOR


def _write_verdict_fixture(tmp_path):
    path = tmp_path / "VERDICTS.md"
    path.write_text(
        "2026-01-01 sample-exp/blind-round LEARNED because payoff remained visible\n"
        "2026-01-02 sample-timing/grammar VERDICT because ramps need easing\n"
        "2026-01-03 sample-exp/round-2 LEARNED because quality gate was added\n")
    return path


def test_supported_verdict_tokens_parse(tmp_path, monkeypatch):
    monkeypatch.setattr(learn, "VERDICTS", str(_write_verdict_fixture(tmp_path)))
    rows, unparsed = learn.parse_verdicts_full()
    assert unparsed == []
    assert {r["item"]: r["verdict"] for r in rows} == {
        "sample-exp/blind-round": "LEARNED",
        "sample-timing/grammar": "VERDICT",
        "sample-exp/round-2": "LEARNED",
    }


def test_learned_and_verdict_lines_are_not_constraints(tmp_path, monkeypatch):
    monkeypatch.setattr(learn, "VERDICTS", str(_write_verdict_fixture(tmp_path)))
    rows, _ = learn.parse_verdicts_full()
    assert all(r["verdict"] != "CONSTRAINT" for r in rows)
