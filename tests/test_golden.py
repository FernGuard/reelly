"""Golden frames (reelly.golden): effects must keep looking the same.
Runs only where Chrome is available and references exist for this
machine's renderer tier (the PC's RTX 5090); elsewhere it skips."""
import os
import shutil

import pytest

from reelly import config, golden


@pytest.mark.skipif(not config.CHROME or not os.path.exists(golden.ROOT),
                    reason="no Chrome or no golden references")
def test_effects_match_their_golden_frames():
    res = golden.run()
    if all(v is None for v in res.values()):
        pytest.skip(f"no references for renderer tier {golden.tier()}")
    bad = {k: v for k, v in res.items() if v is not None and v > golden.TOL}
    assert not bad, f"effects changed look (99th-pct diff > {golden.TOL:.4f}): {bad}"
