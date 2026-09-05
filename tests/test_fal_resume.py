"""P0 #2: a fal render is billed at queue time, so a timeout or a killed run
must RESUME the job it already paid for, never resubmit and pay twice.

Each test drives audio_post._fal_once against a fake requests layer and a
temp-file pending registry, and asserts on how many times a job is SUBMITTED.
"""
import json

import pytest

from reelly import audio_post


class _Resp:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeRequests:
    """Programmable fal queue: one submit handle, a scripted status sequence,
    and a fixed response body. Counts submits so the tests can prove a resume
    did not pay again."""

    HANDLE = {"request_id": "req-1",
              "status_url": "http://fal/req-1/status",
              "response_url": "http://fal/req-1/response"}

    def __init__(self, status_seq, response=None):
        self.post_calls = 0
        self.get_calls = 0
        self._status_seq = list(status_seq)
        self._last_status = self._status_seq[-1] if self._status_seq else {}
        self._response = response or {"url": "http://fal/out.mp4"}

    def post(self, url, **k):
        self.post_calls += 1
        return _Resp(self.HANDLE)

    def get(self, url, **k):
        self.get_calls += 1
        if url.endswith("/status"):
            self._last_status = (self._status_seq.pop(0) if self._status_seq
                                 else self._last_status)
            return _Resp(self._last_status)
        return _Resp(self._response)


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_post.config, "HOME", str(tmp_path))
    monkeypatch.setattr(audio_post, "_PENDING",
                        str(tmp_path / "fal_pending.json"))
    monkeypatch.setattr(audio_post.time, "sleep", lambda *_: None)
    monkeypatch.setattr(audio_post, "_headers", lambda: {})
    return tmp_path


def _once(fake, monkeypatch):
    monkeypatch.setattr(audio_post, "requests", fake)
    return audio_post._fal_once("ep", {"prompt": "p"}, "detail", "proj",
                                "fal-video", lambda out: out.get("url"), tries=3)


def _pending(tmp_path):
    p = tmp_path / "fal_pending.json"
    return json.loads(p.read_text()) if p.exists() else {}


def test_success_persists_then_clears(wired, monkeypatch):
    """A clean render leaves no pending entry behind."""
    fake = _FakeRequests([{"status": "COMPLETED"}])
    url = _once(fake, monkeypatch)
    assert url == "http://fal/out.mp4"
    assert fake.post_calls == 1
    assert _pending(wired) == {}


def test_timeout_persists_the_handle_for_resume(wired, monkeypatch):
    """A still-generating job times out but KEEPS its handle, so it can be
    picked up rather than re-rendered."""
    fake = _FakeRequests([{"status": "IN_PROGRESS"}])
    with pytest.raises(RuntimeError, match="still generating"):
        _once(fake, monkeypatch)
    reg = _pending(wired)
    assert len(reg) == 1
    assert next(iter(reg.values()))["request_id"] == "req-1"


def test_resume_polls_the_saved_job_without_resubmitting(wired, monkeypatch):
    """The whole point: after a timeout, the next call for the SAME payload
    resumes the saved request and never submits (never pays) again."""
    fake1 = _FakeRequests([{"status": "IN_PROGRESS"}])
    with pytest.raises(RuntimeError, match="still generating"):
        _once(fake1, monkeypatch)

    fake2 = _FakeRequests([{"status": "COMPLETED"}])
    url = _once(fake2, monkeypatch)
    assert url == "http://fal/out.mp4"
    assert fake2.post_calls == 0, "resume must not submit a second render"
    assert _pending(wired) == {}


def test_terminal_failure_clears_pending_so_a_rerun_resubmits(wired, monkeypatch):
    """A FAILED job is gone for good: drop its handle so a rerun starts a fresh
    render instead of forever resuming a dead request."""
    fake = _FakeRequests([{"status": "FAILED", "error": "model blew up"}])
    with pytest.raises(RuntimeError, match="failed"):
        _once(fake, monkeypatch)
    assert _pending(wired) == {}


def test_resume_of_a_dead_saved_job_resubmits_fresh(wired, monkeypatch):
    """If the saved handle now reports FAILED on resume, it is dropped and a
    new render is submitted in the same call."""
    fake1 = _FakeRequests([{"status": "IN_PROGRESS"}])
    with pytest.raises(RuntimeError, match="still generating"):
        _once(fake1, monkeypatch)

    # saved job is now dead; a completed fresh submit should follow
    fake2 = _FakeRequests([{"status": "FAILED"}, {"status": "COMPLETED"}])
    url = _once(fake2, monkeypatch)
    assert url == "http://fal/out.mp4"
    assert fake2.post_calls == 1, "a dead saved job forces exactly one resubmit"
    assert _pending(wired) == {}
