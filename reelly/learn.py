"""M6 learn: close the loop.

Inputs: human verdicts (playbook/feedback/VERDICTS.md) and platform metrics
(metrics.json fetched from analytics export in-session). Output: an evidence report
plus a PLAYBOOK PROPOSAL file — never an automatic playbook edit; reviewer
approves proposals before they merge (internal workflow autonomy rule).

Outlier math is playbook R1: score = views / channel median. Above 10 is a
signal, above 30 is a format to lean on this week.
"""
import datetime
import json
import os
import re
import statistics

from . import direct

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERDICTS = os.path.join(REPO, "playbook", "feedback", "VERDICTS.md")
PROPOSALS = os.path.join(REPO, "playbook", "proposals")


# Every verdict token actually in use in VERDICTS.md. The original alternation
# (KEEP|KILL|FLAW) silently dropped LEARNED/FIXED/ADOPTED lines that were
# already on file, including a validation run whose qualifier
# "(all 30)" broke the old \S* match. CONSTRAINT is the token for structural
# facts ("this account cannot use trending audio") that can never produce
# performance outliers and therefore need their own path to a proposal.
VERDICT_TOKENS = ("KEEP", "KILL", "FLAW", "LEARNED", "FIXED", "ADOPTED",
                  "AUTHORED", "VERDICT", "CONSTRAINT")
_DATED = re.compile(r"\d{4}-\d{2}-\d{2}\s")
_LINE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+(\S+)\s+"
    r"(" + "|".join(VERDICT_TOKENS) + r")"
    r"(?:\(([^)]*)\))?"        # qualifier: "(all 30)", "(fixed coords)" - spaces allowed
    r"(.*?)"                    # optional scope words before 'because'
    r"\s+because\s+(.+)", re.I)


def parse_verdicts_full(path=None):
    """(rows, unparsed). A dated line that fails to parse is REPORTED, never
    silently dropped: a verdict invisible to the learning loop is a verdict
    that never happened."""
    path = path or VERDICTS
    rows, unparsed = [], []
    if not os.path.exists(path):
        return rows, unparsed
    for n, line in enumerate(open(path), 1):
        text = line.strip()
        if not _DATED.match(text):
            continue
        m = _LINE.match(text)
        if m:
            rows.append({"date": m.group(1), "item": m.group(2),
                         "verdict": m.group(3).upper(),
                         "qualifier": (m.group(4) or "").strip(),
                         "scope": (m.group(5) or "").strip(),
                         "why": m.group(6)})
        else:
            unparsed.append({"line": n, "text": text})
    return rows, unparsed


def parse_verdicts(path=None):
    return parse_verdicts_full(path)[0]


def outlier_scores(metrics):
    """metrics: [{clip, platform, views, ...}] (+optional channel_median)."""
    views = [m["views"] for m in metrics if m.get("views")]
    median = (metrics[0].get("channel_median")
              if metrics and metrics[0].get("channel_median")
              else (statistics.median(views) if views else 0))
    out = []
    for m in metrics:
        if not m.get("views") or not median:
            continue
        out.append({**m, "outlier_score": round(m["views"] / median, 1)})
    return sorted(out, key=lambda x: -x["outlier_score"]), median


# Outlier score = views / channel median (playbook R1).
SIGNAL_SCORE = 10.0   # worth surfacing as a signal
ACT_SCORE = 30.0      # worth leaning on this week

# --- Promotion sanity floors -------------------------------------------------
# A metric can only mint a playbook proposal if it clears these floors. They
# exist so a reach spike or a tiny sample can never author a rule on its own.
#
# PLATFORM_MIN_VIEWS: below this a clip is too small to be evidence, whatever
#   its outlier score. A small sample can look like a keeper; the short-video
#   bar sits at 300 views. X is set
#   an order of magnitude higher because an X "view" is an impression (a far
#   cheaper unit than a watched short), so its raw count proves much less.
PLATFORM_MIN_VIEWS = {
    "tiktok": 300,
    "instagram": 300,
    "reels": 300,
    "youtube": 300,
    "shorts": 300,
    "facebook": 300,
    "x": 3000,
}
DEFAULT_MIN_VIEWS = 300

