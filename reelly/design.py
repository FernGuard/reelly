"""Design gate: a vision model judges the composed frame (playbook D1-D6).

WHY THIS EXISTS
placement.py answers "where is the calmest legal rectangle" from pixel
statistics, and on cinematic art those statistics lie: a dark silhouetted
subject scores as the calmest region on the frame, which is how an end card
landed on a leaping character. Metrics
stay as hints; this module makes the COMPOSED FRAME the unit of QC (D5):

  subject_box()  one vision call per moment: where the single dominant subject
                 is, so placement treats it as occupied (D1)
  occupancy()    one vision call returning MANY regions -- every face, body and
                 baked title/logo -- so placement can clear a multi-character
                 lineup and baked lettering, not just one dominant subject (D1/D2/D6)
  critique()     the candidate frame with graphics composited, judged against
                 D1-D6; structured issues name the rule and the offending
                 region so the caller can re-place, not just fail

Judges: Gemini (always available, pennies, ledgered) or SOL (gpt-5.6-sol,
`brain="sol"`) when the call warrants the stronger eye. Both return the same
JSON contract. Selecting SOL does not silently switch to Gemini.
"""
import colorsys
import io
import json
import math
import os
import re
import unicodedata

from . import config, ledger

EST_VISION = 0.002          # one small image + short JSON reply
PAD = 0.04                  # D6: subject box padding, fraction of frame height

D_RULES = """D1: nothing may overlap the SUBJECT (the being/object the eye goes to).
D2: ONE brand moment per frame; text/logos baked into the art count. Two logos at once = fail.
D3: nothing floats: every text line needs an anchor (scrim, band or card) and measured contrast.
D4: one primary element per moment: the viewer must know what to read first.
D6: margins: graphics clear of platform chrome and not touching the subject's silhouette.
D7: text color is COMPLEMENTARY to the scene, never analogous (blue scene -> warm text). A designed logotype with a thick dark keyline counts as anchored for D3 ONLY over calm, contrasting backdrops; over busy areas or backdrops sharing the text's hue it still needs a scrim or a calmer position."""

SUBJECT_PROMPT = """Find the main SUBJECT of this vertical frame: the single being or object a
viewer's eye goes to first (a person, creature, vehicle, glowing object).
Reply JSON only:
{"found": true/false, "box": [x0, y0, x1, y1]}
Box in fractions of frame width/height (0..1). If several, box the dominant one."""

CRITIQUE_PROMPT = """You are a senior motion designer reviewing ONE composed frame of a vertical
social video (1080x1920). Graphics were composited onto generated footage.
Overlay text on this frame: {texts}

Judge it against these rules:
""" + D_RULES + """

Be strict: this ships to the public under a brand. Amateur tells (text on the
subject, floating unanchored type, competing brand marks, collisions with art
text) are automatic fails.

Reply JSON only:
{{"pass": true/false,
  "issues": [{{"rule": "D1", "what": "<short>", "region": [x0,y0,x1,y1],
               "fix": "<short, actionable>"}}]}}
Regions in fractions of frame size. Empty issues when pass."""


def _gemini(contents, detail, project):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=config.provider_key("google-genai"))
    ledger.check(EST_VISION)
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL, contents=contents,
        config=types.GenerateContentConfig(response_mime_type="application/json",
                                           temperature=0.2))
    ledger.add("gemini-design", detail, EST_VISION, project)
    try:
        return json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        return None


def _sol(image, prompt, detail, project):
    """gpt-5.6-sol at high effort; the stronger eye for contested frames."""
    import base64
    import requests
    from . import direct
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=88)
    b64 = base64.b64encode(buf.getvalue()).decode()
    ledger.check(direct.EST_REFINE_COST_GPT)
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {config.provider_key('openai')}"},
        json={"model": direct.GPT_MODEL, "reasoning_effort": "high",
              "messages": [{"role": "user", "content": [
                  {"type": "text", "text": prompt},
                  {"type": "image_url", "image_url": {
                      "url": f"data:image/jpeg;base64,{b64}"}}]}],
              "response_format": {"type": "json_object"}},
        timeout=240)
    ledger.add("gpt-design", detail, direct.EST_REFINE_COST_GPT, project)
    return json.loads(r.json()["choices"][0]["message"]["content"])


