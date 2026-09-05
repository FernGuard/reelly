"""OCR: text overlays baked into each frame (captions, hook text, CTAs).
Prefers macOS Vision (fast, on-device, no extra binary); falls back to
pytesseract; skips gracefully if neither is installed.
"""
import json
import os


def _engine():
    try:
        import Vision  # noqa: F401
        return "vision"
    except ImportError:
        pass
    try:
        import pytesseract  # noqa: F401
        return "tesseract"
    except ImportError:
        pass
    return None


def _vision_text(path):
    import Vision
    from Foundation import NSURL

    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
        NSURL.fileURLWithPath_(path), None)
    request = Vision.VNRecognizeTextRequest.alloc().init()
    ok, _ = handler.performRequests_error_([request], None)
    if not ok:
        return ""
    lines = []
    for obs in request.results():
        candidates = obs.topCandidates_(1)
        if candidates:
            lines.append(str(candidates[0].string()))
    return "\n".join(lines)


def _tesseract_text(path):
    import pytesseract
    from PIL import Image
    return pytesseract.image_to_string(Image.open(path)).strip()


def load_cached(path):
    """Previously written ocr.json, or None. The frame set for a slug is
    deterministic, so a cache hit skips the whole per-frame OCR pass (which
    used to re-run on every invocation)."""
    if not os.path.exists(path):
        return None
    try:
        data = json.load(open(path))
        return data if isinstance(data, list) else None
    except (ValueError, OSError):
        return None


def ocr_frames(frames, out_json=None):
    """[{t, path, text}] — one OCR pass per artifact frame."""
    engine = _engine()
    if engine is None:
        print("[skip] ocr (neither pyobjc Vision nor pytesseract installed)")
        results = [{"t": f["t"], "path": f["path"], "text": ""} for f in frames]
    else:
        reader = _vision_text if engine == "vision" else _tesseract_text
        results = []
        for f in frames:
            try:
                text = reader(f["path"])
            except Exception as e:  # noqa: BLE001 - OCR must never break the pipeline
                print(f"[warn] ocr failed on {f['path']}: {e}")
                text = ""
            results.append({"t": f["t"], "path": f["path"], "text": text})
    if out_json:
        json.dump(results, open(out_json, "w"), indent=1)
    return results


def combined_text(results):
    seen, lines = set(), []
    for r in results:
        t = (r.get("text") or "").strip()
        if t and t not in seen:
            seen.add(t)
            lines.append(f"[{r['t']}s] {t}")
    return "\n".join(lines) if lines else "no on-screen text detected"
