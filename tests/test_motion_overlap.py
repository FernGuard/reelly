"""Motion placement no-overlap: centred marks (hook / payoff / CTA / corner mark)
must clear each other's whole Y-band + scrim, not just the tight text box.

Regression for two shipped collisions: the yellow CTA
printed on the payoff type, and a corner mark sat under the centred hook. Both came
from avoiding a neighbour's tight box while every mark renders with a scrim pad,
and from the motion pipeline not modelling centred marks as full-width Y-bands
the way layout.occupied does.
"""
from reelly import motion


def _cta_box(y):
    # the CTA is centred; its ranking box in _events
    return (190, y, 700, 180)


def test_full_width_band_spans_rows_and_inflates_by_pad():
    band = motion._full_width_band((60, 220, 200, 112), pad=40)
    x, y, w, h = band
    assert x == 0 and w == motion.FRAME_W, "band spans the full width"
    assert y == 180, "top = box top - pad"
    assert y + h == 220 + 112 + 40, "bottom = box bottom + pad"


def test_none_box_yields_no_band():
    assert motion._full_width_band(None, pad=40) is None


def test_cta_inside_payoff_band_is_caught():
    """A CTA row overlapping the payoff's Y-band registers overlap, so the ranker
    rejects it -- even though its centred x differs from the payoff's box x."""
    pay_box = (100, 1300, 880, 200)                 # payoff text box, off-centre x
    band = motion._full_width_band(pay_box, motion.SCRIM_PAD + motion.CTA_SCRIM_PAD)
    assert motion._overlap_fraction(_cta_box(1320), [band]) > 0


def test_cta_clear_of_payoff_band_passes():
    pay_box = (100, 1300, 880, 200)
    band = motion._full_width_band(pay_box, motion.SCRIM_PAD + motion.CTA_SCRIM_PAD)
    # a row well above the band (and its scrim) is fully clear
    assert motion._overlap_fraction(_cta_box(900), [band]) == 0


def test_hook_band_forces_clearance_below_the_corner_mark():
    """The top-left product corner mark becomes a full-width band; a top hook row overlaps it
    (so placement is pushed down), a lower row clears it."""
    bug_box = (60, 220, 200, 112)                   # top-left corner bug
    band = motion._full_width_band(bug_box, motion.SCRIM_PAD)
    hook_top = (80, 230, 920, 260)                  # centred hook sitting at top
    hook_low = (80, 700, 920, 260)
    assert motion._overlap_fraction(hook_top, [band]) > 0
    assert motion._overlap_fraction(hook_low, [band]) == 0
