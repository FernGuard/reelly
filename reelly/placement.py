"""Content-aware placement: put an overlay where the picture has room for it.

Fixed coordinates are a guess that is wrong on most frames. A lower-third
pinned to y=610 lands on a face in one cut and in empty sky in the next, and a
caption sized 52px is huge over a close-up and unreadable over a wide shot.

This module looks at the actual frame the overlay will sit on and answers four
questions with evidence:

  WHERE  the calmest rectangle that is inside the platform safe zone, is not
         already occupied by the hook / captions / end tag, and is away from
         the subject. "Calm" is low local detail: text over a flat wall reads,
         text over foliage does not.
  HOW BIG  scaled so the text fills a target share of the frame width, then
         reduced only if it will not fit the room that exists.
  WHAT COLOUR  measured against the backdrop luminance behind it, so light
         text never lands on a bright sky.
  HOW LOUD  a bright, busy backdrop gets a heavier scrim and stroke; a calm
         dark one gets almost none.

Everything returns plain numbers, so the planner can record them in the cut
plan and a human can read why a mark is where it is.
"""
import os
import subprocess
import tempfile

from . import config

W, H = 1080, 1920
# Platform chrome (G2/CO3). Nothing of ours is placed outside this.
SAFE = {"top": 200, "bottom": 1500, "left": 60, "right": 940}
COLS, ROWS = 12, 24          # grid over the frame; cell is 90 x 80 px
# Karaoke cues are drawn centred at y=1430 and wrap to two lines on a long
# phrase, so the band they can occupy is much taller than one line. Reserving
# only the single line is how an end card ended up sitting on top of a caption.
CAPTION_BAND = (1315, 1580)
SAMPLE_W = 360               # analyse at 1/3 scale, plenty for a detail map

INK, PAPER, ACCENT, MARKER = "#090c0a", "#FCFCFB", "#17cdff", "#ffd60a"


def _frame(video, t, dst):
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-ss", f"{max(0.0, t):.2f}",
                    "-i", video, "-frames:v", "1", "-vf", f"scale={SAMPLE_W}:-2",
                    dst], check=True)
    return dst


def grid(video, t):
    """Per-cell (mean luma 0-255, detail 0-255) for the frame at time t."""
    from PIL import Image, ImageFilter
    with tempfile.TemporaryDirectory() as td:
        im = Image.open(_frame(video, t, os.path.join(td, "f.png"))).convert("L")
        edges = im.filter(ImageFilter.FIND_EDGES)
        iw, ih = im.size
        cw, ch = iw / COLS, ih / ROWS
        out = []
        for r in range(ROWS):
            row = []
            for c in range(COLS):
                box = (int(c * cw), int(r * ch), int((c + 1) * cw), int((r + 1) * ch))
                tile, etile = im.crop(box), edges.crop(box)
                n = max(1, tile.width * tile.height)
                luma = sum(tile.getdata()) / n
                detail = sum(etile.getdata()) / n
                row.append((luma, detail))
            out.append(row)
        return out


def occupied_bands(plan, t):
    """Y ranges already carrying text at time t, so we never stack on them.

    Delegates to the single layout authority (layout.occupied) so this is the
    SAME schedule finalize burns and overlays draws -- no longer a hand-copied
    mirror of finalize's layout that went stale on split cuts (the 2026-08-16
    collision root cause). Template comes from the cut's composition.
    """
    from . import layout
    tmpl = (plan.get("composition") or {}).get("cam")
    return layout.occupied(plan, tmpl, t)


def content_band(g, floor=3.0):
    """Rows where the picture actually lives, in pixels.

    A reframed 16:9 source sits in a band with near-black blurred bars above
    and below it. Those bars score as the calmest region on every frame, so an
    unconstrained search parks every mark in the dead bar and never uses the
    picture. Find the band and place inside it.
    """
    rows = [r for r in range(ROWS)
            if sum(d for _, d in g[r]) / COLS > floor]
    if len(rows) < 3:
        return SAFE["top"], SAFE["bottom"]          # flat frame: whole safe area
    ch = H / ROWS
    return int(rows[0] * ch), int((rows[-1] + 1) * ch)


def best_row(g, box_w, box_h, bands, band):
    """Calmest vertical position for a CENTRED box: search y only.

    A wide centred card has no x to choose, and searching for one is worse than
    pointless: the x-legality test rejects every grid cell for a box wider than
    the safe span, `best_box` falls through to its corner fallback, and the
    fallback ignores the zone it was asked to respect. That is how a closing
    card landed on top of a caption.
    """
    ch = H / ROWS
    x = int((W - box_w) / 2)
    need_r = max(1, round(box_h / ch))
    best, best_score = None, None
    for r in range(ROWS - need_r + 1):
        y = r * ch
        if y < band[0] or y + box_h > band[1]:
            continue
        cells = [c for rr in range(r, r + need_r) for c in g[rr]]
        score = sum(d for _, d in cells) / len(cells)
        for b0, b1 in bands:
            if y < b1 and y + box_h > b0:
                score += 400
        if best_score is None or score < best_score:
            best, best_score = int(y), score
    if best is None:                       # zone genuinely too short: sit at its floor
        return x, max(SAFE["top"], int(band[1] - box_h)), 999.0
    return x, best, best_score


