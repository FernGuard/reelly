"""Run heartbeats: which reelly commands are alive, on what stage, and when
they last made progress.

`reelly runs` reads these, so a stalled background session is found in
seconds instead of discovered hours later. One JSON file per process under
~/.reelly/runs/ (pid in the name: parallel sessions never collide). Writers
update on stage transitions (via timing.stage) and inside long poll loops
(FAL). The semantics:

  - clean exit           -> file removed (nothing to report)
  - uncaught exception   -> file kept, stage says CRASHED: <error>
  - killed / hung / lost -> file kept, dead pid or stale beat = STUCK/DEAD

Heartbeats must never kill a render: every write is best-effort.
"""
import atexit
import json
import os
import re
import sys
import time

from . import config

RUNS_DIR = os.path.join(config.HOME, "runs")
STALL_S = 120     # a live pid with no beat for this long is flagged STUCK?
PRUNE_S = 86400   # dead entries older than a day are removed by report()

_current = {"path": None, "cmd": None, "project": None,
            "started": None, "crashed": False}


def _slug(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s))[:80] or "run"


def start(cmd, project=""):
    """Begin heartbeating for this process (called once by the CLI).
    Library imports never write anything: beat() is a no-op before start()."""
    _current.update(cmd=str(cmd), project=str(project or ""),
                    started=time.time(), crashed=False,
                    path=os.path.join(
                        RUNS_DIR, f"{_slug(project or cmd)}.{os.getpid()}.json"))
    beat("starting")
    orig_hook = sys.excepthook

    def _hook(tp, val, tb):
        _current["crashed"] = True
        beat(f"CRASHED: {tp.__name__}: {val}")
        orig_hook(tp, val, tb)

    sys.excepthook = _hook
    atexit.register(_cleanup)


def beat(stage):
    """Record progress. Callable from any thread; no-op unless start() ran."""
    if not _current["path"]:
        return
    try:
        os.makedirs(RUNS_DIR, exist_ok=True)
        json.dump({"cmd": _current["cmd"], "project": _current["project"],
                   "pid": os.getpid(), "started": _current["started"],
                   "beat": time.time(), "stage": str(stage)[:160]},
                  open(_current["path"], "w"))
    except OSError:
        pass


def _cleanup():
    if _current["crashed"]:
        return  # keep the file: `reelly runs` names the crash
    p = _current["path"]
    if p and os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _age(seconds):
    return f"{seconds / 60:.0f}m" if seconds >= 60 else f"{seconds:.0f}s"


def report():
    """Print every known run; return the count of STUCK?/DEAD/CRASHED ones
    (so `reelly runs` can exit non-zero when something needs attention)."""
    now = time.time()
    rows = []
    for fn in (sorted(os.listdir(RUNS_DIR)) if os.path.isdir(RUNS_DIR) else []):
        p = os.path.join(RUNS_DIR, fn)
        try:
            r = json.load(open(p))
        except (ValueError, OSError):
            continue
        alive = _pid_alive(r.get("pid"))
        age = max(0, now - r.get("beat", 0))
        if not alive and age > PRUNE_S:
            try:
                os.remove(p)
            except OSError:
                pass
            continue
        stage = r.get("stage", "")
        state = ("CRASHED" if stage.startswith("CRASHED") else
                 "STUCK?" if alive and age > STALL_S else
                 "live" if alive else "DEAD")
        rows.append((state, age, r))
    if not rows:
        print("no active, stuck, or crashed runs")
        return 0
    print(f"{'state':<9}{'beat':>5}  {'pid':>6}  {'cmd':<11}{'project':<26}stage")
    bad = 0
    for state, age, r in rows:
        if state != "live":
            bad += 1
        print(f"{state:<9}{_age(age):>5}  {r.get('pid', ''):>6}  "
              f"{str(r.get('cmd', ''))[:10]:<11}"
              f"{str(r.get('project', ''))[:25]:<26}{r.get('stage', '')}")
    if bad:
        print(f"\n{bad} run(s) need attention: STUCK? = live pid, no progress "
              f"for {STALL_S}s; DEAD = pid gone without a clean exit.")
    return bad
