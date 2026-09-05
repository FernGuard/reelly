"""Model-swappable multi-reference video requests.

`_video_request` takes N references (@Image1..@ImageN), caps each at the model's
own `max_refs` (NOT a blanket 3), and puts them under the model's reference key.
Swapping models is a different `model` string and nothing else. Regression for
the winner-flythrough enablement (reviewer 2026-08-19): seedance takes more than
3 refs; H3/MiniMax is available for these; swapping must be trivial.
"""
from reelly import motion


def test_seedance_takes_multiple_refs_under_image_urls():
    refs = [f"data:img{i}" for i in range(6)]
    ep, payload, est = motion._video_request("seedance", refs, "fly", 8)
    assert ep == motion.VIDEO_ENDPOINT
    assert payload["image_urls"] == refs          # all six, not capped to 3
    assert payload["aspect_ratio"] == "9:16"
    assert "image_url" not in payload


def test_refs_are_capped_at_the_model_max_not_three():
    refs = [f"data:img{i}" for i in range(9)]
    _, payload, _ = motion._video_request("seedance", refs, "fly", 8)
    assert len(payload["image_urls"]) == motion.VIDEO_MODELS["seedance"]["max_refs"]
    assert motion.VIDEO_MODELS["seedance"]["max_refs"] > 3


def test_minimax_h3_is_reference_to_video_multi_ref():
    # H3 reference-to-video's schema takes a `reference_image_urls` ARRAY (up to
    # 9) + `aspect_ratio` (native portrait, no letterbox) + an integer duration.
    # This replaced the old single-frame image-to-video entry, which could not
    # ingest @Image2..N. All refs must ride as a list under reference_image_urls.
    refs = ["data:a", "data:b", "data:c", "data:d"]
    ep, payload, _ = motion._video_request("minimax", refs, "fly", 6)
    assert ep == "minimax/h3/reference-to-video"
    assert payload["reference_image_urls"] == refs   # full list, not one frame
    assert "image_url" not in payload and "image_urls" not in payload
    assert motion.VIDEO_MODELS["minimax"]["max_refs"] == 9
    assert payload["aspect_ratio"] == "9:16"         # native portrait
    # live endpoint takes prompt_expansion_mode (enum), NOT an enable_* boolean;
    # "disabled" keeps our pinned beats (default "balanced" would rewrite them).
    assert payload["prompt_expansion_mode"] == "disabled"
    assert "enable_prompt_expansion" not in payload
    assert isinstance(payload["duration"], int)      # H3 wants an int length


def test_rescale_shots_hits_the_target_total():
    # --seconds forces the total; shots keep their count and sum to exactly it.
    out = motion._rescale_shots([{"seconds": 4}, {"seconds": 4}], 15)
    assert sum(s["seconds"] for s in out) == 15
    assert len(out) == 2
    assert all(s["seconds"] >= 1 for s in out)


def test_rescale_shots_single_shot_and_empty():
    assert sum(s["seconds"] for s in motion._rescale_shots([{"seconds": 4}], 15)) == 15
    # empty/None plan still produces a single shot of the full length
    assert sum(s["seconds"] for s in motion._rescale_shots(None, 15)) == 15


def test_grok_is_single_frame_image_to_video():
    refs = ["data:a", "data:b", "data:c"]
    ep, payload, _ = motion._video_request("grok", refs, "fly", 6)
    assert payload["image_url"] == "data:a"       # single frame, first ref
    assert "image_urls" not in payload


def test_h3max_is_the_default_single_frame_fast_path():
    # h3max = H3 Max image-to-video: ONE keyframe (image_url), 768P, no aspect_ratio
    # (follows the source), no loras, no audio refs. It is the DEFAULT engine.
    import inspect
    assert "h3max" in motion.VIDEO_MODELS and motion.VIDEO_MODELS["h3max"]["max_refs"] == 1
    assert inspect.signature(motion.run).parameters["video_model"].default == "h3max"
    ep, payload, _ = motion._video_request("h3max", ["data:a", "data:b"], "epic fight", 15)
    assert ep == "minimax/h3-max/image-to-video"
    assert payload["image_url"] == "data:a"       # single first frame
    assert "image_urls" not in payload and "reference_image_urls" not in payload
    assert payload["resolution"] == "768P"
    assert payload["prompt_expansion_mode"] == "disabled"
    assert "aspect_ratio" not in payload and "loras" not in payload
    assert isinstance(payload["duration"], int)


def test_a_single_string_ref_still_works():
    ep, payload, _ = motion._video_request("seedance", "data:only", "x", 8)
    assert payload["image_urls"] == ["data:only"]


def test_hero_is_single_frame_regardless_of_refs():
    ep, payload, _ = motion._video_request("seedance", ["data:a", "data:b"], "x", 8, hero=True)
    assert ep == motion.VIDEO_ENDPOINT_HERO
    assert payload["image_url"] == "data:a"


def test_unknown_model_falls_back_to_seedance():
    _, payload, _ = motion._video_request("nope", ["data:a"], "x", 8)
    assert "image_urls" in payload


def test_empty_refs_is_an_error():
    import pytest
    with pytest.raises(ValueError):
        motion._video_request("seedance", [], "x", 8)