def best_box(g, box_w, box_h, bands, avoid=(), band=None):
    """Calmest free rectangle for a box of this size, in pixels.

    Score is mean detail plus a penalty for brushing an occupied band or a
    box already placed on this frame. Ties break toward the lower third,
    which is where a viewer's eye is least likely to be during a reveal.
    """
    cw, ch = W / COLS, H / ROWS
    need_c, need_r = max(1, round(box_w / cw)), max(1, round(box_h / ch))
    best, best_score = None, None
    for r in range(ROWS - need_r + 1):
        for c in range(COLS - need_c + 1):
            x, y = c * cw, r * ch
            if x < SAFE["left"] or x + box_w > SAFE["right"] + 140:
                continue
            if y < SAFE["top"] or y + box_h > SAFE["bottom"]:
                continue
            if band and (y < band[0] or y + box_h > band[1]):
                continue        # keep marks on the picture, not the letterbox
            cells = [g[rr][cc] for rr in range(r, r + need_r)
                     for cc in range(c, c + need_c)]
            detail = sum(d for _, d in cells) / len(cells)
            score = detail
            for b0, b1 in bands:                       # overlapping existing text
                if y < b1 and y + box_h > b0:
                    score += 400
            for ax, ay, aw, ah in avoid:               # overlapping a sibling mark
                if x < ax + aw and x + box_w > ax and y < ay + ah and y + box_h > ay:
                    score += 400
            score -= (y / H) * 12                      # mild preference for lower
            if best_score is None or score < best_score:
                best, best_score = (int(x), int(y)), score
    if best is None:                                   # nothing legal: fall back low-left
        return SAFE["left"], SAFE["bottom"] - box_h, 999.0
    return best[0], best[1], best_score


def backdrop(g, x, y, box_w, box_h):
    """(mean luma, mean detail) behind a box, for colour and scrim decisions."""
    cw, ch = W / COLS, H / ROWS
    c0, r0 = int(x / cw), int(y / ch)
    c1, r1 = min(COLS, int((x + box_w) / cw) + 1), min(ROWS, int((y + box_h) / ch) + 1)
    cells = [g[r][c] for r in range(r0, max(r0 + 1, r1))
             for c in range(c0, max(c0 + 1, c1))]
    if not cells:
        return 128.0, 40.0
    return (sum(l for l, _ in cells) / len(cells),
            sum(d for _, d in cells) / len(cells))


def style_for(luma, detail, register):
    """Colour, stroke weight and scrim, measured against the backdrop."""
    dark = luma < 118
    busy = detail > 26
    if register == "meme":
        # Marker yellow is the brand's, but it dies on a bright or sandy frame.
        color = MARKER if dark else "#d12d20"
        stroke = 7 if busy else 5
        scrim = 0.0 if (dark and not busy) else (0.42 if busy else 0.28)
    else:
        color = PAPER if dark else INK
        stroke = 0
        scrim = 0.55 if (busy or not dark) else 0.38
    return {"color": color, "stroke": stroke, "scrim": round(scrim, 2),
            "backdrop_luma": round(luma), "backdrop_detail": round(detail)}


# Rough advance widths per font at size 1, measured from rendered samples.
ADV = {"marker": 0.52, "plex": 0.55}


def fit(text, max_w, font="marker", target_w=0.74, cap=104, floor=34):
    """Font size and wrap width so the line fills its share of the frame.

    Sizes to `target_w` of the frame first, then shrinks only if the room that
    actually exists is smaller. Long text wraps rather than shrinking to
    illegibility.
    """
    want = int((W * target_w) / max(1, len(text)) / ADV[font])
    size = max(floor, min(cap, want))
    while size > floor and len(text) * ADV[font] * size > max_w * 2.0:
        size -= 2
    lines = max(1, round(len(text) * ADV[font] * size / max(1, max_w)))
    return size, lines


CARD_W = 900             # closing card width; must fit 1080 with margin to spare
CARD_TEXT = 52


