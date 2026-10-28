"""Single layout authority for on-screen text (2026-08-16, reviewer).

Root cause it replaces: text was placed by TWO uncoordinated systems --
finalize._burn_events (hook / karaoke / game name) and overlays.autoplan (the
yellow MEME callouts + brand), the only shared model being
placement.occupied_bands, a hand-copied MIRROR of finalize's OLD full-frame
layout. It was stale (didn't track finalize's split positions), layout-blind
(never read the composition, so it reserved full-frame bands on split cuts) and
ignorant of the game name. So the gfx layer "avoided" empty bands and its
callouts landed on the hook, the game name and the karaoke.

This module is the ONE place that decides where text goes. Both the burn layer
(finalize) and the gfx layer (overlays) call it, so they agree by construction.
It is:

- a per-TEMPLATE registry: each layout Reelly builds (split today; corner and
  full-frame reframe slot in next) contributes its own face/footage/text
  geometry. `SCOPE 2026-08-16: split only; the registry is the seam for the
  rest.`
- a SOCIAL-PLATFORM template: fixed safe margins (handle/status top, action
  rail right, caption/nav bottom) that hold on TikTok / Reels / Shorts;
- GEOMETRY-AWARE: text zones come from the cut's composition so nothing lands
  on the face or the footage -- split text uses the CENTER seam and the BOTTOM
  band; letterboxed templates prefer their bars;
- a SCHEDULER ("keep all, stagger in time"): every element gets a reserved
  (zone, y, time-window); when two want one zone at once they stagger so only
  one is in a zone at a time -- collisions become structurally impossible;
- DETERMINISTIC: pure arithmetic, no ffmpeg frame sampling and no per-cell
  image analysis, so placement is effectively free.
"""

FRAME_W, FRAME_H = 1080, 1920

# ONE layout that holds on TikTok, Reels AND Shorts (owner decision
# 2026-10-28): the platform chrome is modelled as REGIONS, the union of all
# three, and no text of ours may enter any of them. Practitioner safe-zone
# guides, not official specs (none exist for organic posts); check a real
# phone before publishing. Per platform (1080x1920):
#   header   TikTok ~130-220, Reels ~270, Shorts ~288 tall
#   bottom   TikTok from ~1440, Reels ~1250, Shorts ~1248 (caption, handle,
#            sound, nav)
#   rail     TikTok/Reels ~120-140 wide, Shorts ~192; the like/comment/share
#            column lives in the LOWER part of the screen, not the top band
UI_ZONES = {
    "header": (0, 0, 1080, 288),
    "bottom_ui": (0, 1250, 1080, 1920),
    "right_rail": (888, 800, 1080, 1920),
    "left_edge": (0, 0, 60, 1920),
}
SAFE_TOP = 288        # below the tallest header (Shorts)
SAFE_BOTTOM = 1250    # above the tallest caption/nav block (Reels, Shorts)
SAFE_RIGHT = 940      # right edge above the rail (top half)
SAFE_LEFT = 60
RAIL_X, RAIL_Y = 888, 800   # the action rail: x >= RAIL_X from y >= RAIL_Y


def safe_span(y0, y1):
    """(x0, x1) free of platform chrome for a band of rows y0..y1."""
    right = RAIL_X if y1 > RAIL_Y else SAFE_RIGHT
    return SAFE_LEFT, right


def centered_width(y0, y1):
    """Widest box CENTRED on the frame that stays inside safe_span: text
    layers are centred, so the narrower side sets the width."""
    x0, x1 = safe_span(y0, y1)
    mid = FRAME_W // 2
    return 2 * min(mid - x0, x1 - mid)


def ui_hits(x0, y0, x1, y1):
    """Names of the UI_ZONES a box overlaps (empty = safe)."""
    return [name for name, (a, b, c, d) in UI_ZONES.items()
            if x0 < c and x1 > a and y0 < d and y1 > b]

GAME_LABEL_H = 64     # the small persistent game-name strip
LINE_H = 130          # rough two-line text-block height, for band reservation

# Full-frame / fallback text bands: a reframed 16:9 picture sits mid-frame with
# near-black bars above and below. Text lives in the bars / thirds. The hook
# band starts below the tallest platform header; the game-name strip sits
# under it, still in the top bar of a 1.5x reframe (picture from y 504). The
# bottom band ends above the tallest caption/nav block, so on a reframed
# 16:9 it overlaps the picture's lower edge: captioned cuts pay that price to
# stay readable on every platform.
FULL_CENTER = (SAFE_TOP + 2, 430)
FULL_BOTTOM = (1110, SAFE_BOTTOM)


