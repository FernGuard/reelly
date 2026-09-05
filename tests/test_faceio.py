"""faceio: batched frame extraction (spawn counts, scale math) and the
detection disk cache (round-trip, invalidation, corruption tolerance).

ffmpeg/ffprobe are stubbed -- nothing here needs real media or the model.
"""
import os

import pytest

from reelly import faceio


# --------------------------------------------------------------------- cache

@pytest.fixture()
def cache_env(tmp_path, monkeypatch):
    monkeypatch.setattr(faceio, "CACHE_DIR", str(tmp_path / "cache"))
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"not really a video")
    return str(video)


def test_cache_round_trip(cache_env):
    assert faceio.cache_get(cache_env, "box:1.00:2.00:7") is None
    faceio.cache_put(cache_env, "box:1.00:2.00:7", {"box": [10, 20, 30]})
    assert faceio.cache_get(cache_env, "box:1.00:2.00:7") == {"box": [10, 20, 30]}


def test_cache_keys_are_independent(cache_env):
    faceio.cache_put(cache_env, "a", 1)
    faceio.cache_put(cache_env, "b", 2)
    assert faceio.cache_get(cache_env, "a") == 1
    assert faceio.cache_get(cache_env, "b") == 2


def test_cache_invalidates_on_mtime_change(cache_env):
    faceio.cache_put(cache_env, "k", "v")
    st = os.stat(cache_env)
    os.utime(cache_env, (st.st_atime, st.st_mtime + 100))
    assert faceio.cache_get(cache_env, "k") is None


def test_cache_invalidates_on_size_change(cache_env):
    faceio.cache_put(cache_env, "k", "v")
    st = os.stat(cache_env)
    with open(cache_env, "ab") as f:
        f.write(b"x")
    os.utime(cache_env, (st.st_atime, st.st_mtime))  # same mtime, new size
    assert faceio.cache_get(cache_env, "k") is None


def test_corrupt_cache_file_is_ignored(cache_env):
    faceio.cache_put(cache_env, "k", "v")
    with open(faceio._cache_path(cache_env), "w") as f:
        f.write("{ not json")
    assert faceio.cache_get(cache_env, "k") is None
    faceio.cache_put(cache_env, "k2", "v2")     # put over corruption survives
    assert faceio.cache_get(cache_env, "k2") == "v2"


# ---------------------------------------------------------------- extraction

@pytest.fixture()
def fake_ffmpeg(monkeypatch):
    """Stub ffprobe (1920x1080 source) and ffmpeg (all-zero frames)."""
    calls = []
    monkeypatch.setattr(faceio, "_probe_dims", lambda v: (1920, 1080))

    class R:
        returncode = 0

    def spawn(cmd):
        calls.append(cmd)
        n = int(cmd[cmd.index("-frames:v") + 1])
        r = R()
        r.stdout = b"\x00" * (640 * 360 * 3 * n)
        return r

    monkeypatch.setattr(faceio, "_spawn", spawn)
    return calls


def test_uniform_times_use_one_spawn(fake_ffmpeg):
    out = faceio.extract_frames("v.mp4", [1.0, 2.0, 3.0, 4.0, 5.0])
    assert len(fake_ffmpeg) == 1
    assert len(out) == 5
    assert all(f is not None for f, _ in out)
    assert all(f.shape == (360, 640, 3) for f, _ in out)


def test_scale_maps_back_to_source(fake_ffmpeg):
    out = faceio.extract_frames("v.mp4", [1.0, 2.0, 3.0])
    for _, scale in out:
        assert scale == pytest.approx(1920 / 640)


def test_single_time_is_one_spawn(fake_ffmpeg):
    out = faceio.extract_frames("v.mp4", [3.5])
    assert len(fake_ffmpeg) == 1
    assert len(out) == 1 and out[0][0] is not None


def test_sparse_times_batch_into_few_spawns(fake_ffmpeg):
    # two evenly spaced runs, one big gap: 2 spawns, never 4
    out = faceio.extract_frames("v.mp4", [0.0, 1.0, 50.0, 51.0])
    assert len(fake_ffmpeg) == 2
    assert len(out) == 4


def test_short_output_pads_with_none(fake_ffmpeg, monkeypatch):
    class R:
        returncode = 0
        stdout = b"\x00" * (640 * 360 * 3 * 2)   # only 2 of 4 frames

    monkeypatch.setattr(faceio, "_spawn", lambda cmd: R())
    out = faceio.extract_frames("v.mp4", [1.0, 2.0, 3.0, 4.0])
    assert [f is None for f, _ in out] == [False, False, True, True]


def test_unprobeable_video_yields_nones_without_spawning(fake_ffmpeg, monkeypatch):
    monkeypatch.setattr(faceio, "_probe_dims", lambda v: None)
    out = faceio.extract_frames("nope.mp4", [1.0, 2.0])
    assert out == [(None, 1.0), (None, 1.0)]
    assert fake_ffmpeg == []


def test_empty_times():
    assert faceio.extract_frames("v.mp4", []) == []


def test_out_dims_never_upscale_and_stay_even():
    assert faceio._out_dims((1920, 1080), 640) == (640, 360)
    assert faceio._out_dims((320, 240), 640) == (320, 240)
    assert faceio._out_dims((1080, 1920), 640) == (640, 1138)


def test_parallel_cache_puts_keep_every_key(cache_env):
    # cache_put is read-modify-write; without the in-process lock, parallel
    # writers overwrite each other's snapshots and keys vanish
    import threading

    n = 24
    barrier = threading.Barrier(8, timeout=10)

    def put(i):
        barrier.wait()
        faceio.cache_put(cache_env, f"k{i}", i)

    for batch in range(0, n, 8):
        ts = [threading.Thread(target=put, args=(i,))
              for i in range(batch, batch + 8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
    for i in range(n):
        assert faceio.cache_get(cache_env, f"k{i}") == i