def plan_endcard(video, t, plan, aspect, text="", height_frac=0.058,
                 card_w=CARD_W):
    """Placement and geometry for the stacked closing card (logo over the ask).

    The card is a fixed width that fits the frame with margin, and its height is
    computed from the logo and however many lines the ask wraps to. Laying the
    logo and a sentence out in a row came to ~1490px on a 1080px frame, so a
    centred card was clipped at both ends: in a vertical format the closing card
    stacks, and its width is a ceiling rather than a hope.
    """
    g = grid(video, t)
    inner = card_w - 80
    logo_h = int(H * height_frac)
    if logo_h * aspect > inner:                    # wide wordmark: fit the width
        logo_h = int(inner / max(0.1, aspect))
    lines = max(1, int(len(text) * ADV["plex"] * CARD_TEXT / inner) + 1) if text else 0
    box_h = int(logo_h * 1.48 + lines * CARD_TEXT * 1.24 + (logo_h * 0.26 if lines else 0))
    b0, b1 = content_band(g)
    b0, b1 = b0 + 40, b1 - 40
    zone0, zone1 = max(b0, int(H * 0.50)), min(b1, SAFE["bottom"])
    if plan.get("captions") != "none":
        zone1 = min(zone1, CAPTION_BAND[0] - 24)
    if zone1 - zone0 < box_h:
        zone0 = max(b0, zone1 - box_h - 8)
    x, y, score = best_row(g, card_w, box_h, occupied_bands(plan, t), (zone0, zone1))
    luma, detail = backdrop(g, x, y, card_w, box_h)
    st = style_for(luma, detail, "pro")
    return {"x": x, "y": y, "w": card_w, "h": logo_h, "box_h": box_h,
            "size": CARD_TEXT, "lines": lines, "calm_score": round(score, 1), **st}


def _plan_endcard_row(video, t, plan, aspect, height_frac=0.075):
    """Placement for the single closing card (logo + the ask).

    A CTA is not placed wherever the frame happens to be calmest: short-form
    convention puts the ask in the lower third, above the platform's own UI, so
    a thumb is already near it and it never fights the caption line. This picks
    the calmest band INSIDE that zone rather than across the whole frame, and
    keeps the card fully on the picture so it never straddles the letterbox
    seam.
    """
    g = grid(video, t)
    h = int(H * height_frac)
    box_h, box_w = int(h * 1.5), min(SAFE["right"] + 140 - SAFE["left"],
                                     int(h * aspect) + int(h * 0.7))
    b0, b1 = content_band(g)
    b0, b1 = b0 + 40, b1 - 40                      # inset: never on the seam
    zone0 = max(b0, int(H * 0.52))                 # lower half, above the chrome
    zone1 = min(b1, SAFE["bottom"])
    # A captioned cut already owns the caption band; the card sits ABOVE it with
    # a real gap. Sharing that space is how the card landed on top of a cue.
    if plan.get("captions") != "none":
        zone1 = min(zone1, CAPTION_BAND[0] - 24)
    if zone1 - zone0 < box_h:                      # not enough room: sit as low as fits
        zone0 = max(b0, zone1 - box_h - 8)
    # RULE, not a measurement: the closing card is centred. x is the one
    # coordinate we do not search for, because an off-centre brand card reads as
    # an accident while a centred one reads as a designed end frame. Only the
    # row is chosen, and only inside the zone.
    x, y, score = best_row(g, box_w, box_h, occupied_bands(plan, t), (zone0, zone1))
    luma, detail = backdrop(g, x, y, box_w, box_h)
    st = style_for(luma, detail, "pro")
    return {"x": x, "y": y, "w": box_w, "h": h, "box_h": box_h,
            "calm_score": round(score, 1), **st}


def plan_image(video, t, plan, aspect, height_frac=0.062, avoid=()):
    """Placement for a fixed-aspect asset (a logo badge), sized off the frame.

    Height is a share of frame height rather than a pixel constant so the mark
    reads the same on every cut, and the box is measured from the asset's own
    aspect instead of guessed.
    """
    g = grid(video, t)
    h = int(H * height_frac)
    box_h = int(h * 1.44)                      # logo plus its padding
    box_w = int(h * aspect) + int(h * 0.68)
    box_w = min(box_w, SAFE["right"] + 140 - SAFE["left"])
    band = content_band(g)
    x, y, score = best_box(g, box_w, box_h, occupied_bands(plan, t), avoid, band)
    luma, detail = backdrop(g, x, y, box_w, box_h)
    st = style_for(luma, detail, "pro")
    return {"x": x, "y": y, "w": box_w, "h": h, "box_h": box_h,
            "calm_score": round(score, 1), **st}


def plan_mark(video, t, plan, text, register, avoid=(), zone=None):
    """Full placement decision for one mark at one moment.

    `zone` optionally clamps the search band inside the content band: a hook
    belongs in the upper frame even when the calmest pixels are at the bottom,
    so the caller states the zone and the search stays honest within it.
    """
    g = grid(video, t)
    font = "marker" if register == "meme" else "plex"
    room_w = SAFE["right"] + 140 - SAFE["left"]
    size, lines = fit(text, room_w, font)
    box_w = min(room_w, int(len(text) * ADV[font] * size / max(1, lines)) + 60)
    box_h = int(size * 1.35 * lines) + (34 if register == "pro" else 18)
    band = content_band(g)
    if zone:
        band = (max(band[0], zone[0]), min(band[1], zone[1]))
        if band[1] - band[0] < box_h:              # zone too short: keep content band
            band = content_band(g)
    x, y, score = best_box(g, box_w, box_h, occupied_bands(plan, t), avoid,
                           band)
    luma, detail = backdrop(g, x, y, box_w, box_h)
    st = style_for(luma, detail, register)
    return {"x": x, "y": y, "w": box_w, "h": box_h, "size": size, "lines": lines,
            "calm_score": round(score, 1), **st}
