"""direct._batched_refine: the cost semantics the serial refine loop had.

Two properties are money, not style:
- --max-cuts N stops SUBMITTING once N plans exist (batch granularity), so a
  small run does not pay for all 12 candidate refinements;
- a budget-cap RuntimeError in one worker sets the abort flag, in-flight
  siblings short-circuit before their next LLM call, and the exception
  re-raises for the earliest failing candidate of its batch.

The default batch width is 12 — run()'s candidate cap — so a normal run is a
SINGLE batch (two sequential 6-wide batches measured 48.9s). At that width the
abort Event, not batch sequencing, is what bounds post-cap spend; the explicit
batch_size=6 tests below keep the multi-batch machinery honest for callers
that pass a smaller width.
"""
import threading

import pytest

from reelly.direct import _batched_refine


def _cands(n):
    return [{"i": i} for i in range(n)]


def test_consume_sees_candidate_order_and_all_results():
    order = []

    def worker(ci, cand, abort):
        return ci * 10

    def consume(cand, res):
        order.append((cand["i"], res))
        return False

    _batched_refine(_cands(8), worker, consume, batch_size=6)
    assert order == [(i, i * 10) for i in range(8)]


def test_max_cuts_stop_prevents_later_batches():
    calls = []

    def worker(ci, cand, abort):
        calls.append(ci)
        return ci

    def consume(cand, res):
        return res >= 2      # "enough plans" after the third candidate

    _batched_refine(_cands(12), worker, consume, batch_size=6)
    # first batch of 6 was already submitted (parallelism costs batch
    # granularity) but the second batch of 6 must never be paid for
    assert sorted(calls) == [0, 1, 2, 3, 4, 5]


def test_stop_exactly_on_batch_boundary_submits_nothing_more():
    calls = []

    def worker(ci, cand, abort):
        calls.append(ci)
        return ci

    _batched_refine(_cands(12), worker, lambda c, r: r == 5, batch_size=6)
    assert sorted(calls) == list(range(6))


def test_worker_exception_reraises_and_skips_batch_consume():
    consumed = []

    def worker(ci, cand, abort):
        if ci == 2:
            raise RuntimeError("budget cap: refine would exceed the ledger")
        return ci

    with pytest.raises(RuntimeError, match="budget cap"):
        _batched_refine(_cands(6), worker, lambda c, r: consumed.append(c),
                        batch_size=6)
    assert consumed == []    # the failing batch is never consumed


def test_earliest_failure_in_candidate_order_wins():
    def worker(ci, cand, abort):
        if ci in (1, 4):
            raise RuntimeError(f"boom {ci}")
        return ci

    with pytest.raises(RuntimeError, match="boom 1"):
        _batched_refine(_cands(6), worker, lambda c, r: False, batch_size=6)


def test_abort_flag_short_circuits_inflight_siblings():
    """One worker raising must let in-flight siblings skip their next
    (expensive) LLM call instead of finishing and getting paid for."""
    started = threading.Barrier(3, timeout=5)
    paid = []

    def worker(ci, cand, abort):
        started.wait()               # all three in flight together
        if ci == 0:
            raise RuntimeError("budget cap")
        abort.wait(timeout=5)        # deterministically observe the failure
        if abort.is_set():
            return None              # what _refine_one does before an LLM call
        paid.append(ci)
        return ci

    with pytest.raises(RuntimeError, match="budget cap"):
        _batched_refine(_cands(3), worker, lambda c, r: False,
                        batch_size=3, max_workers=3)
    assert paid == []                # nobody paid after the cap hit


def test_default_single_batch_covers_the_candidate_cap():
    """Default width equals run()'s 12-candidate cap: one batch, no second
    serialised round of LLM calls; max_cuts bookkeeping is unchanged."""
    calls, consumed = [], []

    def worker(ci, cand, abort):
        calls.append(ci)
        return ci

    _batched_refine(_cands(12), worker,
                    lambda c, r: consumed.append(c["i"]) or r >= 2)
    assert sorted(calls) == list(range(12))   # all submitted in the one batch
    assert consumed == [0, 1, 2]              # consume stopped at "enough plans"


def test_max_cuts_never_submits_a_second_batch_at_default_width():
    """A --max-cuts run must still never pay for a batch past the one where
    enough plans existed — at 12-wide that means candidates 13+ are never
    submitted."""
    calls = []

    def worker(ci, cand, abort):
        calls.append(ci)
        return ci

    _batched_refine(_cands(24), worker, lambda c, r: r >= 2)
    assert sorted(calls) == list(range(12))


def test_abort_short_circuits_at_default_width():
    """With one 12-wide batch there is no 'next batch' to withhold: the abort
    Event alone must stop in-flight siblings from paying for their LLM call."""
    started = threading.Barrier(3, timeout=5)
    paid = []

    def worker(ci, cand, abort):
        started.wait()
        if ci == 0:
            raise RuntimeError("budget cap")
        abort.wait(timeout=5)
        if abort.is_set():
            return None
        paid.append(ci)
        return ci

    with pytest.raises(RuntimeError, match="budget cap"):
        _batched_refine(_cands(3), worker, lambda c, r: False)  # defaults
    assert paid == []


def test_exception_in_second_batch_after_first_consumed():
    consumed = []

    def worker(ci, cand, abort):
        if ci == 7:
            raise ValueError("network")
        return ci

    with pytest.raises(ValueError, match="network"):
        _batched_refine(_cands(12), worker,
                        lambda c, r: consumed.append(c["i"]) or False,
                        batch_size=6)
    assert consumed == [0, 1, 2, 3, 4, 5]   # batch 1 fully bookkept first
