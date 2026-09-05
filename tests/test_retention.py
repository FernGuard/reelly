"""Retention checks. Each test names the failure it protects against."""
from reelly import retention


# --- engagement bait -------------------------------------------------------

def test_a_cut_that_asks_for_nothing_is_flagged():
    """Comments, shares, saves and rewatches drive distribution. A cut with no
    ask collects none of them."""
    v = retention.bait({"caption": "A noir world built in Adventure Project.",
                        "cta": "make yours on ExampleBrand", "hook": "Twelve years on the force"})
    assert v[1] == "WARN"


def test_a_question_counts_as_comment_bait():
    v = retention.bait({"caption": "Which one are you playing?", "cta": "", "hook": ""})
    assert v[1] == "PASS" and "comment" in v[2]


def test_share_and_save_asks_are_recognised():
    assert retention.bait({"caption": "send this to someone stuck"})[1] == "PASS"
    assert retention.bait({"caption": "save this for later"})[1] == "PASS"


def test_bait_reads_overlay_lines_too():
    """The strongest ask is often burned into the picture, not the caption."""
    v = retention.bait({"caption": "", "cta": "", "hook": "",
                        "overlay_lines": [{"t": 6.0, "text": "WHAT DO YOU DO"}]})
    assert v[1] == "PASS"


def test_bait_survives_an_empty_plan():
    assert retention.bait({})[1] == "WARN"


# --- thresholds ------------------------------------------------------------

def test_still_threshold_is_below_slow_drift():
    """If the threshold sat above a slow Ken Burns push, every moving card would
    read as static and the check would cry wolf."""
    assert 0 < retention.STILL_DIFF < 0.05


def test_monotony_limit_matches_the_source_guidance():
    """Cut every 2 to 3 seconds; 3.5 gives a little slack before warning."""
    assert 3.0 <= retention.MAX_STATIC_S <= 4.0