def _coerce_box(value):
    """Pull four coordinates out of whatever shape the vision model returned.

    The contract asks for ``[x0, y0, x1, y1]`` but real replies vary: a nested
    ``[[...]]`` (this is the ``expected 4, got 1`` crash from 2026-07-31), a
    corner dict, a comma string, or ``[x, y, w, h]``. Return four floats or None.
    """
    seen = 0
    while isinstance(value, (list, tuple)) and len(value) == 1 and seen < 4:
        value, seen = value[0], seen + 1          # unwrap [[...]] / [ {...} ]
    if isinstance(value, str):
        value = re.findall(r"-?\d+(?:\.\d+)?", value)
    if isinstance(value, dict):
        for keys in (("x0", "y0", "x1", "y1"), ("xmin", "ymin", "xmax", "ymax"),
                     ("left", "top", "right", "bottom"), ("x", "y", "w", "h")):
            if all(k in value for k in keys):
                vals = [value[k] for k in keys]
                if keys == ("x", "y", "w", "h"):
                    x, y, w, h = vals
                    vals = [x, y, x + w if _num(w) and _num(x) else w,
                            y + h if _num(h) and _num(y) else h]
                value = vals
                break
        else:
            return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    out = []
    for n in value:
        if isinstance(n, bool):
            return None
        if isinstance(n, str):
            try:
                n = float(n)
            except ValueError:
                return None
        if not isinstance(n, (int, float)):
            return None
        out.append(float(n))
    return out


def _num(n):
    return isinstance(n, (int, float)) and not isinstance(n, bool)


def _extract_box(v):
    """The subject box out of a response dict, trying the keys models actually
    use (box, bbox, box_2d, ...) before treating the dict itself as corners."""
    if not isinstance(v, dict):
        return None
    for key in ("box", "bbox", "box_2d", "boxes", "region", "subject"):
        if key in v:
            box = _coerce_box(v[key])
            if box is not None:
                return box
    return _coerce_box(v)


def _normalize_box(box, W, H):
    """Coordinates in whatever scale the model used -> (x0, y0, x1, y1) fractions
    in 0..1, or None if degenerate.

    Observed 2026-07-31: gemini-3.5-flash returns 0..1000 per-mille integers
    (e.g. [345, 50, 548, 949]) regardless of the "fractions (0..1)" instruction,
    which the old ``0 <= n <= 1`` guard rejected on EVERY frame. We detect the
    scale by magnitude: <=1.5 already fractions, <=1000 the gemini per-mille
    contract, larger than that absolute pixels. Corners are sorted so a swapped
    x0/x1 or y0/y1 is tolerated rather than failed.
    """
    m = max(abs(n) for n in box)
    if m <= 1.5:
        frac = list(box)
    elif m <= 1000.0 + 1e-6:
        frac = [n / 1000.0 for n in box]
    else:
        frac = [box[0] / W, box[1] / H, box[2] / W, box[3] / H]
    x0, x1 = sorted((frac[0], frac[2]))
    y0, y1 = sorted((frac[1], frac[3]))
    x0, y0, x1, y1 = (max(0.0, min(1.0, n)) for n in (x0, y0, x1, y1))
    if x1 - x0 < 1e-3 or y1 - y0 < 1e-3:
        return None
    return (x0, y0, x1, y1)


def subject_box(image, project=""):
    """Padded subject pixel box (x, y, w, h) for a PIL frame, or None (D1/D6)."""
    v = _gemini([image, SUBJECT_PROMPT], "subject box", project)
    if not isinstance(v, dict) or v.get("found") is False:
        return None
    raw = _extract_box(v)
    if raw is None:
        print("[design] malformed subject box from vision; skipping sample")
        return None
    frac = _normalize_box(raw, *image.size)
    if frac is None:
        print("[design] out-of-bounds subject box from vision; skipping sample")
        return None
    x0, y0, x1, y1 = frac
    W, H = image.size
    pad = PAD * H
    x, y = max(0, x0 * W - pad), max(0, y0 * H - pad)
    return (int(x), int(y), int(min(W, x1 * W + pad) - x),
            int(min(H, y1 * H + pad) - y))


