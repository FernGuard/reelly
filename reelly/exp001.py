"""EXP-001 blind pool builder: shuffle both brains' previews under neutral
names; the model map is sealed in qc/ and only opened after verdicts."""
import json
import os
import random
import shutil
import sys

from . import direct


def build(project, tags=("", "gpt")):
    root = direct.resolve_project(project)
    pool = os.path.join(root, "deliverables", "cuts", "sample-exp-blind")
    shutil.rmtree(pool, ignore_errors=True)
    os.makedirs(pool)
    entries = []
    for tag in tags:
        sfx = f"_{tag}" if tag else ""
        d = os.path.join(root, "deliverables", "cuts", f"previews{sfx}")
        brain = "gpt" if tag == "gpt" else "gemini"
        # only cuts in the CURRENT plan file; stale renders from older plan
        # sets may still sit in the previews dir
        plans = json.load(open(os.path.join(root, "edl", f"cut_plans{sfx}.json")))
        for p in plans:
            f = f"{p['id']}.mp4"
            src = os.path.join(d, f)
            if os.path.exists(src):
                entries.append({"brain": brain, "src": src, "cut": f,
                                "title": p.get("title", "")})
    random.shuffle(entries)
    key = []
    for i, e in enumerate(entries, 1):
        blind = f"clip_{i:02d}.mp4"
        shutil.copy2(e["src"], os.path.join(pool, blind))
        key.append({"blind": blind, "brain": e["brain"], "cut": e["cut"]})
    keyfile = os.path.join(root, "qc", "sample-exp-key.json")
    json.dump(key, open(keyfile, "w"), indent=1)
    print(f"{len(key)} blind clips -> {pool}")
    print(f"sealed key -> {keyfile} (do not open before verdicts)")


def score(project, verdicts):
    """Unseal the key and score. verdicts: {\"clip_01\": (\"KEEP\", \"why\"), ...}"""
    root = direct.resolve_project(project)
    key = json.load(open(os.path.join(root, "qc", "sample-exp-key.json")))
    by = {}
    rows = []
    for k in key:
        v, why = verdicts.get(k["blind"].replace(".mp4", ""), ("SKIP", ""))
        b = by.setdefault(k["brain"], {"keep": 0, "kill": 0})
        if v.upper() == "KEEP":
            b["keep"] += 1
        elif v.upper() == "KILL":
            b["kill"] += 1
        rows.append(f"{k['blind']}  {k['brain']:6}  {k['cut']}  "
                    f"{k.get('title', ''):40.40}  {v.upper():4}  {why}")
    lines = ["EXP-001 unsealed:", *rows, ""]
    for brain, b in sorted(by.items()):
        n = b["keep"] + b["kill"]
        rate = b["keep"] / n if n else 0
        lines.append(f"{brain}: {b['keep']}/{n} kept ({rate:.0%})")
    return "\n".join(lines)


if __name__ == "__main__":
    build(sys.argv[1])
