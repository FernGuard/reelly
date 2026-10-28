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
for _var in ("REELLY_BRANDKIT", "REELLY_BRAND", "REELLY_ALLOW_PAID",
             "REELLY_PROJECTS"):
    os.environ.pop(_var, None)
# no real mediapipe probe (a child process per run) unless a test asks for one:
# face tests stub the detectors, and the suite must not depend on a GPU session
os.environ.setdefault("REELLY_FACE", "off")
