"""Post-verdict delivery: turn screened plans into the set that actually ships.

WHY THIS STAGE EXISTS (gap 2026-07-31)
After screening, someone had to hand-delete the killed cuts, hand-renumber the
survivors, and hand-fix DESCRIPTION/REVIEW to match -- and the hand-renaming
silently broke the link between delivered files and edl/cut_plans.json. Every
step of that is mechanical, so the machine does it and WRITES THE MAPPING:

  - reads each plan's `screened` verdict (safety.py: clean = KEEP, a recorded
    reason = KILL, absent = not ready);
  - refuses to deliver while any cut is unscreened (a delivery stage must not
    guess a verdict);
  - copies only the KEEPs, only in the account's chosen variants, renumbered
    into a contiguous set under deliverables/delivery/;
  - regenerates each DESCRIPTION.md for the new numbering and writes
    DELIVERY.md plus mapping.json so delivered names trace back to plans.

Killed cuts are archived by leaving deliverables/final/ untouched: the ship
set is a separate directory, so nothing is destroyed and nothing dead ships.
"""
import json
import os
import shutil

from . import accounts, direct, products


def _verdict(plan):
    """('keep'|'kill'|'unscreened', detail) from the screened block."""
    s = plan.get("screened")
    if not s:
        return "unscreened", "no screened verdict on the plan"
    if isinstance(s, str):
        s = {"verdict": s}
    v = str(s.get("verdict", "")).strip().lower()
    if not v:
        return "unscreened", "screened block present but no verdict"
    if v.startswith("clean") or v.startswith("keep"):
        return "keep", s.get("verdict")
    return "kill", s.get("verdict")


def run(project, tag=None, product="video", account=None, variants=None,
        targets_for=None):
    root = direct.resolve_project(project)
    profile = accounts.for_project(root, account)
    wanted = accounts.variants_for(root, profile, variants)
    targets = products.delivery_targets(root, targets_for)
    sfx = f"_{tag}" if tag else ""
    plans = json.load(open(os.path.join(root, "edl", f"cut_plans{sfx}.json")))
    src = os.path.join(root, "deliverables", f"final{sfx}")
    out = os.path.join(root, "deliverables", f"delivery{sfx}")

    keeps, kills, unscreened = [], [], []
    for p in plans:
        v, detail = _verdict(p)
        {"keep": keeps, "kill": kills, "unscreened": unscreened}[v].append((p, detail))
    if unscreened:
        names = ", ".join(p["id"] for p, _ in unscreened)
        raise SystemExit(
            f"deliver: {len(unscreened)} cut(s) not screened yet ({names}). "
            f"Watch each end to end and record plan['screened'] "
            f"(P-SCREEN); delivery does not guess verdicts.")
    if not keeps:
        raise SystemExit("deliver: no KEEP verdicts; nothing to ship.")

    os.makedirs(out, exist_ok=True)
    mapping = {"account": profile["name"], "variants": wanted,
               "targets": targets, "delivered": {}, "killed": []}
    missing = []
    for i, (p, _) in enumerate(keeps, 1):
        new_id = f"cut_{i:02d}"
        files = []
        for v in wanted:
            s = accounts.suffix(v)
            src_f = os.path.join(src, f"{p['id']}{s}.mp4")
            if not os.path.exists(src_f):
                missing.append(f"{p['id']}{s}.mp4 (variant {v})")
                continue
            dst_f = os.path.join(out, f"{new_id}{s}.mp4")
            shutil.copy2(src_f, dst_f)
            files.append(os.path.basename(dst_f))
        renamed = dict(p, id=new_id)
        products.description_md(product, renamed,
                                os.path.join(out, f"{new_id}_DESCRIPTION.md"),
                                targets=targets, account=profile,
                                variants=wanted)
        mapping["delivered"][new_id] = {
            "source_cut": p["id"], "title": p.get("title"), "files": files,
            "screened": p.get("screened")}
        print(f"[deliver] {new_id} <- {p['id']}: {len(files)} file(s)")
    for p, detail in kills:
        mapping["killed"].append({"cut": p["id"], "why": detail})
        print(f"[deliver] killed {p['id']}: {detail}")
    if missing:
        raise SystemExit(
            "deliver: missing variant file(s): " + ", ".join(missing) +
            ". Re-run `reelly cut`/`finalize` with these variants first; "
            "a partial delivery set must not look complete.")

    json.dump(mapping, open(os.path.join(out, "mapping.json"), "w"), indent=1)
    L = [f"# Delivery: {os.path.basename(root)}", "",
         f"Account **{profile['name']}** | variants: {', '.join(wanted)} | "
         f"targets: {', '.join(targets)}", "",
         f"{len(keeps)} cut(s) shipped, {len(kills)} killed (left in "
         f"`deliverables/final{sfx}/`, nothing deleted).", ""]
    for new_id, m in mapping["delivered"].items():
        L += [f"## {new_id} — {m['title']}  (was {m['source_cut']})", ""]
        L += [f"- `{f}`" for f in m["files"]]
        L += [f"- Posting block: `{new_id}_DESCRIPTION.md`", ""]
    if kills:
        L += ["## Killed", ""]
        L += [f"- {k['cut']}: {k['why']}" for k in mapping["killed"]]
        L.append("")
    L += ["Provenance: `mapping.json` links every delivered file to its plan "
          "in `edl/cut_plans%s.json`." % sfx, ""]
    open(os.path.join(out, "DELIVERY.md"), "w").write("\n".join(L))
    print(f"[deliver] {len(keeps)} shipped, {len(kills)} killed -> {out}")
    return mapping
