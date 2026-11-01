"""What fell back while a cut rendered, so a reviewer sees it.

Many render steps keep the cut alive when an effect fails: GPU text falls
back to browser text, a shader layer is skipped, impact marks stay off. That
is the right call for the deliverable, but a log line is easy to miss. Each
fallback is noted here (per cut: one thread renders one cut), written to
qc/degraded/<cut>.json, raised by the judge as a WARN and listed in
REVIEW.md.
"""
import json
import os
import threading

_CUT = threading.local()
_FILE_LOCK = threading.Lock()   # finalize and the overlay thread both add to a cut


def begin():
    """Start collecting for the cut this thread renders now."""
    _CUT.notes = []


def note(stage, msg):
    """Record (and print) one fallback: stage = what, msg = what happened."""
    print(f"        [fallback] {stage}: {msg}")
    notes = getattr(_CUT, "notes", None)
    if notes is not None and f"{stage}: {msg}" not in notes:
        notes.append(f"{stage}: {msg}")


def collect():
    """The notes for this thread's cut (and stop collecting)."""
    notes = getattr(_CUT, "notes", None) or []
    _CUT.notes = None
    return notes


def write(root, cut_id, notes):
    """qc/degraded/<cut>.json, written every run (an empty list = clean)."""
    d = os.path.join(root, "qc", "degraded")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{cut_id}.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(notes, f, indent=1)
    os.replace(tmp, p)
    return p


def append(root, cut_id, notes):
    """Add notes to a cut's record (the overlay pass runs on its own thread,
    after finalize opened the record)."""
    with _FILE_LOCK:
        have = read(root, cut_id) or []
        write(root, cut_id, have + [n for n in notes if n not in have])


def read(root, cut_id):
    """The notes from the last finalize of a cut, or None (not recorded)."""
    try:
        return json.load(open(os.path.join(root, "qc", "degraded", f"{cut_id}.json"),
                              encoding="utf-8"))
    except (OSError, ValueError):
        return None


def gate(root, cut_id):
    """Judge row: PASS when everything the brand asked for rendered."""
    notes = read(root, cut_id)
    if notes is None:
        return ("fallbacks", "SKIP", "not recorded (finalize predates the check)")
    if not notes:
        return ("fallbacks", "PASS", "every configured effect rendered")
    return ("fallbacks", "WARN", "; ".join(notes))
