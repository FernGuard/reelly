"""Tiny stage timing: print how long the expensive stages take, and keep an
append-only timings.json per project so slow runs stay explainable later."""
import contextlib
import datetime
import json
import os
import threading
import time

_LOCK = threading.Lock()  # stages can close from worker threads


@contextlib.contextmanager
def stage(label, path=None):
    """Time a stage: prints `[time] <label> <seconds>s` and, when `path` is
    given, appends {label, seconds, ts} to that timings.json. Also heartbeats
    the run log, so `reelly runs` names the stage a session is sitting on."""
    from . import runlog
    runlog.beat(label)
    t0 = time.monotonic()
    try:
        yield
    finally:
        dt = time.monotonic() - t0
        print(f"[time] {label} {dt:.1f}s")
        if path:
            entry = {"label": label, "seconds": round(dt, 3),
                     "ts": datetime.datetime.now().isoformat(timespec="seconds")}
            with _LOCK:
                try:
                    data = json.load(open(path)) if os.path.exists(path) else []
                    if not isinstance(data, list):
                        data = []
                except (ValueError, OSError):
                    data = []
                data.append(entry)
                try:
                    d = os.path.dirname(path)
                    if d:
                        os.makedirs(d, exist_ok=True)
                    json.dump(data, open(path, "w"), indent=1)
                except OSError as e:  # timing must never kill a render
                    print(f"[time] could not write {path}: {e}")


def timings_path(root):
    """Canonical per-project timings file, next to the analysis dir."""
    return os.path.join(root, "timings.json")
