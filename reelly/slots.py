"""Machine-wide work slots for the ffmpeg passes that fight over the same
performance cores.

The per-process caps (analyze's 2-decode semaphore, finalize's 5 workers)
stop meaning anything when several projects run in parallel: N runs bring
N budgets and everything crawls while looking stuck. These pools live in
~/.reelly/slots/ and are shared by every reelly process on the machine.

flock-based on purpose (msvcrt byte locks on Windows, same semantics): a
killed or crashed process releases its slot the moment its fd closes, so
there is no stale-lock cleanup and no daemon. A
long wait heartbeats the run log and says what it is waiting for, so a
slot queue reads as "waiting", never as "hung".

Pools (defaults match today's single-run behaviour; only STACKED runs share):
  decode  2 slots  full-decode analysis passes (REELLY_DECODE_SLOTS)
  render  5 slots  segment cut+encode renders  (REELLY_RENDER_SLOTS)
"""
import contextlib
import os
import time

try:                        # POSIX
    import fcntl
except ImportError:         # Windows
    fcntl = None
    import msvcrt

from . import config

SLOTS_DIR = os.path.join(config.HOME, "slots")
POOLS = {"decode": 2, "render": 5}
_WAIT_NOTE_S = 10  # say something (and heartbeat) after this long in the queue


def _try_lock(f):
    """Non-blocking exclusive lock on an open slot file; raises OSError when
    another holder has it."""
    if fcntl is not None:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)


def _lock_blocking(f):
    """Blocking exclusive lock (released when the file closes)."""
    if fcntl is not None:
        fcntl.flock(f, fcntl.LOCK_EX)
        return
    while True:
        try:
            _try_lock(f)
            return
        except OSError:
            time.sleep(0.05)


def _unlock(f):
    if fcntl is not None:
        fcntl.flock(f, fcntl.LOCK_UN)
    else:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def _pool_size(pool):
    default = POOLS.get(pool, 2)
    try:
        return max(1, int(os.environ.get(f"REELLY_{pool.upper()}_SLOTS", default)))
    except ValueError:
        return default


@contextlib.contextmanager
def hold(pool, label=""):
    """Block until a slot in `pool` is free, hold it for the with-body.

    Nested holds from the same process take distinct slots (flock conflicts
    across file descriptors), so the budget counts concurrent WORK, not
    processes."""
    n = _pool_size(pool)
    os.makedirs(SLOTS_DIR, exist_ok=True)
    fh, t0, noted = None, time.monotonic(), 0.0
    while fh is None:
        for i in range(n):
            f = open(os.path.join(SLOTS_DIR, f"{pool}.{i}"), "a+")
            try:
                _try_lock(f)
                fh = f
                break
            except OSError:
                f.close()
        if fh is None:
            waited = time.monotonic() - t0
            if waited - noted >= _WAIT_NOTE_S:
                noted = waited
                from . import runlog
                msg = (f"waiting for a {pool} slot ({n} machine-wide)"
                       + (f" -- {label}" if label else ""))
                print(f"[slots] {msg} ({waited:.0f}s)")
                runlog.beat(msg)
            time.sleep(0.5)
    try:
        yield
    finally:
        try:
            _unlock(fh)
        except OSError:
            pass
        fh.close()
