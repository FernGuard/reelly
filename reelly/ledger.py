"""Cost ledger: every AI call is logged; a monthly cap is enforced.

The cap is a default, not a wall: sprint mode raises it temporarily
(`reelly budget sprint <cap> --days N --reason "..."`) and the sprint itself
is recorded so spend spikes stay explainable later.
"""
import datetime
import json
import os

from . import config

LEDGER = os.path.join(config.HOME, "ledger.json")
DEFAULT_CAP = 20.0


def _load():
    if os.path.exists(LEDGER):
        return json.load(open(LEDGER))
    return {"entries": [], "budget": {"monthly_cap": DEFAULT_CAP, "sprint": None}}


def _save(d):
    os.makedirs(config.HOME, exist_ok=True)
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(d, fh, indent=1)
    os.replace(tmp, LEDGER)


def month_spend(d=None):
    d = d or _load()
    month = datetime.date.today().strftime("%Y-%m")
    return sum(e["cost"] for e in d["entries"] if e["ts"].startswith(month))


def effective_cap(d=None):
    """(cap, sprint_or_None); an expired sprint falls back to the monthly cap."""
    d = d or _load()
    s = d["budget"].get("sprint")
    if s and s.get("until", "") >= datetime.date.today().isoformat():
        return float(s["cap"]), s
    return float(d["budget"].get("monthly_cap", DEFAULT_CAP)), None


def check(estimated=0.0):
    """Refuse a call that would cross the cap; warn from 80 percent."""
    d = _load()
    cap, sprint = effective_cap(d)
    spent = month_spend(d)
    if spent + estimated > cap:
        mode = f"sprint cap ${cap:.2f} until {sprint['until']}" if sprint else f"monthly cap ${cap:.2f}"
        raise RuntimeError(
            f"budget: ${spent:.2f} spent this month + ~${estimated:.2f} would cross the {mode}. "
            f"Raise it temporarily: reelly budget sprint <cap> --days N --reason '...'")
    if spent + estimated > 0.8 * cap:
        print(f"[budget] warning: ${spent + estimated:.2f} of ${cap:.2f} this month (80 percent zone)")


def add(service, detail, cost, project=""):
    import fcntl
    os.makedirs(config.HOME, exist_ok=True)
    # Exclusive lock around load-modify-save: concurrent renders each append
    # here, and the unlocked read-modify-write raced (a render once read the
    # file mid-write and died on a JSONDecodeError). _save is atomic
    # (tmp + rename) so lock-free readers always see a complete file.
    with open(LEDGER + ".lock", "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = _load()
        d["entries"].append({
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "project": project, "service": service, "detail": detail,
            "cost": round(float(cost), 4),
        })
        _save(d)


def start_sprint(cap, days, reason):
    d = _load()
    until = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    d["budget"]["sprint"] = {"cap": float(cap), "until": until, "reason": reason,
                             "started": datetime.date.today().isoformat()}
    _save(d)
    return until


def end_sprint():
    d = _load()
    d["budget"]["sprint"] = None
    _save(d)


def report():
    d = _load()
    cap, sprint = effective_cap(d)
    spent = month_spend(d)
    head = f"This month: ${spent:.2f} of ${cap:.2f}"
    head += f" (SPRINT until {sprint['until']}: {sprint['reason']})" if sprint else " (default cap)"
    lines = [head]
    month = datetime.date.today().strftime("%Y-%m")
    by = {}
    for e in d["entries"]:
        if e["ts"].startswith(month):
            by[e["service"]] = by.get(e["service"], 0.0) + e["cost"]
    for k, v in sorted(by.items(), key=lambda x: -x[1]):
        lines.append(f"  {k}: ${v:.2f}")
    return "\n".join(lines)
