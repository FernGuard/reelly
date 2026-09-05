"""A sizzle reel must never come out EMPTY because the plan brain could not land
a <=6-word payoff. After the retries, the last real plan ships with auto-trimmed
copy instead of SystemExit (MAR-108 spirit, plan edition)."""
from reelly import sizzle, direct


def test_trim_words_caps_and_keeps_end_punctuation():
    assert sizzle._trim_words("one two three four five six seven.", 6) == \
        "one two three four five six."
    assert sizzle._trim_words("short line", 6) == "short line"
    assert sizzle._trim_words("", 6) == ""


def test_plan_ships_trimmed_instead_of_systemexit(monkeypatch):
    reply = {"shots": [{"role": "body", "dur": 2.0, "id": "a"}],
             "hook": "hook line", "cta": "play",
             "payoff": "one two three four five six seven"}   # 7 words, over limit
    monkeypatch.setattr(direct, "_ask_json", lambda *a, **k: dict(reply))
    monkeypatch.setattr(sizzle, "_pool_lines", lambda pool: "")
    # validator only ever complains about the over-long payoff
    monkeypatch.setattr(sizzle, "_validate",
                        lambda p, pool, sec:
                        [f"payoff is {len(p.get('payoff','').split())} words (limit 6)"]
                        if len(p.get("payoff", "").split()) > 6 else [])
    out = sizzle.plan("games", [], seconds=24, tries=3)
    assert out is not None, "should ship a plan, not raise"
    assert len(out["payoff"].split()) <= 6, "payoff auto-trimmed to the limit"
