"""Golden frames (reelly.golden): effects must keep looking the same.
Runs where Chrome is available and references exist for this machine's
renderer tier. REELLY_GOLDEN=required (the Windows worker sets it) turns a
missing tier into a failure: a GPU that silently fell back to another
renderer must not pass by skipping."""
import os

import pytest

from reelly import config, golden


@pytest.mark.skipif(not config.CHROME or not os.path.exists(golden.ROOT),
                    reason="no Chrome or no golden references")
def test_effects_match_their_golden_frames():
    res = golden.run()
    if all(v is None for v in res.values()):
        msg = f"no references for renderer tier {golden.tier()}"
        if os.environ.get("REELLY_GOLDEN", "").strip().lower() == "required":
            pytest.fail(msg)
        pytest.skip(msg)
    missing = [k for k, v in res.items() if v == float("inf")]
    assert not missing, f"cases with no reference on {golden.tier()}: {missing}"
    bad = {k: v for k, v in res.items() if v is not None and v > golden.TOL}
    assert not bad, f"effects changed look (99th-pct diff > {golden.TOL:.4f}): {bad}"
