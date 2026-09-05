"""C11 retake dedupe: keep the last take, drop earlier attempts."""
from reelly.direct import _retake_drops, _subtract_regions


def S(s, e, text):
    return {"s": s, "e": e, "text": text}


def test_repeated_line_drops_earlier_take():
    sents = [
        S(0, 4, "The last layer is a weird one and people forget it."),
        S(6, 10, "The last layer is a weird one and it makes the others work."),
        S(12, 16, "Something completely different happens here."),
    ]
    drops = _retake_drops(sents)
    assert drops == [[0, 4]]


def test_last_take_of_a_chain_survives():
    line = "You can see a little swell on the propulsion %s."
    sents = [S(i * 6, i * 6 + 4, line % w) for i, w in enumerate(["engines", "jets", "engine"])]
    drops = _retake_drops(sents)
    assert [d[0] for d in drops] == [0, 6]  # takes 1 and 2 drop, take 3 stays


def test_negation_contrast_is_not_a_retake():
    sents = [
        S(0, 3, "Hey this is what I like about the game."),
        S(4, 7, "This is what I don't like about the game."),
    ]
    assert _retake_drops(sents) == []


def test_left_right_comparison_is_not_a_retake():
    sents = [
        S(0, 4, "When a drone is hit on the left there is a yellow particle."),
        S(5, 9, "When a drone is hit there is a particle with many layers."),
    ]
    assert _retake_drops(sents) == []


def test_false_start_prefix_drops():
    sents = [
        S(0, 2, "But on the right,"),
        S(3, 8, "But on the right when the drone is hit there is a particle."),
    ]
    assert _retake_drops(sents) == [[0, 2]]


def test_mid_sentence_self_restart():
    sents = [
        S(0, 10, "but on the right when the drone but on the right when the drone is hit there is a particle"),
    ]
    drops = _retake_drops(sents)
    assert len(drops) == 1
    s, e = drops[0]
    assert s == 0 and 3 < e < 7  # cut ends near the restart point


def test_unrelated_far_apart_lines_survive():
    sents = [
        S(0, 4, "Let us go through it okay?"),
        S(50, 54, "Let us go through them okay?"),  # beyond the retake window
    ]
    assert _retake_drops(sents) == []


def test_subtract_regions():
    keep = [[0, 100]]
    out = _subtract_regions(keep, [[10, 20], [50, 60]])
    assert out == [[0, 10], [20, 50], [60, 100]]
    # slivers below min_keep vanish
    out = _subtract_regions([[0, 10]], [[0.1, 9.8]])
    assert out == []
