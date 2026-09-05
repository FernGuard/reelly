"""PRISM CLI: python -m reelly.prism <url-or-mp4> [--csv analytics.csv] [--engine claude|gemini] [--out reports/]

Breaks one short-form video into hook, pacing, overlay text, CTA, and a
replicable formula: ffmpeg pulls hook-dense frames + scene cuts + audio,
mlx-whisper/OCR add transcript and on-screen text (both optional, skipped
gracefully if unavailable), and an LLM (Claude by default, Gemini native-video
on request) turns it all into a structured read.
"""
import argparse
import json
import os

from . import analyze as analyze_mod
from . import artifacts, ingest
from . import ocr as ocr_mod
from . import report
from . import transcribe as transcribe_mod


def run(target, csv_path=None, engine="claude", out_dir="reports", work_root=None, force=False):
    video, work_dir, source_url, meta = ingest.fetch(target, work_root)
    slug = os.path.basename(work_dir)
    caption = meta.get("description") or meta.get("title") or ""
    print(f"[prism] {slug}: {video}")

    print("[run ] artifacts (frames, scene cuts, audio) ...")
    art = artifacts.build(video, work_dir, force=force)

    words_p = os.path.join(work_dir, "transcript.json")
    words = None if force else transcribe_mod.load_cached(words_p)
    if words is None:
        words = transcribe_mod.transcribe(art["audio_wav"], words_p)
    transcript_text = transcribe_mod.text_of(words)

    ocr_p = os.path.join(work_dir, "ocr.json")
    ocr_results = None if force else ocr_mod.load_cached(ocr_p)
    if ocr_results is None:
        ocr_results = ocr_mod.ocr_frames(art["frames"], out_json=ocr_p)
    ocr_text = ocr_mod.combined_text(ocr_results)

    metrics_row = report.match_csv(csv_path, source_url) if csv_path else None
    metrics = report.summarize_metrics(metrics_row)

    print(f"[run ] analyze ({engine}) ...")
    analysis = analyze_mod.run(engine, video, art["frames"], caption,
                               json.dumps(metrics) if metrics else None,
                               transcript_text, ocr_text, art["duration"], art["scene_cuts"])

    data = report.build(slug, source_url, video, caption, art, transcript_text, ocr_text, analysis, metrics)
    json_p, md_p = report.write(out_dir, slug, data)
    print(f"[done] report: {md_p}")
    return data


def main():
    ap = argparse.ArgumentParser(prog="python -m reelly.prism",
                                 description="Analyze one TikTok/Reels/Shorts video (or local mp4).")
    ap.add_argument("target", help="TikTok/Reels/Shorts URL, or a path to a local .mp4")
    ap.add_argument("--csv", help="scheduler Post Performance export to pull Views/Engagements/rate from")
    ap.add_argument("--engine", choices=["claude", "gemini"], default="claude",
                    help="claude analyzes extracted frames (default, works standalone); "
                         "gemini uploads the mp4 for native video analysis if GEMINI_API_KEY is set")
    ap.add_argument("--out", default="reports", help="report output directory (default: reports/)")
    ap.add_argument("--work-root", help="work dir root (default: config.PRISM_WORK)")
    ap.add_argument("--force", action="store_true", help="ignore cached artifacts/transcript")
    args = ap.parse_args()
    run(args.target, csv_path=args.csv, engine=args.engine, out_dir=args.out,
        work_root=args.work_root, force=args.force)


if __name__ == "__main__":
    main()
