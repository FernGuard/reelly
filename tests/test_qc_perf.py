"""QC performance work: single-decode judge analysis, batched frame
extraction, and disk caches for activity/grade. Each test names the
regression it protects against."""
import json
import os
import subprocess

from reelly import activity, config, faceio, grade, judge, visual_qc


def _make_clip(path, dur=3.0, size="320x240"):
    """Tiny test clip: testsrc2 video + 440Hz sine audio (same recipe as
    test_upgrades)."""
    subprocess.run(
        [config.FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", path], check=True)
    return path


# --- combined stderr parsing ------------------------------------------------

EBUR_BLOCK = """[Parsed_ebur128_1 @ 0x600002] Summary:

  Integrated loudness:
    I:         -14.2 LUFS
    Threshold: -24.6 LUFS

  Loudness range:
    LRA:         3.1 LU
    Threshold:  -34.6 LUFS
    LRA low:   -16.0 LUFS
    LRA high:  -12.9 LUFS

  True peak:
    Peak:       -1.3 dBFS
"""

BLACK_LINES = ("[blackdetect @ 0x7f8] black_start:1.2 black_end:1.8 "
               "black_duration:0.6\n"
               "[blackdetect @ 0x7f8] black_start:3.45 black_end:3.9 "
               "black_duration:0.45\n")

FREEZE_LINES = ("[freezedetect @ 0x7f9] lavfi.freezedetect.freeze_start: "
                "2.5\n"
                "[freezedetect @ 0x7f9] lavfi.freezedetect.freeze_duration: "
                "6.0\n")

# what a combined single-decode run looks like: per-frame filter lines
# interleaved during decode, ebur128 summary block contiguous at the end
COMBINED = ("frame=  42 fps=0.0 q=-0.0 size=N/A\n"
            + BLACK_LINES.splitlines()[0] + "\n"
            + FREEZE_LINES
            + BLACK_LINES.splitlines()[1] + "\n"
            + EBUR_BLOCK)


def test_combined_stderr_yields_same_metrics_as_separate_passes():
    """The whole consolidation is only legal if parsing the interleaved
    stderr gives byte-identical metric values."""
    assert judge._parse_loudness(COMBINED) == judge._parse_loudness(EBUR_BLOCK)
    assert judge._parse_loudness(COMBINED) == (-14.2, -1.3)
    assert judge._parse_black(COMBINED) == judge._parse_black(BLACK_LINES)
    assert judge._parse_black(COMBINED) == ["1.2", "3.45"]
    assert judge._parse_freeze(COMBINED) == judge._parse_freeze(FREEZE_LINES)
    assert judge._parse_freeze(COMBINED) == ["2.5"]


def test_parsers_survive_empty_and_garbage_stderr():
    assert judge._parse_loudness("") == (None, None)
    assert judge._parse_black("no filters ran") == []
    assert judge._parse_freeze("") == []


def test_combined_invocation_matches_separate_passes_on_real_clip(tmp_path):
    """The one-decode filter graph must produce the same parsed values as
    the three old single-filter decodes on this machine's ffmpeg build."""
    clip = _make_clip(str(tmp_path / "c.mp4"))
    err_c = judge._analysis_stderr(clip, True, True)
    err_e = judge._ffmpeg_stderr(["-i", clip, "-filter_complex",
                                  judge.EBUR128])
    err_b = judge._ffmpeg_stderr(["-i", clip, "-vf", judge.BLACKDETECT,
                                  "-an"])
    err_f = judge._ffmpeg_stderr(["-i", clip, "-vf", judge.FREEZEDETECT,
                                  "-an"])
    assert judge._parse_loudness(err_c) == judge._parse_loudness(err_e)
    assert judge._parse_black(err_c) == judge._parse_black(err_b)
    assert judge._parse_freeze(err_c) == judge._parse_freeze(err_f)


def test_check_file_uses_a_single_analysis_decode(tmp_path, monkeypatch):
    """check_file used to decode the file three times (ebur128, blackdetect,
    freezedetect); now it must be one ffmpeg invocation."""
    clip = _make_clip(str(tmp_path / "c.mp4"))
    calls = []
    real = judge._ffmpeg_stderr

    def counting(args):
        calls.append(args)
        return real(args)

    monkeypatch.setattr(judge, "_ffmpeg_stderr", counting)
    rep = judge.check_file(clip, expect_vertical=False)
    assert len(calls) == 1
    gates = dict((g, s) for g, s, _ in rep["results"])
    assert gates["streams"] == "PASS"
    assert gates["black_frames"] == "PASS"      # testsrc2 is never black
    assert gates["frozen_video"] == "PASS"      # and never frozen
    assert "loudness" in gates and "true_peak" in gates


# --- batched frame extraction -----------------------------------------------

def test_visual_qc_composite_extracts_frames_in_one_spawn(tmp_path,
                                                          monkeypatch):
    """The filmstrip used to cost N_FRAMES ffmpeg spawns per join."""
    clip = _make_clip(str(tmp_path / "c.mp4"), dur=4.0)
    spawns = []
    real = faceio._spawn

    def counting(cmd):
        spawns.append(cmd)
        return real(cmd)

    monkeypatch.setattr(faceio, "_spawn", counting)
    out = visual_qc.composite(clip, 2.0, str(tmp_path / "j.png"),
                              words=[{"t": "hi", "s": 1.0, "e": 1.3}],
                              title="c.mp4")
    assert out and os.path.getsize(out) > 10_000
    assert len(spawns) == 1


def test_activity_frames_batched_into_one_spawn(tmp_path, monkeypatch):
    """activity used to spawn ffmpeg + decode a PNG per sampled still."""
    clip = _make_clip(str(tmp_path / "c.mp4"), dur=4.0)
    spawns = []
    real = faceio._spawn

    def counting(cmd):
        spawns.append(cmd)
        return real(cmd)

    monkeypatch.setattr(faceio, "_spawn", counting)
    imgs = activity._frames(clip, [0.5, 1.0, 1.5, 2.0, 2.5, 3.0])
    assert len(imgs) == 6
    assert len(spawns) == 1


# --- activity cache -----------------------------------------------------------

def _fake_video(tmp_path, name="v.mp4", size=100):
    p = tmp_path / name
    p.write_bytes(b"0" * size)
    return str(p)


def test_active_box_cache_roundtrip_and_invalidation(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "CACHE_DIR", str(tmp_path / "cache"))
    vid = _fake_video(tmp_path)
    calls = []

    def fake(video, segments, src_wh, samples, pad, min_w_frac):
        calls.append(1)
        return (10, 20, 640, 682)

    monkeypatch.setattr(activity, "_active_box_uncached", fake)
    segs = [[0.0, 5.0], [8.0, 12.0]]
    b1 = activity.active_box(vid, segs, (1920, 1080))
    b2 = activity.active_box(vid, segs, (1920, 1080))
    assert b1 == b2 == (10, 20, 640, 682)
    assert len(calls) == 1                     # second call was a cache hit
    # different params -> different cache entry
    activity.active_box(vid, segs, (1920, 1080), samples=5)
    assert len(calls) == 2
    # file identity change (size) invalidates
    open(vid, "ab").write(b"x")
    activity.active_box(vid, segs, (1920, 1080))
    assert len(calls) == 3


def test_active_box_caches_none_result(tmp_path, monkeypatch):
    """'no signal' is a real answer and must be cached too."""
    monkeypatch.setattr(activity, "CACHE_DIR", str(tmp_path / "cache"))
    vid = _fake_video(tmp_path)
    calls = []

    def fake(video, segments, src_wh, samples, pad, min_w_frac):
        calls.append(1)
        return None

    monkeypatch.setattr(activity, "_active_box_uncached", fake)
    assert activity.active_box(vid, [[0, 2]], (1920, 1080)) is None
    assert activity.active_box(vid, [[0, 2]], (1920, 1080)) is None
    assert len(calls) == 1


def test_active_box_corrupt_cache_is_never_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(activity, "CACHE_DIR", str(tmp_path / "cache"))
    vid = _fake_video(tmp_path)
    calls = []

    def fake(video, segments, src_wh, samples, pad, min_w_frac):
        calls.append(1)
        return (0, 0, 100, 100)

    monkeypatch.setattr(activity, "_active_box_uncached", fake)
    activity.active_box(vid, [[0, 2]], (1920, 1080))
    for f in os.listdir(str(tmp_path / "cache")):
        with open(os.path.join(str(tmp_path / "cache"), f), "w") as fh:
            fh.write("{not json")
    assert activity.active_box(vid, [[0, 2]], (1920, 1080)) == (0, 0, 100, 100)
    assert len(calls) == 2


# --- grade cache ---------------------------------------------------------------

STATS = {"y_mean": 0.5, "y_range": 0.72, "sat_mean": 0.25}


def test_frame_stats_cache_roundtrip_and_invalidation(tmp_path, monkeypatch):
    monkeypatch.setattr(grade, "CACHE_DIR", str(tmp_path / "cache"))
    vid = _fake_video(tmp_path)
    calls = []

    def fake(video, start, duration, n_samples=10):
        calls.append(1)
        return dict(STATS)

    monkeypatch.setattr(grade, "_frame_stats_uncached", fake)
    s1 = grade._frame_stats(vid, 0.0, 10.0)
    s2 = grade._frame_stats(vid, 0.0, 10.0)
    assert s1 == s2 == STATS and len(calls) == 1
    # a different range is a different measurement
    grade._frame_stats(vid, 2.0, 10.0)
    assert len(calls) == 2
    # mtime/size change invalidates
    open(vid, "ab").write(b"x")
    grade._frame_stats(vid, 0.0, 10.0)
    assert len(calls) == 3


def test_auto_grade_identical_from_cache(tmp_path, monkeypatch):
    """The eq string derived from cached stats must equal the fresh one."""
    monkeypatch.setattr(grade, "CACHE_DIR", str(tmp_path / "cache"))
    vid = _fake_video(tmp_path)
    monkeypatch.setattr(
        grade, "_frame_stats_uncached",
        lambda *a, **k: {"y_mean": 0.30, "y_range": 0.50, "sat_mean": 0.15})
    fresh = grade.auto_grade(vid, 0, 10)
    cached = grade.auto_grade(vid, 0, 10)
    assert fresh == cached and "gamma" in fresh[0]


def test_failed_stats_are_not_cached(tmp_path, monkeypatch):
    """A transient decode failure must not stick as a cached None."""
    monkeypatch.setattr(grade, "CACHE_DIR", str(tmp_path / "cache"))
    vid = _fake_video(tmp_path)
    calls = []

    def fake(video, start, duration, n_samples=10):
        calls.append(1)
        return None if len(calls) == 1 else dict(STATS)

    monkeypatch.setattr(grade, "_frame_stats_uncached", fake)
    assert grade._frame_stats(vid, 0.0, 10.0) is None
    assert grade._frame_stats(vid, 0.0, 10.0) == STATS
    assert len(calls) == 2


def test_grade_corrupt_cache_is_never_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(grade, "CACHE_DIR", str(tmp_path / "cache"))
    vid = _fake_video(tmp_path)
    monkeypatch.setattr(grade, "_frame_stats_uncached",
                        lambda *a, **k: dict(STATS))
    grade._frame_stats(vid, 0.0, 10.0)
    for f in os.listdir(str(tmp_path / "cache")):
        with open(os.path.join(str(tmp_path / "cache"), f), "w") as fh:
            fh.write("]]garbage")
    assert grade._frame_stats(vid, 0.0, 10.0) == STATS