# Engagement rate = engagements / views.
# ER_FLOOR: when a row carries engagement data, an ER under this marks a
#   reach-only anomaly and blocks promotion on every path.
# ER_PROMOTE: at or above this ER a KEEP clip at modest reach earns a proposal
#   on engagement strength alone, with no outlier score required.
ER_FLOOR = 0.02
ER_PROMOTE = 0.05


def _row_er(m):
    """Engagement rate for a metric row, or None when the row carries no
    engagement data (legacy views-only rows). None means 'no ER signal', which
    is deliberately distinct from a measured ER of zero."""
    views = m.get("views") or 0
    if not views or m.get("engagements") is None:
        return None
    return m["engagements"] / views


def _keep_items(verdicts):
    return [v["item"] for v in (verdicts or []) if v.get("verdict") == "KEEP"]


def _slug(s):
    """lowercase, non-alphanumeric runs -> single hyphen, trimmed. Bridges
    verdict slugs ('sample-clip/formula') and the human clip labels scheduler
    returns ('Sample Clip')."""
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _matches_keep(clip, keep_items):
    """A clip matches a KEEP verdict when, after slug-normalising both sides,
    the verdict item (or any of its /-segments) is a substring of the clip
    label or vice versa. Verdict items are slugs while metrics rows carry
    human copy, so exact matching misses every real pairing."""
    clip_slug = _slug(clip)
    if not clip_slug:
        return False
    for item in keep_items:
        candidates = [item] + item.split("/")
        for cand in candidates:
            cs = _slug(cand)
            if cs and (cs in clip_slug or clip_slug in cs):
                return True
    return False


def metric_promotions(metrics, verdicts=None, evidence_fn=None):
    """Playbook-proposal strings earned by platform metrics.

    Two paths, both gated by PLATFORM_MIN_VIEWS and ER_FLOOR:
      * outlier   - score = views / channel median >= SIGNAL_SCORE.
      * engagement - a KEEP clip whose ER >= ER_PROMOTE at modest reach.

    evidence_fn(clip) -> plan-evidence dict (or None) enriches an outlier line
    with its format/hook but is NOT required to promote. The original gate
    refused any outlier that lacked a matching cut plan, so a plain `learn
    --metrics <json>` run (no resolvable project) promoted nothing even at
    score 140 - that is the bug this function fixes."""
    keeps = _keep_items(verdicts)
    scored, _median = outlier_scores(metrics)
    props = []
    for m in scored:
        views = m.get("views") or 0
        platform = (m.get("platform") or "").lower()
        min_views = PLATFORM_MIN_VIEWS.get(platform, DEFAULT_MIN_VIEWS)
        if views < min_views:
            continue
        er = _row_er(m)
        # Reach-only anomaly guard. Only fires when the row actually carries
        # engagement data, so legacy views-only outliers are unaffected.
        if er is not None and er < ER_FLOOR:
            continue
        score = m["outlier_score"]
        clip = m.get("clip", "")
        if score >= SIGNAL_SCORE:
            ev = evidence_fn(clip) if evidence_fn else None
            if ev:
                props.append(
                    f"- PROMOTE (score {score}): format {ev['format']} with hook "
                    f"style '{ev['hook']}' outperformed on {m.get('platform')}; "
                    f"consider making this pairing a default for this content "
                    f"lane. Evidence: {clip}, playbook v{ev['playbook']}.")
            else:
                props.append(
                    f"- PROMOTE (score {score}): {clip} on {m.get('platform')} "
                    f"hit {views} views ({score}x channel median); surface the "
                    f"format/hook it used as a default for this lane.")
        elif er is not None and er >= ER_PROMOTE and _matches_keep(clip, keeps):
            props.append(
                f"- PROMOTE (ER {er * 100:.2f}% at {views} views): {clip} on "
                f"{m.get('platform')} is a KEEP that earned strong engagement at "
                f"modest reach; promote its format on engagement strength rather "
                f"than raw views.")
    return props


