"""Single image -> finished vertical post (the `motion` stage, v2).

WHY THIS EXISTS
Content often starts from one still (keyart, a screenshot, a poster), not a
recording. Every other stage assumes footage; this one makes footage.

THE ARCHITECTURE (settled by the Jul 29 V1->V5 review rounds; playbook M1-M8)
  brain       direct._ask_json authors the post as data: hook/payoff/CTA under
              the copy contract (M8), a story archetype with a conflict cold
              open (M1), a character, and per-shot camera direction
  generation  TEXT-FREE video only (M7): character + world + camera. The model
              proved it cannot be trusted with type (it restyles it, moves it
              into platform chrome, copies stray text from references, and
              once rendered a layout card as a literal banner). REAL-ART mode
              (--real-art, or "real_art": true in the campaign spec) is the
              treatment for posts about EXISTING games and real product UI: it
              skips the invented character frame and animates the real still by
              CAMERA ONLY under a "invent nothing, alter no existing text/logos"
              contract (see REAL_ART_CONTRACT)
  type        ALL type is composited by our layer: campaign lettering assets
              (generated once per campaign, style-ref derived per post,
              spelling-verified) + brand sans for the CTA. Hook readable on
              frame 1 (H-rule; frame 1 is the cover), payoff and CTA hold to
              the last frame
  gates       design critic on composed frames (D1-D7), judge (incl. the copy
              contract + hook-from-frame-1), ledger on every AI call, and the
              human `screened` verdict before anything publishes

CAMPAIGN SPEC (the variables; the code above is the invariant)
~/.reelly/campaigns/<name>.json:
  {"product": "video", "register": "pro", "cta": "watch the full version",
   "lettering_style_ref": "/path/to/locked-lettering.png",
   "palette": {"scene": "cool blue", "accent": "warm ember gold"},
   "archetypes": ["identity reversal", "zero-cost access"],
   "real_art": false}   # true for existing-game / real-UI posts (camera-only)

MODEL TIERS (M2: draft first, hero is an explicit spend)
  draft  bytedance/seedance-2.0/fast/reference-to-video  720p  ~$2.50
  hero   bytedance/seedance-2.5/image-to-video            720p ~$5+  (real-art only; fal caps Seedance at 720p, we upscale to 1080x1920)
Prices drift: re-check the fal model page before trusting estimates.
"""
import base64
import json
import os
import subprocess

from . import config, ledger

# Playbook: Motion section (M-rules, playbook v0.15). Cited in plans.
RULES = {
    "M1": "M1: the hook is a promise and the clip must pay it off on screen",
    "M2": "M2: draft tier renders the review artifact; hero is an explicit ask",
    "M3": "M3: graphics are placed from the generated frames, subject-aware",
    "M4": "M4: generated media is labelled AI in the plan (provenance recorded)",
    "M5": "M5: one message, one CTA; the end card is the only ask",
    "M6": "M6: references carry no text and derive from one character source",
    "M7": "M7: the video model never renders type; the compositor owns all text",
    "M8": "M8: copy contract: on brand, fun, one clear CTA, short",
}

EST_I2V = 2.50
EST_FRAME = 0.15
VIDEO_ENDPOINT = "bytedance/seedance-2.0/fast/reference-to-video"
# HERO IS A DIFFERENT MODEL, NOT THE SAME ONE TURNED UP. The `fast` tier
# rejects 1080p outright ("Input should be '480p' or '720p'"), so the hero
# path asked for a resolution its own endpoint cannot produce and every hero
# render died at the API. Seedance 2.5 is the quality tier and is already what
# `sizzle` uses for its cinecards. It takes the source still as the FIRST
# FRAME (image-to-video) rather than as a loose reference, which is exactly
# the real-art contract: move the camera over the real art, invent nothing.
VIDEO_ENDPOINT_HERO = "bytedance/seedance-2.5/image-to-video"
IMAGE_ENDPOINT = "google/nano-banana-pro/edit"

EST_MINIMAX = 3.00
EST_GROK_VIDEO = 2.00
EST_GROK_IMAGE = 0.15
EST_H3MAX = 0.60      # H3 Max i2v: 15s @ 768P (promo $0.04/s). ~20x faster + cheaper than base H3.

# ------------------------------------------------------------ model registry
#
# WHY MORE THAN ONE MODEL. Seedance is excellent at moving a camera over
# existing artwork and at stylised worlds, and poor at believable humans -- it
# invented a photorealistic spokeswoman and a three-person meeting when asked
# to animate a cast panel (MAR-37). The answer to "our people look wrong" is
# to pick a model built for people, not to ban people from the frame.
#
#   seedance  default. Stylised art, camera moves, game worlds.
#   minimax   MiniMax H3 reference-to-video. Multi-reference (up to 9), native
#             portrait, integer `duration`, resolution up to 2K.
#   grok      Grok Imagine v1.5. Photoreal, strong text rendering, and the
#             only model here that reaches 1080p. v1.5 dropped the
#             aspect_ratio input the older endpoint had, so the SOURCE must
#             already be portrait -- nothing downstream will reframe it.
#   h3max     DEFAULT. H3 Max image-to-video, the FAST path: animates ONE
#             composed keyframe at 768P in ~20s for ~$0.60/15s -- roughly 20x
#             faster + cheaper than base H3. Single frame only (image_url), no
#             loras, no audio refs, no aspect_ratio (follows the source).
#
# EVERY ID IS USED VERBATIM in the queue URL, and a wrong one 404s at POLL
# time rather than at submit -- it fails after the money is spent. All five
# ids below were checked against fal.ai before being wired in, and each
# payload matches that model's published schema, which differ more than they
# look: MiniMax takes `image_url`, Seedance draft takes `image_urls`.

VIDEO_MODELS = ("seedance", "minimax", "grok")
# Per-model prompt ceilings. MiniMax hard-rejects over 2000 chars with
# "String should have at most 2000 characters" -- and it rejects at SUBMIT,
# so it is cheap to hit but still a dead render. Our real-art prompt plus the
# contract runs well past that.
# grok caps at 2000; H3 publishes no limit, so it is left uncapped.
PROMPT_CAP = {"minimax": 0, "grok": 1900, "seedance": 0, "h3max": 0}


def _fit_prompt(model, prompt):
    """Trim a prompt to the model's ceiling, keeping whole sentences."""
    cap = PROMPT_CAP.get(model, 0)
    if not cap or len(prompt) <= cap:
        return prompt
    cut = prompt[:cap]
    stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:stop + 1] if stop > cap // 2 else cut).strip()


IMAGE_MODELS = ("nano-banana", "grok")


# Video-model registry: ONE place to add/swap a model or raise its reference
# cap. `ref_key` is the payload field the model wants its reference art under --
# a LIST under `image_urls` for the multi-reference models (feed @Image1..N up to
# `max_refs`), a single `image_url` for the single-frame image-to-video models.
# `max_refs` is the model's real ceiling (raise it here, per model -- NOT a
# blanket 3). `duration` is how that endpoint wants the length encoded.
# Swapping models at a call site is just passing a different `model` string.
#   - H3 (MiniMax) is strong on stylised camera-flythroughs over reference art.
#   - prompt_expansion_mode "disabled" on H3: our prompts carry explicit beats;
#     the endpoint defaults to "balanced", whose VLM rewrite discards the
#     sequencing we asked for (there is NO enable_prompt_expansion boolean).
#   - Grok v1.5 is the only 1080p path but is single-frame image-to-video.
VIDEO_MODELS = {
    # Seedance 2.0 fast IS reference-to-video: it takes an `image_urls` ARRAY
    # (up to 9 per the fal schema) + `aspect_ratio`. This is the multi-reference
    # workflow (@Image1..@ImageN).
    "seedance": dict(endpoint=VIDEO_ENDPOINT, ref_key="image_urls", max_refs=9,
                     duration="str", est=EST_I2V, ref_tag="@Image{n}",
                     extra={"aspect_ratio": "9:16", "resolution": "720p",
                            "generate_audio": True}),
    # H3 REFERENCE-TO-VIDEO (not image-to-video): the fal endpoint
    # `minimax/h3/reference-to-video` takes a `reference_image_urls` ARRAY (up to
    # 9) plus `aspect_ratio` (so it renders native portrait -- no letterbox) and
    # an integer `duration`. This is the multi-reference workflow; the old
    # image-to-video entry only accepted ONE first frame and could not ingest
    # @Image2..N, so it is replaced. prompt_expansion_mode "disabled": our prompts
    # are pinned (the live endpoint has no enable_prompt_expansion boolean; it
    # takes prompt_expansion_mode, default "balanced" -- which would rewrite them).
    # H3 names its references "Image 1"/"Image 2" (a space, no @) -- confirmed
    # against the fal endpoint schema (reference_image_urls, up to 12 assets).
    "minimax":  dict(endpoint="minimax/h3/reference-to-video",
                     ref_key="reference_image_urls", max_refs=9,
                     duration="int", est=EST_MINIMAX, ref_tag="Image {n}",
                     extra={"aspect_ratio": "9:16", "resolution": "768P",
                            "prompt_expansion_mode": "disabled",
                            "enable_safety_checker": True}),
    "grok":     dict(endpoint="xai/grok-imagine-video/v1.5/image-to-video",
                     ref_key="image_url", max_refs=1, duration="int",
                     est=EST_GROK_VIDEO, ref_tag="@Image{n}",
                     extra={"resolution": "1080p"}),
    # H3 MAX IMAGE-TO-VIDEO -- the DEFAULT fast path. `minimax/h3-max/image-to-video`
    # animates ONE keyframe under `image_url` (optional `end_image_url`) at 480P/768P.
    # It has NO loras, NO reference_audio_urls, and NO aspect_ratio (output follows
    # the source, so the keyframe must already be portrait). prompt_expansion_mode is
    # required. A 15s 768P clip renders in ~20s for ~$0.60 (promo) -- ~20x faster and
    # cheaper than base H3. Single-frame, so the pipeline composes the scene into one
    # cinematic keyframe first, then this animates it (motion prompt, no @Image tags).
    "h3max":    dict(endpoint="minimax/h3-max/image-to-video",
                     ref_key="image_url", max_refs=1, duration="int",
                     est=EST_H3MAX, ref_tag="@Image{n}",
                     extra={"resolution": "768P",
                            "prompt_expansion_mode": "disabled",
                            "enable_safety_checker": True}),
}


def _retag_prompt(prompt, model):
    """Render the prompt's canonical `@Image{n}` reference tags in the syntax the
    chosen model expects, so swapping models FORCES the prompt to match its
    reference API. The tag style is a per-model registry field (`ref_tag`):
    seedance/grok use `@Image1`, MiniMax H3 uses `Image 1` (a space, no @).
    A model with no `ref_tag` keeps the canonical `@Image{n}` form."""
    import re
    spec = VIDEO_MODELS.get(model) or {}
    tag = spec.get("ref_tag", "@Image{n}")
    return re.sub(r"@Image(\d+)", lambda m: tag.format(n=m.group(1)), prompt)


def _video_request(model, refs, prompt, seconds, hero=False):
    """(endpoint, payload, est_usd) for the chosen video model.

    `refs` is ONE data URI or a LIST of them (@Image1..@ImageN). Each model
    takes up to its own `max_refs` (see VIDEO_MODELS -- raise per model, never a
    blanket cap of 3). The reference art rides under the model's `ref_key`: a
    list for the multi-reference models, a single frame for the rest. Swapping
    models is just a different `model` string; nothing else at the call site
    changes.
    """
    prompt = _fit_prompt(model, prompt)
    refs = [refs] if isinstance(refs, str) else list(refs)
    if not refs:
        raise ValueError("_video_request needs at least one reference image")
    if hero:
        return (VIDEO_ENDPOINT_HERO,
                {"prompt": prompt, "image_url": refs[0], "aspect_ratio": "9:16",
                 "resolution": "720p", "duration": str(seconds),
                 "generate_audio": True},
                EST_I2V * 2)
    spec = VIDEO_MODELS.get(model) or VIDEO_MODELS["seedance"]
    used = refs[:max(1, spec["max_refs"])]
    # Multi-reference models (seedance image_urls, H3 reference_image_urls) take a
    # LIST; single-frame models (grok image_url) take one. Keyed on max_refs so a
    # new multi-ref endpoint just needs max_refs > 1, not a special-cased name.
    ref_field = ({spec["ref_key"]: used} if spec["max_refs"] > 1
                 else {spec["ref_key"]: used[0]})
    dur = str(seconds) if spec["duration"] == "str" else max(1, int(round(seconds)))
    payload = {"prompt": prompt, **ref_field, "duration": dur, **spec["extra"]}
    return (spec["endpoint"], payload, spec["est"])


def generate_from_refs(refs, prompt, seconds, out, model="seedance",
                       project="", label="refs-video"):
    """ONE video from N reference images + an explicit prompt, on any model.

    The reference-first, model-swappable entry for composed SEQUENCES (winner
    flythroughs, card reveals) that are neither a single hero nor a footage cut.
    `refs` is a list of image paths (become @Image1..@ImageN). Feed the model
    the exact sequence in the prompt; it holds the reference art and moves the
    camera. Change `model` to swap engines (seedance / minimax H3 / grok)."""
    from . import audio_post
    uris = [_data_uri(p) for p in refs]
    endpoint, payload, est = _video_request(model, uris, prompt, seconds)
    url = audio_post._fal(endpoint, payload, est, f"{label} {model}", project,
                          service="fal-video", find=_find_video_url, tries=900)
    tmp = out + ".dl.mp4"
    audio_post._download(url, tmp)
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", tmp,
                    "-vf", "scale=1080:1920:flags=lanczos",
                    "-c:v", "libx264", "-crf", "17", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k", out], check=True)
    os.remove(tmp)
    return out


def _image_request(model, prompt, refs=(), num_images=1, aspect="9:16"):
    """(endpoint, payload, est_usd) for the chosen image model.

    `refs` are data URIs to edit from; empty means pure text-to-image.
    """
    refs = list(refs)
    if model == "grok":
        if refs:
            return ("xai/grok-imagine-image/edit",
                    {"prompt": prompt, "image_urls": refs[:3],
                     "num_images": num_images, "aspect_ratio": aspect,
                     "resolution": "2k", "output_format": "png"},
                    EST_GROK_IMAGE)
        return ("xai/grok-imagine-image",
                {"prompt": prompt, "num_images": num_images,
                 "aspect_ratio": aspect, "resolution": "2k",
                 "output_format": "png"},
                EST_GROK_IMAGE)
    return (IMAGE_ENDPOINT,
            {"prompt": prompt, "image_urls": refs, "num_images": num_images},
            EST_FRAME)

