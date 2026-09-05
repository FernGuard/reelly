"""Sizzle reels: a montage cut from a POOL of clips, not from one recording.

WHY THIS EXISTS
`direct`/`cut` are session editors. They take one analysed recording and find
the moments inside it. A studio sizzle is the other shape entirely: many
finished artifacts, none of them the "source recording", assembled into a
30-second argument for a product. Before this module the only way to build one
was a hand-authored ffmpeg script with the shot list typed in by a human
(a one-off ffmpeg shot list). That workflow does not belong in the engine, so
the capability lives here instead.

WHAT IT ADDS THAT A SHOT LIST CANNOT
1. A CLEARANCE PASS (`survey`). Every candidate clip is looked at by a vision
   model before it can be planned, and it answers the two questions that have
   burned this content op before: does the frame carry RETIRED branding
   (retired brand chrome),
   and does it carry weapons or blood (a standing reach penalty). Both are
   HARD EXCLUSIONS here, in the engine, so a future sizzle cannot quietly
   reintroduce them the way a typed shot list can.
2. A BRAIN that authors the shot order and the copy together under the copy
   contract, linted by brandkit before a frame is rendered.
3. HOUSE GRAMMAR in the assembler. Off-format sources are not letterboxed and
   not cropped to death: they sit sharp and centred on a blurred fill of
   themselves.
4. An ENDING THAT IS A SEGMENT. The endcard is appended after the last content
   frame, never composited on top of it, so "the card ate the payoff" is
   structurally impossible (the designed-endings rule).
"""
import concurrent.futures
import json
import os
import re
import subprocess
import tempfile
import time

from . import audio_post, brandkit, config, face, ledger, media, products

FPS = 30
EST_SURVEY = 0.02          # one vision call per clip, few frames each
SURVEY_FRAMES = 6
GRAMMAR_BLUR = 42          # sigma of the fill blur behind off-format sources
FILL_TOLERANCE = 0.34      # how far off 16:9 a source may be and still fill
OUTRO_S = 4.0              # the appended GENERATED end sequence; the CTA
                           # is typeset into it and needs time to read

# Seedance 2.5 (reviewer, 2026-08-10), image-to-video: one text-free still from
# `art` animated into the bed. NOTE the id has no "fal-ai/" prefix and 2.5 has
# no "fast" tier -- `fal-ai/bytedance/seedance-2.5/fast/reference-to-video`
# submits fine and then 404s at poll time ("Path ... not found"), so a wrong id
# here fails late, not at submit.
# image-to-video, not reference-to-video: the input IS the first frame, which
# is what design-then-animate means. reference-to-video is for supplying
# subject/style references instead.
CARD_ENDPOINT = "bytedance/seedance-2.5/image-to-video"
EST_CARD = 2.50


# One concept per studio, and they must NOT rhyme: a title card that could be
# swapped between products is not a title card for either of them. Each says
# what that studio makes, in a picture, before a word is on screen.
# All are TEXT-FREE by construction (M7): the model never renders type, and the
# wordmark is composited afterwards from the registered logo file.
# Beat 2 of the spine, in the product's own words. Plain nouns a stranger
# already understands -- never our internal vocabulary.
MAKES = {"video": "AI video shows, episode by episode",
         "story": "interactive stories people play and choose their way through",
         "adventure": "playable adventure games",
         "games": "playable games"}

CARD_CONCEPTS = {
    "video": {
        "still": "A dark film sound stage the instant it comes alive: banks of "
                 "cinema lights snapping on in sequence down a long truss, "
                 "atmospheric haze catching every beam, a camera crane arm "
                 "sweeping through the light. Deep blue shadows, warm key "
                 "light, anamorphic flare, shallow depth of field, cinematic.",
        "motion": "The lights continue igniting one after another down the "
                  "truss as the camera cranes smoothly forward through the "
                  "haze; flares bloom and drift. Confident, accelerating.",
    },
    "story": {
        "still": "An open illustrated book on a dark table, its pages lifting "
                 "and unfolding upward into a miniature painted world that "
                 "grows out of the paper: lantern-lit stone alleys, hanging "
                 "cloth, still water. Ink blooming outward into colour. Warm "
                 "amber against deep teal, volumetric light, cinematic.",
        "motion": "The pages keep unfolding and the little world rises and "
                  "expands out of the book toward the camera as it pushes "
                  "slowly in; ink washes bloom into full colour.",
    },
    "adventure": {
        "still": "A tall doorway of warm light opening in a dark painted "
                 "corridor, and through it layered painted worlds receding "
                 "into the distance, each one a different place and season. "
                 "Dust motes, heavy volumetric shafts, rich saturated colour, "
                 "painterly, cinematic.",
        "motion": "The doorway swings wider and the camera moves through it "
                  "into the layered worlds beyond, light sweeping across as "
                  "each further world resolves. Awe, forward momentum.",
    },
    "games": {
        "still": "A dark arcade coming to life, cabinet screens lighting one "
                 "by one down a long row, reflections on a glossy floor, "
                 "coloured light spill. Cinematic, shallow depth of field.",
        "motion": "Screens keep lighting down the row as the camera tracks "
                  "smoothly past them, reflections streaking on the floor.",
    },
}


def sh(*args):
    return subprocess.run(list(args), capture_output=True, text=True)


# ---------------------------------------------------------------- clearance

SURVEY_PROMPT = """You are clearing footage for a 30-second product sizzle reel
for {studio}, a creation tool on example.invalid. You are shown {n} frames sampled
evenly across ONE clip, in order.

Answer about the clip as a whole, as JSON:

{{"summary": "one sentence, concrete, what is actually on screen",
  "subject": "2-4 words naming the thing being shown (a world, a show, a
              character, a scene) -- this may be burned on screen as a label,
              so it must be a proper name or a plain noun phrase, never a
              sentence and never our internal vocabulary",
  "kind": "output" | "interface" | "mixed",
  "beauty": 0-10,
  "motion": 0-10,
  "retired_branding": true/false,
  "retired_branding_note": "what you saw, or empty",
  "weapons_or_blood": true/false,
  "weapons_or_blood_note": "what you saw, or empty",
  "face_overlay": true/false,
  "best_frame": <1-based index of the strongest single frame>}}

Definitions you must apply strictly:
- "output" = the thing the tool MADE (a playing scene, generated art, a game
  world). "interface" = the tool's own UI, chrome, dashboards, browser tabs.
- retired_branding: the wordmarks "OldBrand" and "FormerBrand" are RETIRED product
  names. If either appears ANYWHERE in the frame, including a browser tab, a
  page header or a favicon, this is true. The current names are Video Project
  and Story Project.
- weapons_or_blood: any firearm, drawn blade, explosion aimed at a person, or
  visible blood/gore, including on an item card or inventory screen.
- face_overlay: a webcam/facecam picture-in-picture of a real person.
- beauty: how good this looks as a hero shot, not how useful it is.
"""


MAX_WINDOW = 120.0         # a pool entry never covers more than this

# How a pool entry is recognised as example.invalid catalogue footage (beat 1)
# rather than one studio's own output. Path-based on purpose: the vision pass
# describes these as plain interface, which is exactly why it cannot be the
# thing that identifies them.
CATALOGUE_MARK = "/catalogue/t"


def windows(clip, span=MAX_WINDOW):
    """A long recording is not one candidate, it is many.

    Six frames spread over a 32-minute world recording describe nothing and
    the "best moment" they point at is noise. Anything longer than `span` is
    therefore entered into the pool as consecutive windows, each surveyed and
    planned as its own candidate shot.
    """
    dur = media.duration(clip)
    if dur <= span * 1.35:
        return [(clip, 0.0, round(dur, 2))]
    n = int(round(dur / span))
    step = dur / n
    return [(clip, round(i * step, 2), round(min((i + 1) * step, dur), 2))
            for i in range(n)]


def _crop_vf(crop):
    """`W:H:X:Y` -> an ffmpeg crop filter, or '' when no crop is set.

    The crop is applied to the SURVEY frames as well as to the render, which
    is the point: the clearance pass then judges the frame that will actually
    ship. Cropping a player out of its app chrome and calling the branding
    handled is a claim; letting the vision gate re-check the cropped frame is
    a verification.
    """
    return f"crop={crop}," if crop else ""


def crop_map(specs):
    """--crop values -> a function (path) -> crop or None.

    A real pool is mixed. The Video Project pool is screen recordings of the
    player, which need the app chrome cropped away, PLUS finished episode
    files that are already bare video and would be destroyed by the same
    crop. So a crop is a rule about WHICH sources it applies to, not one
    global rectangle: `--crop W:H:X:Y` still means all of them, and
    `--crop 'SUBSTR=W:H:X:Y'` means only paths containing SUBSTR.
    """
    rules, default = [], None
    for spec in (specs or []):
        if "=" in spec:
            pat, val = spec.split("=", 1)
            rules.append((pat, val))
        else:
            default = spec
    def pick(path):
        for pat, val in rules:
            if pat in path:
                return val
        return default
    return pick


def _frames(clip, t0, t1, n, outdir, tag, crop=None):
    """n frames sampled across a window, skipping its first and last 6%."""
    span = t1 - t0
    out = []
    for i in range(n):
        t = t0 + span * (0.06 + (0.88 * i / max(1, n - 1)))
        p = os.path.join(outdir, f"{tag}.{i}.jpg")
        sh(config.FFMPEG, "-v", "error", "-ss", f"{t:.2f}", "-i", clip,
           "-frames:v", "1", "-vf", _crop_vf(crop) + "scale=768:-2",
           "-q:v", "4", p)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            out.append((t, p))
    return out


def _json_from(resp):
    """The survey object out of a Gemini response, or None.

    `resp.text` is not reliable here even with a JSON mime type: a thinking
    model returns several parts and the convenience accessor either raises or
    hands back a concatenation with the reasoning glued to the front. So walk
    the parts, and fall back to the outermost braces in whatever text arrived.
    """
    texts = []
    try:
        for cand in (resp.candidates or []):
            for part in (getattr(cand.content, "parts", None) or []):
                t = getattr(part, "text", None)
                if t and not getattr(part, "thought", False):
                    texts.append(t)
    except (AttributeError, TypeError):
        pass
    if not texts:
        try:
            texts = [resp.text or ""]
        except (AttributeError, ValueError):
            return None
    for t in texts + ["".join(texts)]:
        t = t.strip()
        if t.startswith("```"):
            t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
        try:
            d = json.loads(t)
            if isinstance(d, dict):
                return d
        except ValueError:
            pass
        i, j = t.find("{"), t.rfind("}")
        if 0 <= i < j:
            try:
                d = json.loads(t[i:j + 1])
                if isinstance(d, dict):
                    return d
            except ValueError:
                pass
    return None


def _cache_key(clip, t0, crop=None):
    """Cache identity for a surveyed window.

    The crop MUST be part of it. A clearance answer is about the frame that
    was looked at, so changing the crop and reusing the old record would serve
    a verdict about pixels that are no longer in the shot -- exactly the claim
    the clearance pass exists to stop being taken on trust.
    """
    return f"{clip}#{t0}" + (f"#{crop}" if crop else "")


