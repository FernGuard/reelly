"""REPORT: human markdown + machine JSON, optionally cross-referenced against
a scheduler Post Performance CSV export.
"""
import csv
import json
import os


def _strip_query(url):
    return url.split("?")[0].rstrip("/") if url else url


def match_csv(csv_path, url):
    """The matching scheduler export row for `url`, or None (no csv, no url, no match)."""
    if not csv_path or not url:
        return None
    target = _strip_query(url)
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            link = row.get("Link") or row.get("link")
            if link and _strip_query(link) == target:
                return row
    return None


def summarize_metrics(row):
    if not row:
        return None
    views = row.get("Views") or row.get("Video Views")
    engagements = row.get("Engagements")
    rate = next((v for k, v in row.items() if "rate" in k.lower() and v), None)
    if rate is None and views and engagements:
        try:
            rate = round(float(engagements) / float(views) * 100, 2)
        except (ValueError, ZeroDivisionError):
            rate = None
    return {"views": views, "engagements": engagements, "rate": rate}


def build(slug, source_url, video, caption, artifacts, transcript_text, ocr_text, analysis, metrics=None):
    duration = artifacts["duration"]
    cuts = artifacts["scene_cuts"]
    return {
        "slug": slug,
        "source_url": source_url,
        "video": video,
        "caption": caption,
        "duration_s": round(duration, 2),
        "scene_cuts": cuts,
        "cuts_per_sec": round(len(cuts) / duration, 3) if duration else 0.0,
        "transcript": transcript_text,
        "ocr_text": ocr_text,
        "metrics": metrics,
        "analysis": analysis,
    }


def _render_md(data):
    a = data["analysis"]
    lines = [f"# PRISM report: {data['slug']}", ""]
    if data["source_url"]:
        lines.append(f"Source: {data['source_url']}")
    lines.append(f"Duration: {data['duration_s']}s | "
                 f"{len(data['scene_cuts'])} scene cuts (~{data['cuts_per_sec']} cuts/sec)")
    if data["caption"]:
        lines.append(f"Caption: {data['caption']}")
    m = data["metrics"]
    if m:
        lines.append(f"Performance: {m.get('views', '?')} views, "
                     f"{m.get('engagements', '?')} engagements, {m.get('rate', '?')} engagement rate")
    lines.append("")

    for key, title in [("hook_description", "Hook"), ("overlay_text", "Overlay text"),
                        ("visual_style", "Visual style"), ("pacing", "Pacing"),
                        ("cta", "CTA"), ("why_it_performed", "Why it performed"),
                        ("replicable_formula", "Replicable formula")]:
        lines += [f"## {title}", "", str(a.get(key, "")), ""]

    lines += ["## Transcript", "", data["transcript"], ""]
    lines += ["## On-screen OCR text", "", data["ocr_text"], ""]
    return "\n".join(lines) + "\n"


def write(out_dir, slug, data):
    os.makedirs(out_dir, exist_ok=True)
    json_p = os.path.join(out_dir, f"{slug}.json")
    md_p = os.path.join(out_dir, f"{slug}.md")
    json.dump(data, open(json_p, "w"), indent=2)
    open(md_p, "w").write(_render_md(data))
    return json_p, md_p
