"""Vocabulary corrections applied to ASR word timing.

The public defaults contain only generic technical terms. Add organization- or
product-specific vocabulary in a private fork or a future external vocabulary
file; do not commit client names to the public repository.
"""
import re

TERMS = [
    "Reelly", "microdrama", "vibe coding", "ACES tone mapping", "hitstop",
    "Three.js", "DaVinci Resolve", "bloom", "vignette",
]

# Known generic ASR mishearings. Multi-word keys collapse to one corrected cue
# while preserving the first start time and final end time.
CORRECTIONS = {
    "reely": "Reelly",
    "byte coded": "vibe coded",
    "byte coder": "vibe coder",
    "byte coders": "vibe coders",
    "byte coding": "vibe coding",
    "bite coded": "vibe coded",
    "bite coding": "vibe coding",
    "hit stop": "hitstop",
    "aces toning": "ACES tone mapping",
    "shield on the hold": "shield on the hull",
    "tone maping": "tone mapping",
    "micro drama": "microdrama",
    "microdramas": "microdramas",
}

CASING = {"reelly": "Reelly", "aces": "ACES"}

_SPLIT = re.compile(r"^(\W*)(.*?)(\W*)$", re.S)
_MAX_SPAN = max(len(k.split()) for k in CORRECTIONS)


def _core(token):
    return _SPLIT.match(token).groups()


def correct_word(token):
    """Correct a single-token mishearing or casing slip."""
    pre, core, post = _core(token)
    fixed = CORRECTIONS.get(core.lower()) or CASING.get(core.lower())
    return f"{pre}{fixed}{post}" if fixed else token


def correct_words(words):
    """Correct ``[{t, s, e}]`` entries, including multi-word spans."""
    out, i = [], 0
    while i < len(words):
        matched = False
        for n in range(min(_MAX_SPAN, len(words) - i), 1, -1):
            span = words[i:i + n]
            phrase = " ".join(_core(w["t"])[1] for w in span).lower()
            fixed = CORRECTIONS.get(phrase)
            if fixed:
                pre = _core(span[0]["t"])[0]
                post = _core(span[-1]["t"])[2]
                out.append({**span[0], "t": f"{pre}{fixed}{post}", "e": span[-1]["e"]})
                i += n
                matched = True
                break
        if not matched:
            w = dict(words[i])
            w["t"] = correct_word(w["t"])
            out.append(w)
            i += 1
    return out


def bias_prompt():
    return "Vocabulary: " + ", ".join(TERMS) + "."