def _split_geometry(comp):
    """(cam_h, screen_y, screen_h) for a split cut, per preview._split_filter."""
    ch = int((comp or {}).get("cam_h", 768))
    act = (comp or {}).get("screen_crop")
    out_h = (int(1080 * act[3] / act[2]) // 2 * 2) if act else 608
    scr_y = ch + max(0, (FRAME_H - ch - out_h) // 2)
    return ch, scr_y, out_h


def _zones_split(comp):
    """Split: facecam top (face), screen mid (footage). Text in the seam gap
    (center) and the band below the screen (bottom); the game name gets its own
    strip at the bottom of the seam so it never shares a line with the hook."""
    ch, scr_y, out_h = _split_geometry(comp)
    gl0 = max(ch + 8, scr_y - GAME_LABEL_H)
    below0 = scr_y + out_h
    # Karaoke goes in the neutral band BELOW the screen (off the footage), and
    # that band must end above the platform caption/nav block (SAFE_BOTTOM).
    # With no room there (bottom=None) the karaoke falls back to the thirds.
    BOTTOM_FLOOR = SAFE_BOTTOM
    bottom = ((below0 + 8, BOTTOM_FLOOR)
              if (BOTTOM_FLOOR - below0) >= 110 else None)
    return {"center": (ch + 8, gl0 - 8),
            "game_label": (gl0, scr_y - 8),
            "bottom": bottom}


def _zones_full(comp):
    """No-facecam / fallback: letterbox bars (preferred) as text zones."""
    return {"center": FULL_CENTER,
            "game_label": (FULL_CENTER[1], FULL_CENTER[1] + GAME_LABEL_H),
            "bottom": FULL_BOTTOM}


# Template registry. Add "corner"/"full" builders here next; unknown templates
# fall back to the letterbox model rather than crashing.
TEMPLATES = {"split": _zones_split}


def zones(comp, template):
    return TEMPLATES.get(template or "", _zones_full)(comp)


def _y_top(band, pad=6):
    return int(band[0] + pad)


def _center_busy(plan, show_s, gap=0.3):
    """Time windows the CENTER slot is already occupied by finalize's burn: the
    hook, then every timed overlay line. The meme scheduler staggers around
    these so a callout never lands on a hook or a teaser line."""
    busy = []
    if (plan.get("hook") or {}).get("text"):
        busy.append([0.0, show_s + gap])
    for o in (plan.get("overlay_lines") or []):
        t0 = float(o.get("t", 0))
        busy.append([t0 - gap, t0 + float(o.get("show_s", 3.0)) + gap])
    return sorted(busy)


def _first_free(intervals, start, length, limit, gap=0.3):
    """Earliest t0 >= start with [t0, t0+length] clear of every interval and
    finishing by `limit`; None when no free gap fits (caller drops the element)."""
    ivs = sorted(intervals)
    t = start
    moved = True
    while moved:
        moved = False
        for a, b in ivs:
            if t < b and t + length > a:      # overlap -> jump past this one
                t = b + gap
                moved = True
        if t + length > limit:
            return None
    return t


def occupied(plan, template, t, meme_windows=None):
    """Y-bands (y0, y1) carrying text at time t -- the ONE avoid-model every
    placer consults (replaces placement.occupied_bands). Derived from the same
    schedule finalize burns and overlays draws, so it is never stale."""
    sched = plan_text(plan, template, meme_windows=meme_windows)
    bands = []
    for kind, slot in sched.items():
        wins = slot.get("windows")
        if wins is None:                      # karaoke: active whenever captions run
            active = True
        else:
            active = any(w[0] - 0.3 <= t <= w[1] + 0.3 for w in wins)
        if active:
            h = GAME_LABEL_H if kind == "game_name" else LINE_H
            bands.append((slot["y"] - 8, slot["y"] + h))
    # Burned-in text detected on the SOURCE still (motion pre-flight) is static:
    # it is on screen the whole cut, so it is always-active occupancy every
    # placer must route around -- the reason pre-flight no longer blanket-rejects
    # a source that carries text (reviewer 2026-08-18).
    for b in plan.get("source_text_bands") or []:
        if isinstance(b, (list, tuple)) and len(b) == 2 and b[1] > b[0]:
            bands.append((int(b[0]), int(b[1])))
    return bands


def plan_text(plan, template, meme_windows=None):
    """Assign every text element a slot: {kind: {"y": int, "windows": [[t0,t1]]|None}}.

    Kinds: hook, game_name, karaoke, meme, endtag. Both finalize (hook,
    game_name, karaoke, endtag) and overlays (meme) read the SAME result, so
    the two layers cannot disagree.

    Staggering: the CENTER slot is time-shared. The hook owns it for
    [0, show_s]. Requested meme windows that intersect the hook (or each other)
    are pushed later so only one thing is in center at a time. The game name
    has its own strip; the karaoke owns the bottom slot; neither collides with
    center. windows=None on karaoke means "whenever captions play" (per-word
    timing stays with the caller).
    """
    z = zones(plan.get("composition"), template)
    dur = float(plan.get("duration_s", 0) or 0)
    hook = plan.get("hook") or {}
    show_s = float(hook.get("show_s", 3.6) or 3.6)
    bottom = z["bottom"] or FULL_BOTTOM
    out = {}

    if str(plan.get("game_name") or "").strip():
        out["game_name"] = {"y": _y_top(z["game_label"]), "windows": [[0.0, dur]]}
    if hook.get("text"):
        out["hook"] = {"y": _y_top(z["center"]), "windows": [[0.0, show_s]]}
    if plan.get("captions") != "none":
        out["karaoke"] = {"y": _y_top(bottom), "windows": None}

    if meme_windows:
        # The CENTER slot is shared by the hook AND the timed overlay lines
        # (finalize burns both there). Stagger each meme callout into the first
        # free gap so it never lands on a hook or a teaser line -- that overlap
        # was the residual collision.
        # Callouts live in CONTENT time only, never over the appended outro
        # card; if there is no free content gap, the callout is DROPPED rather
        # than stacked.
        outro = plan.get("outro") or {}
        content_end = dur - float(outro.get("len_s", 0) or 0) if outro else dur
        busy = _center_busy(plan, show_s)
        placed = []
        for w in meme_windows:
            length = max(0.8, float(w[1]) - float(w[0]))
            t0 = _first_free(busy + placed, max(0.0, float(w[0])), length, content_end)
            if t0 is None:
                continue
            placed.append([round(t0, 2), round(min(content_end, t0 + length), 2)])
        if placed:
            out["meme"] = {"y": _y_top(z["center"]), "windows": placed}

    out["endtag"] = {"y": _y_top(bottom), "windows": [[max(0.0, dur - 2.2), dur]]}
    return out
