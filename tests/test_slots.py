"""Machine-wide slot pool: the budget is shared, crash-safe, and env-tunable."""
import os
import subprocess
import sys
import threading
import time

import pytest

from reelly import slots


@pytest.fixture
def pool_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(slots, "SLOTS_DIR", str(tmp_path / "slots"))
    return slots.SLOTS_DIR


def test_one_slot_serializes_two_holders(pool_dir, monkeypatch):
    monkeypatch.setenv("REELLY_DECODE_SLOTS", "1")
    active, peak, lock = [0], [0], threading.Lock()

    def work():
        with slots.hold("decode"):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.2)
            with lock:
                active[0] -= 1

    ts = [threading.Thread(target=work) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 1  # never two holders on a 1-slot pool


def test_two_slots_allow_two_concurrent(pool_dir, monkeypatch):
    monkeypatch.setenv("REELLY_DECODE_SLOTS", "2")
    peak, active, lock = [0], [0], threading.Lock()

    def work():
        with slots.hold("decode"):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.3)
            with lock:
                active[0] -= 1

    ts = [threading.Thread(target=work) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 2


def test_slot_released_on_exception(pool_dir, monkeypatch):
    monkeypatch.setenv("REELLY_RENDER_SLOTS", "1")
    with pytest.raises(ValueError):
        with slots.hold("render"):
            raise ValueError("boom")
    # slot must be free again immediately
    t0 = time.monotonic()
    with slots.hold("render"):
        pass
    assert time.monotonic() - t0 < 1


def test_killed_process_releases_slot(pool_dir, monkeypatch):
    """The crash-safety property the whole design leans on: a dead holder's
    flock evaporates with its fd, no cleanup required."""
    monkeypatch.setenv("REELLY_DECODE_SLOTS", "1")
    code = (
        "import sys, time; sys.path.insert(0, sys.argv[1]);"
        "from reelly import slots; slots.SLOTS_DIR = sys.argv[2];"
        "import os; os.environ['REELLY_DECODE_SLOTS']='1';"
        "ctx = slots.hold('decode'); ctx.__enter__(); print('HELD', flush=True);"
        "time.sleep(60)")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(slots.__file__)))
    p = subprocess.Popen([sys.executable, "-c", code, repo, slots.SLOTS_DIR],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "HELD"
    p.kill()
    p.wait()
    t0 = time.monotonic()
    with slots.hold("decode"):
        pass
    assert time.monotonic() - t0 < 5


def test_bad_env_falls_back_to_default(pool_dir, monkeypatch):
    monkeypatch.setenv("REELLY_DECODE_SLOTS", "banana")
    assert slots._pool_size("decode") == 2
