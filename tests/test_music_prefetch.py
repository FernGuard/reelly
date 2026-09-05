"""audio_post music prefetch: beds queue at plan time and download while
segments encode; double-generation is impossible (check-then-act under the
registry lock on the output path); finalize standalone still works with no
prefetch having happened.
"""
import threading
import time

import pytest

from reelly import audio_post


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    monkeypatch.setattr(audio_post, "_PREFETCH", {})


def test_prefetch_of_cached_file_resolves_without_generating(tmp_path, monkeypatch):
    out = tmp_path / "cut_01_music.mp3"
    out.write_bytes(b"bed")
    called = []
    monkeypatch.setattr(audio_post, "_music_generate",
                        lambda *a, **k: called.append(a))
    fut = audio_post.prefetch_music({"id": "cut_01"}, str(out))
    assert fut.result(timeout=5) == str(out)
    assert called == []


def test_prefetch_is_idempotent_and_registered(tmp_path, monkeypatch):
    out = str(tmp_path / "m.mp3")
    release = threading.Event()
    calls = []

    def fake_gen(plan, path, project=""):
        calls.append(path)
        release.wait(5)
        open(path, "wb").write(b"bed")
        return path

    monkeypatch.setattr(audio_post, "_music_generate", fake_gen)
    f1 = audio_post.prefetch_music({"id": "m"}, out)
    f2 = audio_post.prefetch_music({"id": "m"}, out)
    assert f1 is f2                            # one submission, one payment
    assert audio_post.wait_for(out) is f1      # finalize can find it
    release.set()
    assert f1.result(timeout=5) == out
    assert calls == [out]


def test_music_joins_inflight_prefetch_instead_of_regenerating(tmp_path, monkeypatch):
    out = str(tmp_path / "m.mp3")
    release = threading.Event()
    calls = []

    def fake_gen(plan, path, project=""):
        calls.append(path)
        release.wait(5)
        open(path, "wb").write(b"bed")
        return path

    monkeypatch.setattr(audio_post, "_music_generate", fake_gen)
    audio_post.prefetch_music({"id": "m"}, out)
    got = []
    t = threading.Thread(target=lambda: got.append(audio_post.music({"id": "m"}, out)))
    t.start()
    time.sleep(0.05)          # music() is now waiting on the in-flight future
    release.set()
    t.join(5)
    assert got == [out]
    assert calls == [out]     # exactly one generation, ever


def test_music_standalone_without_prefetch(tmp_path, monkeypatch):
    """finalize called on its own: no registry entry, music() generates."""
    out = str(tmp_path / "solo.mp3")

    def fake_gen(plan, path, project=""):
        open(path, "wb").write(b"bed")
        return path

    monkeypatch.setattr(audio_post, "_music_generate", fake_gen)
    assert audio_post.music({"id": "solo"}, out) == out
    # and the cache path short-circuits on a second call
    monkeypatch.setattr(audio_post, "_music_generate",
                        lambda *a, **k: pytest.fail("regenerated a cached bed"))
    assert audio_post.music({"id": "solo"}, out) == out


def test_failed_prefetch_falls_back_to_inline_generation(tmp_path, monkeypatch):
    out = str(tmp_path / "f.mp3")
    monkeypatch.setattr(audio_post, "_music_generate",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fal down")))
    fut = audio_post.prefetch_music({"id": "f"}, out)
    with pytest.raises(RuntimeError):
        fut.result(timeout=5)

    def good_gen(plan, path, project=""):
        open(path, "wb").write(b"bed")
        return path

    monkeypatch.setattr(audio_post, "_music_generate", good_gen)
    assert audio_post.music({"id": "f"}, out) == out