# ---------- D1/D2/D6: full occupancy, not one dominant subject ----------
#
# subject_box models the frame as ONE thing the eye goes to. On multi-character
# art -- a six-character lineup filling the frame -- every candidate row
# overlaps that single box, least-overlap
# degenerates, and the payoff lands across a character's face. Baked titles and
# logos (SampleTitle, ExampleBrand in those same reports) are flagged D2 by the critic
# but never MODELLED, so placement cannot clear them. occupancy() returns three
# lists in one vision call so placement can reason about every face, body and
# baked mark at once, weighting faces heaviest.

OCCUPANCY_PROMPT = """Map everything a text overlay must avoid in this vertical
frame (1080x1920). Return THREE separate lists -- never merge them:
- faces: a tight box around EACH human/character/creature FACE or head. ONE box
  per face; a six-character lineup has six boxes. This is the most important list.
- subjects: a box around each prominent BODY / figure / vehicle / focal object
  (the body, not the face, which is already in faces).
- text_regions: a box around any text, title, logo, wordmark, watermark, caption
  or UI that is already BAKED INTO the art.
Partial answers are fine: list what you are sure of, leave a list empty when
nothing of that kind is present. Never invent boxes to fill a list.
Reply JSON only, each box as [x0, y0, x1, y1] in fractions of frame size (0..1):
{"faces": [[...], ...], "subjects": [[...], ...], "text_regions": [[...], ...]}"""

# A model that answers the "JSON list" contract with one flat regions list
# (each item tagged with a kind) is routed back into the three buckets by these
# keyword hints; anything unrecognised falls to subjects (avoided, never faced).
_KIND_TO_KEY = {
    "face": "faces", "head": "faces",
    "subject": "subjects", "body": "subjects", "figure": "subjects",
    "person": "subjects", "character": "subjects", "object": "subjects",
    "vehicle": "subjects", "creature": "subjects",
    "text": "text_regions", "title": "text_regions", "logo": "text_regions",
    "wordmark": "text_regions", "watermark": "text_regions",
    "caption": "text_regions", "ui": "text_regions",
}


def _boxes_from_list(items, W, H, pad_frac):
    """Each raw box in a region list -> padded pixel (x, y, w, h). Reuses the
    same _coerce_box/_normalize_box path as subject_box, so per-mille integers,
    nested lists, corner dicts and swapped corners are all tolerated. Malformed
    entries are skipped, never fatal (D1 must degrade gracefully on partial
    replies, not crash the whole frame)."""
    out = []
    if not isinstance(items, (list, tuple)):
        return out
    for item in items:
        raw = _extract_box(item) if isinstance(item, dict) else _coerce_box(item)
        if raw is None:
            continue
        frac = _normalize_box(raw, W, H)
        if frac is None:
            continue
        x0, y0, x1, y1 = frac
        pad = pad_frac * H
        x = max(0.0, x0 * W - pad)
        y = max(0.0, y0 * H - pad)
        out.append((int(x), int(y),
                    int(min(W, x1 * W + pad) - x),
                    int(min(H, y1 * H + pad) - y)))
    return out


def _absorb_flat(items, occ, W, H):
    """Fold a flat [{"kind": "face", "box": [...]}, ...] list into the three
    buckets. The kind keyword decides the bucket; an unknown kind is treated as
    a body (avoided, but never mistaken for a face)."""
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("type")
                   or item.get("label") or "").lower()
        key = next((k for token, k in _KIND_TO_KEY.items() if token in kind),
                   "subjects")
        occ[key].extend(
            _boxes_from_list([item], W, H, 0.0 if key == "text_regions" else PAD))


