"""Synthetic tests for the plain-language gate."""
from reelly import plain


def test_catalog_row_is_caught():
    hits = plain.find("Everyone who publishes lands in the same catalog row.")
    assert hits and any("catalog row" in t for t, _ in hits)


def test_the_plain_language_replacement_passes():
    assert plain.find("The video appears beside the other entries.") == []


def test_configured_retired_product_name_is_caught(monkeypatch):
    monkeypatch.setattr(plain, "RETIRED", {"oldbrand": "CurrentBrand"})
    assert plain.find("Built in OldBrand")


def test_current_studio_names_pass():
    for name in ("Video Project", "Story Project", "Game Project", "Adventure Project"):
        assert plain.find(f"Built in {name}") == []


def test_phase_labels_are_caught():
    assert plain.find("P2 opens today")


def test_ordinary_words_containing_jargon_are_not_flagged():
    """'row' inside 'grow' or 'browse' is not jargon. A checker that cries wolf
    gets switched off."""
    assert plain.find("watch it grow while you browse") == []
    assert plain.find("a narrow arrow, thrown") == []


def test_verdict_reads_every_field_a_viewer_sees():
    v = plain.verdict({"hook": "fine", "cta": "join the cohort",
                       "caption": "fine", "overlay_lines": []})
    assert v[1] == "FAIL" and "cta" in v[2]


def test_verdict_covers_the_caption_too():
    """Caption jargon survives longest: it never goes through a render that
    anyone watches."""
    v = plain.verdict({"hook": "fine", "cta": "fine",
                       "caption": "lands in the catalog row", "overlay_lines": []})
    assert v[1] == "FAIL" and "caption" in v[2]


def test_verdict_covers_overlay_lines_with_their_timestamps():
    v = plain.verdict({"hook": "fine", "cta": "fine", "caption": "fine",
                       "overlay_lines": [{"t": 6.0, "text": "the tentpole drops"}]})
    assert v[1] == "FAIL" and "overlay@6.0s" in v[2]


def test_clean_plan_passes():
    v = plain.verdict({"hook": "Something is under the shutter", "cta": "make yours now",
                       "caption": "A noir world built in Adventure Project.",
                       "overlay_lines": [{"t": 6.0, "text": "Twelve years on the force"}]})
    assert v[1] == "PASS"


def test_empty_fields_do_not_crash():
    assert plain.verdict({}) [1] == "PASS"
    assert plain.find(None) == []


def test_cold_audience_words_are_caught():
    """Not jargon, but wrong: 'strangers' frames a creator's audience as unknown
    people rather than as the right ones. The brand's word is folk."""
    assert plain.find("Then strangers can play it")
    assert plain.find("find your tribe")
    assert plain.find("Then it finds its folk") == []