def _survey_one(entry, studio, project, crop=None, tries=3):
    """One window's clearance record. NEVER raises.

    A survey is a per-window question and the answers are independent, so one
    window that the vision API refuses (it returns 503 "unable to process
    input image" on some frames) must cost exactly that window. Letting it
    propagate out of the thread pool aborts a whole 48-window run, which is
    how the first Adventure pass died.
    """
    clip, t0, t1 = entry
    key = _cache_key(clip, t0, crop)
    last = ""
    for n in range(tries):
        try:
            return _survey_call(entry, key, studio, project, crop)
        except Exception as e:      # noqa: BLE001 — isolate the window
            last = f"{type(e).__name__}: {e}"[:160]
            if n + 1 < tries:
                time.sleep(1.5 * (n + 1))
    return {"id": key, "file": clip, "start": t0, "end": t1,
            "error": f"survey failed after {tries} tries: {last}"}


def _survey_call(entry, key, studio, project, crop=None):
    from google import genai
    from google.genai import types
    clip, t0, t1 = entry
    with tempfile.TemporaryDirectory() as td:
        frames = _frames(clip, t0, t1, SURVEY_FRAMES, td, "f", crop)
        if not frames:
            return {"id": key, "file": clip, "start": t0, "end": t1,
                    "error": "no frames decoded"}
        client = genai.Client(api_key=config.provider_key("google-genai"))
        parts = [SURVEY_PROMPT.format(studio=studio, n=len(frames))]
        for _, p in frames:
            parts.append(types.Part.from_bytes(data=open(p, "rb").read(),
                                               mime_type="image/jpeg"))
        ledger.check(EST_SURVEY)
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL, contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.2))
        ledger.add("gemini-visual", f"sizzle survey {os.path.basename(clip)}",
                   EST_SURVEY, project)
    d = _json_from(resp)
    if d is None:
        return {"id": key, "file": clip, "start": t0, "end": t1,
                "error": "unparseable survey"}
    idx = int(d.get("best_frame") or 1)
    idx = min(max(idx, 1), len(frames)) - 1
    d.update({"id": key, "file": clip, "start": t0, "end": t1, "crop": crop,
              "best_t": round(frames[idx][0], 2), "duration": round(t1 - t0, 2)})
    return d


def survey(clips, product, out_json, project="", workers=4, crop=None,
           window=MAX_WINDOW):
    """`crop` is a callable (path)->crop|None, or a plain crop string."""
    pick = crop if callable(crop) else (lambda _p, c=crop: c)
    """Vision clearance + description for every candidate window. Cached."""
    studio = products.PRODUCTS[product]["name"]
    entries = [w for c in clips for w in windows(c, window)]
    have = {}
    if os.path.exists(out_json):
        try:
            have = {c["id"]: c for c in json.load(open(out_json))["clips"]}
        except (ValueError, OSError, KeyError):
            have = {}
    # A cached FAILURE is not an answer, it is a missing one. Only successful
    # surveys are treated as done, so a transient parse or API failure gets
    # another attempt on the next run instead of silently shrinking the pool.
    todo = [e for e in entries
            if not have.get(_cache_key(e[0], e[1], pick(e[0])))
            or have[_cache_key(e[0], e[1], pick(e[0]))].get("error")]
    if todo:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for d in ex.map(
                    lambda e: _survey_one(e, studio, project, pick(e[0])), todo):
                have[d["id"]] = d
                flag = ("BLOCKED" if d.get("retired_branding")
                        or d.get("weapons_or_blood") else "ok")
                print(f"[survey] {flag:8} "
                      f"{os.path.basename(d['file'])[:40]:40}"
                      f"@{d['start']:>6.0f}s "
                      f"{d.get('summary', d.get('error', ''))[:62]}")
    ordered = [have[_cache_key(e[0], e[1], pick(e[0]))] for e in entries
               if _cache_key(e[0], e[1], pick(e[0])) in have]
    os.makedirs(os.path.dirname(os.path.abspath(out_json)) or ".", exist_ok=True)
    json.dump({"product": product, "clips": ordered}, open(out_json, "w"),
              indent=1)
    return ordered


def cleared(surveyed, allow_weapons=False):
    """The pool the brain is allowed to plan from, and why each drop happened.

    `allow_weapons` scopes the no-guns/no-blood rule instead of deleting it.
    The rule exists because weapons and blood cost REACH on the vertical feeds
    -- it is a distribution fact about those platforms, not a taste call --
    so it is a property of where a file is going, not of the file. A reel cut
    for a store page or a deck is not a feed post and does not pay that
    penalty. Turning it off is therefore a per-run decision a human makes,
    it is recorded in the plan, and the build writes a NOT-FOR-SOCIAL note
    next to the output so the scoped-off run cannot drift into the calendar.
    Retired branding is NEVER scopable: it is untrue whatever the surface is.
    """
    keep, drops = [], []
    for c in surveyed:
        if c.get("error"):
            drops.append((c["file"], c["error"]))
        elif c.get("retired_branding"):
            drops.append((c["file"], "retired branding: "
                          + (c.get("retired_branding_note") or "OldBrand/FormerBrand")))
        elif c.get("weapons_or_blood") and not allow_weapons:
            drops.append((c["file"], "weapons/blood: "
                          + (c.get("weapons_or_blood_note") or "")))
        else:
            keep.append(c)
    return keep, drops


NOT_FOR_SOCIAL = """# NOT FOR THE SOCIAL CALENDAR

`{out}` was built with `--allow-weapons`, which scopes OFF the no-guns/no-blood
rule. That rule is a reach penalty on TikTok / Reels / Shorts, so this file is
cleared for surfaces that do not pay it -- store pages, the site, decks,
Steam -- and is NOT cleared for the vertical feeds.

Shots carrying weapons or blood in this cut:
{shots}

To get a feed-safe version, rebuild without `--allow-weapons`.
"""


def _write_social_warning(out, plan, pool):
    by_id = {c["id"]: c for c in pool}
    flagged = [f"- {os.path.basename(s['file'])} @{s['at']}s: "
               f"{by_id.get(s.get('id'), {}).get('weapons_or_blood_note', '')}"
               for s in plan["shots"]
               if by_id.get(s.get("id"), {}).get("weapons_or_blood")]
    p = os.path.join(os.path.dirname(os.path.abspath(out)),
                     "NOT-FOR-SOCIAL.md")
    with open(p, "w") as f:
        f.write(NOT_FOR_SOCIAL.format(
            out=os.path.basename(out),
            shots="\n".join(flagged) or "- (none in the final cut)"))
    print(f"[gate  ] weapons rule scoped OFF; wrote {p}")
    return p


# -------------------------------------------------------------------- brain

PLAN_PROMPT = """You are the editor brain for a {seconds}-second SIZZLE REEL for
{studio}, a creation tool on example.invalid. A sizzle reel makes a stranger want the
product by showing what it MAKES. It is not a tutorial and not a feature tour.

THE POOL. Every clip you may use, already cleared:
{pool}

THE STORY IT HAS TO TELL, in this order. This is the spine, not a suggestion:

  1. THERE IS A PLATFORM.  example.invalid, full of worlds people made.
  2. INSIDE IT IS THIS TOOL, and this is what it makes: {makes}.
  3. LOOK WHAT CAME OUT OF IT.  The most visually stunning creations.
  4. YOUR TURN.

A viewer who has never heard of us must come away knowing all four. A montage
of pretty shots with no spine is the failure mode; every shot has to be
carrying one of those beats.

STRUCTURE, totalling {seconds} seconds of CONTENT (a brand endcard is appended
after your last shot by the renderer -- do not plan it):
- `platform`: 2 or 3 shots, 0.6-1.2s each. Beat 1. Fast, near-subliminal, a
  rush of the platform and the worlds already on it. These MUST be the pool
  entries whose id contains "/catalogue/t" -- that is the example.invalid
  catalogue. They are marked `kind: interface` or `mixed` and that is CORRECT
  here: a library of worlds is what a platform looks like, and the
  output-only rule does not apply to this beat. Do not substitute this
  studio's own worlds; those are beat 3. If no catalogue entries are in the
  pool at all, use your most varied entries and keep them SHORT.
- `title`: ONE shot, 2.0-2.8s. Beat 2. The studio wordmark plus your hook
  line, over a generated cinematic bed. It carries no source clip.
- `body`: 5 to 8 shots. Beat 3. The creations. Each carries a `label`. Order
  them so the look CHANGES every shot -- never two consecutive shots that read
  the same.
- `payoff`: ONE shot, 2.0-3.0s. Beat 4, carrying the payoff line.

RHYTHM. Do not give every body shot the same length; a flat 3.0/3.0/3.0 cut is
the single clearest tell of an automated edit. Vary them deliberately across
1.0s and 3.4s, and give this reel a shape a viewer can feel -- for example
open wide and tighten, or run staccato then hold on the best image. State the
shape you chose in `rhythm`. At least THREE distinct durations must appear
among the body shots, and no more than two consecutive shots may share one.

SHOW THE WORK, NOT THE TOOL. Pool entries are marked `kind: output` (the thing
the studio MADE -- a playing scene, a world, generated art) or `kind:
interface` (its own editor, panels, dashboards, forms). A sizzle sells the
work. Build the cut from `output` entries. You may use at most ONE `interface`
entry in the whole reel, and only if it genuinely shows something being made
rather than a wall of controls; prefer zero. Never open or close on one.

LABELS NAME A THING. Each pool entry carries a "name to label it with". Use it,
or a proper name you can actually read on screen. A label is what the viewer
would type to go and find this -- "Harbor Light", "The Gatehouse". It is never
a genre description ("noir detective scene", "action scene", "anime visual
novel game"); those are the vision model's words, not names, and burning them
on screen makes eight different worlds read as one stock library.
COPY CONTRACT, all four enforced:
- ON BRAND: creator-centric, confident, declarative, earned. No hype
  adjectives, no em dashes, no internal vocabulary.
- FUN: a wink. Never corporate, never flat.
- ONE CTA, plain words, exactly one ask.
- SHORT: hook at most 7 words, payoff at most 8, cta at most 4.
Name what the viewer GETS, never the mechanic and never the absence of a
thing. Make no claim about how long anything took or how easy it is. Never
write the words "OldBrand" or "FormerBrand"; the names are {studio} and example.invalid.

Return JSON only:
{{"hook": "...", "payoff": "...", "cta": "...", "rhythm": "<the shape you chose>",
  "shots": [{{"id": "<exact id from the pool>", "at": <absolute seconds into
              that file, inside the entry's usable range>, "dur": <seconds>,
              "role": "platform"|"title"|"body"|"payoff",
              "label": "<burned label; empty for platform and title>",
              "why": "<one clause>"}}]}}

Rules the renderer enforces and will reject you for: `id` must be an id from
the pool (the title beat is the ONLY shot that may omit it, and it must); `at`
and `at`+`dur` must both fall inside that entry's usable range; the shot
durations must sum to within 1.0s of {seconds}.

REPEATS. Prefer a different pool entry for every shot. When the pool holds
FEWER entries than the cut needs shots, reusing an entry is expected and
correct -- take two different moments from it, at least 6 seconds apart, so
they do not read as the same shot. Never refuse to produce a plan because the
pool is small; a thin pool is a normal input, not an error. Always return the
`shots` array.
"""