def occupancy(image, project=""):
    """Every region a text overlay must avoid in one PIL frame, in ONE vision
    call: {"faces": [...], "subjects": [...], "text_regions": [...]} as pixel
    (x, y, w, h) boxes.

    Faces and subjects are padded (D6: clear of the silhouette); baked text is
    left tight. A malformed reply is RETRIED twice before the window is given
    up as content-blind (the Zombie card burned five renders on a vision reply
    that came back unparseable every time and was never retried). A persistently
    unparseable or a legitimately empty reply returns three empty lists, which
    the caller reads as content-blind: placement then lands type in the safe
    top/bottom edge band, never blind on the middle. It never raises, so one bad
    frame in a sampled window cannot sink the placement."""
    W, H = image.size
    occ = {"faces": [], "subjects": [], "text_regions": []}
    # Retry only an UNPARSEABLE reply (not a dict/list): a well-formed reply that
    # parses to nothing is a genuinely empty frame, and re-asking it just burns
    # a second vision call for the same empty answer.
    v = None
    for attempt in range(3):          # 1 try + 2 retries
        v = _gemini([image, OCCUPANCY_PROMPT], "occupancy map", project)
        if isinstance(v, (dict, list)):
            break
        if attempt < 2:
            print(f"[design] malformed occupancy reply from vision "
                  f"(try {attempt + 1}/3); retrying")
    if isinstance(v, dict):
        occ["faces"] = _boxes_from_list(v.get("faces"), W, H, PAD)
        occ["subjects"] = _boxes_from_list(v.get("subjects"), W, H, PAD)
        occ["text_regions"] = _boxes_from_list(v.get("text_regions"), W, H, 0.0)
        if not any(occ.values()) and isinstance(v.get("regions"), list):
            _absorb_flat(v["regions"], occ, W, H)          # flat-list dialect
    elif isinstance(v, list):
        _absorb_flat(v, occ, W, H)
    else:
        print("[design] malformed occupancy reply from vision after retries; "
              "no regions -- placement falls back to the safe edge band")
    return occ


# ---------- hybrid occupancy: local detectors, Gemini only for text ----------
#
# _sample_occupancy called occupancy() (one Gemini vision call) on 5 frames per
# overlay window. Faces and busy regions do not need a network model: FaceMesh
# (face.detect_faces) already runs locally for the cam insert, and "busy" is a
# pixel statistic. Only BAKED TEXT/logos genuinely need a vision model. So the
# hybrid samples faces/subjects locally on every frame and asks Gemini ONCE per
# window (midpoint) for text_regions: 5 calls -> 1. REELLY_OCCUPANCY=gemini in
# the environment restores the all-Gemini path unchanged.

# busy-region grid: cells whose local edge energy clears this mean (0..255,
# FIND_EDGES response) count as occupied; adjacent busy cells merge into boxes.
BUSY_COLS, BUSY_ROWS = 9, 16
BUSY_EDGE_T = 22.0


def _busy_regions(im):
    """Cheap luma/edge heatmap -> merged pixel boxes of busy areas.

    Downscale, FIND_EDGES, grid the response, flag high-energy cells, and
    merge 4-connected runs into bounding boxes. This stands in for the
    'subjects' list: type should avoid detail-dense areas, and edge density
    is what 'detail-dense' measures. Pure Pillow, no network, no model."""
    from PIL import Image, ImageFilter
    W, H = im.size
    cw, ch = 24, 24
    small = im.convert("L").resize((BUSY_COLS * cw, BUSY_ROWS * ch))
    edges = small.filter(ImageFilter.FIND_EDGES)
    px = edges.tobytes()          # mode "L": one byte per pixel, row-major
    sw = BUSY_COLS * cw
    busy = [[False] * BUSY_COLS for _ in range(BUSY_ROWS)]
    for r in range(BUSY_ROWS):
        for c in range(BUSY_COLS):
            tot = 0
            for yy in range(r * ch, (r + 1) * ch):
                row = yy * sw
                for xx in range(c * cw, (c + 1) * cw):
                    tot += px[row + xx]
            busy[r][c] = (tot / (cw * ch)) >= BUSY_EDGE_T
    # merge 4-connected busy cells into bounding boxes
    seen = [[False] * BUSY_COLS for _ in range(BUSY_ROWS)]
    boxes = []
    for r in range(BUSY_ROWS):
        for c in range(BUSY_COLS):
            if not busy[r][c] or seen[r][c]:
                continue
            stack, cells = [(r, c)], []
            seen[r][c] = True
            while stack:
                rr, cc = stack.pop()
                cells.append((rr, cc))
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nr, nc = rr + dr, cc + dc
                    if (0 <= nr < BUSY_ROWS and 0 <= nc < BUSY_COLS
                            and busy[nr][nc] and not seen[nr][nc]):
                        seen[nr][nc] = True
                        stack.append((nr, nc))
            r0 = min(x[0] for x in cells)
            r1 = max(x[0] for x in cells)
            c0 = min(x[1] for x in cells)
            c1 = max(x[1] for x in cells)
            x = int(c0 / BUSY_COLS * W)
            y = int(r0 / BUSY_ROWS * H)
            boxes.append((x, y, int((c1 + 1 - c0) / BUSY_COLS * W),
                          int((r1 + 1 - r0) / BUSY_ROWS * H)))
    return boxes


