"""ANALYZE: send the video (frames+transcript+OCR, or native video) to an LLM
and get back a structured read on why the video works. Claude frames mode is
the default and must work standalone; Gemini native-video mode is opt-in via
--engine gemini and only used if GEMINI_API_KEY is set.
"""
import base64
import json
import os
import time

from .. import config

SCHEMA_FIELDS = ("hook_description", "overlay_text", "visual_style", "pacing",
                  "cta", "why_it_performed", "replicable_formula")

PROMPT = """You are a short-form video strategist analyzing ONE TikTok/Reels/Shorts video \
to reverse-engineer why it performs (or would perform) well, so a creator can replicate the \
technique on a new video.

Context you're given:
- Frames from the video (hook-dense timestamps near the start, plus the midpoint and final second), OR the full video itself.
- A transcript of the spoken audio (may be "transcript unavailable" if speech-to-text wasn't possible).
- OCR text detected in on-screen overlays/captions (may say no text was detected).
- The caption and any performance metrics posted alongside the video.
- Scene-cut timestamps, so you can reason about pacing (cuts per second).

Caption: {caption}
Metrics: {metrics}
Transcript: {transcript}
On-screen OCR text: {ocr_text}
Duration: {duration:.1f}s. Scene cuts: {n_cuts} over {duration:.1f}s (~{cuts_per_sec:.2f} cuts/sec).

Return ONLY a JSON object with exactly these keys:
- "hook_description": what happens in the first 1-2 seconds and why it stops the scroll
- "overlay_text": summary of the on-screen text/captions and how they're used
- "visual_style": camera work, editing style, color/aesthetic notes
- "pacing": description of cut frequency/rhythm, referencing the cuts/sec figure
- "cta": the call to action, implicit or explicit (or "none" if there isn't one)
- "why_it_performed": the core reasons this video works (or would work) for its audience
- "replicable_formula": a concise, numbered recipe another creator could follow to make a similar video
"""


def _prompt(caption, metrics, transcript, ocr_text, duration, scene_cuts):
    n_cuts = len(scene_cuts)
    cuts_per_sec = n_cuts / duration if duration else 0.0
    return PROMPT.format(caption=caption or "(none provided)",
                          metrics=metrics or "(none provided)",
                          transcript=transcript, ocr_text=ocr_text,
                          duration=duration, n_cuts=n_cuts, cuts_per_sec=cuts_per_sec)


def _parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {}
    return {k: data.get(k, "") for k in SCHEMA_FIELDS}


def analyze_claude(frames, caption, metrics, transcript, ocr_text, duration, scene_cuts, model=None):
    import anthropic

    api_key = config.get_key("anthropic")
    if not api_key:
        raise RuntimeError(config.missing_key_message("anthropic"))
    client = anthropic.Anthropic(api_key=api_key)

    content = [{"type": "text", "text": _prompt(caption, metrics, transcript, ocr_text, duration, scene_cuts)}]
    for f in frames:
        with open(f["path"], "rb") as fh:
            b64 = base64.standard_b64encode(fh.read()).decode()
        content.append({"type": "text", "text": f"Frame at {f['t']}s:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})

    resp = client.messages.create(
        model=model or config.CLAUDE_MODEL, max_tokens=2000,
        messages=[{"role": "user", "content": content}])
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    result = _parse_json(text)
    result["engine"] = "claude"
    return result


def analyze_gemini(video, caption, metrics, transcript, ocr_text, duration, scene_cuts, model=None):
    from google import genai

    api_key = config.get_key("google-genai")
    if not api_key:
        raise RuntimeError(config.missing_key_message("google-genai"))
    client = genai.Client(api_key=api_key)

    f = client.files.upload(file=video)
    t0 = time.monotonic()
    while str(getattr(f.state, "name", f.state)) not in ("ACTIVE", "FAILED"):
        if time.monotonic() - t0 > 300:
            # the poll used to be unbounded: a wedged upload hung prism forever
            raise RuntimeError("Gemini file upload stuck in "
                               f"{getattr(f.state, 'name', f.state)} for 300s")
        time.sleep(2)
        f = client.files.get(name=f.name)
    if str(getattr(f.state, "name", f.state)) == "FAILED":
        raise RuntimeError("Gemini file upload failed")

    prompt = _prompt(caption, metrics, transcript, ocr_text, duration, scene_cuts)
    resp = client.models.generate_content(model=model or config.GEMINI_MODEL, contents=[f, prompt])
    result = _parse_json(resp.text)
    result["engine"] = "gemini"
    return result


def run(engine, video, frames, caption, metrics, transcript, ocr_text, duration, scene_cuts):
    if engine == "gemini":
        if not config.get_key("google-genai"):
            raise RuntimeError(config.missing_key_message("google-genai"))
        return analyze_gemini(video, caption, metrics, transcript, ocr_text, duration, scene_cuts)
    return analyze_claude(frames, caption, metrics, transcript, ocr_text, duration, scene_cuts)
