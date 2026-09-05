"""Screening gate. Every case is something that reached a calendar row."""
import os
import tempfile

from reelly import safety


def test_unscreened_cut_fails():
    """A cut can reach a fully written, ready-to-schedule row before anyone
    notices it opens on a gun and blood. The title can still sound fine."""
    name, status, msg = safety.verdict({"hook": "Something is under the shutter"})
    assert status == "FAIL" and "not screened" in msg


def test_the_failure_message_names_what_to_look_for():
    """A gate that says "no" without saying what to check just gets bypassed."""
    msg = safety.verdict({})[2]
    for c in ("guns", "branding", "trademark", "children"):
        assert c in msg


def test_clean_verdict_passes_and_records_who_looked():
    n, s, m = safety.verdict({"screened": {"by": "reviewer", "on": "2026-07-28",
                                           "verdict": "clean"}})
    assert s == "PASS" and "reviewer" in m and "2026-07-28" in m


def test_a_recorded_rejection_still_fails_the_cut():
    """Somebody looked and wrote down what they saw: good process, dead cut."""
    n, s, m = safety.verdict({"screened": {"by": "c", "on": "d",
                                           "verdict": "axes and a bloodied face"}})
    assert s == "FAIL" and "axes" in m


def test_a_bare_string_verdict_is_accepted():
    assert safety.verdict({"screened": "clean"})[1] == "PASS"


def test_empty_verdict_is_not_a_pass():
    assert safety.verdict({"screened": {"by": "x"}})[1] == "FAIL"


def test_identical_files_are_caught_as_duplicates():
    """A workshop promo and its day-of reminder were set to post the same file
    two days apart on one account. That is a within-platform duplicate."""
    with tempfile.TemporaryDirectory() as td:
        a = os.path.join(td, "promo.mp4")
        b = os.path.join(td, "reminder.mp4")
        c = os.path.join(td, "other.mp4")
        open(a, "wb").write(b"same bytes")
        open(b, "wb").write(b"same bytes")
        open(c, "wb").write(b"different")
        dups = safety.duplicates([a, b, c])
    assert len(dups) == 1
    assert sorted(next(iter(dups.values()))) == ["promo.mp4", "reminder.mp4"]


def test_distinct_files_are_not_flagged():
    with tempfile.TemporaryDirectory() as td:
        a = os.path.join(td, "a.mp4"); open(a, "wb").write(b"one")
        b = os.path.join(td, "b.mp4"); open(b, "wb").write(b"two")
        assert safety.duplicates([a, b]) == {}