def occupancy_local(image, project=""):
    """Occupancy map from LOCAL detectors only, same schema as occupancy():
    {"faces": [...], "subjects": [...], "text_regions": []} pixel (x, y, w, h).

    faces    -- FaceMesh via face.detect_faces (the same detector the cam
                insert trusts), padded like the vision path (D6)
    subjects -- edge/luma busy regions (_busy_regions)
    text     -- always empty here; baked text needs a vision model, and the
                hybrid sampler asks Gemini for it once per window

    Accepts a PIL image or a frame path. Degrades gracefully: a missing
    mediapipe/model yields empty faces (loudly), never an exception."""
    from PIL import Image
    if isinstance(image, str):
        image = Image.open(image)
    im = image.convert("RGB")
    W, H = im.size
    occ = {"faces": [], "subjects": [], "text_regions": []}
    try:
        import numpy as np
        from . import face
        for f in face.detect_faces(np.asarray(im)):
            pad = PAD * H
            x = max(0.0, f["cx"] - f["w"] / 2 - pad)
            y = max(0.0, f["cy"] - f["h"] / 2 - pad)
            w = min(W, f["cx"] + f["w"] / 2 + pad) - x
            h = min(H, f["cy"] + f["h"] / 2 + pad) - y
            if w > 0 and h > 0:
                occ["faces"].append((int(x), int(y), int(w), int(h)))
    except Exception as e:
        print(f"[design] local face detection unavailable ({e}); "
              "faces empty for this frame")
    occ["subjects"] = _busy_regions(im)
    return occ


TEXT_REGIONS_PROMPT = """Find any text BAKED INTO this vertical frame: titles,
logos, wordmarks, watermarks, captions or UI text that are part of the image
itself. A tight box around each. Leave the list empty when there is none;
never invent boxes.
Reply JSON only, each box as [x0, y0, x1, y1] in fractions of frame size (0..1):
{"text_regions": [[...], ...]}"""


def occupancy_text(image, project=""):
    """Baked text/logo boxes only, in ONE vision call: the one part of the
    occupancy map a local detector cannot supply. Returns a list of pixel
    (x, y, w, h) boxes; empty (never raises) on a malformed reply."""
    v = _gemini([image, TEXT_REGIONS_PROMPT], "occupancy text regions", project)
    W, H = image.size
    if isinstance(v, dict):
        return _boxes_from_list(v.get("text_regions"), W, H, 0.0)
    if isinstance(v, list):
        return _boxes_from_list(v, W, H, 0.0)
    print("[design] malformed text-region reply from vision; no regions")
    return []


# ---------- D7: measured lettering/backdrop contrast ----------
#
# A vision critic can re-flag the same warm-on-warm placement when nothing
# measures contrast and forces a scrim. This gate
# reads the actual pixels: dominant hue + luma of the lettering asset vs. the
# region it lands on, escalates the scrim until the contrast is readable, and
# fails loudly with the numbers when even a full scrim cannot rescue it.

INK_LUMA = 12.0             # a dark neutral scrim collapses toward this luma
SCRIM_CAP = 0.82            # the heaviest scrim _scrim_for_box will emit
D7_TARGET_GAP = 70.0        # luma separation (0..255) that reads cleanly
D7_MIN_GAP = 60.0           # below this, unscrimmed type is "low contrast"


