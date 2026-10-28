"""Test isolation: every test run gets a throwaway HOME, set BEFORE reelly is
imported, so module-level paths (config.HOME = ~/.reelly, the SFX cache, the
ledger, slots, the brand kit) never point at the developer's real ~/.reelly.
Before this, a plain `pytest` wrote grade caches, slot files and FAL timing
rows into the real home."""
import os
import tempfile

_REAL_HOME = os.path.expanduser("~")
_TEST_HOME = tempfile.mkdtemp(prefix="reelly-test-home-")
os.environ["HOME"] = _TEST_HOME
# model caches stay where they are: nothing in the suite should download, and
# a fresh HOME must not turn a stray cache lookup into a 1.4 GB fetch
os.environ.setdefault("HF_HOME", os.path.join(_REAL_HOME, ".cache", "huggingface"))
for _var in ("REELLY_BRANDKIT", "REELLY_BRAND", "REELLY_ALLOW_PAID",
             "REELLY_PROJECTS"):
    os.environ.pop(_var, None)
# no real mediapipe probe (a child process per run) unless a test asks for one:
# face tests stub the detectors, and the suite must not depend on a GPU session
os.environ.setdefault("REELLY_FACE", "off")
