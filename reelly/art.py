"""Generate stills for cards that have no truthful source footage.

A card may only show media it is authorized to represent. Generated art made
for the card can be used when no suitable owned source exists, provided its
provenance and disclosure requirements are recorded.

WHAT THESE PROMPTS MUST NEVER PRODUCE
- Text, UI or logos. The card has its own type; generated lettering fights it and
  generated UI would show branding we do not control.
- Weapons or blood unless the workflow explicitly allows them.
- A recognisable real person or real product.
"""
import os
import time

import requests

from . import config, ledger

EST_IMAGE = 0.04
ENDPOINT = "fal-ai/flux/dev"

# Bolted onto every prompt. Models drift toward putting words on screen the
# moment a prompt mentions editing, screens or timelines, and a card with
# generated gibberish text on it is unusable.
NO_TEXT = ("No text, no words, no letters, no captions, no watermarks, no user "
           "interface, no logos, no brand marks. No weapons, no blood.")


def _headers():
    return {"Authorization": f"Key {config.provider_key('fal-ai')}",
            "Content-Type": "application/json"}


def _find_image_url(d):
    if isinstance(d, dict):
        for v in d.values():
            u = _find_image_url(v)
            if u:
                return u
    elif isinstance(d, list):
        for v in d:
            u = _find_image_url(v)
            if u:
                return u
    elif isinstance(d, str) and d.startswith("http") and d.split("?")[0].endswith(
            (".png", ".jpg", ".jpeg", ".webp")):
        return d
    return None


def make(prompt, out, project="", size="portrait_16_9", seed=None):
    """Generate one still. Cached on disk: the same card re-renders for free."""
    if os.path.exists(out):
        return out
    ledger.check(EST_IMAGE)
    payload = {"prompt": f"{prompt} {NO_TEXT}", "image_size": size,
               "num_images": 1, "enable_safety_checker": True}
    if seed is not None:
        payload["seed"] = seed
    r = requests.post(f"https://queue.fal.run/{ENDPOINT}", headers=_headers(),
                      json=payload, timeout=60)
    r.raise_for_status()
    d = r.json()
    for _ in range(150):
        time.sleep(2)
        s = requests.get(d["status_url"], headers=_headers(), timeout=30).json()
        if s.get("status") == "COMPLETED":
            break
        if s.get("status") in ("FAILED", "ERROR"):
            raise RuntimeError(f"fal image failed: {s}")
    res = requests.get(d["response_url"], headers=_headers(), timeout=60).json()
    url = _find_image_url(res)
    if not url:
        raise RuntimeError(f"no image url in fal response: {str(res)[:200]}")
    ledger.add("fal-image", f"art {os.path.basename(out)}", EST_IMAGE, project)
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as rr:
        rr.raise_for_status()
        with open(out, "wb") as f:
            for chunk in rr.iter_content(1 << 16):
                f.write(chunk)
    return out