def title_hint(path):
    """A human title guessed from the file name.

    The vision model may describe what it sees rather than provide a proper
    title. The file name is often a better label, so it is offered to the
    planning model as a candidate.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    # drop a screen-capture recorder suffix so the timestamp does not bleed into
    # the burned name: "Sample Title - screen_2026-01-01_00-00-00" -> the name.
    # Requires digits after "screen" so a real title like "Blue Screen" is kept.
    stem = re.sub(r"[\s_-]+screen[\s_-]?(?:recording)?[\s_-]?\d[\d\s_\-:.]*$", "",
                  stem, flags=re.I)
    # backstop: a bare trailing date/time stamp (2026-08-17 15-54-33 / 20260817)
    stem = re.sub(r"[\s_-]+\d{4}(?:[\s_-]?\d{2}){2,5}$", "", stem)
    stem = re.sub(r"^(capture|clip|sample|t\d+)[_-]", "", stem, flags=re.I)
    stem = re.sub(r"^\d+[_-]", "", stem)
    stem = re.sub(r"[_-]?(T\d+|v\d+|part\d+|\d+x\d+|9x16|16x9)[_-]?", " ",
                  stem, flags=re.I)
    stem = re.sub(r"[_-]+", " ", stem)
    # CamelCase -> spaced words.
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem)
    words = stem.split()
    # Leave small connecting words lowercase when the file already carries case.
    small = {"on", "of", "the", "a", "an", "and", "in", "at", "to", "for"}
    out = [w if i == 0 or w.lower() not in small else w.lower()
           for i, w in enumerate(words)]
    return " ".join(out).strip()


def _pool_lines(pool):
    out = []
    for c in pool:
        hint = title_hint(c["file"])
        name = (f"    name to label it with: {hint}\n"
                if hint and not _is_description(hint)
                else "    no reliable name in the file name: use a title you "
                     "can read on screen, else leave the label empty\n")
        out.append(
            f"- id: {c['id']}\n"
            f"    usable range {c['start']}s to {c['end']}s of that file"
            f" | strongest moment at {c.get('best_t', c['start'])}s\n"
            f"    kind {c.get('kind', '?')} | beauty {c.get('beauty', '?')}"
            f" | motion {c.get('motion', '?')}\n"
            + name
            + f"    what the model saw: {c.get('subject', '')} -- "
              f"{c.get('summary', '')}")
    return "\n".join(out)


# Words that mean the brain described the footage instead of naming it. A
# label built from these is the vision model's prose, and it makes every world
# in the reel read as the same stock clip.
_DESCRIPTIVE = re.compile(
    r"\b(scene|gameplay|game|interface|editor|screen|shot|footage|clip|"
    r"sequence|montage|adventure|visual novel|animation|art|generator|"
    r"creator|preview|demo|dashboard|panel|menu|ui)\b", re.I)


def _is_description(label):
    """True when a label reads as a description rather than a name.

    A real title may of course contain one of these words, so the test is not
    the word alone: it is an all-lowercase label (titles are capitalised) that
    also carries a describing noun.
    """
    text = str(label or "").strip()
    if not text:
        return False
    has_caps = any(w[:1].isupper() for w in text.split())
    return bool(_DESCRIPTIVE.search(text)) and not has_caps


def _validate(plan, pool, seconds):
    """Everything the brain can get wrong that a render would silently honour."""
    errs = []
    by_id = {c["id"]: c for c in pool}
    shots = plan.get("shots") or []
    roles = [s.get("role") for s in shots]
    for want in ("title", "payoff"):
        if roles.count(want) != 1:
            errs.append(f"need exactly one {want} shot, got {roles.count(want)}")
    if not 2 <= roles.count("platform") <= 3:
        errs.append(f"need 2-3 platform shots (beat 1: there is a platform), "
                    f"got {roles.count('platform')}")
    if not 5 <= roles.count("body") <= 8:
        errs.append(f"need 5-8 body shots, got {roles.count('body')}")
    total, seen = 0.0, {}
    for i, s in enumerate(shots):
        total += float(s.get("dur") or 0)
        if s.get("role") == "title":
            if s.get("id"):
                errs.append(f"shot {i}: the title beat carries no source clip")
            continue
        c = by_id.get(s.get("id"))
        if not c:
            errs.append(f"shot {i}: '{s.get('id')}' is not a pool id")
            continue
        at, dur = float(s.get("at") or 0), float(s.get("dur") or 0)
        if s["id"] in seen and len(pool) >= len(shots) - 1:
            errs.append(f"shot {i}: pool entry {s['id']} is already used; "
                        f"every shot must come from a different entry")
        elif s["id"] in seen and abs(at - seen[s["id"]]) < 6.0:
            errs.append(f"shot {i}: the pool is smaller than the cut, so "
                        f"{s['id']} may be used twice, but the two moments "
                        f"must be at least 6s apart")
        seen[s["id"]] = at
        if at < c["start"] or at + dur > c["end"]:
            errs.append(f"shot {i}: {at}s+{dur}s falls outside the usable "
                        f"range {c['start']}-{c['end']}s of {s['id']}")
        # The renderer needs a resolved source; do it here so a legal plan is
        # directly renderable and `file` never disagrees with `id`.
        s["file"] = c["file"]
        s["crop"] = c.get("crop")
        s["kind"] = c.get("kind", "")
        s.setdefault("subject", c.get("subject", ""))
        # Only demand a name when a better one demonstrably exists. Some file
        # names are themselves descriptive ("episode action montage") and
        # rejecting a label while suggesting that same string is a loop the
        # brain cannot escape -- there, whatever it can read off screen wins.
        hint = title_hint(c["file"])
        if s.get("label") and _is_description(s["label"]) \
                and hint and not _is_description(hint):
            errs.append(f"shot {i}: label '{s['label']}' describes a genre "
                        f"instead of naming the thing; use '{hint}' or a "
                        f"title you can read on screen")
    # A sizzle sells the work. Interface shots are allowed only as seasoning,
    # and only while the pool actually offers an alternative -- this is a
    # budget, not a ban, so a thin pool still produces a cut.
    n_out = sum(1 for c in pool if c.get("kind") == "output")
    n_iface = sum(1 for s in shots if s.get("kind") == "interface")
    if n_iface > 1 and n_out >= len(shots) - 1:
        errs.append(f"{n_iface} interface shots; at most 1 is allowed when the "
                    f"pool holds {n_out} 'output' entries. Show what the studio "
                    f"made, not its editor.")
    # The two shots that carry the whole reel. "mixed" counts against them as
    # hard as "interface": the Story payoff that slipped through was tagged
    # mixed, and it put "And yes, it plays" over a settings form -- copy the
    # picture actively contradicted.
    hero = [c for c in pool
            if c.get("kind") == "output" and (c.get("beauty") or 0) >= 7]
    # BEAT 1 IS AN EXCEPTION and this cost a rebuild: the platform shots are
    # not hero images, they are evidence that a platform exists, and evidence
    # of a platform IS its library and dashboards. The catalogue clips survey
    # as `interface`/`mixed` by definition ("the product home dashboard", "a
    # game library interface"), so demanding `output` here made the brain
    # substitute the studio's own worlds and beat 1 stopped saying anything.
    catalogue = [c for c in pool if CATALOGUE_MARK in c["file"]]
    for s in shots:
        if s.get("role") == "platform":
            if catalogue and CATALOGUE_MARK not in (s.get("file") or ""):
                errs.append(
                    f"platform shot uses '{os.path.basename(s.get('file', ''))}'; "
                    f"beat 1 must come from the {len(catalogue)} example.invalid "
                    f"catalogue entries in the pool (ids containing "
                    f"'{CATALOGUE_MARK}'), not from this studio's own worlds")
            continue
        if s.get("role") != "payoff":
            continue
        if s.get("kind") != "output" and len(hero) >= 2:
            errs.append(f"the {s['role']} shot is '{s.get('kind')}', not "
                        f"'output'; open and close on the work itself")
        elif (by_id.get(s.get("id"), {}).get("beauty") or 0) < 7 and len(hero) >= 2:
            errs.append(f"the {s['role']} shot scored "
                        f"{by_id.get(s.get('id'), {}).get('beauty')} for beauty; "
                        f"it carries the reel, use one of the {len(hero)} "
                        f"entries scoring 7+")
    # RHYTHM. A flat cut where every body shot is the same length is the
    # clearest tell of an automated edit, so evenness is a defect the gate
    # names rather than something only a human eye catches.
    body = [round(float(s.get("dur") or 0), 2) for s in shots
            if s.get("role") == "body"]
    if body:
        if len(set(body)) < 3:
            errs.append(f"body shot durations are {sorted(set(body))}; at least "
                        f"THREE distinct lengths are required so the cut has a "
                        f"rhythm instead of a metronome")
        runs, prev, n = [], None, 0
        for d in body:
            n = n + 1 if d == prev else 1
            prev = d
            runs.append(n)
        if max(runs) > 2:
            errs.append("more than two consecutive body shots share a duration")
    plat = [float(s.get("dur") or 0) for s in shots
            if s.get("role") == "platform"]
    if plat and max(plat) > 1.4:
        errs.append(f"platform shots run up to {max(plat)}s; beat 1 is a fast "
                    f"rush, keep each at 1.2s or under")
    if abs(total - seconds) > 1.0:
        errs.append(f"shots total {total:.1f}s, need {seconds}s +/- 1.0")
    bad = brandkit.lint_copy(plan.get("hook"), plan.get("payoff"),
                             plan.get("cta"), plan.get("studio", ""))
    errs.extend(bad)
    return errs


def _trim_words(text, n):
    """First `n` words of `text` (the deterministic repair for an over-long
    hook/payoff), preserving trailing sentence punctuation."""
    from . import brandkit
    words = (text or "").split()
    if len(words) <= n:
        return text or ""
    kept = " ".join(words[:n]).rstrip(",;:- ")
    tail = (text or "").strip()[-1:]
    return kept + (tail if tail in ".!?" and not kept.endswith(tail) else "")


def plan(product, pool, seconds=30, brain="gpt", project="", tries=6):
    """Shot list + copy, retried against the validator until it is legal.

    Never ships EMPTY (MAR-108 spirit): if the brain cannot fully satisfy the
    validator in `tries`, the last plan that HAS a shot list gets its copy
    auto-trimmed to the word limits and ships with a warning, rather than
    SystemExit killing the whole reel over a soft copy fault."""
    from . import direct, brandkit
    studio = products.PRODUCTS[product]["name"]
    key = products.ALIASES.get(product, product)
    prompt = PLAN_PROMPT.format(seconds=seconds, studio=studio,
                                makes=MAKES.get(key, "worlds"),
                                pool=_pool_lines(pool))
    last, last_plan = [], None
    for n in range(tries):
        p = direct._ask_json(prompt if n == 0 else
                             prompt + "\n\nYour previous answer was rejected:\n"
                             + "\n".join(f"- {e}" for e in last)
                             + "\nReturn a corrected plan.",
                             brain, project, f"sizzle plan {product}")
        if not p:
            last = ["no JSON returned"]
            print("[plan  ] brain returned nothing parseable")
            continue
        if not p.get("shots"):
            # Seeing the shape it DID return is the whole debugging signal;
            # "got 0 shots" four times in a row says nothing about why.
            print(f"[plan  ] no shots in reply; keys={sorted(p)[:8]}")
        p["studio"] = studio
        p["product"] = product
        errs = _validate(p, pool, seconds)
        if not errs:
            return p
        last = errs
        if p.get("shots"):
            last_plan = p
        print(f"[plan  ] attempt {n + 1} rejected: {'; '.join(errs[:4])}")
    if last_plan:
        lim = dict(brandkit.DEFAULT_LIMITS)
        for f in ("hook", "payoff", "cta"):
            last_plan[f] = _trim_words(last_plan.get(f) or "", lim.get(f, 999))
        soft = _validate(last_plan, pool, seconds)
        print(f"[plan  ] validator not fully satisfied after {tries} tries; "
              f"auto-trimmed copy and shipping the reel (remaining soft issues: "
              f"{'; '.join(soft[:3]) if soft else 'none'})")
        return last_plan
    raise SystemExit("sizzle plan failed the validator "
                     f"{tries}x with no usable shot list; last: {'; '.join(last)}")


# ---------------------------------------------------------------- assembler

def _aspect(path):
    """w/h of a source, or None when it cannot be probed."""
    try:
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            from PIL import Image
            w, h = Image.open(path).size
            return float(w) / float(h)
        st = media.probe(path)["streams"]
        v = next(x for x in st if x.get("codec_type") == "video")
        return float(v["width"]) / float(v["height"])
    except Exception:      # noqa: BLE001 -- unprobeable just keeps the bed
        return None


def _dims(path):
    """(w, h) of a source in pixels, or None when it cannot be probed."""
    try:
        if path.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            from PIL import Image
            return Image.open(path).size
        st = media.probe(path)["streams"]
        v = next(x for x in st if x.get("codec_type") == "video")
        return int(v["width"]), int(v["height"])
    except Exception:      # noqa: BLE001
        return None


def _fill_crop(src, w, h, at, dur):
    """Face-aware fill crop for a shot: 'crop=w:h:x:y' or None.

    Filling a 16:9 frame from a 16:9-ish source means throwing away the
    overflow, and a bare `crop=W:H` throws it away from the EDGES -- i.e. it
    keeps the middle and cuts whatever is not centred. On app captures and
    live-play footage the speaker is rarely centred, so the thing the shot is
    about is the first thing out of frame. That is the "faces get cutoff"
    note from review.

    `face.region_crop` takes the largest rect of the target aspect that fits
    the source and SLIDES it onto the face, so nothing is scaled differently
    and nothing is letterboxed -- the window just stops being centred by
    default. With no face detected it centres, which is exactly the old
    behaviour, so non-face shots are untouched.
    """
    dims = _dims(src)
    if not dims:
        return None
    try:
        box = face.face_box(src, at, at + dur)
    except Exception:      # noqa: BLE001 -- detection is best-effort
        return None
    if box is None:
        return None
    cw, ch, cx, cy = face.region_crop(dims, box, w / h)
    return f"crop={cw}:{ch}:{cx}:{cy}"


# ONE grade across every sequence: platform rush, body shots, title sequence,
# closing card. "Cinematic" is not something the title has while the shots
# around it stay ungraded -- that is exactly what makes a card look pasted in.
# A gentle S-curve, shadows cooled and highlights left warm, a whisper of
# grain so the frame is not digitally clean, and a soft vignette.
GRADE = ("curves=r='0/0 0.26/0.22 0.75/0.79 1/1':"
         "g='0/0 0.26/0.225 0.75/0.785 1/1':"
         "b='0/0.012 0.26/0.245 0.75/0.775 1/1',"
         "eq=saturation=1.06:contrast=1.05,"
         "vignette=angle=PI/5.2,"
         "noise=alls=4:allf=t+u")


def _grade():
    return GRADE


def _grammar(w, h, crop=None, src=None, still=False, dur=3.0, at=0.0):
    """House grammar filter: sharp centred content on a blurred fill of itself.

    A 16:9 source fills the frame and never gets blurred behind itself. Every
    other shape (the vertical player captures, the phone-shaped app grabs) sits
    sharp and whole on a soft bed, which is typical portrait-sizzle grammar --
    not pillarboxed into black bars, and not cropped until the subject
    falls out of frame.
    """
    # CINEMATIC MEANS FILLING THE FRAME. A sharp rectangle floating on a
    # blurred copy of itself is social-feed grammar, not film grammar -- no
    # cinema screen has ever had a blurry version of the shot around the
    # edges. So anything close enough to 16:9 is CROPPED to fill, which is
    # what a real edit does. Only sources far off the frame (the 9:16 player
    # captures) keep the blurred bed, because cropping those would throw the
    # subject out of shot entirely.
    src_ar = _aspect(src) if src else None
    target = w / h
    fill = src_ar is not None and abs(src_ar - target) / target <= FILL_TOLERANCE
    pre = f"[0:v]{_crop_vf(crop)}" if crop else "[0:v]"
    # A TALL STILL gets a vertical pan, not a bed. Story Project's background
    # plates are 2:3 and square -- pillarboxing them on a blurred copy wastes
    # two thirds of a 16:9 frame and looks like a slideshow. Filling the width
    # and travelling down the plate shows ALL of the art and is the standard
    # treatment for tall artwork in a widescreen cut.
    if still and src_ar is not None and src_ar < target * 0.92:
        # Travel only the middle of the plate. A full-height pan in 1.6s races
        # past the subject and lands on whatever dead space the artist left at
        # the bottom -- the castle plate ended on black cliff. Starting at 14%
        # and moving 42% keeps the eye on the art the whole way.
        travel = (f"(ih-oh)*(0.14+0.42*min(t/{max(0.1, dur):.2f},1))")
        return (pre + f"scale={w}:-2,crop={w}:{h}:0:'{travel}',"
                f"setsar=1,fps={FPS}," + _grade() + ",format=yuv420p")
    if fill:
        # A manual --crop is a human decision about framing; do not second
        # guess it. Stills have no timeline to sample for a face.
        face_crop = None
        if not still and src and not crop:
            face_crop = _fill_crop(src, w, h, at, dur)
        if face_crop:
            # Crop first, then scale: the window is already the target aspect,
            # so this neither upscales differently nor letterboxes -- it just
            # keeps the face instead of the centre.
            return (pre + f"{face_crop},scale={w}:{h},setsar=1,fps={FPS},"
                    + _grade() + ",format=yuv420p")
        return (pre + f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},setsar=1,fps={FPS}," + _grade() +
                ",format=yuv420p")
    return (
        pre + "split=2[a][b];"
        + f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},gblur=sigma={GRAMMAR_BLUR},eq=brightness=-0.14:"
        f"saturation=0.7,setsar=1[bg];"
        f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={FPS}," + _grade() +
        ",format=yuv420p")


def _label_png(text, sub, w, h, workdir, name):
    """The shot's name label, rendered through the shipped overlay templates
    so a sizzle's type is the same type everything else in the brand uses.

    TOP-left, not bottom-left. A clean-footage sizzle can set names low,
    but adventure games and interactive stories
    put their dialogue boxes along the bottom edge, so a low label lands on
    in-game text in most shots. The top edge is empty in nearly all of it.
    """
    from . import overlays
    body = overlays.namecard(text, sub=sub, x=int(w * 0.045),
                             y=int(h * 0.075), size=int(h * 0.052))
    return overlays._render_png(workdir, name, body, size=(w, h))


# --- energy -----------------------------------------------------------------
# What "punchier" is allowed to mean here. NOT a white flash: those are banned
# outright, they read as cheap and they were called out by name. Energy comes
# from the shots themselves moving -- a slow push that accelerates into the
# cut, and a hard cut that lands ON the beat -- plus a short directional blur
# where two shots would otherwise butt awkwardly.
# HOW MUCH MOVE IS ENOUGH. The first pass used 1.000 -> 1.055 across a whole
# shot: 5.5% over three seconds is under 2%/second and is simply not visible,
# which is why the cut read as a slideshow of stills. These are the numbers
# that actually register on screen.
# TUNED DOWN 2026-08-11 after review: "slow the motion down so i can parse
# better" and "the zoom ins on the game footage make it sort of hard to read
# (or maybe the speed of them)". The numbers above were set to stop the cut
# reading as a slideshow, and they overshot -- at 1.55 the opening rush moves
# roughly 18%/second, which is faster than a viewer can read a subtitle or
# find a face. These are still well above the 2%/second that read as static:
# the body push now travels ~4%/second over a 2.5s hold. The shots also got
# LONGER in the same pass, so the per-second rate drops twice over.
PUSH_BODY = (1.00, 1.10)     # was 1.22
PUSH_PLATFORM = (1.00, 1.18) # was 1.55 -- the rush was the worst offender
PUNCH_FROM = 1.15            # was 1.34 -- lands with weight, not a slam
PUNCH_SETTLE = 0.34          # was 0.26 -- and settles less abruptly
WHIP_S = 0.13                # blur-out at an outgoing cut
WHIP_SIGMA = 30              # harder smear so the cut reads as an impact

# Where a shot's zoom holds. Corner values sit at 0.78 rather than 1.0 so the
# subject keeps some air around it instead of being shoved against the edge.
ANCHORS = {
    "center": (0.5, 0.5), "centre": (0.5, 0.5),
    "top-left": (0.22, 0.22), "top-right": (0.78, 0.22),
    "bottom-left": (0.22, 0.78), "bottom-right": (0.78, 0.78),
    "left": (0.25, 0.5), "right": (0.75, 0.5),
    # "top" is the one this was built for: a 9:16 episode sitting on a blurred
    # bed puts its characters' heads in the upper third, and a centred punch
    # takes the tops of their heads off. 0.28 keeps hair and headroom.
    "top": (0.5, 0.28), "bottom": (0.5, 0.72),
}


def _energy(w, h, dur, kind="body", move="push", anchor=None):
    """Per-shot camera move. `move` alternates so no two cuts feel alike.

    push  - drifts in across the shot, accelerating into the cut
    punch - lands oversized and settles fast, so the CUT itself has impact

    Alternating them is the point: a reel where every shot does the same
    thing has no rhythm even when the shot LENGTHS vary, which is what
    "the transitions look exactly the same" meant.

    `anchor` is the focal point the zoom holds, as (fx, fy) in 0..1 of the
    frame. It defaults to dead centre, which is what every shot used to get
    unconditionally -- and that is what cropped the faces: a 1.22-1.34x punch
    into the middle of a full-frame capture throws away the outer fifth, and
    app captures put the speaker's webcam in a CORNER. Anchoring at (0.5, 0.5)
    reproduces the old formula exactly, so shots that do not set it are
    untouched.
    """
    frames = max(1, int(round(dur * FPS)))
    if kind == "platform":
        a, b = PUSH_PLATFORM
        z = f"'{a}+({b}-{a})*pow(on/{frames},1.4)'"
    elif move == "punch":
        settle = max(1, int(round(PUNCH_SETTLE * FPS)))
        a, b = PUSH_BODY
        # oversized for `settle` frames, easing down, then a gentle drift on
        z = (f"'if(lt(on,{settle}),"
             f"{PUNCH_FROM}-({PUNCH_FROM}-{a})*pow(on/{settle},0.55),"
             f"{a}+({b}-{a})*((on-{settle})/{max(1, frames - settle)}))'")
    else:
        a, b = PUSH_BODY
        z = f"'{a}+({b}-{a})*pow(on/{frames},1.5)'"
    # (iw - iw/zoom) * fx is the same expression as iw/2 - iw/zoom/2 when
    # fx is 0.5, so the default path is byte-identical to the old one.
    if not anchor:
        anchor = (0.5, 0.5)
    elif isinstance(anchor, str):
        anchor = ANCHORS.get(anchor, (0.5, 0.5))
    fx, fy = anchor
    fx = min(max(float(fx), 0.0), 1.0)
    fy = min(max(float(fy), 0.0), 1.0)
    return (f"zoompan=z={z}:d=1:x='(iw-iw/zoom)*{fx:.4f}'"
            f":y='(ih-ih/zoom)*{fy:.4f}'"
            f":s={w}x{h}:fps={FPS}")


def _zoom_anchor(shot, is_still, dur):
    """Focal point the shot's zoom should hold, as (fx, fy) in 0..1, or None.

    Order of authority:
      1. an explicit `anchor` on the shot -- a human said where to look
      2. a detected face, when the detector can find one
      3. None, meaning dead centre, the historical behaviour

    Face detection can miss small webcam bubbles in app captures. A shot that
    must keep a corner should set ``anchor`` explicitly rather than rely on
    detection.
    """
    a = shot.get("anchor")
    if a:
        if isinstance(a, str):
            if a not in ANCHORS:
                raise SystemExit(f"unknown anchor {a!r}; use one of "
                                 f"{sorted(ANCHORS)} or [fx, fy]")
            return ANCHORS[a]
        return (float(a[0]), float(a[1]))
    if is_still:
        return None
    dims = _dims(shot["file"])
    if not dims:
        return None
    try:
        box = face.face_box(shot["file"], float(shot.get("at", 0.0)),
                            float(shot.get("at", 0.0)) + dur)
    except Exception:      # noqa: BLE001 -- detection is best-effort
        return None
    if box is None:
        return None
    W, H = dims
    return (box[0] / W, box[1] / H)


def _seg_source(shot, w, h, dst):
    dur = float(shot["dur"])
    is_still = shot["file"].lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
    chain = _grammar(w, h, shot.get("crop"), shot["file"], is_still, dur,
                     float(shot.get("at", 0.0)))
    anchor = _zoom_anchor(shot, is_still, dur)
    energy = _energy(w, h, dur, shot.get("role", "body"),
                     shot.get("move", "push"), anchor)
    chain += "," + energy
    # Blur out of the last few frames so the cut hits with a snap instead of
    # a clean butt-join. Cheap, directional, and invisible as an effect.
    whip = min(WHIP_S, dur * 0.25)
    if shot.get("whip"):
        # gblur's `sigma` is NOT expression-evaluable (no `eval` option), so a
        # ramp has to come from the timeline instead: `enable` switches a
        # fixed blur on for the last few frames. That reads as a snap into the
        # cut, which is the punchier result anyway.
        chain += (f",gblur=sigma={WHIP_SIGMA}:enable='gte(t,{dur - whip:.3f})'")
    frames = int(round(dur * FPS))
    args = [config.FFMPEG, "-v", "error", "-y"]
    # A shot may be a STILL. Story Project's worlds ship as background plates
    # and the example.invalid catalogue as cover art, so a sizzle that can only cut
    # video cannot show either product's actual output. A still is looped into
    # a stream and then takes the same grade and camera move as any clip.
    if shot["file"].lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        args += ["-loop", "1", "-t", f"{dur + 0.5:.2f}",
                 "-framerate", str(FPS), "-i", shot["file"]]
    else:
        args += config.hwdecode_args()
        args += ["-ss", f"{float(shot.get('at', 0)):.2f}",
                 "-t", f"{dur + 0.5:.2f}", "-i", shot["file"]]
    args += ["-filter_complex",
             chain + ",tpad=stop_mode=clone:stop_duration=1.0", "-an"]
    args += config.intermediate_encode_args() + ["-r", str(FPS),
                                                 "-frames:v", str(frames), dst]
    r = sh(*args)
    if not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise RuntimeError(f"segment render failed for {shot['file']}: "
                           f"{r.stderr[-300:]}")
    return dst


TITLE_TYPE_PROMPT = (
    "Integrate the words {spec} into this scene as PHYSICAL cinematic title "
    "typography that exists inside the world: the letters have real "
    "perspective, depth and thickness, they sit in the space rather than on "
    "top of the picture, and the scene's own light rakes across them so they "
    "cast and catch shadow, haze and flare exactly like the objects around "
    "them. "
    # CONTRAST IS A REQUIREMENT, NOT A PREFERENCE. The first title came back
    # as warm gold letters over a bright warm doorway and the line barely
    # read. The model has to be told to separate the type from its backdrop.
    "CONTRAST IS CRITICAL AND THE LAST ATTEMPT FAILED IT: dark brown letters "
    "were placed over a bright warm doorway and could barely be read. The "
    "lettering must be PALE, near-white and self-luminous, and it must sit "
    "against the DARK shadowed parts of the scene, never across the brightest "
    "light source. Give every letter a dark contact shadow so it separates "
    "hard from whatever is behind it. If the scene has no dark area large "
    "enough, deepen the shadows behind the words until it does. "
    "Bold contemporary sans, generous tracking, large, occupying the middle "
    "third of the frame. Spell it EXACTLY as written. Add no other text, no "
    "logos, no watermark, no subtitles, no user interface."
)

# The words are a moving object in the shot, not a caption pinned to it. This
# is the "flying text" ask: the camera and the type move THROUGH each other.
FLY_MOTION = (
    # First attempt at "flying text" traded away the thing that matters: the
    # words swept past camera and were GONE by 2.5s of a 4.6s beat. Motion is
    # worth nothing if the line cannot be read. The camera moves around the
    # type now, not through and past it.
    "The title lettering is a solid three-dimensional object standing in the "
    "world. The camera moves slowly toward and around it with real parallax "
    "so the letters shift perspective and catch the light, but the words stay "
    "CENTRED, WHOLE and FULLY INSIDE THE FRAME for the entire clip. They must "
    "still be complete and readable in the final frame. Never let the words "
    "slide out of shot, never crop them at the edges, never fade them out, "
    "never dissolve them. Around the type the world continues to open up and "
    "reveal itself. "
)

def _typeset_into(still, line, out, project="", tries=3, style_ref=None,
                  aspect="9:16"):
    """Typeset a headline INTO the artwork (design-then-animate, M7-compatible).

    This is the difference between a title SEQUENCE and a title CARD. A card
    is a picture with words laid over it, and it reads as a slide no matter
    how good the picture is -- which is exactly what came back rejected.
    Typesetting into the art makes the words part of the shot, so when
    Seedance animates the frame the letters move WITH the world.

    Generated type is only allowed to ship if it reads back exactly as
    written (the M7/M8 spelling gate), so every attempt goes through the
    vision reader and a misspelling costs a retry, never a delivery.
    """
    from PIL import Image
    from . import design, motion
    spec = f'"{line.rstrip(".")}"'
    seen_last = ""
    # The studio wordmark goes in as a SECOND reference so the generated
    # lettering inherits the brand's letterforms and palette. Without it the
    # model invents a typeface every time and the result is off-brand type
    # with the real logo buried underneath it as an afterthought. This is the
    # same trick motion._lettering uses ("the EXACT same lettering style as
    # this image") -- style only. The mark itself is never drawn: a generated
    # logo is an off-brand logo.
    refs = [_data_uri(still)]
    brand = ""
    if style_ref and os.path.exists(style_ref):
        refs.append(_data_uri(style_ref))
        brand = (" The lettering must use the SAME typeface, weight, letter "
                 "proportions and colour treatment as the wordmark in "
                 "@Image2: match its letterforms exactly. Do NOT draw the "
                 "wordmark, the icon or any logo anywhere in the image - take "
                 "only the type style from it.")
    for i in range(tries):
        url = audio_post._fal(
            motion.IMAGE_ENDPOINT,
            {"prompt": TITLE_TYPE_PROMPT.format(spec=spec) + brand,
             # match the reel's aspect (portrait socials are 9:16) -- nano-banana
             # edit defaults to landscape otherwise, and the wide title was then
             # cropped off both sides
             "image_urls": refs, "num_images": 1, "aspect_ratio": aspect},
            motion.EST_FRAME, f"sizzle title type {line[:24]}", project,
            service="fal-image", find=motion._find_image_url)
        audio_post._download(url, out)
        seen = design.read_text(Image.open(out), project)
        if _norm_words(seen) != _norm_words(line):
            # The general transcription pass reads heavily-stylized glyphs as
            # NO TEXT even when the spelling is perfect -- a known failure
            # already handled in motion._lettering, which I failed to copy:
            # Video Project's title came back correct and the gate saw '' and
            # refused it. A lettering-framed second read recovers those; a
            # real misspelling still fails both.
            seen = design.read_lettering(Image.open(out), project)
        if _norm_words(seen) == _norm_words(line):
            return out
        seen_last = seen
        print(f"[title ] typeset spelling drift (saw {seen!r}), retry {i + 1}")
    raise RuntimeError(
        f"typeset-into-art never spelled {line!r} correctly "
        f"(last read: {seen_last!r}); a misspelled title cannot ship")


def _norm_words(s):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split())


def face_scan(clip, t0=0.0, t1=None, step=0.5, crop=None, top=12):
    """Times in a clip where a FACE is largest, using Reelly's FaceMesh.

    A sizzle for a storytelling tool that never shows a face is selling
    scenery. This picks moments by measured face size instead of by eye, so
    "find the close-ups" stops being a judgement call I get wrong.

    Returns [(t, face_height_fraction_of_frame), ...] largest first.
    """
    from . import face, faceio
    t1 = t1 if t1 is not None else media.duration(clip)
    times = [round(t0 + i * step, 2)
             for i in range(int((t1 - t0) / step))]
    if not times:
        return []
    # extract_frames returns [(frame, scale), ...] -- the scale maps detected
    # coords back to source pixels; face HEIGHT as a fraction of the frame is
    # scale-free, so only the frame is needed here.
    frames = faceio.extract_frames(clip, times, max_width=640)
    out = []
    for t, (fr, _scale) in zip(times, frames):
        if fr is None:
            continue
        if crop:
            cw, ch, cx, cy = (int(v) for v in crop.split(":"))
            sc = fr.shape[1] / _aspect_w(clip)
            fr = fr[int(cy * sc):int((cy + ch) * sc),
                    int(cx * sc):int((cx + cw) * sc)]
            if fr.size == 0:
                continue
        faces = face.detect_faces(fr)
        if faces:
            out.append((t, round(faces[0]["h"] / fr.shape[0], 3)))
    out.sort(key=lambda x: -x[1])
    return out[:top]


def _aspect_w(clip):
    try:
        v = next(x for x in media.probe(clip)["streams"]
                 if x.get("codec_type") == "video")
        return float(v["width"])
    except Exception:
        return 1920.0


def cinecard(product, w, h, dur, workdir, project="", line=None, tag="card"):
    """A GENERATED TITLE SEQUENCE for this studio -- not a bed with type on it.

    reviewer, 2026-08-10, twice: the card still read as flat. The first
    attempt generated a beautiful moving backdrop and then laid a typeset
    headline over the middle of it, which is a slide with a video wallpaper.
    A title SEQUENCE has the words inside the shot.

    So: `art` makes the text-free scene, Nano Banana Pro typesets the line
    INTO that scene (real perspective, lit by the scene's own light), the
    spelling gate reads it back, and Seedance animates the whole frame with
    the letters held. The type moves with the world because it is part of it.

    Cached on (product, tag, line): changing the copy regenerates, and the
    untyped variant used for the outro bed is cached separately.
    """
    from . import art
    key = products.ALIASES.get(product, product)
    stem = f"cinecard_{key}_{tag}"
    out = os.path.join(workdir, stem + ".mp4")
    keyfile = out + ".key"
    ident = _norm_words(line or "")
    if os.path.exists(out) and media.duration(out) >= dur \
            and os.path.exists(keyfile) and open(keyfile).read().strip() == ident:
        return out

    concept = CARD_CONCEPTS.get(key) or CARD_CONCEPTS["video"]
    base = os.path.join(workdir, f"cinecard_{key}_base.png")
    # Generate the title art in the REEL's aspect. Portrait socials are 9:16, so
    # a landscape card center-cropped to 9:16 overflowed its big title off both
    # sides. Match the target instead.
    art_size = "portrait_16_9" if h >= w else "landscape_16_9"
    art.make(concept["still"], base, project=project, size=art_size)

    if line:
        still = os.path.join(workdir, stem + ".png")
        _typeset_into(base, line, still, project=project,
                      style_ref=products.brand_logo(key),
                      aspect=("9:16" if h >= w else "16:9"))
        hold = (FLY_MOTION + "The words are exactly those already in @Image1: "
                "do not add, remove, restyle or respell any text. ")
    else:
        still = base
        hold = ("NO text, NO words, NO letters, NO logos anywhere. ")

    seconds = max(5, int(round(dur)))
    prompt = (f"Create ONE {seconds}-second cinematic landscape video from "
              f"@Image1, holding its exact art direction, palette and subject. "
              f"{concept['motion']} {hold}"
              f"No user interface, no watermark, no people's faces in close "
              f"up, no weapons, no blood. The motion is smooth and continuous "
              f"with no cuts and no flash frames.")
    url = audio_post._fal(
        CARD_ENDPOINT,
        {"prompt": prompt, "image_url": _data_uri(still),
         "resolution": "720p", "duration": str(seconds),
         "generate_audio": False},
        EST_CARD, f"sizzle cinecard {key} {tag}", project,
        service="fal-video", find=_find_video_url, tries=300)
    tmp = out + ".dl.mp4"
    audio_post._download(url, tmp)
    sh(config.FFMPEG, "-y", "-v", "error", "-i", tmp, "-an",
       "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},setsar=1",
       "-c:v", "libx264", "-crf", "17", "-preset", "medium", out)
    if os.path.exists(tmp):
        os.remove(tmp)
    if line and not _text_survives(out, line, workdir, project):
        # The generated title swept its own words out before the beat ended (a
        # title you cannot finish reading is not a title). Do NOT fail the whole
        # reel over it -- fall back to a DETERMINISTIC beat that holds the
        # correctly-typeset still (exact words) with a slow push-in, so a montage
        # always ships a readable title instead of coming out empty. (MAR-108)
        print(f"[card  ] {key} title lost its text by 85%; holding the typeset "
              f"still with a push-in instead (deterministic, always readable)")
        _still_push(still, out, w, h, seconds)
    open(keyfile, "w").write(ident)
    print(f"[card  ] generated {'TITLE SEQUENCE' if line else 'bed'} for "
          f"{key}: {os.path.basename(out)}")
    return out


def _still_push(still, out, w, h, seconds):
    """Deterministic title beat: the exact typeset still, filled to w x h and
    held for `seconds` with a slow push-in. No generation, so the words are
    always readable -- the fallback when a generated title sweeps its own text
    out before the beat ends (MAR-108). Overwrites `out`."""
    frames = max(1, int(round(seconds * FPS)))
    sh(config.FFMPEG, "-y", "-v", "error", "-loop", "1", "-i", still,
       "-t", f"{seconds:.2f}", "-r", str(FPS),
       "-vf", (f"scale={w * 4}:{h * 4}:force_original_aspect_ratio=increase,"
               f"crop={w * 4}:{h * 4},"
               f"zoompan=z='min(zoom+0.0004,1.04)':d={frames}:"
               f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={FPS},"
               f"setsar=1,format=yuv420p"),
       "-c:v", "libx264", "-crf", "17", "-preset", "medium", "-an", out)
    return out


def _text_survives(clip, line, workdir, project="", frac=0.85):
    """Is the line STILL readable near the end of the generated clip?

    The first flying-text attempt swept the words out of shot by 2.5s of a
    4.6s beat and nothing caught it -- the spelling gate only ever looked at
    the still that went IN. Motion is worth nothing if the line cannot be
    finished, so the check now reads a late frame of what came OUT.
    """
    from PIL import Image
    from . import design
    t = media.duration(clip) * frac
    probe = os.path.join(workdir, "_survives.jpg")
    sh(config.FFMPEG, "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", clip,
       "-frames:v", "1", "-vf", "scale=1100:-2", "-q:v", "3", probe)
    if not os.path.exists(probe):
        return True                      # cannot probe: do not block the run
    seen = design.read_text(Image.open(probe), project)
    if _norm_words(seen) != _norm_words(line):
        seen = design.read_lettering(Image.open(probe), project)
    ok = _norm_words(line) in _norm_words(seen) or \
        _norm_words(seen) in _norm_words(line) and len(_norm_words(seen)) > 6
    print(f"[title ] text at {int(frac * 100)}% of clip: "
          f"{'still readable' if ok else 'GONE'} (saw {seen[:48]!r})")
    return ok


def _data_uri(path):
    import base64
    ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
    return (f"data:image/{'jpeg' if ext in ('jpg', 'jpeg') else ext};base64,"
            + base64.b64encode(open(path, "rb").read()).decode())


def _find_video_url(d):
    from . import motion
    return motion._find_video_url(d)


def _title_seq(clip, w, h, dur, workdir, name, product=None):
    """The generated title sequence, trimmed to the beat and normalised.

    Played straight. Its only treatment is the same push the rest of the reel
    uses, so it belongs to the cut instead of sitting outside it.
    """
    dst = os.path.join(workdir, name + ".mp4")
    # Start near the top of the generated clip. The type has to be ON SCREEN
    # and readable for the whole beat; starting a third of the way in cost the
    # line its establishing moment and clipped the tail of the move.
    start = max(0.0, min(0.4, media.duration(clip) - dur))
    r = sh(config.FFMPEG, "-v", "error", "-y", "-ss", f"{start:.2f}",
           "-t", f"{dur:.2f}", "-i", clip, "-an", "-filter_complex",
           f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
           # NO push on a text sequence. The body push scales 22% and that
           # is exactly how much of "…worlds out" and "example.invalid" fell off
           # the right edge. The generated clip already carries its own
           # camera move; adding mine only crops the line.
           f"crop={w}:{h},setsar=1,"
           + _grade() + ",tpad=stop_mode=clone:stop_duration=1.0",
           *config.intermediate_encode_args(), "-r", str(FPS),
           "-frames:v", str(int(round(dur * FPS))), dst)
    if not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise RuntimeError(f"title sequence render failed: {r.stderr[-300:]}")
    # THE PRODUCT GETS NAMED HERE. Without this the reel does not say what it
    # is selling until the end card, and a viewer who leaves at 20s never
    # learns the name at all. The wordmark is the registered file, never
    # generated -- a model-drawn logo is an off-brand logo.
    logo = products.brand_logo(product) if product else None
    if logo and os.path.exists(logo):
        from . import overlays
        # LOW and centred, not middle: the generated headline owns the centre
        # of the frame, and a centred wordmark lands straight on top of it.
        # TOP-LEFT, out of the type's way. Centred-bottom put it directly
        # under a headline that fills the frame, where it read as debris.
        png = overlays._render_png(
            workdir, name + "_wm",
            overlays.badge(logo, x=int(w * 0.045), y=int(h * 0.072),
                           h=int(h * 0.062), scrim=0.0), size=(w, h))
        marked = os.path.join(workdir, name + "_wm.mp4")
        rise = int(h * 0.03)
        r2 = sh(config.FFMPEG, "-v", "error", "-y", "-i", dst,
                "-loop", "1", "-t", f"{dur}", "-framerate", str(FPS), "-i", png,
                "-filter_complex",
                f"[1:v]format=rgba,fade=in:st=0.15:d=0.45:alpha=1,"
                f"fade=out:st={max(0.0, dur - 0.3):.2f}:d=0.3:alpha=1[m];"
                f"[0:v][m]overlay=0:y='{rise}*(1-(1-pow(1-min(max(t-0.15,0)/0.6,1),3)))'"
                f":shortest=1,format=yuv420p",
                *config.intermediate_encode_args(), "-r", str(FPS), marked)
        if os.path.exists(marked) and os.path.getsize(marked) > 0:
            return marked
        print(f"[title ] wordmark composite failed: {r2.stderr[-200:]}")
    return dst


def _bed(src, at, w, h, dur, dst, crop=None):
    """A soft, dark, slowly-drifting still from the film itself, for a brand
    beat to sit on. A brand beat cut to flat black reads as a slide; keeping
    the world behind it keeps the reel one piece."""
    sh(config.FFMPEG, "-v", "error", "-y", "-ss", f"{at:.2f}", "-i", src,
       "-frames:v", "1", "-vf",
       _crop_vf(crop)
       + f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
       dst + ".png")
    # zoompan emits `d` frames PER INPUT FRAME. Feeding it a LOOPED still
    # (-loop 1 -t dur = dur*FPS input frames) therefore emits (dur*FPS)^2
    # frames -- a 2.5s beat came out 187s long and made a "30 second" reel
    # 300 seconds. The still must arrive as exactly ONE frame and zoompan's
    # `d` alone decides the length.
    frames = int(round(dur * FPS))
    zoom = f"zoompan=z='min(zoom+0.0006,1.10)':d={frames}:s={w}x{h}:fps={FPS}"
    # A flat scrim, not an exposure shift: `eq=brightness` on an already-dark
    # frame crushes it to mud and on a bright one barely reads, so the type
    # would have different contrast on every shot. A fixed-opacity black plate
    # gives the same legibility whatever the bed happens to be.
    sh(config.FFMPEG, "-v", "error", "-y",
       "-i", dst + ".png",
       "-f", "lavfi", "-t", f"{dur}", "-i", f"color=c=black:s={w}x{h}:r={FPS}",
       "-filter_complex",
       f"[0:v]{zoom},gblur=sigma={GRAMMAR_BLUR + 8},eq=saturation=0.62,"
       f"setsar=1[b];[1:v]format=rgba,colorchannelmixer=aa=0.38[s];"
       f"[b][s]overlay=0:0:shortest=1,format=yuv420p",
       *config.intermediate_encode_args(), "-frames:v", str(frames), dst)
    return dst


def _brand_beat(line, sub, plan, w, h, dur, workdir, name, bedsrc, bed_at,
                crop=None, cine=None, cine_at=0.0):
    """A brand beat: real wordmark + one line over a moving bed.

    `cine` is the studio's generated cinematic clip; a segment of it is the
    bed. Falls back to a blurred still from the film only when generation was
    skipped, so the beat is never a flat slide by default.
    """
    from . import overlays
    logo = products.brand_logo(plan["product"])
    # Two passes of the SAME layout, each hiding the other element, so the
    # mark and the line can enter on different timings. One flat cross-fade of
    # one flat PNG is what made a generated cinematic bed still read as a
    # slide -- the bed was moving and the type was not.
    png_mark = overlays._render_png(
        workdir, name + "_m",
        overlays.brandcard(line, logo=logo, w=w, h=h, size=int(h * 0.062),
                           sub=sub, only="mark"), size=(w, h))
    png_line = overlays._render_png(
        workdir, name + "_l",
        overlays.brandcard(line, logo=logo, w=w, h=h, size=int(h * 0.062),
                           sub=sub, only="line"), size=(w, h))
    dst = os.path.join(workdir, name + ".mp4")
    bed = os.path.join(workdir, name + "_bed.mp4")
    if cine and os.path.exists(cine):
        # A scrim still goes over the generated bed: the type has to read at
        # the same contrast on every studio's card, and three different
        # generated clips will not agree on how bright they are.
        # `cine` may be a clip or the shared base STILL. A still needs -loop
        # and no seek; feeding one through the clip path yields a single
        # frame and the bed comes out as a freeze.
        is_still = cine.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        src_args = (["-loop", "1", "-t", f"{dur}", "-framerate", str(FPS),
                     "-i", cine] if is_still
                    else ["-ss", f"{cine_at:.2f}", "-t", f"{dur}", "-i", cine])
        sh(config.FFMPEG, "-v", "error", "-y", *src_args,
           "-f", "lavfi", "-t", f"{dur}", "-i", f"color=c=black:s={w}x{h}:r={FPS}",
           "-filter_complex",
           # 34% was not enough on a BRIGHT generated card -- the Story book
           # card puts light exactly where the wordmark sits. A darker FLAT
           # scrim, not a band: a band has to end somewhere, and gblur only
           # blurs within a layer's own bounds, so its top and bottom edges
           # stay as two hard lines across the picture.
           f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
           f"crop={w}:{h},setsar=1,fps={FPS},"
           f"{_energy(w, h, dur, 'body', 'push')},{_grade()}[b];"
           f"[1:v]format=rgba,colorchannelmixer=aa=0.38[s];"
           f"[b][s]overlay=0:0:shortest=1,format=yuv420p",
           *config.intermediate_encode_args(), bed)
    if not os.path.exists(bed) or os.path.getsize(bed) == 0:
        _bed(bedsrc, bed_at, w, h, dur, bed, crop)
    # Each layer is ONE image and has to be looped into its own stream before
    # `fade` can move its alpha over time (a bare still holds frame 0's alpha,
    # which is zero -- an invisible card).
    #
    # The MOVE is in `overlay`'s y expression, which DOES take time: each
    # layer rises into place with an ease-out and settles. ffmpeg cannot scale
    # a stream over time, so a rise is the kinetic move available -- and it is
    # the right one for type anyway. The line is held back 0.22s behind the
    # mark so the card resolves in two steps instead of appearing at once.
    rise = int(h * 0.045)
    lag = 0.22
    ease = "(1-pow(1-min(t/%(d)s,1),3))"          # cubic ease-out, 0 -> 1

    def _y(delay):
        e = ease % {"d": 0.55}
        return (f"'{rise}*(1-{e.replace('t/', f'max(t-{delay},0)/')})'"
                if delay else f"'{rise}*(1-{e})'")

    r = sh(config.FFMPEG, "-v", "error", "-y", "-i", bed,
           "-loop", "1", "-t", f"{dur}", "-framerate", str(FPS), "-i", png_mark,
           "-loop", "1", "-t", f"{dur}", "-framerate", str(FPS), "-i", png_line,
           "-filter_complex",
           f"[1:v]format=rgba,fade=in:st=0:d=0.30:alpha=1,"
           f"fade=out:st={max(0.0, dur - 0.32):.2f}:d=0.32:alpha=1[m];"
           f"[2:v]format=rgba,fade=in:st={lag}:d=0.30:alpha=1,"
           f"fade=out:st={max(0.0, dur - 0.32):.2f}:d=0.32:alpha=1[l];"
           f"[0:v][m]overlay=0:y={_y(0)}[bm];"
           f"[bm][l]overlay=0:y={_y(lag)}:shortest=1,format=yuv420p",
           *config.intermediate_encode_args(), "-r", str(FPS), dst)
    if not os.path.exists(dst) or os.path.getsize(dst) == 0:
        raise RuntimeError(f"brand beat render failed: {r.stderr[-300:]}")
    return dst


def _outro(plan, w, h, dur, workdir, bedsrc, bed_at, crop=None, cine=None,
           cine_at=0.0):
    """The endcard as an APPENDED SEGMENT. Never an overlay on content.

    brandkit.endcard() holds a 1080x1920 asset built for the vertical feeds.
    Scaling that into a 16:9 frame would letterbox a card, so a landscape
    sizzle rebuilds the same two elements (registered wordmark + the studio's
    CTA) at its own frame size instead.
    """
    return _brand_beat(plan["cta"], None, plan, w, h, dur, workdir, "outro",
                       bedsrc, bed_at, crop, cine=cine, cine_at=cine_at)


def render(plan, out, size="1920x1080", project="", workers=4, cine=None,
           title_clip=None, end_clip=None):
    """Shot list -> silent picture cut. Music is a separate pass (`score`)."""
    w, h = (int(x) for x in size.lower().split("x"))
    with tempfile.TemporaryDirectory() as td:
        shots = plan["shots"]
        cold = next(s for s in shots if s.get("role") == "platform")
        pay = next(s for s in shots if s.get("role") == "payoff")
        jobs, parts = [], []
        for i, s in enumerate(shots):
            dst = os.path.join(td, f"s{i:02d}.mp4")
            parts.append(dst)
            jobs.append((i, s, dst))
        # Alternate the camera move shot to shot, and whip every OTHER cut.
        # Two whips in a thirty-second reel is not a texture, it is a rounding
        # error -- that is why the transitions read as identical hard cuts.
        n_body = 0
        for i, sh_ in enumerate(shots):
            if sh_.get("role") == "title":
                continue
            if sh_.get("role") == "platform":
                sh_["whip"] = True          # the rush wants every cut to snap
                continue
            sh_["move"] = "punch" if n_body % 2 else "push"
            sh_["whip"] = (n_body % 2 == 0)
            n_body += 1
        srcs = [(i, s, d) for i, s, d in jobs if s.get("role") != "title"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_seg_source, s, w, h, d): (i, s)
                    for i, s, d in srcs}
            for f in concurrent.futures.as_completed(futs):
                i, s = futs[f]
                f.result()
                print(f"[render] {i:02d} {s['role']:9} {s['dur']}s "
                      f"{os.path.basename(s['file'])[:44]}")
        # Brand beats drive Chrome, so they run on this thread rather than
        # contending with the segment pool.
        for i, s, d in jobs:
            if s.get("role") != "title":
                continue
            if title_clip:
                # The title beat IS the generated sequence. Nothing is composited
                # over it: the headline is already inside the shot, and laying a
                # second copy of the words on top is the slide we are removing.
                parts[i] = _title_seq(title_clip, w, h, float(s["dur"]), td,
                                      f"s{i:02d}t", product=plan.get("product"))
                print(f"[render] {i:02d} title     {s['dur']}s \"{plan['hook']}\"")
            else:
                # No generated title (--no-cinecard, or the generation failed).
                # Ship a DETERMINISTIC title beat -- real wordmark + the hook
                # line over a blurred still from the film -- instead of failing
                # the whole reel. A montage must NEVER come out empty because a
                # title could not be generated; the composited line is fully
                # readable to the end of the beat by construction, which is the
                # readability the generated path only approximates (MAR-108).
                bed = cold if cold.get("file") else pay
                parts[i] = _brand_beat(plan["hook"], None, plan, w, h,
                                       float(s["dur"]), td, f"s{i:02d}t",
                                       bed["file"], float(bed.get("at", 0.0)),
                                       bed.get("crop"), cine=cine)
                print(f"[render] {i:02d} title*    {s['dur']}s \"{plan['hook']}\""
                      f" (deterministic fallback -- no generated card)")

        # Labels burn per shot, after the picture exists, so the label sits on
        # the graded frame and not on a raw source.
        for i, s, d in jobs:
            if s.get("role") == "title":
                continue
            # An authored plan puts its own copy on whatever shot needs it,
            # including the closing beat; only fall back to the plan payoff
            # when a payoff shot carries no line of its own.
            # An explicit "label": "" means this shot carries no copy. Only a
            # shot with NO label key at all inherits the plan payoff -- else
            # the payoff line reappears on a shot that already said it.
            text = (s["label"] if "label" in s
                    else (plan["payoff"] if s.get("role") == "payoff" else None))
            if not text:
                continue
            png = _label_png(text, s.get("credit"), w, h, td, f"l{i:02d}")
            lab = os.path.join(td, f"s{i:02d}L.mp4")
            sdur = float(s["dur"])
            sh(config.FFMPEG, "-v", "error", "-y", "-i", d,
               "-loop", "1", "-t", f"{sdur}", "-framerate", str(FPS), "-i", png,
               "-filter_complex",
               # label fades in over 0.35s and holds; no white flashes anywhere
               f"[1:v]format=rgba,fade=in:st=0:d=0.35:alpha=1,"
               f"fade=out:st={max(0.0, sdur - 0.3):.2f}:d=0.3:alpha=1[l];"
               f"[0:v][l]overlay=0:0:shortest=1,format=yuv420p",
               *config.intermediate_encode_args(), "-r", str(FPS), lab)
            if os.path.exists(lab) and os.path.getsize(lab) > 0:
                parts[i] = lab

        # THE END CARD IS A GENERATED SEQUENCE TOO. Asked for three times and
        # deferred three times because the wordmark has to stay a compositor
        # asset -- but that only ever justified the LOGO being composited, not
        # the whole card being a still with type laid on it. The CTA is now
        # typeset into its own generated shot and flies with the camera; only
        # the wordmark is laid over the top.
        if end_clip:
            parts.append(_title_seq(end_clip, w, h, OUTRO_S, td, "outro",
                                    product=plan.get("product")))
        else:
            cine_end = max(0.0, media.duration(cine) - OUTRO_S) if cine else 0.0
            parts.append(_outro(plan, w, h, OUTRO_S, td,
                                pay["file"], float(pay["at"]), pay.get("crop"),
                                cine=cine, cine_at=cine_end))

        lst = os.path.join(td, "concat.txt")
        with open(lst, "w") as f:
            f.writelines(f"file '{p}'\n" for p in parts)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        r = sh(config.FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0",
               "-i", lst, "-c:v", "libx264", "-preset", "slow", "-crf", "18",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", out)
        if not os.path.exists(out):
            raise RuntimeError(f"concat failed: {r.stderr[-400:]}")
    # A 30-second ask that renders 300 seconds passed every content gate and
    # was still worthless. The plan's own arithmetic is not evidence about the
    # file; measure the file. (This gate is what caught the zoompan bug.)
    want = sum(float(s["dur"]) for s in plan["shots"]) + OUTRO_S
    got = media.duration(out)
    if abs(got - want) > 1.5:
        raise RuntimeError(
            f"rendered length {got:.1f}s does not match the planned "
            f"{want:.1f}s (shots + {OUTRO_S}s outro). A segment rendered at "
            f"the wrong length; the cut is not shippable.")
    return out


# -------------------------------------------------------------------- score

MUSIC_ENDPOINT = "fal-ai/stable-audio-25/text-to-audio"
EST_MUSIC = 0.20


def _stable_audio(prompt, seconds, out, project=""):
    """A purpose-written cinematic bed from Stable Audio 2.5.

    Chosen over the ElevenLabs default because it is built for long-form
    instrumental and sound design rather than song structure, and it is one of
    the models our own audio doctrine already names as pipeline-safe.
    """
    url = audio_post._fal(
        MUSIC_ENDPOINT,
        {"prompt": prompt, "seconds_total": int(round(seconds))},
        EST_MUSIC, f"sizzle bed {seconds:.0f}s", project,
        service="fal-audio", find=audio_post._find_audio_url)
    audio_post._download(url, out)
    return out


def _music_envelope(plan, dur):
    """A volume curve shaped to the cut, as an ffmpeg `volume` expression.

    Anchors are derived from the plan's own structure rather than guessed:
    the bed ducks under the title beat and the tool section (where copy must
    be read), then rises through the closing run and peaks on the payoff.
    """
    t = 0.0
    title_end = body_start = None
    last_body_start = 0.0
    for sh_ in plan["shots"]:
        d = float(sh_["dur"])
        if sh_.get("role") == "title":
            title_end = t + d
        elif sh_.get("role") == "body" and body_start is None:
            body_start = t
        if sh_.get("role") in ("body", "payoff"):
            last_body_start = t
        t += d
    title_end = title_end or dur * 0.3
    body_start = body_start or title_end
    # (time, gain) anchors, linearly interpolated
    pts = [(0.0, 0.62), (title_end - 0.4, 0.80), (body_start + 0.3, 0.58),
           (last_body_start - 4.0, 0.72), (last_body_start, 1.00),
           (dur, 0.92)]
    pts = sorted({round(max(0.0, min(dur, x)), 2): g for x, g in pts}.items())
    expr = f"{pts[-1][1]}"
    for (t0, g0), (t1, g1) in zip(pts, pts[1:]):
        span = max(0.01, t1 - t0)
        expr = (f"if(between(t,{t0},{t1}),"
                f"{g0}+({g1}-{g0})*(t-{t0})/{span},{expr})")
    return f"volume='{expr}':eval=frame"


def score(video, out, plan, work, project=""):
    """Music bed under the picture, at the shipped loudness/true-peak targets.

    The bed goes through `audio_post.music`, which already does kit-library
    first / FAL second and registers what it generates. A sizzle declares
    itself a montage (many segments, no payoff jump) so `pick_bed` reaches for
    a driven bed the same way it would for any montage cut.
    """
    dur = media.duration(video)
    bed = os.path.join(work, "bed.mp3")
    if not os.path.exists(bed):
        if plan.get("music"):
            _stable_audio(plan["music"], dur + 1.0, bed, project=project)
        else:
            audio_post.music({"id": f"sizzle-{plan['product']}",
                              "duration_s": dur,
                              "segments": plan["shots"],
                              "format": "F5",
                              "title": f"{plan['studio']}: {plan['hook']}"},
                             bed, project=project)
    fade = 1.4
    # DYNAMICS. loudnorm alone flattened the bed to 1.2 dB of variation across
    # thirty seconds (LRA 3.3) -- the build written into the music prompt was
    # normalised straight back out, and a sizzle without an arc is a plateau.
    # The envelope is applied AFTER loudnorm so it survives: the bed sits back
    # under the opening and the tool section, where the callouts have to be
    # read, then opens up into the closing worlds and the end card.
    env = _music_envelope(plan, dur)
    sh(config.FFMPEG, "-v", "error", "-y", "-i", video, "-i", bed,
       "-filter_complex",
       f"[1:a]atrim=0:{dur:.2f},asetpts=N/SR/TB,"
       f"afade=t=in:st=0:d=0.6,afade=t=out:st={max(0, dur - fade):.2f}:d={fade},"
       f"loudnorm=I=-11.5:TP=-1.5:LRA=11,{env}[a]",
       "-map", "0:v", "-map", "[a]", "-c:v", "copy",
       "-c:a", "aac", "-b:a", "192k", "-shortest",
       "-movflags", "+faststart", out)
    audio_post.enforce_true_peak(out)
    return out


# ---------------------------------------------------------------------- run

def _expand(patterns):
    import glob
    out = []
    for p in patterns:
        p = os.path.expanduser(p)
        hits = sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
        for h in hits:
            if os.path.isdir(h):
                for root, _, files in os.walk(h):
                    out += [os.path.join(root, f) for f in sorted(files)
                            if f.lower().endswith((".mp4", ".mov", ".m4v"))]
            elif h.lower().endswith((".mp4", ".mov", ".m4v")):
                out.append(h)
    return [p for i, p in enumerate(out) if p not in out[:i]]


def build_from_script(script_path, out, size="1920x1080", project="",
                      work=None, no_cinecard=False):
    """Render a HUMAN-AUTHORED shot list.

    The brain is a good editor of footage it can see; it is not the director
    of a product argument. A sizzle that has to teach what a tool does, in a
    specific order, with specific copy on specific frames, is authored by a
    person -- so this path takes that plan verbatim, skips the survey and the
    brain entirely, and only does what the engine is actually good at:
    grading, moving, cutting, generating the title/end sequences and scoring.

    The clearance gate is skipped too, deliberately: a hand-authored shot list
    names exact files and timecodes a human has looked at, which is a stronger
    guarantee than a vision pass, and the gate exists to protect automatic
    selection.
    """
    plan = json.load(open(script_path))
    product = products.ALIASES.get(plan["product"], plan["product"])
    plan["product"] = product
    plan["studio"] = products.PRODUCTS[product]["name"]
    work = work or os.path.join(os.path.dirname(os.path.abspath(out)), "_work")
    os.makedirs(work, exist_ok=True)

    total = sum(float(s["dur"]) for s in plan["shots"]) + OUTRO_S
    print(f"[script] {plan['studio']}: {len(plan['shots'])} shots, "
          f"{total:.1f}s with outro")
    for s in plan["shots"]:
        if s.get("role") == "title":
            continue
        if s["file"].lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        d = media.duration(s["file"])
        if float(s.get("at", 0)) + float(s["dur"]) > d + 0.05:
            raise SystemExit(f"{os.path.basename(s['file'])}: "
                             f"{s['at']}+{s['dur']} runs past its {d:.1f}s end")

    W, H = (int(x) for x in size.lower().split("x"))
    title_clip = end_clip = cine = None
    if not no_cinecard:
        title_clip = cinecard(product, W, H, 6.0, work, project=project,
                              line=plan["hook"], tag="title")
        end_clip = cinecard(product, W, H, 5.0, work, project=project,
                            line=plan["cta"], tag="end")
        cine = os.path.join(work, f"cinecard_{product}_base.png")
        cine = cine if os.path.exists(cine) else None
    json.dump(plan, open(os.path.join(work, "plan.json"), "w"), indent=1)

    mute = os.path.join(work, "picture.mp4")
    render(plan, mute, size=size, project=project, cine=cine,
           title_clip=title_clip, end_clip=end_clip)
    score(mute, out, plan, work, project=project)
    print(f"[done  ] {out}  ({media.duration(out):.1f}s)")
    return out


def build(product, clip_patterns, out, seconds=30, size="1920x1080",
          brain="gpt", work=None, project="", crop=None,
          allow_weapons=False, window=MAX_WINDOW, no_cinecard=False,
          replan=False):
    crop = crop_map(crop) if isinstance(crop, (list, tuple)) else crop
    product = products.ALIASES.get(product, product)
    clips = _expand(clip_patterns)
    if not clips:
        raise SystemExit("no clips matched")
    work = work or os.path.join(os.path.dirname(os.path.abspath(out)), "_work")
    os.makedirs(work, exist_ok=True)
    print(f"[sizzle] {products.PRODUCTS[product]['name']}: {len(clips)} candidates")

    surveyed = survey(clips, product, os.path.join(work, "survey.json"),
                      project=project, crop=crop, window=window)
    pool, drops = cleared(surveyed, allow_weapons=allow_weapons)
    for f, why in drops:
        print(f"[drop  ] {os.path.basename(f)[:52]:52} {why[:70]}")
    if len(pool) < 6:
        raise SystemExit(f"only {len(pool)} clips cleared; a sizzle needs 6+. "
                         "Widen the pool or fix the sources.")
    print(f"[sizzle] {len(pool)} cleared, {len(drops)} dropped")

    # `--seconds` is the runtime of the file, so the appended brand outro
    # comes out of that budget rather than being added on top of it. A "30
    # second" reel that runs 32.6s is not what was asked for.
    # Reuse a pinned plan when one exists (mirrors `cut --replan`): the brain is
    # non-deterministic, so re-authoring on every run changes the copy -> the
    # title line changes -> the cinecard cache (keyed on the line) misses -> the
    # slow generative title beat regenerates. Reusing plan.json keeps a re-render
    # (e.g. a label/text tweak) on the cheap deterministic layers. --replan forces
    # a fresh plan.
    plan_path = os.path.join(work, "plan.json")
    if not replan and os.path.exists(plan_path):
        p = json.load(open(plan_path))
        print(f"[plan  ] reusing {plan_path} (pass --replan to re-author)")
    else:
        p = plan(product, pool, seconds=seconds - OUTRO_S, brain=brain,
                 project=project)
        p["allow_weapons"] = bool(allow_weapons)
        json.dump(p, open(plan_path, "w"), indent=1)
    if allow_weapons:
        _write_social_warning(out, p, pool)
    print(f"[copy  ] hook   {p['hook']}\n[copy  ] payoff {p['payoff']}\n"
          f"[copy  ] cta    {p['cta']}")

    cine = title_clip = None
    W, H = (int(x) for x in size.lower().split("x"))
    if not no_cinecard:
        try:
            # Two generations: the TITLE SEQUENCE carries the hook inside the
            # shot; the untyped bed backs the closing brand card, where the
            # registered wordmark has to be pixel-exact and therefore stays a
            # compositor asset.
            title_clip = cinecard(product, W, H, 6.0, work, project=project,
                                  line=p["hook"], tag="title")
            # The closing card rides the SAME still the title sequence was
            # built from, moved by a slow push -- not a second generation.
            # A second generation drifts: asked for the same doorway world it
            # returned a rainbow vortex that shared nothing with the title,
            # so the reel opened and closed in two unrelated places. Same
            # still, guaranteed same world, and one less video to pay for.
            cine = os.path.join(work, f"cinecard_"
                                f"{products.ALIASES.get(product, product)}"
                                f"_base.png")
            cine = cine if os.path.exists(cine) else None
        except Exception as e:   # noqa: BLE001 -- a card is not worth the reel
            print(f"[card  ] generation FAILED ({e}); the title beat falls back "
                  f"to a deterministic wordmark + hook over a blurred still from "
                  f"the film (readable, never empty -- MAR-108)")
    # title_clip may be None here (--no-cinecard, or the generation above
    # failed). That is NOT a reason to ship nothing: render() builds a
    # deterministic composited title beat when no generated clip exists, so
    # the reel always ships with a readable title instead of raising (MAR-108).
    mute = os.path.join(work, "picture.mp4")
    render(p, mute, size=size, project=project, cine=cine,
           title_clip=title_clip)
    score(mute, out, p, work, project=project)
    print(f"[done  ] {out}  ({media.duration(out):.1f}s)")
    return out
