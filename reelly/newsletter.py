"""Substack note per video: a cohesive written piece the editor brain
drafts from the transcript, in creator voice.

Voice rules are CARDINAL: no em dashes, no emojis, no robotic tone.
The engine drafts; a human reads before anything ships (standing rule).
"""
import json
import os

import requests

from . import config, direct, ledger, speech
from .products import PRODUCTS, link

EST_COST = 0.08
MODEL = "gpt-5.6-sol"

PROMPT = """You are writing a Substack note for @creator, a builder-first
account about making things with new creative tech. The author (first person)
recorded a working session; below is the spoken transcript and topic map.

Write a cohesive note (350-550 words) that:
- opens with a concrete hook from the session (a moment, not a thesis)
- tells what was made or explored, honestly, including friction and surprises
- lands 2-3 useful takeaways a maker can apply
- ends with one genuine question to the reader, then the link line verbatim:
  {link_line}

VOICE (cardinal, never break): no em dashes anywhere, no emojis, no hype
clichés, no robotic tone. Short sentences welcome. First person, warm,
specific. Do not invent events not in the transcript.

Also produce a title (max 60 chars, no colons-as-crutch) and a one-line
subtitle.

TOPIC MAP:
{topics}

TRANSCRIPT:
{transcript}

Return ONLY JSON: {{"title": "...", "subtitle": "...", "body_markdown": "..."}}"""


def run(project, tag=None, product="video"):
    root = direct.resolve_project(project)
    name = os.path.basename(root)
    words = speech.words_from(os.path.join(root, "analysis", "words.json"))
    text = " ".join(w["t"] for w in words)[:24000]
    tp = os.path.join(root, "analysis", "topics.json")
    topics = json.load(open(tp)) if os.path.exists(tp) else []
    tmap = "\n".join(f"- {c['text'][:100]}" for c in topics[:25])
    p = PRODUCTS[product]
    link_line = f"Built live with {p['name']}: {link(product, 'substack')}"

    ledger.check(EST_COST)
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {config.provider_key('openai')}"},
        json={"model": MODEL,
              "messages": [{"role": "user", "content": PROMPT.format(
                  link_line=link_line, topics=tmap, transcript=text)}],
              "response_format": {"type": "json_object"}},
        timeout=300)
    ledger.add("gpt-newsletter", name, EST_COST, name)
    d = json.loads(r.json()["choices"][0]["message"]["content"])

    # cardinal voice gates, enforced mechanically
    body = d.get("body_markdown", "").replace("—", ", ").replace("–", ", ")
    title = d.get("title", name).replace("—", ", ")

    out = os.path.join(root, "deliverables", "substack")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, "NEWSLETTER.md")
    open(path, "w").write("\n".join([
        f"# {title}", "", f"*{d.get('subtitle', '')}*", "", body, "",
        "---", "DRAFT by Reelly; human review required before publishing.", ""]))
    print(f"[note ] -> {path} ('{title}')")
    return path
