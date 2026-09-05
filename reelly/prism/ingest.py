"""INGEST: pull a TikTok/Reels/Shorts URL via yt-dlp, or pass a local file
through untouched. Everything lands under a per-video work dir so re-runs
of later stages can reuse what is already there.
"""
import json
import os
import re
import subprocess

from .. import config

URL_RE = re.compile(r"^https?://", re.I)


def is_url(target):
    return bool(URL_RE.match(target))


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:80] or "video"


def workspace(slug, work_root=None):
    root = os.path.join(work_root or config.PRISM_WORK, slug)
    os.makedirs(root, exist_ok=True)
    return root


def fetch(target, work_root=None):
    """Returns (video_path, workdir, source_url_or_None, meta_dict)."""
    if is_url(target):
        return _fetch_url(target, work_root)
    return _fetch_local(target, work_root)


def _fetch_local(path, work_root):
    path = os.path.abspath(path)
    slug = slugify(os.path.splitext(os.path.basename(path))[0])
    root = workspace(slug, work_root)
    dst = os.path.join(root, "source" + os.path.splitext(path)[1])
    if os.path.islink(dst) or os.path.exists(dst):
        os.remove(dst)
    os.symlink(path, dst)
    return dst, root, None, {}


def _fetch_url(url, work_root):
    slug_hint = slugify(url.split("?")[0].rstrip("/").split("/")[-1])
    root = workspace(slug_hint, work_root)
    video_p = os.path.join(root, "source.mp4")
    info_p = os.path.join(root, "source.info.json")
    if os.path.exists(video_p) and os.path.exists(info_p):
        print(f"[skip] download (cached: {os.path.basename(video_p)})")
        return video_p, root, url, json.load(open(info_p))

    print(f"[run ] yt-dlp {url} ...")
    subprocess.run(["yt-dlp", "-f", "mp4/best", "--merge-output-format", "mp4",
                    "--write-info-json", "-o", os.path.join(root, "source.%(ext)s"),
                    url], check=True)
    if not os.path.exists(video_p):
        # yt-dlp may have named the output something else if it wasn't already mp4
        candidates = [f for f in os.listdir(root)
                      if f.startswith("source.") and not f.endswith((".json", ".part"))]
        if candidates:
            os.rename(os.path.join(root, candidates[0]), video_p)
    meta = json.load(open(info_p)) if os.path.exists(info_p) else {}
    return video_p, root, url, meta