def hue_luma(image):
    """Saturation-weighted circular-mean hue (degrees, or None near-greyscale)
    and mean luma (0..255) over the visible pixels of a PIL image. Fully
    transparent pixels (a lettering PNG's background) are ignored."""
    im = image.convert("RGBA")
    im.thumbnail((80, 80))
    sx = sy = lum = wn = 0.0
    n = 0
    for r, g, b, a in im.getdata():
        if a < 40:
            continue
        n += 1
        lum += 0.299 * r + 0.587 * g + 0.114 * b
        mx, mn = max(r, g, b), min(r, g, b)
        if mx == 0:
            continue
        sat = (mx - mn) / mx
        if sat < 0.12:                              # too grey for a real hue
            continue
        h = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)[0]
        ang = h * 2 * math.pi
        sx += sat * math.cos(ang)
        sy += sat * math.sin(ang)
        wn += sat
    if n == 0:
        return None, 0.0
    luma = lum / n
    if wn < 1e-6:
        return None, luma
    return math.degrees(math.atan2(sy, sx)) % 360.0, luma


def _is_warm(hue):
    return hue is not None and (hue <= 60.0 or hue >= 330.0)


def _hue_delta(a, b):
    if a is None or b is None:
        return None
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def contrast_gate(asset_image, backdrop_image, current_scrim=0.0):
    """D7 verdict for one lettering asset over its backdrop region.

    Returns the measured hue/luma of both, whether the pairing is warm-on-warm
    or low-luma, the scrim needed to rescue it (escalated from ``current_scrim``),
    and ``pass``: False only when even a full scrim leaves the type unreadable,
    i.e. the asset itself is too dark to separate from a darkened backdrop and
    the real fix is a colour change, not a scrim.
    """
    a_hue, a_luma = hue_luma(asset_image)
    b_hue, b_luma = hue_luma(backdrop_image)
    delta = _hue_delta(a_hue, b_hue)
    warm_on_warm = bool(_is_warm(a_hue) and _is_warm(b_hue)
                        and delta is not None and delta <= 45.0)
    luma_gap = abs(a_luma - b_luma)
    low_luma = luma_gap < D7_MIN_GAP
    measured = {
        "asset_hue": None if a_hue is None else round(a_hue),
        "backdrop_hue": None if b_hue is None else round(b_hue),
        "asset_luma": round(a_luma), "backdrop_luma": round(b_luma),
        "hue_delta": None if delta is None else round(delta),
        "luma_gap": round(luma_gap),
    }
    if not (warm_on_warm or low_luma):
        return {"pass": True, "escalated": False, "scrim": round(current_scrim, 2),
                "warm_on_warm": warm_on_warm, "low_luma": low_luma, **measured}
    # A dark scrim is the D3/D7 fix: it drags the backdrop the eye sees behind
    # the glyphs toward INK_LUMA. Find the smallest scrim reaching a clean gap.
    scrim = max(current_scrim, 0.0)
    while scrim < SCRIM_CAP:
        eff_b = b_luma * (1 - scrim) + INK_LUMA * scrim
        if abs(a_luma - eff_b) >= D7_TARGET_GAP:
            break
        scrim = round(scrim + 0.06, 2)
    scrim = round(min(scrim, SCRIM_CAP), 2)
    eff_b = b_luma * (1 - scrim) + INK_LUMA * scrim
    ok = abs(a_luma - eff_b) >= D7_TARGET_GAP
    return {"pass": bool(ok), "escalated": True, "scrim": scrim,
            "warm_on_warm": warm_on_warm, "low_luma": low_luma, **measured}


def critique(image, texts, brain="gemini", project=""):
    """D-rules verdict on one composed frame: {"pass": bool, "issues": [...]}."""
    prompt = CRITIQUE_PROMPT.format(texts=json.dumps(texts))
    if brain == "sol":
        return _sol(image, prompt, "frame critique", project)
    v = _gemini([image, prompt], "frame critique", project)
    return v or {"pass": True, "issues": [{"rule": "D5", "what": "critic unavailable",
                                           "region": [0, 0, 0, 0],
                                           "fix": "manual frame check"}]}