# Placement safe band for composited type (M3/D6). Hook/payoff seek the
# minimum-occupancy row anywhere inside this band -- top-to-bottom, not just the
# upper third -- because on a full-frame character lineup the only clear row can
# be low. The right rail is heavier than the left (like/share/comment buttons
# live there). The CTA keeps its own lower band but now also clears faces.
FRAME_W, FRAME_H = 1080, 1920
SAFE_TOP = 120             # px clearance from the top edge
SAFE_BOTTOM = 320          # px clearance from the bottom edge
SAFE_LEFT = 60
SAFE_RIGHT = 120
# A lettering line is CENTRED on the frame (CSS left:50%/translateX(-50%)), so
# the rail it reaches first is the closer of the two -- the 120px right rail,
# not the 60px left. The widest a centred line can be without crossing either is
# twice its distance to the nearer rail. The old gate compared width against
# FRAME_W-SAFE_LEFT-SAFE_RIGHT (=900), which is the width of a LEFT-ANCHORED safe
# box; a centred 900px line still overran the right action rail by 30px.
MAX_LETTER_W = int(2 * min(FRAME_W / 2 - SAFE_LEFT,
                           (FRAME_W - SAFE_RIGHT) - FRAME_W / 2))  # = 840
# Reserved layout zones (reviewer 2026-08-13): the frame is three bands -- a TOP
# text band (hook/payoff), a MIDDLE subject band, and a BOTTOM CTA band. Text
# lives ONLY in the top/bottom bands and the subject ONLY in the middle, so type
# never lands on the subject. These fractions are stated to the image/video model
# at generation time (COMPOSITION_CONTRACT) and checked back by the band gate.
TEXT_ZONE_TOP_FRAC = 0.35        # top 35% reserved for hook/payoff
TEXT_ZONE_BOTTOM_FRAC = 0.22     # bottom 22% reserved for the CTA
TEXT_ZONE_TOP_Y = int(FRAME_H * TEXT_ZONE_TOP_FRAC)              # 672
TEXT_ZONE_BOTTOM_Y = int(FRAME_H * (1 - TEXT_ZONE_BOTTOM_FRAC))  # 1497
# Weighted occupancy: covering a FACE is the cardinal sin (D1); baked text/logos
# and critic-flagged avoid-regions come next (D2/D6); a body is least bad. These
# are relative weights on overlap AREA, so the ranking prefers a row that grazes
# a body over one that touches a face even slightly.
FACE_WEIGHT = 6.0
TEXT_WEIGHT = 3.0
BODY_WEIGHT = 1.0
D1_FACE_TOL = 0.03         # measured face-overlap fraction that fails D1

MOTION_PROMPT = """You are the editor brain for a vertical social post built
from ONE still image. Author the post as data. Return JSON only.

THE MESSAGE (what this post must communicate): {message}
THE IMAGE (scene context): {image_name}
STORY ARCHETYPES that measurably perform (pick the best fit): {archetypes}
BRAND PALETTE: scene is {scene_palette}; text accents are {accent_palette}
(complementary by rule D7 - never match text color to the scene).

COPY CONTRACT (M8 - every field, all four at once):
- ON BRAND: creator-centric, confident, declarative. Earned, not hypey.
  No em dashes, no emojis, no internal jargon. Name the benefit a stranger
  gets, never the mechanic.
- FUN: playful energy, a wink. Fun is voice and surprise, never hype
  adjectives, never corporate. FUN NEVER BREAKS MEANING: the hook must
  survive a literal one-read parse by a stranger. Wordplay is allowed only
  if the plain reading still makes sense ("your anime still can" fails:
  an anime cannot draw).
- ONE CLEAR CTA: a single unambiguous ask in plain words (M5).
- VOICE / ATTRIBUTION (M9): an organization account must never speak in the
  first person as a third-party creator. The CTA invites the viewer to act; it
  never puts words in a creator's mouth or claims someone else's work.
- SHORT: hook <= 7 words. payoff <= 6 words (it must fit the reserved top text
  band on two short lines; longer payoffs dominate the frame and crowd the CTA).
  cta <= 4 words. caption is one sentence and the hook of it fits the first 125
  characters.

STORY RULES:
- M1: cold open ON CONFLICT: shot 1 is mid-action, never setup-first. The
  hook is a promise; the payoff line lands it on screen in shot 2.
- The character you invent is the subject a viewer cares about. Describe them
  concretely (look, outfit, energy) so two shots can keep them identical.
- Shots carry CAMERA direction (angle, movement, foreground occlusion) and
  what moves vs stays. Aggressive beats flat: low angles, whip-tracks,
  orbit-and-push, motion blur, parallax. NO text in any shot (M7).

Return:
{{"archetype": "<one of the archetypes>",
  "hook": {{"text": "..."}},
  "payoff": {{"text": "..."}},
  "cta": "...", "caption": "one sentence, plain, fun",
  "register": "pro" | "meme",
  "character": "<concrete visual description>",
  "shots": [
    {{"seconds": 4, "prompt": "<shot 1: cold-open conflict, camera + action, no text>"}},
    {{"seconds": 4, "prompt": "<shot 2: payoff beat, camera + action, no text>"}}],
  "sound": "<one line per shot, e.g. 'roar and whoosh; hum resolving warm'>",
  "because": ["M1: ...", "M8: ...", "..."]}}"""


# ---------- plumbing ----------

def _data_uri(path):
    ext = (os.path.splitext(path)[1].lstrip(".").lower() or "png")
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    return f"data:image/{'png' if ext == 'png' else ext};base64,{b64}"


def _find_url(exts):
    def find(d):
        if isinstance(d, dict):
            for v in d.values():
                u = find(v)
                if u:
                    return u
        elif isinstance(d, list):
            for v in d:
                u = find(v)
                if u:
                    return u
        elif isinstance(d, str) and d.startswith("http") and d.split("?")[0].endswith(exts):
            return d
        return None
    return find


_find_video_url = _find_url((".mp4", ".mov", ".webm"))
_find_image_url = _find_url((".png", ".jpg", ".jpeg", ".webp"))