def _plan_evidence(root, clip_id, tag=None):
    sfx = f"_{tag}" if tag else ""
    p = os.path.join(root, "edl", f"cut_plans{sfx}.json")
    if not os.path.exists(p):
        return None
    for plan in json.load(open(p)):
        if plan["id"] == clip_id:
            return {"format": plan.get("format"), "hook": plan["hook"]["text"],
                    "duration": plan["duration_s"],
                    "playbook": plan.get("playbook_version"),
                    "because": plan.get("because", [])}
    return None


def run(project=None, metrics_path=None, tag=None):
    today = datetime.date.today().isoformat()
    os.makedirs(PROPOSALS, exist_ok=True)
    L = [f"# Learn report {today}", ""]

    verdicts, unparsed = parse_verdicts_full()
    by_kind = {}
    for v in verdicts:
        by_kind[v["verdict"]] = by_kind.get(v["verdict"], 0) + 1
    counts = " / ".join(f"{n} {k.lower()}" for k, n in sorted(by_kind.items()))
    L += [f"## Verdicts on file: {len(verdicts)} ({counts or 'none'})", ""]
    for v in verdicts[-8:]:
        L.append(f"- {v['date']} {v['item']} {v['verdict']}: {v['why'][:110]}")
    L.append("")

    if unparsed:
        L += [f"## WARNING: {len(unparsed)} verdict-shaped line(s) DID NOT PARSE", "",
              "These lines are invisible to the learning loop until reworded "
              "(`YYYY-MM-DD <item> <VERDICT> because ...`):", ""]
        for u in unparsed:
            L.append(f"- VERDICTS.md:{u['line']}: {u['text'][:110]}")
        L.append("")

    proposals = []
    # Structural constraints bypass the outlier gate: "this account physically
    # cannot use trending audio" will never generate a score >= 10 no matter
    # how true it is. A CONSTRAINT verdict goes straight to a proposal (human
    # approval still required before any rule changes).
    for v in verdicts:
        if v["verdict"] == "CONSTRAINT":
            scope = f" [{v['scope']}]" if v["scope"] else ""
            proposals.append(
                f"- CONSTRAINT ({v['date']} {v['item']}{scope}): {v['why']} "
                f"Promoted without outlier evidence: structural constraints "
                f"never produce outliers, so the score gate does not apply.")
    if metrics_path and os.path.exists(metrics_path):
        metrics = json.load(open(metrics_path))
        scored, median = outlier_scores(metrics)
        L += [f"## Outlier scores (R1; channel median {median:.0f} views)", ""]
        root = direct.resolve_project(project) if project else None
        for m in scored:
            flag = ("ACT-THIS-WEEK" if m["outlier_score"] >= ACT_SCORE
                    else "SIGNAL" if m["outlier_score"] >= SIGNAL_SCORE else "")
            ev = _plan_evidence(root, m.get("clip", ""), tag) if root else None
            line = (f"- {m.get('clip')} on {m.get('platform')}: "
                    f"{m['views']} views, score {m['outlier_score']} {flag}")
            if ev:
                line += f" | format {ev['format']}, hook '{ev['hook']}'"
            L.append(line)
        evidence_fn = (lambda clip: _plan_evidence(root, clip, tag)) if root else None
        proposals += metric_promotions(metrics, verdicts, evidence_fn)
        L.append("")

    if proposals:
        pp = os.path.join(PROPOSALS, f"{today}-proposals.md")
        open(pp, "w").write("\n".join(
            [f"# Playbook proposals {today}", "",
             "Evidence-backed; HUMAN APPROVAL REQUIRED before any rule changes.",
             "", *proposals, ""]))
        L.append(f"## {len(proposals)} playbook proposals -> {pp}")
    else:
        L.append("## No promotion-grade evidence this run "
                 "(need outliers >= 10, a high-ER KEEP clip, or CONSTRAINT "
                 "verdicts)")

    report = "\n".join(L)
    print(report)
    if project:
        root = direct.resolve_project(project)
        os.makedirs(os.path.join(root, "qc"), exist_ok=True)
        open(os.path.join(root, "qc", f"learn_{today}.md"), "w").write(report + "\n")
    return report