STYLE_MATCH_PROMPT = """Image 1 is a frame from a generated video. Image 2 is the
SOURCE artwork whose ART STYLE the video must keep: the medium, texture and
finish (claymation, pixel art, flat 2D anime, painterly, 3D toon render...), NOT
the subject, scene or colours. Does Image 1 render in the SAME art style as
Image 2? A different medium (for example flat 2D when the source is claymation)
is a FAIL.
Reply JSON only: {"match": true|false, "drift": "<what differs, empty if same>"}"""


def style_match(frame, reference, project=""):
    """Does a generated frame keep the SOURCE artwork's art style? Compares the
    two images DIRECTLY -- the source image is the style reference, not a lossy
    text descriptor. Returns {"match": bool, "drift": str}; a missing reference
    or a garbled reply is a pass (never block a render on an absent critic)."""
    if reference is None:
        return {"match": True, "drift": ""}
    from PIL import Image
    if isinstance(reference, str):
        reference = Image.open(reference)
    v = _gemini([frame, reference, STYLE_MATCH_PROMPT], "style match", project)
    if not isinstance(v, dict) or "match" not in v:
        return {"match": True, "drift": ""}
    return {"match": bool(v.get("match")), "drift": str(v.get("drift") or "")}


LOCATE_PROMPT = """The narrator is talking about: "{phrase}". In THIS frame, find
the SINGLE on-screen thing they mean -- a UI element, object or region the viewer
should look at. If there is a clear referent, give a tight box around it and
whether a RING suits it (a small compact thing) or CORNER BRACKETS suit it (a
larger area/region). If there is NO clear on-screen referent, found:false (do not
invent one).
Reply JSON only: {{"found": true|false, "box": [x0, y0, x1, y1] in 0..1 fractions,
"shape": "circle"|"brackets"}}"""


def locate_referent(frame, phrase, project=""):
    """Where on THIS frame is the thing the narrator names? Returns
    {"box": (x, y, w, h) pixels, "shape": "circle"|"brackets"} or None when there
    is no clear referent -- so a pointer overlay is only ever placed on something
    real, never invented. Used by the transcript-driven pointer planner."""
    v = _gemini([frame, LOCATE_PROMPT.format(phrase=phrase)], "locate referent", project)
    if not isinstance(v, dict) or not v.get("found"):
        return None
    W, H = frame.size
    boxes = _boxes_from_list([v.get("box")], W, H, 0.0)
    if not boxes:
        return None
    shape = "brackets" if v.get("shape") == "brackets" else "circle"
    return {"box": boxes[0], "shape": shape}


READ_PROMPT = """Transcribe ALL text visible in this image, in reading order.
Reply JSON only: {"text": "<all text as one string>"}"""


def read_text(image, project=""):
    """What text does this image actually carry? Used as the spelling gate on
    generated lettering (M7/M8): generated type ships only if it reads back
    exactly as written."""
    v = _gemini([image, READ_PROMPT], "read text", project)
    return (v or {}).get("text", "")


READ_LETTERING_PROMPT = """This image contains one short phrase in stylized
decorative DISPLAY LETTERING (brush strokes, carved letters, heavy outlines,
gradients) on a plain background. It is lettering art, not a scene; do not
report "no text". Transcribe the exact words the lettering spells, letter by
letter.
Reply JSON only: {"text": "<the phrase>"}"""


def read_lettering(image, project=""):
    """Second-chance read for decorative type the general READ_PROMPT pass
    misses: general transcription read heavily-stylized glyphs as no text at
    all three times on 2026-08-05, failing correctly-spelled assets into the
    human-override path. Framing the image as lettering art recovers those."""
    v = _gemini([image, READ_LETTERING_PROMPT], "read lettering", project)
    return (v or {}).get("text", "")


