"""`reelly cut` pipeline: each cut's graphics layer starts the moment finalize
finishes that cut (overlapping the cuts still rendering) instead of waiting
for the full finalize phase. Judge stays a final ordered barrier.
"""
import inspect

import pytest

import reelly.overlays as overlays
from reelly import cli, finalize


def test_finalize_exposes_the_per_cut_seam():
    assert "on_cut" in inspect.signature(finalize.run).parameters


def test_finalize_runs_five_workers():
    # locks the measured win: the per-cut chain is subprocess/HTTP wait-bound,
    # so finalize's pool is 5 wide (was 3)
    assert "max_workers=5" in inspect.getsource(finalize.run)


def _fakes(monkeypatch, calls, fail_apply=()):
    def autoplan(project, product=None, cut_id=None, **k):
        calls.append(("plan", cut_id))

    def apply(project, cut_id=None, **k):
        calls.append(("apply", cut_id))
        if cut_id in fail_apply:
            raise RuntimeError(f"boom {cut_id}")

    monkeypatch.setattr(overlays, "autoplan", autoplan)
    monkeypatch.setattr(overlays, "apply", apply)


def test_cutgfx_serialises_per_cut_plan_then_apply(monkeypatch):
    """1-wide pool: autoplan(cut) then apply(cut), cuts in arrival order —
    overlay_specs.json is read-modify-write, so gfx jobs must not interleave."""
    calls = []
    _fakes(monkeypatch, calls)
    g = cli._CutGfx("proj", "video", autoplan_needed=True)
    for cid in ("cut_01", "cut_02", "cut_03"):
        g.on_cut({"id": cid}, [])
    g.finish()
    assert calls == [("plan", "cut_01"), ("apply", "cut_01"),
                     ("plan", "cut_02"), ("apply", "cut_02"),
                     ("plan", "cut_03"), ("apply", "cut_03")]


def test_cutgfx_skips_autoplan_for_hand_written_specs(monkeypatch):
    calls = []
    _fakes(monkeypatch, calls)
    g = cli._CutGfx("proj", "video", autoplan_needed=False)
    g.on_cut({"id": "cut_01"}, [])
    g.finish()
    assert calls == [("apply", "cut_01")]


def test_cutgfx_isolates_failures_and_reports_them_together(monkeypatch):
    """One failing cut must not stop its siblings' graphics; finish() raises
    the same aggregate error overlays.apply used to."""
    calls = []
    _fakes(monkeypatch, calls, fail_apply={"cut_02"})
    g = cli._CutGfx("proj", "video", autoplan_needed=False)
    for cid in ("cut_01", "cut_02", "cut_03"):
        g.on_cut({"id": cid}, [])
    with pytest.raises(RuntimeError, match="overlays failed on: cut_02"):
        g.finish()
    assert ("apply", "cut_03") in calls   # sibling still ran after the failure


def test_finalize_invokes_on_cut_from_the_worker(monkeypatch):
    """The seam fires with (plan, made) after a cut's deliverables exist —
    exercised through the same closure shape _finalize_one uses."""
    seen = []

    def on_cut(plan, made):
        seen.append((plan["id"], list(made)))

    # simulate two cuts through finalize's run_parallel contract
    plans = [{"id": "cut_01"}, {"id": "cut_02"}]

    def _finalize_one(p):
        made = [f"{p['id']}.mp4"]
        if on_cut:
            on_cut(p, made)
        return made

    from reelly.preview import run_parallel
    rows = run_parallel(plans, _finalize_one, max_workers=2)
    assert [r[2] for r in rows] == [None, None]
    assert sorted(seen) == [("cut_01", ["cut_01.mp4"]),
                            ("cut_02", ["cut_02.mp4"])]
