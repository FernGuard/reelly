"""Test isolation: every test run gets a throwaway Reelly home, set BEFORE
reelly is imported, so module-level paths (config.HOME, the SFX cache, the
ledger, slots, the brand kit) never point at the developer's real ~/.reelly.
Before this, a plain `pytest` wrote grade caches, slot files and FAL timing
rows into the real home. REELLY_HOME, not HOME/USERPROFILE: redirecting the
OS home breaks Chrome on Windows."""
import os
import tempfile

_TEST_HOME = tempfile.mkdtemp(prefix="reelly-test-home-")
os.environ["REELLY_HOME"] = os.path.join(_TEST_HOME, ".reelly")
# no setting from the machine leaks in: every REELLY_* behaviour the
# developer or a render machine has set (fps, effects, GPU tier, profile,
# motion...) is dropped; tests that need one set it themselves. Only the
# "where is this tool" paths (and a render machine's golden-frame demand) stay.
_WHERE = ("REELLY_CHROME", "REELLY_IRIS", "REELLY_DEPTH_MODEL", "REELLY_SEGMENTER",
          "REELLY_GOLDEN")
for _var in [v for v in os.environ if v.startswith("REELLY_") and v not in _WHERE]:
    os.environ.pop(_var, None)
os.environ["REELLY_HOME"] = os.path.join(_TEST_HOME, ".reelly")
# no real mediapipe probe (a child process per run) unless a test asks for one:
# face tests stub the detectors, and the suite must not depend on a GPU session
os.environ.setdefault("REELLY_FACE", "off")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_render_state():
    """Per-thread render state never leaks from one test into the next."""
    yield
    from reelly import degrade, shaderfx
    shaderfx.set_content_type(None)
    degrade.collect()