def _dur(path):
    out = subprocess.run([config.FFPROBE, "-v", "error", "-show_entries",
                          "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _frame_at(vid, t):
    """Grab one frame. Seeking at or past the last frame makes ffmpeg write an
    EMPTY file and still exit 0, so the return code proves nothing -- the size
    does. Clamp into the clip, then walk backwards before giving up."""
    import tempfile
    from PIL import Image
    try:
        span = _dur(vid)
    except Exception:
        span = 0.0
    t = max(0.0, t)
    if span > 0:
        t = min(t, max(0.0, span - 0.05))
    fd, p = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        for at in (t, max(0.0, t - 0.25), max(0.0, t - 1.0), 0.0):
            subprocess.run([config.FFMPEG, "-y", "-v", "error",
                            "-ss", f"{at:.2f}", "-i", vid, "-frames:v", "1", p],
                           check=False)
            if os.path.getsize(p) > 0:
                return Image.open(p).convert("RGB")
        raise RuntimeError(
            f"ffmpeg wrote no frame from {vid} at t={t:.2f}s (duration {span:.2f}s)")
    finally:
        if os.path.exists(p):
            os.unlink(p)


def _scaffold(name):
    root = os.path.join(config.DEFAULT_PROJECTS, name)
    for d in ("edl", "deliverables/final", "analysis", "qc", "source", "type"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    words = os.path.join(root, "analysis", "words.json")
    if not os.path.exists(words):
        # No speech exists: an honest empty transcript keeps judge/learn working.
        json.dump({"segments": []}, open(words, "w"))
    return root


def _warn_lettering_style(name, spec):
    """Loud warning when a campaign's lettering style is missing or is the SAME
    asset another campaign already uses.

    One style asset shared across campaigns/registers makes every campaign's
    type read identically — the candidates->pick flow (see the module docstring)
    exists precisely so each register gets its own style. This does not raise:
    style reuse is sometimes deliberate, so the human decides. It just refuses
    to be silent about it."""
    ref = spec.get("lettering_style_ref")
    if not ref:
        print(f"[motion] WARNING: campaign {name!r} has NO lettering_style_ref. "
              "Generate candidates and pick one per register (see module "
              "docstring: candidates -> pick) before this campaign can letter.")
        return
    camp_dir = os.path.join(config.HOME, "campaigns")
    norm = os.path.abspath(os.path.expanduser(ref))
    shared = []
    for fn in sorted(os.listdir(camp_dir)) if os.path.isdir(camp_dir) else []:
        if not fn.endswith(".json") or fn == f"{name}.json":
            continue
        try:
            other = json.load(open(os.path.join(camp_dir, fn)))
        except (json.JSONDecodeError, OSError):
            continue
        oref = other.get("lettering_style_ref")
        if oref and os.path.abspath(os.path.expanduser(oref)) == norm:
            shared.append(os.path.splitext(fn)[0])
    if shared:
        print(f"[motion] WARNING: campaign {name!r} reuses lettering_style_ref "
              f"{ref!r} verbatim from campaign(s) {', '.join(shared)}. Each "
              "register should get its own style pick (candidates -> pick); "
              "shared type makes the campaigns read as one.")


def load_campaign(name):
    """Campaign spec: the per-campaign variables (type asset, palette, CTA).
    Paths are per-machine, so specs live in ~/.reelly/campaigns, not the repo.

    LETTERING STYLE, candidates -> pick (why load warns): a campaign's
    `lettering_style_ref` is the LOCKED style asset every post's type is derived
    from, so it must be chosen per register, not inherited. The intended flow is:
    generate a spread of candidate style plates for the register, put them in
    front of reviewer, and pin the ONE he picks into this spec. A missing ref, or
    a ref copied verbatim out of another campaign's spec, is the shortcut that
    flattens every campaign to the same type (see memory
    `lettering-style-matches-content-register`); `load_campaign` warns loudly on
    both so the shortcut is a decision, not an accident.
    """
    p = os.path.join(config.HOME, "campaigns", f"{name}.json")
    if not os.path.exists(p):
        raise SystemExit(f"no campaign spec at {p}")
    spec = json.load(open(p))
    _warn_lettering_style(name, spec)
    return spec


# ---------- authoring ----------

SENSE_PROMPT = """Read this social post hook literally, as a stranger would:
HOOK: {hook}
PAYOFF: {payoff}
Reply JSON only:
{{"literal_meaning": "<one sentence: what the hook literally says>",
  "makes_sense": true/false}}
makes_sense is false if the literal reading is nonsense or needs contortion
(e.g. attributing an action to something that cannot do it)."""


def _apply_copy_override(plan, override):
    """Fold a human's hook/payoff/cta/caption into a brain-authored plan and
    stamp it hand-authored. The brain still supplies the structural fields
    (character, shots, sound) that generation needs; the human owns the words."""
    plan = dict(plan)
    if override.get("hook"):
        h = override["hook"]
        plan["hook"] = h if isinstance(h, dict) else {"text": str(h)}
    if override.get("payoff"):
        p = override["payoff"]
        plan["payoff"] = p if isinstance(p, dict) else {"text": str(p)}
    if override.get("cta"):
        plan["cta"] = str(override["cta"])
    if override.get("caption"):
        plan["caption"] = str(override["caption"])
    plan["copy_source"] = "hand-authored"
    return plan


def _author(message, image, campaign, brain, project, tries=4, copy_override=None):
    """Author the post as data under the copy contract (M8).

    copy_override: a supported escape hatch for when the sense gate keeps
    rejecting good copy. The brain is still run once for the structural fields
    (character, shots, sound), then the human's hook/payoff/cta/caption are
    folded in and the literal-sense gate is skipped (a human is the authority a
    stranger-read stands in for). Provenance is recorded as hand-authored.
    """
    from . import brandkit, design, direct
    pal = campaign.get("palette", {})
    note = ""
    last_why = None
    lint_saves = 0
    for i in range(tries):
        plan = direct._ask_json(
            MOTION_PROMPT.format(
                message=message, image_name=os.path.basename(image),
                archetypes=", ".join(campaign.get("archetypes", ["identity reversal"])),
                scene_palette=pal.get("scene", "unspecified"),
                accent_palette=pal.get("accent", "complementary to the scene")) + note,
            brain, project, f"motion plan {message[:40]}")
        if not plan:
            last_why = "brain returned no parseable JSON"
            print(f"[motion] {last_why}; retry {i + 1}/{tries}")
            continue
        if copy_override:
            # The human words are the deliverable; the brain only had to hand us
            # a structure to hang them on. No sense gate: trust the author.
            plan = _apply_copy_override(plan, copy_override)
            print("[motion] copy override applied (hand-authored; sense gate skipped)")
            # Hand-authored copy is still LINTED (limits/banned names are
            # mechanical facts, not taste), but the human is the authority:
            # violations are reported loudly, never blocked on.
            for why in brandkit.lint_copy(
                    plan.get("hook", {}).get("text", ""),
                    plan.get("payoff", {}).get("text", ""),
                    plan.get("cta", ""), campaign.get("product", "")):
                print(f"[motion] WARNING hand-authored copy violates the "
                      f"contract: {why}")
            return plan
        # CODE gate before the paid sense gate: limits, one-CTA, retired
        # names and wordmark spelling are mechanical rules. Catching them
        # here saves the Gemini round-trip the prompt-trust version spent
        # discovering them (when it discovered them at all).
        violations = brandkit.lint_copy(
            plan["hook"]["text"], plan["payoff"]["text"],
            plan.get("cta", ""), campaign.get("product", ""))
        if violations:
            lint_saves += 1
            last_why = "; ".join(violations)
            print(f"[motion] copy linter rejected the draft BEFORE the sense "
                  f"gate ({len(violations)} violation(s); {lint_saves} Gemini "
                  f"call(s) saved so far): {last_why}")
            note = ("\n\nYOUR PREVIOUS COPY VIOLATED THE CONTRACT. Fix every "
                    "violation:\n- " + "\n- ".join(violations))
            continue
        # M8: fun never breaks meaning. A cheap literal-parse check catches
        # clever-hollow hooks before anything renders (paid for: "Can't draw?
        # Your anime still can." shipped to review reading as nonsense).
        v = design._gemini([SENSE_PROMPT.format(hook=plan["hook"]["text"],
                                                payoff=plan["payoff"]["text"])],
                           "hook sense check", project)
        if v and v.get("makes_sense"):
            plan["copy_source"] = "brain"
            return plan
        last_why = (v or {}).get("literal_meaning") or "unparseable (sense gate returned nothing)"
        print(f"[motion] hook failed the literal-sense check ({last_why!r}); "
              f"re-authoring (try {i + 1}/{tries})")
        note = ("\n\nYOUR PREVIOUS HOOK FAILED the literal-sense rule: "
                f"{plan['hook']['text']!r} literally means {last_why!r}. "
                "Write copy whose plain reading is true and clear.")
    raise RuntimeError(
        f"brain could not produce a hook that parses literally after {tries} "
        f"tries (last reason: {last_why!r}). Pass --copy to hand-author the "
        f"hook/payoff/cta/caption and record it as hand-authored in the plan.")


# ---------- generation (text-free, M6/M7) ----------

# The composition contract makes the model LEAVE ROOM FOR THE TEXT (reviewer
# 2026-08-13): reserve clean top/bottom bands for the overlays and keep the
# subject unobstructed in the middle, so type never has to land on a face. Stated
# to every generation step and verified afterwards by the band-clear gate.
COMPOSITION_CONTRACT = (
    "COMPOSITION (vertical 9:16): render ONE continuous full-frame scene that "
    "fills the whole vertical frame edge to edge -- NEVER letterboxed, NEVER "
    "blurred bands or bars. Keep the TOP "
    f"{int(TEXT_ZONE_TOP_FRAC * 100)}% and the BOTTOM {int(TEXT_ZONE_BOTTOM_FRAC * 100)}% "
    "of the frame CALM and UNCLUTTERED and fully in focus -- plain open sky, "
    "simple ground or quiet background there, with NO faces, characters or "
    "important detail -- so overlaid text stays readable. Place the main subject "
    "centered in the MIDDLE band, fully visible with breathing room; the subject "
    "must not enter the top or bottom text areas. ")


def _character_frame(source, character, shot1_prompt, project, out):
    """A text-free frame introducing the character into the scene. It anchors
    shot 1's composition AND serves as the character reference (M6). Style comes
    from the source image (passed as the visual reference below); this prompt no
    longer hardcodes "anime" -- that word contradicted "match the art style" and
    forced a claymation source into flat 2D."""
    from . import audio_post
    if os.path.exists(out):
        return out
    prompt = (f"Transform this scene into a dramatic mid-action shot: {character}. "
              f"{shot1_prompt} Match the reference image's art style, medium and "
              "lighting exactly -- same look, do not restyle it. "
              + COMPOSITION_CONTRACT +
              "NO text, NO words, NO letters, NO logos anywhere.")
    url = audio_post._fal(IMAGE_ENDPOINT,
                          {"prompt": prompt, "image_urls": [_data_uri(source)], "num_images": 1},
                          EST_FRAME, "motion character frame", project,
                          service="fal-image", find=_find_image_url)
    audio_post._download(url, out)
    return out


def _background_frame(source, ai, project, out):
    """A clean BACKGROUND PLATE (the scene with no character in it) to pair with
    the character reference. Reference-first (reviewer, 2026-08-12): a single
    busy keyframe made the model invent BOTH the character and the environment
    from scratch -- the photorealistic woman, the three men at a table, the wall
    of gibberish UI (MAR-37). Giving reference-to-video a separate, stable
    background to hold means it only has to carry the character's identity."""
    from . import audio_post
    if os.path.exists(out):
        return out
    scene = ai["shots"][0].get("setting") or ai["shots"][0]["prompt"]
    prompt = (
        "Produce a clean BACKGROUND PLATE for this scene: the environment ONLY -- "
        f"{scene}. Absolutely NO people, NO characters, NO figures; the scene is "
        "empty of any subject. Keep the reference image's art style, medium, "
        "lighting and palette exactly -- do not restyle it. "
        + COMPOSITION_CONTRACT +
        "NO text, NO words, NO letters, NO logos anywhere.")
    url = audio_post._fal(IMAGE_ENDPOINT,
                          {"prompt": prompt, "image_urls": [_data_uri(source)], "num_images": 1},
                          EST_FRAME, "motion background frame", project,
                          service="fal-image", find=_find_image_url)
    audio_post._download(url, out)
    return out


def _generate(char_frame, background, ai, tier, project, out,
              video_model="h3max", escalate=""):
    from . import audio_post
    shots = ai["shots"]
    total = sum(int(s.get("seconds", 4)) for s in shots)
    cut = int(shots[0].get("seconds", 4))
    single_frame = (VIDEO_MODELS.get(video_model) or {}).get("max_refs", 9) == 1
    if single_frame:
        # Single-keyframe image-to-video (h3max default, grok): the ONE composed
        # keyframe IS the whole scene, so describe motion + camera over it with NO
        # @Image2 background reference (only one frame is passed to the model).
        prompt = (
            f"Create ONE {total}-second cinematic vertical video from this single keyframe image. "
            "Hold the image's exact art style, characters, wardrobe and setting -- do not restyle "
            "(no anime-fication of non-anime art), "
            "do not invent new characters, do not change the scene. "
            "NO text, NO words, NO letters, NO logos anywhere in the video (M7). "
            + COMPOSITION_CONTRACT + escalate +
            f"Action and camera: {shots[0]['prompt']} {shots[1].get('prompt', '')} "
            "Smooth continuous cinematic camera movement; the video ENDS on the scene at full "
            "strength, never on emptiness. "
            f"Sound: {ai.get('sound', 'cinematic')}.")
    elif os.environ.get("REELLY_SINGLE_SHOT"):
        # ONE continuous take (REELLY_SINGLE_SHOT=1): a single smooth camera move
        # for the whole clip instead of two shots with a hard cut -- the two-shot
        # cut reads as "two zoom-ins one after the other". The hook->payoff
        # lettering still swaps at `cut` as an overlay over the continuous video.
        # Keep the face in the lower two-thirds / upper third clear so the caption
        # never lands on it.
        prompt = (
            f"Create ONE {total}-second cinematic vertical video that is a SINGLE CONTINUOUS SHOT "
            "with NO cuts -- one smooth, unbroken, gentle camera move for the whole duration (a "
            "slow push-in with subtle parallax that gradually eases; never restart, re-zoom or cut). "
            "Hold the reference images' art style, medium and finish for the WHOLE video -- do not "
            "restyle (no anime-fication of non-anime art). "
            "NO text, NO words, NO letters, NO logos anywhere in the video (M7). "
            "The character is the person in @Image1: same face, hair and outfit the whole time. "
            "@Image2 is the CLEAN BACKGROUND PLATE the character inhabits -- compose the character "
            "into this environment; do NOT invent a different scene and do NOT add any other people. "
            + COMPOSITION_CONTRACT + escalate +
            f"Action across the take: {shots[0]['prompt']} {shots[1].get('prompt', '')} "
            "Keep the character's FACE in the lower two-thirds of the frame and the UPPER THIRD "
            "clear and simple throughout, so on-screen captions never cover the face. The video "
            "ENDS on the scene at full strength, never on an empty background. "
            f"Sound: {ai.get('sound', 'cinematic')}.")
    else:
        prompt = (
            f"Create ONE {total}-second cinematic vertical video with TWO SHOTS and a hard cut "
            f"at {cut} seconds. Hold the reference images' art style, medium and finish for the "
            "WHOLE video, both shots -- do not restyle (no anime-fication of non-anime art). "
            "NO text, NO words, NO letters, NO logos anywhere in the video (M7). "
            "The character is the person in @Image1: same face, hair, outfit, identical in both shots. "
            "@Image2 is the CLEAN BACKGROUND PLATE the character inhabits -- compose the character "
            "into this environment; do NOT invent a different scene and do NOT add any other people. "
            + COMPOSITION_CONTRACT + escalate +
            f"SHOT 1 (0-{cut}s): {shots[0]['prompt']} Keep the upper third relatively clear of the subject. "
            f"SHOT 2 ({cut}-{total}s): hard cut. {shots[1]['prompt']} In the final two seconds the "
            "subject settles toward the middle of the frame, leaving the lower quarter clear; the video "
            "ENDS on the scene at full strength, never on an empty background. "
            f"Sound: {ai.get('sound', 'cinematic, matched to each shot')}.")
    # Reference-first is MULTI-image: feed BOTH the character (@Image1) and the
    # clean background plate (@Image2). Both seedance and MiniMax H3 are true
    # reference-to-video (a reference array); _video_request trims to each
    # model's ceiling, so a single-frame model (grok) just keeps the character.
    # _retag_prompt rewrites the @Image tags into the chosen model's syntax, so
    # switching models forces the prompt to match its reference API.
    prompt = _retag_prompt(prompt, video_model)
    refs = ([_data_uri(char_frame)] if single_frame
            else [_data_uri(char_frame), _data_uri(background)])
    endpoint, payload, est = _video_request(video_model, refs, prompt, total)
    url = audio_post._fal(endpoint, payload, est,
                          f"motion i2v {video_model} {tier}", project,
                          service="fal-video", find=_find_video_url, tries=900)
    tmp = out + ".dl.mp4"
    audio_post._download(url, tmp)
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", tmp,
                    "-vf", "scale=1080:1920:flags=lanczos",
                    "-c:v", "libx264", "-crf", "17", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k", out], check=True)
    os.remove(tmp)
    return out, cut, total


# The contract that keeps a real-art render honest: the model animates the
# camera over footage that already exists and invents nothing. Isolated as a
# constant because it is the whole point of the mode and the judge equivalent
# for real posts (existing games, real product UI) is "did we alter the art".
REAL_ART_CONTRACT = (
    "This is REAL existing artwork or product footage. Do NOT invent characters, "
    "objects, UI, or gameplay. Do NOT add, remove, reposition, or alter any "
    "existing text, logos, HUD, or interface. Keep everything already in the "
    "frame exactly as it is; nothing enters or leaves the scene. Your only job "
    "is CAMERA MOVEMENT over the still: push-ins, pans, tilts, parallax, "
    "rack-focus, subtle handheld drift. NO new text of any kind (M7).")


def real_art_prompt(ai, cut, total):
    """Reference-only camera-move prompt for real art (no invented character).

    The brain's shot prompts still steer the motion, but they are read as
    CAMERA direction over the existing frame, never as a licence to add subjects
    or action the still does not contain."""
    shots = ai["shots"]
    return (
        f"Create ONE {total}-second cinematic vertical video from a single still "
        f"image, with two camera moves and a soft cut at {cut} seconds. "
        f"{REAL_ART_CONTRACT} "
        f"MOVE 1 (0-{cut}s): {shots[0]['prompt']} (read strictly as camera "
        "direction over the real art; keep the upper third relatively clear). "
        f"MOVE 2 ({cut}-{total}s): soft cut. {shots[1]['prompt']} (camera "
        "direction only). In the final two seconds settle to a steady framing; "
        "the video ENDS on the real art at full strength, never on emptiness. "
        f"Sound: {ai.get('sound', 'ambient, matched to the scene')}.")


def _generate_real_art(source, ai, tier, project, out, video_model="h3max",
                       video_prompt=None):
    """Real-art i2v: the source still is the ONLY reference and the model may
    only move the camera over it (gap: `motion.run` used to invent a character
    even for posts about existing games and real product UI).

    720p on the Seedance tiers: fal caps Seedance there -- 2.0/fast AND 2.5
    both answer a 1080p request with "Input should be '480p' or '720p'", which
    is why every hero render used to die at the API. Hero buys a BETTER MODEL,
    not more pixels; the lanczos upscale below still delivers 1080x1920.
    """
    from . import audio_post
    shots = ai["shots"]
    total = sum(int(s.get("seconds", 4)) for s in shots)
    cut = int(shots[0].get("seconds", 4))
    hero = tier == "hero"
    # A hand-authored `video_prompt` is used VERBATIM (mirrors --copy for
    # lettering): the shipped real_art_prompt forces "two camera moves and a soft
    # cut", which breaks a one-continuous-shot reveal (a cut lets the model swap
    # subjects). When pinned, the prompt owns the motion; the compositor still
    # swaps hook->payoff lettering at `cut` (overlay, not a video cut).
    vprompt = (video_prompt.strip() if video_prompt and video_prompt.strip()
               else real_art_prompt(ai, cut, total))
    vprompt = _retag_prompt(vprompt, video_model)
    endpoint, payload, est = _video_request(
        video_model, _data_uri(source), vprompt, total, hero=hero)
    url = audio_post._fal(endpoint, payload, est,
                          f"motion i2v real-art {video_model} {tier}", project,
                          service="fal-video", find=_find_video_url, tries=900)
    tmp = out + ".dl.mp4"
    audio_post._download(url, tmp)
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", tmp,
                    "-vf", "scale=1080:1920:flags=lanczos",
                    "-c:v", "libx264", "-crf", "17", "-preset", "medium",
                    "-c:a", "aac", "-b:a", "192k", out], check=True)
    os.remove(tmp)
    return out, cut, total


# ---------- type (campaign lettering, spelling-verified, M7/M8) ----------

def _normalize(s):
    # Kept as the local spelling-gate seam for callers/tests; the canonical
    # implementation lives with OCR in design.py.
    from . import design
    return design.normalize_spelling(s)


def _lettering_key(style_ref, text):
    """A content hash of the exact text AND the style asset it derives from.
    Keying the cache on this is what stops a rerun with new copy from silently
    deploying the old line (paid for: a stale payoff.png shipped an old line)."""
    import hashlib
    h = hashlib.sha256()
    h.update(text.encode("utf-8"))
    h.update(b"\x00")
    h.update(os.path.abspath(style_ref).encode("utf-8"))
    try:
        with open(style_ref, "rb") as fh:
            h.update(fh.read())
    except OSError:
        pass
    return h.hexdigest()


def _lettering(style_ref, text, project, out, tries=4):
    """Per-post lettering derived from the campaign's locked style asset.
    Generate -> read back the text with the vision critic -> retry on drift."""
    import numpy as np
    from PIL import Image
    from . import audio_post, design
    # Text-and-style keyed cache: the asset is only reused when BOTH the exact
    # text and the style ref that produced it are unchanged. A fixed path alone
    # (type/payoff.png) reused a stale line whenever the copy changed (M4/M7).
    key = _lettering_key(style_ref, text)
    keyfile = out + ".key"
    if os.path.exists(out) and os.path.exists(keyfile):
        cached = open(keyfile).read().strip()
        if cached == key:
            print(f"[motion] reusing cached lettering: {text!r}")
            return out
        print(f"[motion] lettering copy changed; regenerating (cache was stale for {text!r})")
    words = text.split()
    lines = ([" ".join(words[:len(words) // 2 + len(words) % 2]),
              " ".join(words[len(words) // 2 + len(words) % 2:])]
             if len(words) > 3 else [text])
    # Opt-in typeset fallback (REELLY_TYPESET_LETTERING=1): render the line as
    # plain brand type via the shipped HTML overlay layer instead of a styled AI
    # plate, for when the fal lettering model refuses benign copy. Default (no
    # env var) is unchanged -- normal titles keep the AI lettering.
    if os.environ.get("REELLY_TYPESET_LETTERING"):
        from . import overlays
        safe = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
        body = (
            '<div style="position:absolute; inset:0; display:flex; '
            'align-items:center; justify-content:center;">'
            '<div style="font-family:\'IBM Plex Sans\',sans-serif; font-weight:800; '
            'font-size:88px; line-height:1.06; letter-spacing:-0.01em; '
            'text-align:center; max-width:900px; color:#FCFCFB; '
            '-webkit-text-stroke:9px #0a0c0a; paint-order:stroke fill; '
            'text-shadow:0 4px 26px rgba(0,0,0,.85), 0 2px 4px rgba(0,0,0,.95);">'
            f'{safe}</div></div>')
        td = os.path.dirname(out) or "."
        nm = os.path.splitext(os.path.basename(out))[0]
        overlays._render_png(td, nm, body, size=(1000, 400))
        open(keyfile, "w").write(key)
        print(f"[motion] typeset lettering (no fal): {text!r}")
        return out
    spec = " and ".join(f"\"{ln}\" on line {i + 1}" for i, ln in enumerate(lines))
    # THE REFERENCE IS A STYLE SAMPLE, NOT CONTENT TO COPY. When the style ref
    # is an A-Z specimen sheet -- which is exactly what `_auto_style_spec`
    # generates -- the image model kept reproducing the alphabet instead of
    # setting the requested words, and the spelling gate then read back
    # 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' and burned a retry. Saying so explicitly,
    # twice and in both directions, is what stops it.
    prompt = (f"The attached image is a STYLE REFERENCE ONLY. Copy its letterforms, "
              f"colours, texture, depth and finish, but DO NOT copy any of its words "
              f"or letters. Ignore what the reference spells; it may be an alphabet "
              f"chart, and if so you must NOT output an alphabet.\n"
              f"Render EXACTLY this text and nothing else: {spec}. "
              f"Spell it exactly as written, including punctuation. "
              f"CONTRASTING AND LEGIBLE: keep the fill bright and the outline "
              f"thick and dark, so the words stay readable when laid over busy "
              f"or similarly-coloured artwork. Never render the words in a "
              f"colour close to the reference artwork's dominant colour. "
              f"Plain pure black background, centered, no scene, no extra text, "
              f"no alphabet, no sample characters.")
    raw = out + ".raw.png"
    failure = None
    for i in range(tries):
        url = audio_post._fal(IMAGE_ENDPOINT,
                              {"prompt": prompt, "image_urls": [_data_uri(style_ref)], "num_images": 1},
                              EST_FRAME, f"lettering {text[:24]}", project,
                              service="fal-image", find=_find_image_url)
        audio_post._download(url, raw)
        seen = design.read_text(Image.open(raw), project)
        # The general transcription pass reads heavily-stylized glyphs as no
        # text (or garbage) even when the spelling is perfect. One
        # lettering-framed read before counting the attempt as a failure keeps
        # good assets out of the human-override path; a real misspelling fails
        # both reads.
        if _normalize(seen) != _normalize(text):
            seen = design.read_lettering(Image.open(raw), project)
        if _normalize(seen) != _normalize(text):
            failure = f"spelling: OCR saw {seen!r}"
            print(f"[motion] lettering spelling drift (saw {seen!r}), retry {i + 1}")
            continue
        # Case gate: the spelling compare upper-cases both sides, so a stray
        # interior capital ('Yours won't eitheR.') matched and SHIPPED. Fail it
        # here -- narrow enough that an all-caps lettering style still passes.
        if not design.spelling_case_ok(seen, text):
            failure = f"case: OCR saw {seen!r} (stray interior capital)"
            print(f"[motion] lettering case drift (saw {seen!r}), retry {i + 1}")
            continue
        # Style fidelity: same brush, same gradient, and the locked spec's
        # THICK DARK KEYLINE must survive derivation (paid for: a derived
        # title lost its keyline and died over warm flames).
        fv = design.lettering_style_fidelity(Image.open(style_ref), Image.open(raw), project)
        if fv.get("match"):
            break
        failure = f"style: {fv['missing']}"
        print(f"[motion] lettering style drift ({fv['missing']}), retry {i + 1}")
    else:
        who = os.environ.get("REELLY_LETTERING_OVERRIDE_BY", "").strip()
        if not who or not os.path.exists(raw):
            raise RuntimeError(f"lettering failed for {text!r}: {failure or 'unknown reason'}")
        # The override attests that a human LOOKED AT THIS RAW. Requiring the
        # reviewed file's hash makes a pre-set override inert: a blanket env
        # var once auto-accepted a hallucinated hook ("she walked out") that
        # the spelling gate had correctly rejected three times. Flow: the run
        # fails, the human reads the raw, exports its sha1, reruns.
        import hashlib
        want = os.environ.get("REELLY_LETTERING_OVERRIDE_SHA", "").strip().lower()
        have = hashlib.sha1(open(raw, "rb").read()).hexdigest()
        if not want or not have.startswith(want):
            raise RuntimeError(
                f"lettering failed for {text!r}: {failure or 'unknown reason'}. "
                f"To accept this exact raw after reviewing it, set "
                f"REELLY_LETTERING_OVERRIDE_SHA={have[:12]} alongside "
                f"REELLY_LETTERING_OVERRIDE_BY (raw: {raw})")
        from datetime import datetime, timezone
        audit = {"kind": "lettering", "text": text, "accepted_raw": raw,
                 "raw_sha1": have,
                 "by": who, "at": datetime.now(timezone.utc).isoformat(),
                 "reason": failure or "critic rejection"}
        print(f"[motion] HUMAN LETTERING OVERRIDE by {who}: {audit['reason']}")
        with open(out + ".override.json", "w") as fh:
            json.dump(audit, fh, indent=2)
    im = np.array(Image.open(raw).convert("RGB")).astype(int)
    # Row-wise background from the left/right margins: decorative plates come
    # back with gradient/vignetted backgrounds, and a single global median
    # left a translucent panel behind the glyphs (the green box). Text is
    # centered, so each row's margins are pure background.
    margins = np.concatenate([im[:, :40], im[:, -40:]], axis=1)
    bg = np.median(margins, axis=1, keepdims=True)
    alpha = np.clip((np.abs(im - bg).sum(axis=2) - 45) * 4, 0, 255).astype(np.uint8)
    ys, xs = np.nonzero(alpha > 20)
    rgba = np.dstack([im.astype(np.uint8), alpha])
    Image.fromarray(rgba, "RGBA").crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)).save(out)
    open(keyfile, "w").write(key)
    os.remove(raw)
    return out


def _sample_subjects(vid, t0, t1, project, samples=5, diag=None):
    """Single-subject boxes across a whole overlay window (legacy).

    Superseded on the placement path by _sample_occupancy, which models every
    face and baked mark rather than one dominant subject; retained because it is
    the narrow single-subject sampler and its content-blind-fallback contract is
    still exercised directly. When no box survives, the window is placed
    content-blind (D1 unenforced), recorded in ``diag`` so the plan and the
    design report can say so out loud."""
    from . import design
    if t1 <= t0:
        times = [max(0.0, t0)]
    else:
        step = (t1 - t0) / max(1, samples - 1)
        times = [max(0.0, t0 + i * step) for i in range(samples)]
    boxes = []
    for t in times:
        frame = _frame_at(vid, t)
        box = design.subject_box(frame, project)
        if (not isinstance(box, (list, tuple)) or len(box) != 4 or
                any(isinstance(n, bool) or not isinstance(n, (int, float)) for n in box)):
            continue
        x, y, w, h = box
        frame_w, frame_h = getattr(frame, "size", (1080, 1920))
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > frame_w or y + h > frame_h:
            continue
        boxes.append(tuple(box))
    if not boxes:
        print("[motion] WARNING content-blind fallback: no valid subject boxes "
              f"for window [{t0:.1f},{t1:.1f}]s; placement cannot enforce D1")
        if diag is not None:
            diag["placement"] = "content-blind fallback"
            diag.setdefault("windows", []).append(
                {"window": [round(t0, 2), round(t1, 2)], "boxes": 0})
    elif diag is not None:
        diag.setdefault("windows", []).append(
            {"window": [round(t0, 2), round(t1, 2)], "boxes": len(boxes)})
    return boxes


def _box_overlap(box, boxes):
    x, y, w, h = box
    return sum(max(0, min(x + w, bx + bw) - max(x, bx)) *
               max(0, min(y + h, by + bh) - max(y, by))
               for bx, by, bw, bh in boxes)


def _overlap_fraction(box, boxes):
    """Fraction of ``box``'s area covered by ``boxes`` (0..1). Used by the
    measured D1 gate: how much of a lettering box lands on faces."""
    _, _, w, h = box
    return min(1.0, _box_overlap(box, boxes) / max(1, w * h))


def _weighted_overlap(box, occ):
    """Occupancy cost of placing ``box``. ``occ`` is either the occupancy dict
    (faces/subjects/text_regions, each weighted) or a plain list of avoid boxes
    (uniform body weight, the legacy contract _rank_rows was written against).
    Faces dominate so a row is pushed off a face before it is pushed off a body."""
    if isinstance(occ, dict):
        return (FACE_WEIGHT * _box_overlap(box, occ.get("faces", ()))
                + TEXT_WEIGHT * _box_overlap(box, occ.get("text_regions", ()))
                + BODY_WEIGHT * _box_overlap(box, occ.get("subjects", ())))
    return _box_overlap(box, occ)


def _sample_occupancy(vid, t0, t1, project, samples=5, diag=None):
    """Occupancy map (faces/subjects/text_regions) unioned across a whole
    overlay window, not one hero frame.

    This is the multi-region successor to _sample_subjects: it models EVERY face
    in a lineup and any baked title/logo, so least-occupancy placement has a real
    minimum to find instead of one box every row overlaps. When NO region of any
    kind survives the window, placement goes content-blind (D1 unenforced) and
    that is recorded LOUD in ``diag`` and printed -- the fallback never hides.

    HYBRID (default): faces/subjects come from LOCAL detectors on every sample
    (design.occupancy_local: FaceMesh + edge heatmap) and Gemini is asked ONCE,
    at the window midpoint, only for text_regions -- the one thing a local
    detector cannot see. 5 vision calls per window -> 1. Set the environment
    variable REELLY_OCCUPANCY=gemini to restore the all-Gemini path."""
    from . import design
    # Text-only iteration cache: the occupancy map describes the VIDEO
    # (faces, subjects, baked titles), not the overlay text, so it is keyed
    # by the base render's content hash and reused across recomposites.
    # Placement still checks the fresh overlay boxes against the map every
    # run, so changed text is always re-verified; a changed video misses
    # the hash and resamples.
    import hashlib
    cache_file, cache, chash = vid + ".occ.json", {}, None
    ckey = f"{round(t0, 2)}-{round(t1, 2)}-{samples}"
    try:
        chash = hashlib.sha1(open(vid, "rb").read()).hexdigest()[:16]
        if os.path.exists(cache_file):
            cache = json.load(open(cache_file))
        if cache.get("hash") == chash and ckey in cache.get("windows", {}):
            occ = {k: [tuple(b) for b in v]
                   for k, v in cache["windows"][ckey].items()}
            if diag is not None:
                diag.setdefault("windows", []).append(
                    {"window": [round(t0, 2), round(t1, 2)], "cached": True,
                     **{k: len(v) for k, v in occ.items()}})
            return occ
    except Exception:
        cache, chash = {}, None
    if t1 <= t0:
        times = [max(0.0, t0)]
    else:
        step = (t1 - t0) / max(1, samples - 1)
        times = [max(0.0, t0 + i * step) for i in range(samples)]
    occ = {"faces": [], "subjects": [], "text_regions": []}

    def _absorb(frame, regions):
        if not isinstance(regions, dict):
            return
        fw, fh = getattr(frame, "size", (FRAME_W, FRAME_H))
        for key in occ:
            for box in regions.get(key) or ():
                if (not isinstance(box, (list, tuple)) or len(box) != 4 or
                        any(isinstance(n, bool) or not isinstance(n, (int, float))
                            for n in box)):
                    continue
                x, y, w, h = box
                if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > fw or y + h > fh:
                    continue
                occ[key].append(tuple(box))

    if os.environ.get("REELLY_OCCUPANCY", "").strip().lower() == "gemini":
        for t in times:
            frame = _frame_at(vid, t)
            _absorb(frame, design.occupancy(frame, project))
    else:
        frames = [_frame_at(vid, t) for t in times]
        for frame in frames:
            _absorb(frame, design.occupancy_local(frame, project))
        mid = frames[len(frames) // 2]
        _absorb(mid, {"text_regions": design.occupancy_text(mid, project)})
        if not occ["faces"]:
            # FaceMesh only sees photographic faces: illustrated/painted heads
            # (game key art, anime/noir stills) sample as zero faces and D1 goes
            # unenforced (the elven payoff landed on the elf's face). A vision
            # ask covers that blind spot -- but a ref-to-video subject WALKS
            # across the shot, so its illustrated face TRAVELS over the caption's
            # on-screen duration. Asking at ONE midpoint frame (the old fallback)
            # clears only that instant, and the rising face drifts back under the
            # caption (MAR-67 noir reveal: "is open." landed on the detective's
            # face in the hook's back half as she walked toward camera). Sample
            # several frames spanning the window and UNION the illustrated face's
            # travel so placement clears its whole path -- mirroring what the
            # local detectors already do for photographic faces. Bounded to
            # REELLY_ILLUS_FACE_SAMPLES vision calls (default 3: head, mid, tail)
            # so the hybrid stays cheap instead of one ask per sample.
            n = max(1, int(os.environ.get("REELLY_ILLUS_FACE_SAMPLES", "3")))
            if len(frames) <= 1:
                idxs = [0]
            else:
                idxs = sorted({round(i * (len(frames) - 1) / max(1, n - 1))
                               for i in range(n)})
            for fi in idxs:
                _absorb(frames[fi],
                        {"faces": design.occupancy(frames[fi], project).get("faces") or []})
    counts = {k: len(v) for k, v in occ.items()}
    if sum(counts.values()) == 0:
        print("[motion] WARNING content-blind fallback: no occupancy regions "
              f"(faces/subjects/text) for window [{t0:.1f},{t1:.1f}]s; "
              "placement cannot enforce D1")
        if diag is not None:
            diag["placement"] = "content-blind fallback"
            diag.setdefault("windows", []).append(
                {"window": [round(t0, 2), round(t1, 2)], **counts})
    elif diag is not None:
        diag.setdefault("windows", []).append(
            {"window": [round(t0, 2), round(t1, 2)], **counts})
    if chash:
        try:
            windows = cache.get("windows", {}) if cache.get("hash") == chash else {}
            windows[ckey] = occ
            with open(cache_file, "w") as fh:
                json.dump({"hash": chash, "windows": windows}, fh)
        except Exception as e:
            print(f"[motion] occupancy cache write failed ({e}); continuing")
    return occ


def _candidate_rows(box_h, step=70):
    """Every legal top-y for a box of height ``box_h`` inside the safe band,
    stepped finely enough that a clear row between faces is actually reachable
    (the old fixed [230, 460, 690] could not describe a low clear row)."""
    lo = SAFE_TOP
    hi = max(lo, FRAME_H - SAFE_BOTTOM - box_h)
    rows = list(range(lo, hi + 1, step))
    if not rows or rows[-1] != hi:
        rows.append(hi)
    return rows


def _scrim_for_box(g, x, y, width, height):
    """Opacity from the worst covered cell: calm keylined type needs none."""
    from . import placement
    cw, ch = 1080 / placement.COLS, 1920 / placement.ROWS
    c0, r0 = max(0, int(x / cw)), max(0, int(y / ch))
    c1 = min(placement.COLS, int((x + width) / cw) + 1)
    r1 = min(placement.ROWS, int((y + height) / ch) + 1)
    cells = [g[r][c] for r in range(r0, max(r0 + 1, r1))
             for c in range(c0, max(c0 + 1, c1))]
    worst_luma = max((l for l, _ in cells), default=0)
    worst_detail = max((d for _, d in cells), default=0)
    if worst_detail <= 26 and worst_luma <= 118:
        return 0.0
    # STRENGTHENED after review: type over busy art was reported as hard to
    # read. The old curve topped out at 0.82 and started at 0.24, which was too
    # timid on detailed frames -- a scrim that light leaves lettering competing
    # with the art behind it. Calm, dark frames still get 0.0 (the branch
    # above), so this only bites where legibility was actually at risk.
    busy = max(0.0, min(1.0, (worst_detail - 26) / 54))
    bright = max(0.0, min(1.0, (worst_luma - 118) / 100))
    return round(min(0.92, 0.36 + 0.44 * busy + 0.32 * bright), 2)


EDGE_TOLERANCE = 0.15      # rows within 15% of the best score count as equal


def _edge_affinity(yy, box_h):
    """0.0 when the box sits against the top or bottom edge, 1.0 dead centre."""
    centre = (yy + box_h / 2.0) / FRAME_H
    return 1.0 - min(1.0, abs(centre - 0.5) * 2.0)


def _rank_rows(candidates, x, width, box_h, occ):
    """Candidate y rows ordered by least WEIGHTED occupancy, then by how close
    the row sits to the top or bottom EDGE.

    ``occ`` is the occupancy dict (faces weighed heaviest) or -- for the legacy
    single-list callers and tests -- a plain list of avoid boxes.

    The edge term is why this is not a plain sort. Occupancy alone put type
    across the middle of the subject whenever the art filled the frame: on a
    full-bleed character card EVERY row overlaps something, so "least bad" was
    still the character's face, while the sky above it and the floor below it
    sat empty. Reviewers kept reporting type on the action "while there is
    plenty of space at the top or bottom", and they were right.

    So rows scoring within EDGE_TOLERANCE of the best are treated as equally
    clear, and among those the most edge-ward wins. A genuinely clear centre row
    still beats an occupied edge row -- the tolerance is relative to the best
    score, not absolute, so this cannot override a real face overlap."""
    scored = [(yy, _weighted_overlap((x, yy, width, box_h), occ))
              for yy in candidates]
    if not scored:
        return []
    best = min(s for _, s in scored)
    # Relative band, with a small absolute floor so that a best score of 0.0
    # still admits the near-zero rows rather than only exact ties.
    cutoff = best + max(abs(best) * EDGE_TOLERANCE, 1e-4)
    return [yy for yy, _ in sorted(
        scored,
        key=lambda p: (p[1] > cutoff,                 # clear rows first
                       _edge_affinity(p[0], box_h),   # then hug an edge
                       p[1],                          # then least occupied
                       p[0]))]


def _place_lettering(vid, t0, t1, asset, project, width=920, avoid=(), attempt=0,
                     diag=None, prefer=""):
    """Occupancy-aware y for a lettering asset over [t0, t1] (M3/D1): sample the
    shot into a full occupancy map, then take the minimum-occupancy row in the
    safe band -- weighting faces heaviest so a multi-character lineup no longer
    degenerates to 'every row overlaps the one subject, so land on the face'."""
    from PIL import Image
    from . import design, placement  # noqa: F401  (placement.grid used below)
    asset_img = Image.open(asset)
    w, h = asset_img.size
    # HARD GATE (was a warning that shipped): a centred line may not cross the
    # platform action rails. The image is CSS-scaled to `width`px, so clamping
    # width shrinks the whole line proportionally -- box_h follows from it below,
    # so no re-request is needed, the type just fits.
    if width > MAX_LETTER_W:
        print(f"[motion] lettering width {width}px exceeds the {MAX_LETTER_W}px "
              f"a centred line can span between the {SAFE_LEFT}/{SAFE_RIGHT} side "
              "rails; shrinking to fit")
        width = MAX_LETTER_W
    box_h = int(width * h / max(1, w))
    occ = _sample_occupancy(vid, t0, t1, project, diag=diag)
    faces = occ["faces"]
    # Critic-flagged avoid-regions (D1/D3 re-place loop) count as text-weight:
    # as important to clear as a baked logo, still short of a face.
    rank_occ = dict(occ, text_regions=list(occ["text_regions"]) + list(avoid))
    # Lettering renders horizontally centred (img_tag pins left:50%/translateX),
    # so score at the true centred x; width is already clamped to the rails above.
    x = (FRAME_W - width) // 2
    candidates = _candidate_rows(box_h)
    # Anchor preference (reviewer 2026-08-21): bias the caption into the lower or
    # upper band when asked, so the type sits by the subject's legs / empty
    # foreground instead of the face. Still ranks by occupancy WITHIN the band, so
    # face/contrast avoidance is preserved; only falls back to the full range if
    # the band has no legal row.
    if prefer in ("bottom", "top") and candidates:
        lo, hi = candidates[0], candidates[-1]
        mid = lo + (hi - lo) * 0.5
        band = ([r for r in candidates if r >= mid] if prefer == "bottom"
                else [r for r in candidates if r <= mid])
        if band:
            candidates = band
    ranked = _rank_rows(candidates, x, width, box_h, rank_occ)
    # Explicit anchor wins on POSITION, not occupancy (reviewer 2026-08-21): when
    # asked for bottom, take the lowest legal row (by the subject's legs / fore-
    # ground) even if busier -- the scrim/contrast pass below keeps it readable.
    # The D7 fallback still steps to the next rows in this order if a row is truly
    # unreadable, so it stays at the bottom rather than jumping back to mid-frame.
    if prefer == "bottom":
        ranked = sorted(ranked, reverse=True)
    elif prefer == "top":
        ranked = sorted(ranked)
    # D3/D7 on the actual backdrop: a keyline anchors on calm dark pixels, but
    # busy or bright-warm pixels behind warm lettering need a scrim (the critic
    # was right about the flames; this encodes the concession). All pure pixel
    # math on already-loaded frames, so measuring extra rows costs nothing.
    g = placement.grid(vid, (t0 + t1) / 2)
    frame = _frame_at(vid, (t0 + t1) / 2)

    def _measure(row):
        yy = max(SAFE_TOP, min(row, FRAME_H - SAFE_BOTTOM - box_h))
        ov = _overlap_fraction((x, yy, width, box_h), faces)
        sc = _scrim_for_box(g, x, yy, width, box_h)
        region = frame.crop((int(x), int(yy), int(min(FRAME_W, x + width)),
                             int(min(FRAME_H, yy + box_h))))
        d7 = design.contrast_gate(asset_img, region, current_scrim=sc)
        return yy, ov, max(sc, d7["scrim"]), d7

    y, face_ov, scrim, d7 = _measure(ranked[min(attempt, len(ranked) - 1)])
    if not d7["pass"]:
        # The measurement must CHANGE the composite: when even the full scrim
        # cannot make this row readable, move to the best-ranked row that
        # reads, instead of re-flagging the same row on every attempt (the
        # odyssey warm-on-warm loop). Face clearance still outranks contrast:
        # a fallback row never trades onto a face.
        tried = {y}
        for alt in ranked:
            ay, aov, asc, ad7 = _measure(alt)
            if ay in tried or aov > face_ov:
                continue
            tried.add(ay)
            if ad7["pass"]:
                print(f"[motion] D7 unreadable at y={y} even at full scrim; "
                      f"moving {os.path.basename(asset)} to y={ay}")
                y, face_ov, scrim, d7 = ay, aov, asc, ad7
                break
    # Measured D1: how much of the placed box still lands on faces. On art with
    # no clear band this stays > 0 and the gate FAILS loud with the fraction,
    # rather than the critic re-flagging D1 forever with no number.
    if diag is not None:
        diag.setdefault("d1", []).append(
            {"asset": os.path.basename(asset), "y": int(y),
             "box": [int(x), int(y), int(width), int(box_h)],
             "faces": len(faces), "face_overlap": round(face_ov, 3)})
        diag.setdefault("d7", []).append({"asset": os.path.basename(asset),
                                          "y": int(y), **d7})
    return x, y, width, scrim


# ---------- assembly ----------

# The managed account brand bug rides the WHOLE clip (reviewer 2026-08-13), not just the
# end-card: a persistent, low-opacity real wordmark in a corner so the mark's
# colour and form carry through the video. Stamped, never generated -- the video
# stays logo-free at generation time (M7) and the mark is composited on top, so
# there is no risk of the model drawing a garbled logo. Env-tunable so placement
# and weight can be dialled without a code change.
BUG_OPACITY = float(os.environ.get("REELLY_BUG_OPACITY", "0.62"))
BUG_HEIGHT = int(os.environ.get("REELLY_BUG_HEIGHT", "112"))  # readable, was 60 (too small)
BUG_CORNER = os.environ.get("REELLY_BUG_CORNER", "top-left")  # {top,bottom}-{left,right}


def _brand_wordmark():
    """The registered product wordmark for the persistent corner mark, or None."""
    from . import products
    try:
        key = os.environ.get("REELLY_BUG_PRODUCT", "video")
        logo = products.brand_logo(key)
    except Exception:
        logo = None
    return logo if logo and os.path.exists(logo) else None


def _corner_bug_box():
    """Product corner-mark box in frame coordinates, or ``None``.

    The same box is used to draw the mark and keep lettering outside it.
    """
    from PIL import Image
    logo = _brand_wordmark()
    if not logo:
        return None
    try:
        iw, ih = Image.open(logo).size
    except Exception:
        return None
    w = max(1, int(BUG_HEIGHT * iw / max(1, ih)))
    vert, _, horiz = BUG_CORNER.partition("-")
    y = SAFE_TOP if vert != "bottom" else FRAME_H - SAFE_BOTTOM - BUG_HEIGHT
    x = SAFE_LEFT if horiz != "right" else FRAME_W - SAFE_RIGHT - w
    return (x, y, w, BUG_HEIGHT)


def _corner_bug_event(t_end):
    """Persistent corner mark for ``[0, t_end]``, or None without a logo.

    It ends before the CTA card so the final beat shows only one mark.
    """
    import base64 as b64mod
    logo = _brand_wordmark()
    box = _corner_bug_box()
    if not logo or not box:
        return None
    vert, _, horiz = BUG_CORNER.partition("-")
    css_v = f"top:{SAFE_TOP}px" if vert != "bottom" else f"bottom:{SAFE_BOTTOM}px"
    css_h = f"left:{SAFE_LEFT}px" if horiz != "right" else f"right:{SAFE_RIGHT}px"
    b = b64mod.b64encode(open(logo, "rb").read()).decode()
    html = (f'<img src="data:image/png;base64,{b}" style="position:absolute; '
            f'{css_v}; {css_h}; height:{BUG_HEIGHT}px; opacity:{BUG_OPACITY}; '
            f'filter:drop-shadow(0 2px 6px rgba(0,0,0,.55));"/>')
    return {"template": "raw", "args": [html], "t": [0.0, float(t_end)],
            "ent": "none", "fade_in": False, "fade_out": True,
            "why": f"managed account brand bug ({BUG_CORNER}); ends before the CTA card so one mark shows"}


SCRIM_PAD = 40          # img_tag draws a 40px scrim pad around hook/payoff
CTA_SCRIM_PAD = 30      # the CTA container's vertical padding


def _full_width_band(box, pad=0):
    """A centred full-width mark can only clear ANOTHER centred mark by clearing
    its whole Y-RANGE -- they always share the horizontal centre, so avoiding a
    neighbour's tight text box still lets the two SCRIMS collide at the edges
    (a corner mark under the hook, the CTA on the payoff). Model the avoid-region
    as a full-width band over the source box's rows, inflated by its scrim pad --
    the same Y-band model layout.occupied uses. (reviewer 2026-08-18)"""
    if not box:
        return None
    _x, y, _w, h = box
    y0 = max(0, min(int(y) - pad, FRAME_H))
    y1 = max(0, min(int(y) + int(h) + pad, FRAME_H))
    return (0, y0, FRAME_W, max(1, y1 - y0))


def _events(root, vid, ai, campaign, cut, total, project, attempt=0, avoid=(),
            diag=None):
    import base64 as b64mod
    from . import design
    from PIL import Image
    type_dir = os.path.join(root, "type")
    style = campaign["lettering_style_ref"]
    hook_png = _lettering(style, ai["hook"]["text"], project,
                          os.path.join(type_dir, "hook.png"))
    pay_png = _lettering(style, ai["payoff"]["text"], project,
                         os.path.join(type_dir, "payoff.png"))

    def img_tag(path, y, width, scrim=0.0):
        b = b64mod.b64encode(open(path, "rb").read()).decode()
        img = (f'<img src="data:image/png;base64,{b}" style="position:absolute; left:50%; '
               f'transform:translateX(-50%); top:{y}px; width:{width}px;"/>')
        if scrim <= 0:
            return img
        # A simple flat 40%-opaque container with small rounded corners (reviewer
        # 2026-08-13), sized to the asset's real drawn height. No gradients/halos.
        with Image.open(path) as _im:
            iw, ih = _im.size
        draw_h = int(width * ih / max(1, iw))
        pad = 40
        return (f'<div style="position:absolute; left:50%; transform:translateX(-50%); '
                f'top:{y - pad}px; width:{width + 2 * pad}px; height:{draw_h + 2 * pad}px; '
                f'background:rgba(9,12,10,0.40); border-radius:20px;"></div>'
                + img)

    # Keep the hook/payoff off the persistent managed account bug (it is a stamped mark in
    # the corner; treat it like a baked logo the type must clear).
    # The bug is a centred/corner mark and the hook is a full-width centred line;
    # they clear only by clearing the bug's Y-band, not its tight box.
    bug_box = _corner_bug_box()
    bug_band = _full_width_band(bug_box, SCRIM_PAD)
    place_avoid = list(avoid) + ([bug_band] if bug_band else [])
    # Caption scale (reviewer 2026-08-21): a touch smaller than the old
    # 920/880 defaults so the type reads as a caption, not a takeover, and
    # leaves more breathing room above a rising subject. Overridable per-render
    # via REELLY_CAPTION_W_HOOK / REELLY_CAPTION_W_PAYOFF.
    _hook_w = int(os.environ.get("REELLY_CAPTION_W_HOOK", "760") or "760")
    _pay_w = int(os.environ.get("REELLY_CAPTION_W_PAYOFF", "720") or "720")
    _anchor = os.environ.get("REELLY_CAPTION_ANCHOR", "").strip().lower()
    hx, hy, hw, hs = _place_lettering(vid, 0.0, cut, hook_png, project,
                                       width=_hook_w, prefer=_anchor,
                                       avoid=place_avoid, attempt=attempt, diag=diag)
    px, py, pw, ps = _place_lettering(vid, cut, total, pay_png, project, width=_pay_w,
                                       prefer=_anchor,
                                       avoid=place_avoid, attempt=attempt, diag=diag)
    cta_text = (ai.get("cta") or campaign.get("cta", "")).strip()
    # CTA / endcard colour is configurable (was hardcoded amber #f7b733). Default
    # is clean white -- the amber collides with dark/monochrome keyart and reads
    # as a different brand. Override per-post via ai["cta_color"] or per-campaign
    # via campaign["cta_color"].
    cta_color = str(ai.get("cta_color") or campaign.get("cta_color") or "#ffffff").strip()
    # Optional event deep-link, rendered as a small clean subtext line beneath the
    # CTA on the endcard (there was no link render path before). Legible, same
    # container so it cannot collide with the CTA or payoff.
    event_link = str(ai.get("event_link") or campaign.get("event_link") or "").strip()
    # M5 + the logo rule: the end card uses the REAL registered wordmark when
    # the campaign's product has one (a designed mark beats typeset text);
    # typeset CTA is the fallback, never the preference.
    from . import products
    logo = products.brand_logo(campaign.get("product", ""))
    # CTA stays in its lower safe-zone band (G11/M5) but now picks the row that
    # least covers the occupancy map during its window -- and crucially clears
    # faces (the odyssey end cards put the button across the cat characters).
    end_occ = _sample_occupancy(vid, max(0.0, total - 1.8), total, project, diag=diag)
    end_rank = dict(end_occ, text_regions=list(end_occ["text_regions"]) + list(avoid))
    cta_box_w, cta_box_h = 700, 180
    # The payoff holds to the last frame too, and when it owns the lower band
    # every fixed row lands inside it (the elven end card stacked the CTA on
    # the payoff type). Offer rows stacked just above/below the payoff as
    # extra candidates, and require clearing the payoff box like a face.
    # _place_lettering returns (x, y, width, SCRIM) -- the drawn height comes
    # from the asset's aspect at the drawn width. (Using the scrim as height
    # made the payoff a zero-height box and stacked the CTA onto the type.)
    with Image.open(pay_png) as _pim:
        pay_h = int(pw * _pim.size[1] / _pim.size[0])
    pay_box = (px, py, pw, pay_h)
    # The CTA and the payoff are both centred and co-timed in the last beat, so
    # the CTA must clear the payoff's whole Y-band (both scrims), not its tight
    # box -- otherwise the yellow CTA prints on the payoff type. Fold the band
    # into the occupancy the CTA ranks against as well.
    pay_band = _full_width_band(pay_box, SCRIM_PAD + CTA_SCRIM_PAD)
    end_rank["text_regions"] = list(end_rank["text_regions"]) + [pay_band]
    stacked = [int(py - cta_box_h - 30), int(py + pay_h + 30)]
    lo, hi = SAFE_TOP, FRAME_H - SAFE_BOTTOM - cta_box_h
    # A GUARANTEED ESCAPE FROM THE PAYOFF. The fixed rows plus `stacked` can all
    # be unusable at once: once the edge-bias in _rank_rows started pulling the
    # payoff into the bottom band, the below-payoff row fell past `hi` and the
    # three fixed rows all landed inside the payoff, so nothing cleared it and
    # the CTA printed across the type on two separate cards. These two extra
    # candidates cannot both be blocked -- one hugs the payoff from above
    # (clamped into the band instead of dropped), the other retreats to the
    # opposite end of the frame entirely.
    above_pay = max(lo, min(hi, int(py) - cta_box_h - 30))
    opposite = lo if (py + pay_h / 2) > FRAME_H / 2 else hi
    cta_candidates = [yy for yy in [1360, 1210, 1060] + stacked + [above_pay, opposite]
                      if lo <= yy <= hi]
    cta_candidates = list(dict.fromkeys(cta_candidates))
    ranked_cta = sorted(
        cta_candidates,
        key=lambda yy: (_weighted_overlap((190, yy, cta_box_w, cta_box_h), end_rank), -yy))
    # Retry attempts are driven by hook/payoff failures; demoting the CTA to a
    # worse row on each retry pushed it onto banners and faces the ranking had
    # already cleared. Cycle only within rows that clear faces AND the payoff
    # (best rank first), degrading to face-clear, then to the full ranking.
    def _cta_clear(yy, boxes):
        return _overlap_fraction((190, yy, cta_box_w, cta_box_h), boxes) == 0
    # Face AND payoff clearance are BOTH hard: prefer rows that clear both. When
    # none exists the frame is over-constrained (a full-frame subject + a long
    # payoff) -- do NOT categorically sacrifice the face (that regression put the
    # CTA at 59% over the cat). Minimize the worst harm instead (a face overlap
    # counts double a payoff graze), and let the band-clear gate flag/re-render
    # this frame rather than pretend it is fine.
    fully_clear = [yy for yy in ranked_cta
                   if _cta_clear(yy, end_occ["faces"]) and _cta_clear(yy, [pay_band])]
    if fully_clear:
        pool = fully_clear
    else:
        pool = sorted(ranked_cta, key=lambda yy: (
            2.0 * _overlap_fraction((190, yy, cta_box_w, cta_box_h), end_occ["faces"])
            + _overlap_fraction((190, yy, cta_box_w, cta_box_h), [pay_band])))
    cta_y = pool[min(attempt, len(pool) - 1)]
    cta_face_ov = _overlap_fraction((190, cta_y, cta_box_w, cta_box_h), end_occ["faces"])
    if diag is not None:
        diag.setdefault("d1", []).append(
            {"asset": "cta", "y": int(cta_y),
             "box": [190, int(cta_y), cta_box_w, cta_box_h],
             "faces": len(end_occ["faces"]), "face_overlap": round(cta_face_ov, 3)})
    # Optional event-link subtext line (clean, smaller, same colour, its own row
    # inside the container so it never collides with the CTA text or the payoff).
    link_el = (f'<div style="font-family:\'IBM Plex Sans\',sans-serif; font-weight:500; '
               f'font-size:30px; color:{cta_color}; letter-spacing:0.01em; '
               f'opacity:0.92; white-space:nowrap; margin-top:10px;">{event_link}</div>'
               if event_link else '')
    # Flat 40%-opaque container, small rounded corners (reviewer 2026-08-13).
    cta = (f'<div style="position:absolute; left:50%; transform:translateX(-50%); top:{cta_y}px;'
           f' border-radius:20px; padding:22px 44px; background:rgba(9,12,10,0.40);'
           f' display:flex; flex-direction:column; align-items:center;'
           f" font-family:'IBM Plex Sans',sans-serif; font-weight:600; font-size:58px;"
           f' color:{cta_color}; letter-spacing:0.02em; white-space:nowrap;">'
           f'<div>{cta_text}</div>{link_el}</div>')

    if logo and os.path.exists(logo):
        from PIL import Image as _Im
        iw, ih = _Im.open(logo).size
        lb = b64mod.b64encode(open(logo, "rb").read()).decode()
        lh = 96
        cta_el = (f'<div style="position:absolute; left:50%; transform:translateX(-50%); '
                  f'top:{cta_y}px; display:flex; flex-direction:column; align-items:center; '
                  f'gap:14px; border-radius:20px; padding:30px 56px; '
                  # flat 40%-opaque container, small rounded corners (reviewer 2026-08-13)
                  f'background:rgba(9,12,10,0.40);">'
                  f'<img src="data:image/png;base64,{lb}" style="height:{lh}px; '
                  f'max-width:520px; object-fit:contain;"/>'
                  f"<div style=\"font-family:'IBM Plex Sans',sans-serif; font-weight:600; "
                  f'font-size:52px; color:{cta_color};">{cta_text}</div>{link_el}</div>')
    else:
        cta_el = cta

    events = [
        {"template": "raw", "args": [img_tag(hook_png, hy, hw, hs)],
         "t": [0.0, round(cut - 0.05, 2)], "ent": "none", "fade_in": False,
         "sfx": ["whoosh.mp3", -16],
         "why": f"hook lettering from frame 1 (H-rule, cover frame), occupancy-aware y={hy}"},
        {"template": "raw", "args": [img_tag(pay_png, py, pw, ps)],
         "t": [round(cut + 0.05, 2), float(total)], "ent": "rise",
         "sfx": ["pop.mp3", -14], "fade_out": False,
         "why": f"payoff swaps on the cut, holds to last frame (M1), y={py}"},
        {"template": "raw", "args": [cta_el], "t": [round(total - 1.8, 2), float(total)],
         "ent": "rise", "sfx": ["ding.mp3", -18], "fade_out": False,
         "why": "single CTA, brand sans, safe zone, holds to last frame (M5)"},
    ]
    # The bug ends as the CTA end-card rises (which carries its own managed account mark), so
    # the final beat shows one mark, not two stacked.
    bug = _corner_bug_event(round(max(0.0, total - 1.8), 2))
    if bug:
        events.insert(0, bug)          # bottom layer: rides the clip under the type
    spec_path = os.path.join(root, "edl", "overlay_specs.json")
    specs = json.load(open(spec_path)) if os.path.exists(spec_path) else {}
    specs["cut_01"] = events
    json.dump(specs, open(spec_path, "w"), indent=1)
    return events


def _style_gate(video, source, cut, total, project, root):
    """Multi-frame art-style consistency check (reviewer 2026-08-13): a single
    keyframe cannot see shot-to-shot style drift -- the flaw that shipped a
    flat-anime shot 1 next to a claymation shot 2 -- so sample across BOTH shots
    and compare each frame directly to the SOURCE image (the style reference, not
    a lossy text phrase). A style fail needs a VIDEO re-render (recompose only
    moves type), so this REPORTS loudly into qc/style_report.md rather than
    looping. Returns {"pass", "frames"}."""
    from . import design
    if not source or not os.path.exists(source):
        return {"pass": True, "frames": []}
    ts = sorted({round(t, 2) for t in
                 (0.6, max(0.6, cut - 0.4), cut + 0.4, max(cut + 0.4, total - 0.6))
                 if 0.0 <= t <= max(0.0, total)})
    frames, ok = [], True
    for t in ts:
        v = design.style_match(_frame_at(video, t), source, project)
        frames.append({"t": t, **v})
        ok = ok and v.get("match", True)
    qc = os.path.join(root, "qc")
    os.makedirs(qc, exist_ok=True)
    L = ["# Art-style consistency (vs the source image)", "",
         f"Result: {'PASS' if ok else 'FAIL'} "
         f"({len(ts)} frames sampled across both shots)", ""]
    for f in frames:
        L.append(f"- t={f['t']}s: {'match' if f.get('match') else 'DRIFT'}"
                 + (f" -- {f['drift']}" if f.get("drift") else ""))
    with open(os.path.join(qc, "style_report.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")
    if not ok:
        print("[motion] STYLE GATE FAIL: video drifts from the source art style; "
              "see qc/style_report.md (needs a re-render)")
    return {"pass": ok, "frames": frames}


# A text zone is "clear" when no FACE sits MEANINGFULLY in it (reviewer
# 2026-08-13). Background scenery -- sky, clouds, towers, ground -- is fine; the
# 40% text plate handles legibility over it. We do NOT block on scene detail (that
# false-flagged a rich clay world everywhere), and a face that merely GRAZES a
# zone edge (a running character's head-top clipping the boundary) does not count
# -- only a face whose CENTRE is in the zone, or that covers a substantial share
# of it, blocks.
BAND_FACE_COVER = 0.15      # a face covering >15% of a text zone counts as "in" it


def _zone_overlap_frac(boxes, y0, y1):
    """Fraction of the full-width [y0, y1] text zone covered by these boxes."""
    zone = FRAME_W * max(1, y1 - y0)
    covered = 0
    for b in boxes:
        if not (isinstance(b, (list, tuple)) and len(b) == 4):
            continue
        x, y, w, h = b
        oy0, oy1 = max(y0, y), min(y1, y + h)
        ox0, ox1 = max(0, x), min(FRAME_W, x + w)
        if oy1 > oy0 and ox1 > ox0:
            covered += (oy1 - oy0) * (ox1 - ox0)
    return min(1.0, covered / zone)


def _face_in_zone(faces, y0, y1):
    """True when a face is MEANINGFULLY inside the [y0, y1] text zone -- its centre
    sits in the zone, or the faces cover a substantial share of it. A face that
    only grazes the zone edge does not count."""
    for b in faces:
        if isinstance(b, (list, tuple)) and len(b) == 4:
            cy = b[1] + b[3] / 2.0
            if y0 <= cy < y1:
                return True
    return _zone_overlap_frac(faces, y0, y1) > BAND_FACE_COVER


def _band_clear_gate(video, cut, total, project, root):
    """Verify the reserved TOP/BOTTOM text zones are clear of FACES so overlays
    never land on a face (reviewer: the text must ALWAYS have a clean home).
    Occupancy is sampled across the whole clip (multi-frame union, never one
    keyframe). Scenery detail is NOT a failure and an edge-graze is not either --
    only a face meaningfully in a zone is. Returns {pass, top, bottom, faces}."""
    faces = _sample_occupancy(video, 0.0, max(0.0, total), project)["faces"]
    top = not _face_in_zone(faces, 0, TEXT_ZONE_TOP_Y)
    bottom = not _face_in_zone(faces, TEXT_ZONE_BOTTOM_Y, FRAME_H)
    qc = os.path.join(root, "qc")
    os.makedirs(qc, exist_ok=True)
    with open(os.path.join(qc, "band_report.md"), "w") as fh:
        fh.write("\n".join([
            "# Text-zone clearance (reserved bands) -- faces only", "",
            f"TOP band (y < {TEXT_ZONE_TOP_Y}px): {'CLEAR' if top else 'BLOCKED (face)'}",
            f"BOTTOM band (y > {TEXT_ZONE_BOTTOM_Y}px): {'CLEAR' if bottom else 'BLOCKED (face)'}",
            f"faces={len(faces)}", ""]) + "\n")
    if not (top and bottom):
        print(f"[motion] BAND GATE: top={'clear' if top else 'BLOCKED'}, "
              f"bottom={'clear' if bottom else 'BLOCKED'} -- a face is in a text zone")
    return {"pass": top and bottom, "top": top, "bottom": bottom, "faces": len(faces)}


def _escalation_note(band):
    """A stronger instruction for the ONE re-render, naming which zone a face
    invaded (art-style drift is parked -- the style gate reports it but does not
    drive re-renders)."""
    notes = []
    if not band.get("top", True):
        notes.append("CRITICAL: keep the TOP third of the frame completely clear of the "
                     "subject and any face -- move the subject lower.")
    if not band.get("bottom", True):
        notes.append("CRITICAL: keep the BOTTOM quarter of the frame completely clear of "
                     "the subject and any face -- move the subject higher.")
    return (" ".join(notes) + " ") if notes else ""


def _write_review_preview(root, video, cut, total):
    """Save representative frames of a gate-failing render to _REVIEW/ so a human
    sees WHAT it looks like (reviewer: show the image/video) instead of only a
    pass/fail line, and can overrule."""
    d = os.path.join(root, "_REVIEW")
    os.makedirs(d, exist_ok=True)
    for label, t in (("hook", 0.6), ("payoff", min(max(0.0, total - 0.6), cut + 0.6)),
                     ("end", max(0.0, total - 0.4))):
        try:
            _frame_at(video, t).save(os.path.join(d, f"{label}.png"))
        except Exception:
            pass


def _content_band(profile, floor_frac=0.16):
    """(top, bottom) rows carrying real picture, from a per-row high-frequency
    energy profile. The model letterboxes wide shots with a BLURRED (low-energy)
    bed top/bottom; those rows fall below the floor while the sharp centre stays
    above it. Returns the full span when there is too little signal to trust."""
    import numpy as np
    prof = np.asarray(profile, dtype=float)
    H = len(prof)
    if H == 0 or prof.max() <= 0:
        return 0, H
    keep = np.where(prof > prof.max() * floor_frac)[0]
    if len(keep) < H * 0.25:
        return 0, H
    return int(keep[0]), int(keep[-1] + 1)


def _deletterbox(base):
    """MAR-106: a reference-to-video render sometimes composes a WIDER shot
    centred in the portrait frame and fills the top/bottom with its own BLURRED
    bed (the model ignores the edge-to-edge instruction on pull-back shots).
    Detect a low-energy blurred band top/bottom vs a sharp centre across sampled
    frames; if found, zoom into the content band so the picture fills the frame.
    No-op on a full-bleed render (idempotent), so it never harms a good one."""
    import numpy as np
    from PIL import Image
    try:
        dur = _dur(base)
    except Exception:
        return base
    profs = []
    for f in (0.2, 0.4, 0.6, 0.8):
        try:
            a = np.asarray(Image.open(_frame_at(base, dur * f)).convert("L"),
                           dtype=np.float32)
            profs.append(np.abs(np.diff(a, axis=0)).mean(axis=1))
        except Exception:
            continue
    if not profs:
        return base
    n = min(len(p) for p in profs)
    prof = np.mean([p[:n] for p in profs], axis=0)
    H = n
    top, bot = _content_band(prof)
    band = bot - top
    if (top <= H * 0.05 and bot >= H * 0.95) or band <= 0 or band >= H * 0.9:
        return base                                   # full-bleed: nothing to do
    zoom = min(2.0, max(1.05, H / float(band)))
    print(f"[motion] MAR-106: base self-letterboxed (content rows {top}-{bot} of "
          f"{H}); zooming {zoom:.2f}x to fill the frame")
    tmp = base + ".delb.mp4"
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-i", base, "-vf",
                    f"scale=iw*{zoom:.4f}:ih*{zoom:.4f},crop=1080:1920,setsar=1",
                    "-c:v", "libx264", "-crf", "16", "-preset", "medium",
                    "-c:a", "copy", tmp], check=True)
    os.replace(tmp, base)
    return base


def _generate_checked(root, src, ai, tier, project, base, video_model):
    """Reference-first generation with a quality gate + ONE re-render. Build the
    character and background references, generate the reference-to-video, then
    check art-style consistency (vs the source) AND that the reserved text bands
    are clear. On failure, regenerate references + video ONCE with an escalated
    instruction; if it still fails, keep the render but write a _REVIEW preview and
    flag it (blocked) rather than pretend it passed. Returns (base, cut, total,
    report)."""
    src_dir = os.path.join(root, "source")
    char_p = os.path.join(src_dir, "character_frame.png")
    bg_p = os.path.join(src_dir, "background_frame.png")
    style = band = {"pass": True}
    escalate = ""
    base_out, cut, total, tt = base, 4, 8, 8
    single_frame = (VIDEO_MODELS.get(video_model) or {}).get("max_refs", 9) == 1
    for attempt in range(2):                    # 1 render + 1 re-render
        char = _character_frame(src, ai["character"], ai["shots"][0]["prompt"], project, char_p)
        # Single-frame models (h3max default, grok) animate ONE keyframe, so the
        # separate background plate is never used -- skip that paid generation.
        bg = char if single_frame else _background_frame(src, ai, project, bg_p)
        print(f"[motion] {tier} render: {VIDEO_MODELS[video_model]['endpoint']} "
              + ("(single keyframe i2v)" if single_frame else "(reference-first char+bg)")
              + (" [re-render after gate fail]" if attempt else ""))
        base_out, cut, total = _generate(char, bg, ai, tier, project, base,
                                         video_model=video_model, escalate=escalate)
        tt = min(total, _dur(base_out))
        # The style gate REPORTS drift (art-style reliability is parked) but does
        # not drive the re-render; only a face in a text zone (band gate) does.
        style = _style_gate(base_out, src, cut, tt, project, root)
        band = _band_clear_gate(base_out, cut, tt, project, root)
        if band.get("pass"):
            return base_out, cut, total, {"style": style, "band": band,
                                          "attempts": attempt + 1, "blocked": False}
        if attempt == 0:
            escalate = _escalation_note(band)
            print("[motion] a face is in a text zone; regenerating references + video once")
            for f in (base_out, char_p, bg_p):
                try:
                    os.remove(f)
                except OSError:
                    pass
    _write_review_preview(root, base_out, cut, tt)
    print("[motion] BAND GATE still failing after one re-render (a face is in a text "
          "zone); wrote _REVIEW/ preview -- review before shipping")
    return base_out, cut, total, {"style": style, "band": band,
                                  "attempts": 2, "blocked": True}


def _design_gate(root, out_video, ai, cut, total, project, recompose=None,
                 diag=None):
    """Critic verdict on the composed frames a viewer will actually see (D5).

    Three gates decide the result: the vision critic (D1-D6), the MEASURED D1
    face-overlap gate (``diag['d1']`` -- a placed box still on a face fails with
    its fraction) and the MEASURED D7 contrast gate (``diag['d7']``). Any one
    failing fails the gate, and all of them -- plus a content-blind placement
    warning -- are written into qc/design_report.md so nothing fails silently."""
    from . import design
    checks = [("hook", 1.2, [ai["hook"]["text"]]),
              ("payoff", cut + 1.2, [ai["payoff"]["text"]]),
              ("end", total - 0.4, [ai["payoff"]["text"], ai.get("cta", "")])]
    attempts = []
    for attempt in range(3):
        report, ok = [], True
        for name, t, texts in checks:
            v = design.critique(_frame_at(out_video, t), texts, project=project)
            report.append((name, v))
            ok = ok and bool(v.get("pass"))
        attempts.append({"attempt": attempt + 1, "pass": ok,
                         "checks": [{"name": n, **v} for n, v in report]})
        retry_issues = [issue for _, v in report for issue in v.get("issues", [])
                        if issue.get("rule") in ("D1", "D3")]
        if ok or not retry_issues or recompose is None or attempt == 2:
            break
        avoid = [design.region_to_box(i["region"]) for i in retry_issues
                 if len(i.get("region") or []) == 4]
        out_video = recompose(attempt + 1, avoid)
    diag = diag or {}
    d7_fails = [d for d in diag.get("d7", []) if not d.get("pass", True)]
    # Measured D1: a placed box that still lands on a face beyond tolerance fails
    # the gate outright, with the fraction. Placement already minimised this, so
    # a survivor means the art has no face-free band -- a human must know.
    d1_fails = [d for d in diag.get("d1", [])
                if d.get("face_overlap", 0.0) > D1_FACE_TOL]
    placement = diag.get("placement", "subject-aware")
    ok = ok and not d7_fails and not d1_fails
    qc = os.path.join(root, "qc")
    os.makedirs(qc, exist_ok=True)
    L = ["# Design gate (D1-D7)", "",
         f"**Placement: {placement}**"
         + ("  \n> content-blind fallback: the vision occupancy map was "
            "unavailable, so lettering was positioned WITHOUT face/subject data "
            "and D1 (nothing overlaps a face) could not be enforced."
            if placement != "subject-aware" else ""), ""]
    for a in attempts:
        L.append(f"## Attempt {a['attempt']}: {'PASS' if a['pass'] else 'FAIL'}")
        for v in a["checks"]:
            L.append(f"### {v['name']}: {'PASS' if v.get('pass') else 'FAIL'}")
            for i in v.get("issues", []):
                L.append(f"- {i.get('rule')}: {i.get('what')} -> {i.get('fix')}")
        L.append("")
    if diag.get("d1"):
        L.append("## D1 measured face overlap (lettering vs faces)")
        for d in diag["d1"]:
            frac = d.get("face_overlap", 0.0)
            verdict = "FAIL" if frac > D1_FACE_TOL else "PASS"
            L.append(
                f"- {d.get('asset')} @y={d.get('y')} box={d.get('box')}: {verdict} "
                f"face_overlap={frac * 100:.1f}% faces={d.get('faces')}")
        L.append("")
    if diag.get("d7"):
        L.append("## D7 measured contrast (lettering vs backdrop)")
        for d in diag["d7"]:
            verdict = "PASS" if d.get("pass") else "FAIL"
            flags = ", ".join(f for f, on in
                              (("warm-on-warm", d.get("warm_on_warm")),
                               ("low-luma", d.get("low_luma"))) if on) or "clear"
            L.append(
                f"- {d.get('asset')} @y={d.get('y')}: {verdict} [{flags}] "
                f"asset_luma={d.get('asset_luma')} backdrop_luma="
                f"{d.get('backdrop_luma')} luma_gap={d.get('luma_gap')} "
                f"asset_hue={d.get('asset_hue')} backdrop_hue={d.get('backdrop_hue')} "
                f"hue_delta={d.get('hue_delta')} scrim->{d.get('scrim')}")
        L.append("")
    open(os.path.join(qc, "design_report.md"), "w").write("\n".join(L))
    return {"pass": ok, "attempts": attempts, "placement": placement,
            "d1": diag.get("d1", []), "d7": diag.get("d7", [])}


# ---------- entry ----------

def _auto_style_spec(image, root, project):
    """Content-aware fallback when NO campaign is named: derive a one-off
    lettering style FROM the source art itself and pin it project-locally.

    Every campaign-less post used to inherit the CLI's default campaign
    lettering, which put one campaign's brush font on everything (the
    systematic content-blind type reviewer flagged 2026-08-05). A plate
    generated from the source's own register is content-aware by
    construction; it is cached in the project's type-system so recomposites
    reuse it, and it is never shared across projects."""
    from . import audio_post
    ref = os.path.join(root, "type-system", "auto_style.png")
    if not os.path.exists(ref):
        os.makedirs(os.path.dirname(ref), exist_ok=True)
        # NEITHER an A-Z chart NOR words that could pass for copy. An alphabet
        # plate made the lettering model output the alphabet -- the spelling
        # gate read back "ABCDEFGHIJKLMNOPQRSTUVWXYZ", then "FOOD FIGHT WEEK
        # NOPQRST UVWXYZ", and it cost renders on four separate cards. Real
        # words leak too (a plate once blended "STYLE SAMPLE TEXT" into a
        # hook). So: two short, pronounceable, obviously-not-a-headline words
        # that show upper and lower case plus punctuation and digits. If they
        # ever do leak, the spelling gate catches them exactly as it caught
        # the alphabet -- but they do not read as "the thing to render".
        # MATCH THE MATERIAL, NOT THE PALETTE. This used to ask for a style
        # matching the artwork's "era, energy, material, palette" -- and on a
        # red strawberry it duly produced coral letters, which were then laid
        # over that same red strawberry and were unreadable. Type has to fight
        # the picture, not blend into it: keep the era and the material so the
        # lettering still belongs to the piece, but force a high-contrast
        # colour and a heavy outline so it survives any backdrop it lands on.
        prompt = ("Type specimen plate for a social post about this artwork: "
                  "the words 'Moonveil' on one line and 'Quartz 48' on a "
                  "second line, in a bold display "
                  "lettering style that MATCHES the artwork's era, energy, "
                  "material and craft, so the type clearly belongs to the "
                  "piece. But its COLOUR must be COMPLEMENTARY AND CONTRASTING, "
                  "never the artwork's own dominant hue: take the fill from the "
                  "opposite side of the colour wheel to that dominant hue, and "
                  "separate it in value too (light type against dark art, dark "
                  "type against light art). Give every letter a thick outline "
                  "in the opposing value so it stays readable over busy or "
                  "same-coloured backgrounds. Highly readable, centered, on "
                  "a plain pure black background, no other elements, no words, "
                  "no watermark")
        url = audio_post._fal(IMAGE_ENDPOINT,
                              {"prompt": prompt, "image_urls": [_data_uri(image)],
                               "num_images": 1},
                              EST_FRAME, "auto lettering style", project,
                              service="fal-image", find=_find_image_url)
        audio_post._download(url, ref)
        print(f"[motion] no campaign named: content-aware lettering style "
              f"derived from the source art -> {ref}")
    return {"name": f"auto:{os.path.basename(root)}", "product": "video",
            "register": "auto", "cta": "", "lettering_style_ref": ref,
            "palette": {}}


class SourceRejected(RuntimeError):
    """A source still failed pre-flight before any paid render was submitted."""


TARGET_ASPECT = FRAME_W / FRAME_H          # 9:16 portrait
PREFLIGHT_ASPECT_TOL = 0.30                # fraction off target before crop+warn


def _center_crop_portrait(img):
    """Center-crop a still to the 9:16 target instead of letting it squash. The
    og-images were 1200x630 landscape stretched into portrait -- a 3.4x aspect
    change (fault 2); a center crop keeps pixels square and the subject roughly
    where it was."""
    w, h = img.size
    if w / h > TARGET_ASPECT:              # too wide -> trim the sides
        nw = max(1, int(h * TARGET_ASPECT))
        x0 = (w - nw) // 2
        return img.crop((x0, 0, x0 + nw, h))
    nh = max(1, int(w / TARGET_ASPECT))    # too tall -> trim top/bottom
    y0 = (h - nh) // 2
    return img.crop((0, y0, w, y0 + nh))


def _merge_bands(bands, gap=8):
    """Merge overlapping / near-touching (y0, y1) bands into a minimal set."""
    ordered = sorted((int(b[0]), int(b[1])) for b in bands if b[1] > b[0])
    out = []
    for y0, y1 in ordered:
        if out and y0 <= out[-1][1] + gap:
            out[-1] = (out[-1][0], max(out[-1][1], y1))
        else:
            out.append((y0, y1))
    return out


def _free_span(zone, bands):
    """Tallest contiguous run inside ``zone`` (y0, y1) left free by ``bands``."""
    z0, z1 = zone
    cursor, best = z0, 0
    for b0, b1 in sorted(bands):
        if b1 <= z0 or b0 >= z1:            # band is outside the zone
            continue
        best = max(best, max(0, min(b0, z1) - cursor))
        cursor = max(cursor, min(b1, z1))
    best = max(best, z1 - cursor)
    return best


def _has_free_caption_zone(bands, min_free=90):
    """A caption can still land iff at least one of the two full-frame text
    zones (center hook / bottom karaoke) keeps a free run tall enough for a
    line. Only when BOTH are blanketed is the source genuinely unusable."""
    from . import layout
    return (_free_span(layout.FULL_CENTER, bands) >= min_free
            or _free_span(layout.FULL_BOTTOM, bands) >= min_free)


def _preflight_source(src, real_art, project="", spec=None):
    """Screen the SOURCE before any paid render (fix-list P1 #5, the single
    biggest lesson: six of nine content faults were visible in the source before
    a cent was spent). Refuse a source that will waste a render with a NAMED
    reason, auto-fix what is cheaply fixable, warn on the rest. Mutates ``src``
    in place when it center-crops. Raises SourceRejected on a hard fault unless
    REELLY_SKIP_PREFLIGHT is set. Returns the list of (y0, y1) render-space bands
    carrying burned-in source text, for the layout authority to route around."""
    from PIL import Image
    from . import design, layout
    img = Image.open(src).convert("RGB")
    problems, warnings = [], []
    source_text_bands = []

    # (fault 1) burned-in type. NOT a blanket reject (reviewer 2026-08-18):
    # sometimes the source IS a screen of text on purpose (a terminal, a
    # screenplay page), and there is already ONE no-overlap layout authority
    # (layout.occupied) that decides where our caption lands. So register each
    # baked-text box as a static avoid-band (scaled into the 1080x1920 render
    # space) that every placer -- finalize, overlays, placement -- keeps the
    # caption clear of, and reject ONLY when text blankets the frame so no
    # caption zone is left free.
    sw, sh = img.size
    scale = layout.FRAME_H / float(sh) if sh else 1.0
    for (x, y, w, h) in design.occupancy_text(img, project) or []:
        y0 = max(0, int(y * scale) - 8)
        y1 = min(layout.FRAME_H, int((y + h) * scale) + 8)
        if y1 > y0:
            source_text_bands.append((y0, y1))
    source_text_bands = _merge_bands(source_text_bands)
    if source_text_bands:
        if _has_free_caption_zone(source_text_bands):
            warnings.append(
                f"source carries burned-in text in {len(source_text_bands)} "
                "band(s); the caption will be routed clear of it "
                "(no-overlap layout authority)")
        else:
            problems.append(
                "source is blanketed with burned-in text and leaves no clear "
                "band for our caption -- re-export with a clear area or crop the "
                "text out")

    # (fault 5) character faces in a real-art source: real-art animates the art by
    # camera only, and a single keyframe makes the model invent/warp the people.
    if real_art:
        faces = design.occupancy_local(img, project).get("faces") or []
        if faces:
            problems.append(
                f"real-art source has {len(faces)} character face(s); real-art is "
                "camera-on-art only and the model invents people from one keyframe "
                "-- drop --real-art and use the reference-to-video character path")

    # (fault 3) source dominant hue vs the intended type hue: coral type on a red
    # strawberry read as unlabelled. Only checked when the campaign declares a
    # type hue; skipped (not guessed) otherwise.
    type_hue = (spec or {}).get("type_hue") if isinstance(spec, dict) else None
    if type_hue is not None:
        src_hue, _ = design.hue_luma(img)
        delta = design._hue_delta(src_hue, float(type_hue))
        if delta is not None and delta < 30.0:
            warnings.append(
                f"source hue {src_hue:.0f}deg is within {delta:.0f}deg of the "
                f"intended type hue {float(type_hue):.0f}deg; the caption will read "
                "low-contrast -- recolor the type or force a scrim")

    # (fault 2) aspect: auto-crop to portrait rather than squash; warn when far off.
    w, h = img.size
    off = abs(w / h - TARGET_ASPECT) / TARGET_ASPECT
    if off > PREFLIGHT_ASPECT_TOL:
        _center_crop_portrait(img).save(src)
        warnings.append(
            f"source aspect {w}x{h} is {off * 100:.0f}% off the 9:16 target; "
            "center-cropped to portrait to avoid a squash -- check the crop kept "
            "the subject")

    for msg in warnings:
        print(f"[motion] preflight WARNING: {msg}")
    if problems and os.environ.get("REELLY_SKIP_PREFLIGHT", "").strip():
        for p in problems:
            print(f"[motion] preflight OVERRIDDEN (REELLY_SKIP_PREFLIGHT): {p}")
        problems = []
    if problems:
        raise SourceRejected(
            "source failed pre-flight before any paid render:\n  - "
            + "\n  - ".join(problems)
            + "\nset REELLY_SKIP_PREFLIGHT=1 to render it anyway.")
    return source_text_bands


def _write_manifest(root, plan):
    """One human-facing summary of what shipped, written into the delivery dir so
    'what shipped' lives in ONE clean place (copy, gate results, source)."""
    prov = plan.get("provenance", {})
    m = {
        "cut": "final/cut_01.mp4",
        "hook": plan.get("hook", {}).get("text"),
        "payoff": plan.get("payoff", {}).get("text"),
        "cta": plan.get("cta"),
        "caption": plan.get("caption"),
        "source_image": prov.get("source_image"),
        "model": prov.get("model"),
        "real_art": prov.get("real_art"),
        "design_gate": plan.get("design_gate", {}).get("result"),
        "style_gate": plan.get("style_gate", {}).get("result"),
        "band_gate": plan.get("band_gate", {}).get("result"),
        "quality_blocked": bool(plan.get("quality_blocked", False)),
    }
    with open(os.path.join(root, "deliverables", "manifest.json"), "w") as fh:
        json.dump(m, fh, indent=2)


def _cleanup_work(root):
    """Delete disposable intermediates after a SUCCESSFUL render (reviewer: fewer
    files, only what is necessary). Removes the whole _work/ intermediate dir
    (base render, occupancy cache, overlay layers) and the pre-crop raw lettering
    + billing-cache keys. Keeps the deliverable + manifest, the source, the shipped
    hook/payoff PNGs and any human overrides, the plan and the qc reports.
    Set REELLY_KEEP_WORK=1 while iterating to preserve everything."""
    import shutil
    removed = []
    work = os.path.join(root, "_work")
    if os.path.isdir(work):
        shutil.rmtree(work, ignore_errors=True)
        removed.append("_work/")
    tdir = os.path.join(root, "type")
    for fn in (os.listdir(tdir) if os.path.isdir(tdir) else []):
        if fn.endswith(".raw.png") or fn.endswith(".key"):
            try:
                os.remove(os.path.join(tdir, fn))
                removed.append("type/" + fn)
            except OSError:
                pass
    if removed:
        print(f"[motion] cleaned {len(removed)} intermediate(s) "
              "(set REELLY_KEEP_WORK=1 to keep them while iterating)")


def _rescale_shots(shots, seconds):
    """Rescale a brain shot plan so the per-shot `seconds` sum to `seconds` total,
    keeping the shot COUNT and (roughly) the cut ratio the brain chose. Used by
    --seconds: `total` feeds BOTH the prompt ("ONE {total}-second video ... hard
    cut at {cut}s") and the H3 `duration`, so the two must agree. The last shot
    absorbs the rounding remainder; every shot stays at least 1s."""
    shots = list(shots or [{"seconds": 4}])
    planned = sum(max(1, int(s.get("seconds", 4))) for s in shots) or 1
    alloc = 0
    for s in shots[:-1]:
        s["seconds"] = max(1, round(int(s.get("seconds", 4)) * seconds / planned))
        alloc += s["seconds"]
    shots[-1]["seconds"] = max(1, seconds - alloc)
    return shots


def run(image, message, campaign=None, name=None, tier="draft",
        brain="gpt", skip_judge=False, real_art=False, copy_override=None,
        video_model="h3max", image_model="nano-banana", seconds=None,
        speech=False, music=True):
    from . import direct, judge, overlays
    spec = load_campaign(campaign) if campaign else None
    # real-art is a first-class treatment for posts about EXISTING games and
    # real product UI: the still is animated by camera only and no character is
    # invented. A campaign can pin it so every post in the campaign is real-art.
    real_art = real_art or bool(spec.get("real_art") if spec else False)
    image = os.path.abspath(image)
    name = name or "motion-" + os.path.splitext(os.path.basename(image))[0].lower()
    root = _scaffold(name)
    if spec is None:
        spec = _auto_style_spec(image, root, name)
    src = os.path.join(root, "source", os.path.basename(image))
    if not os.path.exists(src):
        import shutil
        shutil.copy2(image, src)

    ai = _author(message, image, spec, brain, name, copy_override=copy_override)
    if seconds:
        ai["shots"] = _rescale_shots(ai.get("shots"), seconds)
        print(f"[motion] --seconds {seconds}: shots -> "
              f"{[s['seconds'] for s in ai['shots']]} "
              f"(total {sum(s['seconds'] for s in ai['shots'])}s)")
    print(f"[motion] archetype={ai.get('archetype')} hook={ai['hook']['text']!r} "
          f"cta={ai.get('cta') or spec.get('cta')!r} "
          f"copy={ai.get('copy_source', 'brain')}"
          + (" real-art" if real_art else ""))

    # Intermediates live in _work/ (a sibling of deliverables/, NOT inside it), so
    # deliverables/ holds only the shippable cut -- one clean delivery directory
    # (reviewer 2026-08-13). _work/ is auto-deleted after a successful render.
    gfx_dir = os.path.join(root, "_work")
    os.makedirs(gfx_dir, exist_ok=True)
    # the text-free base is an intermediate: it lives in _work/ so judge only
    # gates what could actually ship
    base = os.path.join(gfx_dir, "cut_01_base.mp4")
    gen_report = None
    source_text_bands = []
    if not os.path.exists(base):
        # Screen the source before spending on a render (P1 #5). Skipped when a
        # base already exists -- that render is already paid for.
        source_text_bands = _preflight_source(src, real_art, project=name, spec=spec)
    if os.path.exists(base):
        print(f"[motion] using existing base render (delete to regenerate)")
        cut = int(ai["shots"][0].get("seconds", 4))
        total = int(sum(int(s.get("seconds", 4)) for s in ai["shots"]))
    elif real_art:
        print("[motion] real-art mode: reference-only camera moves on the real "
              "art, no invented character (M6/M7)")
        base, cut, total = _generate_real_art(
            src, ai, tier, name, base, video_model=video_model,
            video_prompt=(copy_override or {}).get("video_prompt"))
    else:
        # Reference-first (M6, reviewer 2026-08-12): build the character AND a
        # clean background plate BEFORE the video, then drive reference-to-video
        # with both -- never a single busy keyframe that makes the model invent
        # character and scene at once (MAR-37). Gated on art-style consistency and
        # reserved-text-zone clearance, with one re-render on failure.
        base, cut, total, gen_report = _generate_checked(
            root, src, ai, tier, name, base, video_model)
    _deletterbox(base)          # MAR-106: auto-fix the model's self-letterboxing
    dur = _dur(base)
    total = min(total, dur)

    # Art-style + text-zone gates come from the checked generation; real-art and an
    # already-existing base are treated as passing (nothing to re-generate).
    style_gate = (gen_report or {}).get("style", {"pass": True, "frames": []})
    band_gate = (gen_report or {}).get("band", {"pass": True})

    plan = {
        "id": "cut_01", "platform": "tiktok", "format": "image-motion",
        "title": ai["hook"]["text"][:60],
        "segments": [[0.0, dur]], "duration_s": round(dur, 2),
        "source_range": [0.0, dur],
        "hook": {"type": "lettering", "text": ai["hook"]["text"], "show_s": float(cut)},
        "overlay_lines": [], "caption": ai.get("caption", ""),
        "cta": ai.get("cta") or spec.get("cta", ""), "captions": "none",
        "payoff": {"text": ai["payoff"]["text"], "at_s": float(cut)},
        "register": ai.get("register", spec.get("register", "pro")),
        "archetype": ai.get("archetype"), "campaign": campaign,
        "source_text_bands": source_text_bands,
        "planned_from": "image", "speech": bool(speech),
        "playbook_version": direct.PLAYBOOK_VERSION,
        "because": (ai.get("because") or []) + [RULES["M2"], RULES["M7"]],
        "provenance": {"generated": True, "model": VIDEO_ENDPOINT, "tier": tier,
                       "source_image": os.path.basename(image),
                       "real_art": bool(real_art),
                       "copy": ai.get("copy_source", "brain"),
                       "shots": ai["shots"]},
    }
    json.dump([plan], open(os.path.join(root, "edl", "cut_plans.json"), "w"), indent=1)

    print("[motion] type + placement (occupancy-aware, M3/M7) ...")
    diag = {"placement": "subject-aware", "windows": [], "d1": [], "d7": []}
    events = _events(root, base, ai, spec, cut, total, name, diag=diag)
    out = os.path.join(root, "deliverables", "final", "cut_01.mp4")
    # H3 Max returns native foley/ambience on every render; the composite ALWAYS
    # keeps it (plus SFX) -- native audio is the baseline, never stripped. By
    # default a background music bed rides UNDER it (self-contained: foley + SFX
    # + bed); when the clip carries dialogue (--speech) the bed ducks under the
    # native voice. --trending (music=False) drops the bed so an account's
    # trending audio rides on top -- an opt-in, like speech.
    mus = None
    if music:
        from . import audio_post
        mus = audio_post.music(plan, os.path.join(gfx_dir, "cut_01_music.mp3"), name)
    overlays._composite(base, out, events, gfx_dir, music=mus, speech=speech)

    def recompose(attempt, avoid):
        diag["placement"], diag["windows"] = "subject-aware", []
        diag["d1"], diag["d7"] = [], []
        revised = _events(root, base, ai, spec, cut, total, name,
                          attempt=attempt, avoid=avoid, diag=diag)
        overlays._composite(base, out, revised, gfx_dir, music=mus, speech=speech)
        return out

    gate = _design_gate(root, out, ai, cut, total, name, recompose=recompose,
                        diag=diag)
    plan["placement"] = gate["placement"]
    plan["design_gate"] = {"result": "pass" if gate["pass"] else "fail",
                           "placement": gate["placement"],
                           "d1": gate["d1"],
                           "d7": gate["d7"],
                           "attempts": gate["attempts"]}
    plan["style_gate"] = {"result": "pass" if style_gate["pass"] else "fail",
                          "frames": style_gate.get("frames", [])}
    plan["band_gate"] = {"result": "pass" if band_gate.get("pass", True) else "fail",
                         "top": band_gate.get("top", True),
                         "bottom": band_gate.get("bottom", True)}
    if gen_report and gen_report.get("blocked"):
        plan["quality_blocked"] = True
        print("[motion] QUALITY BLOCKED: style/text-zone gate failed after a "
              "re-render; see _REVIEW/ and qc/ before shipping")
    if not style_gate.get("pass", True):
        print("[motion] STYLE GATE FAIL: see qc/style_report.md (art style drifted "
              "from the source)")
    overrides = []
    for leaf in ("hook.png.override.json", "payoff.png.override.json"):
        p = os.path.join(root, "type", leaf)
        if os.path.exists(p):
            overrides.append(json.load(open(p)))
    if overrides:
        plan["human_overrides"] = overrides
    json.dump([plan], open(os.path.join(root, "edl", "cut_plans.json"), "w"), indent=1)
    if not gate["pass"]:
        print("[motion] DESIGN GATE FAIL: see qc/design_report.md")

    if not skip_judge:
        judge.run(name)

    # One clean delivery dir + fewer files (org pass, reviewer 2026-08-13): write
    # the manifest, then delete intermediates on a SUCCESSFUL, unblocked render.
    # A failed/blocked render keeps _work/ and _REVIEW/ for triage.
    _write_manifest(root, plan)
    blocked = bool(gen_report and gen_report.get("blocked"))
    if gate["pass"] and not blocked and not os.environ.get("REELLY_KEEP_WORK", "").strip():
        _cleanup_work(root)

    print(f"[motion] project {root}")
    print("[motion] deliverable: deliverables/final/cut_01.mp4 (+ manifest.json)")
    print("[motion] screen it end to end, then record plan['screened'] "
          "before publishing (house rule: a human approves everything).")
    return root
