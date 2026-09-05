"""Retention checks: the things short-form algorithms actually weight.

SOURCE
Distilled from public practitioner writing on short-form distribution
and kept only where it is MEASURABLE. Anything that was
account farming (residential proxies, device resets, aged accounts, engagement
groups) is deliberately absent: it is against platform terms and it is not what
this tool does.

WHAT IS WORTH CHECKING, AND WHY

1. VISUAL MONOTONY (`static_stretch`)
   "cut every 2-3 seconds; the viewer's brain reads each cut as new content."
   A held frame is the cheapest way to lose someone, and it is measurable, so
   it is a check rather than advice.

2. LOOPABILITY (`loopable`)
   Rewatch rate is one of TikTok's strongest signals, and a clip whose last
   frame resembles its first gets rewatched without the viewer deciding to.
   Our cards currently end on a held CTA over a different shot, which is the
   opposite. Measured as similarity between first and last frame.

3. ENGAGEMENT BAIT (`bait`)
   Distribution is driven by comments, shares, saves and rewatches, and each
   platform weights them differently: saves lead on Instagram, shares and
   rewatch on TikTok. A cut that asks for nothing gets none of them. We already
   require 2+ engagement handles; this names the specific ask.

DELIBERATELY NOT ADOPTED: the article's length advice ("5-9s or 60+, avoid the
15-45s dead zone"). It contradicts our own P-LEN gate of 20-28s, which came
from a conservative public default. One practitioner's claim does not override
measured results, so it is logged as a hypothesis to test, not a rule.
"""
import os
import subprocess

from . import config

# A held frame past this reads as a stall. The source says cut every 2-3s.
MAX_STATIC_S = 3.5
# How different two samples half a second apart must be to count as movement.
# Tuned so slow Ken Burns drift registers but compression noise does not.
STILL_DIFF = 0.012
# How similar first and last frame must be to call a clip loopable.
LOOP_MAX_DIFF = 0.18

# The ask that actually moves each platform. Saves lead on Instagram; shares
# and rewatches lead on TikTok.
BAIT = {
    # Our own strongest asks are burned into the picture and carry no question
    # mark: "WHAT DO YOU DO", "WHAT CARD ARE YOU PLAYING", "DO YOU OPEN THE
    # DOOR". Matching only on "?" missed every one of them.
    "comment": ("?", "which", "what would", "what do you", "what card",
                "who is", "would you", "do you", "are you", "where would",
                "how would", "pick", "tell me"),
    "share": ("send this", "tag ", "show this"),
    "save": ("save this", "keep this", "steal this"),
    "rewatch": ("watch again", "look closer", "did you catch", "wait for it"),
}


def _samples(path, every=0.5, w=32, h=56):
    """Small grayscale frames at a fixed cadence, as flat pixel lists."""
    import tempfile
    from PIL import Image
    with tempfile.TemporaryDirectory() as td:
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", path,
                        "-vf", f"fps=1/{every},scale={w}:{h}",
                        os.path.join(td, "f%05d.png")], check=True)
        out = []
        for f in sorted(os.listdir(td)):
            out.append(list(Image.open(os.path.join(td, f)).convert("L").getdata()))
    return out


def _diff(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / (len(a) * 255)


def static_stretch(path, duration=None, every=0.5, moved=STILL_DIFF):
    """Longest run where the picture stays effectively the same.

    Sampled over time rather than frame to frame, because our transitions are
    gradual: a 0.6s wipe never makes two ADJACENT frames differ enough to look
    like a cut, so adjacent-frame scene detection reported zero changes on every
    card we own. Comparing across half a second measures what a viewer actually
    perceives, which is whether anything has happened lately.
    """
    frames = _samples(path, every)
    if len(frames) < 2:
        return 0.0, 0.0, 0
    changes = [i for i in range(1, len(frames))
               if _diff(frames[i - 1], frames[i]) > moved]
    marks = [0] + changes + [len(frames) - 1]
    worst_n, at_i = 0, 0
    for a, b in zip(marks, marks[1:]):
        if b - a > worst_n:
            worst_n, at_i = b - a, a
    return worst_n * every, at_i * every, len(changes)


def monotony(path, duration=None, limit=MAX_STATIC_S):
    """Judge check. A held frame is the cheapest way to lose a viewer."""
    worst, at, changes = static_stretch(path, duration)
    msg = f"longest still {worst:.1f}s at {at:.1f}s, {changes} visual changes"
    if worst <= limit:
        return ("visual_monotony", "PASS", msg)
    # WARN, not FAIL: a deliberate held beat can be right, and this should
    # inform the human rather than block a cut that is already working.
    return ("visual_monotony", "WARN", f"{msg} (over {limit}s)")


def loopable(path):
    """Does the last frame resemble the first? Rewatch is a top TikTok signal."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        a = os.path.join(td, "a.png")
        b = os.path.join(td, "b.png")
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", path,
                        "-frames:v", "1", "-vf", "scale=32:56", a], check=True)
        subprocess.run([config.FFMPEG, "-y", "-v", "error", "-sseof", "-0.3",
                        "-i", path, "-frames:v", "1", "-vf", "scale=32:56", b],
                       check=True)
        try:
            from PIL import Image
            pa, pb = Image.open(a).convert("L"), Image.open(b).convert("L")
            px_a, px_b = list(pa.getdata()), list(pb.getdata())
            diff = sum(abs(x - y) for x, y in zip(px_a, px_b)) / (len(px_a) * 255)
        except Exception:
            return ("loopable", "SKIP", "could not compare frames")
    if diff <= LOOP_MAX_DIFF:
        return ("loopable", "PASS", f"ends close to where it starts (diff {diff:.2f})")
    return ("loopable", "WARN",
            f"ends far from where it starts (diff {diff:.2f}); a loop-friendly "
            "ending raises rewatch, which is one of TikTok's strongest signals")


def bait(plan):
    """Does the cut ask for anything? Name which lever it pulls."""
    text = " ".join(str(plan.get(k) or "") for k in ("caption", "cta", "hook"))
    text += " " + " ".join(str(l.get("text") or "")
                           for l in (plan.get("overlay_lines") or []))
    low = text.lower()
    found = [kind for kind, cues in BAIT.items() if any(c in low for c in cues)]
    if found:
        return ("engagement_bait", "PASS", "asks for: " + ", ".join(sorted(found)))
    return ("engagement_bait", "WARN",
            "asks for nothing. Comments, shares, saves and rewatches are what "
            "drive distribution; saves lead on Instagram, shares and rewatch on "
            "TikTok")