def normalize_spelling(value):
    """Canonical text used by the lettering OCR gate.

    OCR engines are inconsistent about typographic apostrophes, thousands
    separators and whether a currency glyph is transcribed as a symbol or a
    word.  Preserve the semantic currency marker, then discard presentation
    punctuation on *both* sides of the comparison.
    """
    text = unicodedata.normalize("NFKC", str(value or ""))
    for symbol in "$€£¥₹":
        text = text.replace(symbol, " ")
    currency_words = {"DOLLARS", "DOLLAR", "EUROS", "EURO", "POUNDS",
                      "POUND", "YEN", "RUPEES", "RUPEE", "USD", "EUR",
                      "GBP", "JPY", "INR"}
    tokens = []
    for token in text.upper().split():
        cleaned = "".join(ch for ch in token if ch.isalnum())
        if cleaned:
            if cleaned not in currency_words:
                tokens.append(cleaned)
    return "".join(tokens)


def _alnum_words(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    spaced = "".join(ch if ch.isalnum() else " " for ch in text)
    return spaced.split()


def spelling_case_ok(seen, text):
    """False only for a STRAY INTERIOR CAPITAL -- an uppercase letter inside a
    word that the intended text has lowercase, while the rest of that word's
    lowercase-intended interior stays lowercase in the read. That mixed pattern
    is exactly 'eitheR' for 'either', which the case-blind normalize_spelling
    gate passed (both upper-cased to 'EITHER').

    A wholesale all-caps read -- a caps lettering STYLE, or plain OCR case drift
    -- shows no such mix and passes: the gate only fires on a lone capital amid
    otherwise-lowercase letters, never on uniform casing. Words whose letters do
    not match case-insensitively are left to the spelling gate proper.
    """
    sw, tw = _alnum_words(seen), _alnum_words(text)
    if len(sw) != len(tw):
        return True                      # misaligned -> the spelling gate's job
    for s_word, t_word in zip(sw, tw):
        if len(s_word) != len(t_word) or s_word.upper() != t_word.upper():
            continue                     # letters differ -> spelling gate, not here
        interior = [sc for i, (sc, tc) in enumerate(zip(s_word, t_word))
                    if i > 0 and tc.islower()]
        if any(c.isupper() for c in interior) and any(c.islower() for c in interior):
            return False                 # a lone cap amid lowercase = stray
    return True


def lettering_style_fidelity(style_image, lettering_image, project=""):
    """Return a concrete style rejection or pass.

    A critic response of ``match:false`` without a reason is not actionable.
    Re-ask once for the missing diagnosis; if it still cannot name one, the
    rendering passes rather than burning a good asset on an opaque rejection.
    """
    # JUDGE AGAINST THE REFERENCE, NOT AGAINST A REMEMBERED CAMPAIGN. This
    # prompt used to hardcode "warm gradient fill and a THICK DARK outline" --
    # the register of one early campaign. Every later campaign whose type is
    # anything else (flat, clay, chrome, letterpress) was rejected forever no
    # matter how exactly it matched its OWN locked style, because the critic
    # was comparing image 2 to a sentence instead of to image 1. Lettering is
    # per-campaign and in the content's register, so the only correct question
    # is whether the two images share a style.
    prompt = ('Image 1 is the locked lettering style. Image 2 is new lettering '
              'that must match it. Judge ONLY against image 1: do the two share '
              'the same letterforms, fill treatment, outline weight, texture and '
              'finish? Do not apply any style description of your own, and do not '
              'require any feature image 1 does not itself have. Reply JSON only: '
              '{"match": true/false, "missing": "<short concrete reason or null>"}')
    verdict = _gemini([style_image, lettering_image, prompt],
                      "lettering style fidelity", project)
    if verdict and verdict.get("match"):
        return {"match": True, "missing": None}
    reason = str((verdict or {}).get("missing") or "").strip()
    if reason:
        return {"match": False, "missing": reason}
    retry = _gemini(
        [style_image, lettering_image,
         prompt + " Your previous rejection had no reason. If rejecting, name the specific visible mismatch."],
        "lettering style fidelity reason", project)
    if retry and retry.get("match"):
        return {"match": True, "missing": None}
    reason = str((retry or {}).get("missing") or "").strip()
    return ({"match": False, "missing": reason} if reason
            else {"match": True, "missing": None})


def region_to_box(region, W=1080, H=1920):
    """Critic region fractions -> pixel (x, y, w, h) for placement avoid lists."""
    x0, y0, x1, y1 = region
    return (int(x0 * W), int(y0 * H), int((x1 - x0) * W), int((y1 - y0) * H))
