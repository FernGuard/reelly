"""Windows portability: file locks without fcntl, file paths inside ffmpeg
filters, and the ledger writing on every platform."""
import os
import threading

from reelly import config, ledger, media, slots


def test_filter_path_quotes_and_escapes_drive_colons():
    assert media.filter_path(r"C:\Temp\x.txt") == r"'C\:/Temp/x.txt'"
    assert media.filter_path("/tmp/a.txt") == "'/tmp/a.txt'"


def test_filter_path_survives_a_real_ffmpeg_filter(tmp_path):
    """The file lands where the path said, through ffmpeg's own parser."""
    import subprocess
    out = tmp_path / "stats dir" / "m.txt"
    out.parent.mkdir()
    r = subprocess.run([config.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=black:s=64x64:d=0.2", "-vf",
                        f"signalstats,metadata=print:file={media.filter_path(out)}",
                        "-f", "null", "-"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "signalstats.YAVG" in out.read_text()


def test_slot_lock_is_exclusive_and_released(tmp_path, monkeypatch):
    monkeypatch.setattr(slots, "SLOTS_DIR", str(tmp_path))
    monkeypatch.setenv("REELLY_DECODE_SLOTS", "1")
    order = []
    with slots.hold("decode"):
        def other():
            with slots.hold("decode"):
                order.append("second")
        t = threading.Thread(target=other)
        t.start()
        t.join(0.8)
        assert t.is_alive()                  # blocked while the first holds it
        order.append("first")
    t.join(5)
    assert order == ["first", "second"]


def test_ledger_add_works_on_this_platform(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "HOME", str(tmp_path))
    monkeypatch.setattr(ledger, "LEDGER", str(tmp_path / "ledger.json"))
    ledger.add("codex-cli", "smoke", 0.0, "proj")
    ledger.add("codex-cli", "smoke 2", 0.0, "proj")
    assert len(ledger._load()["entries"]) == 2
