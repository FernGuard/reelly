"""Ingest guard: an all-black source fails in seconds, before any AI spend."""
import subprocess
import pytest
from reelly import config, media


def _clip(path, color, dur=3):
    subprocess.run([config.FFMPEG, "-y", "-v", "error", "-f", "lavfi",
                    "-i", f"color={color}:size=320x240:rate=10:duration={dur}",
                    "-pix_fmt", "yuv420p", path], check=True)


def test_black_source_refused(tmp_path):
    p = str(tmp_path / "black.mp4")
    _clip(p, "black")
    with pytest.raises(SystemExit, match="SOURCE IS BLACK"):
        media.assert_not_black(p)


def test_real_content_passes(tmp_path):
    p = str(tmp_path / "gray.mp4")
    _clip(p, "gray")
    media.assert_not_black(p)  # must not raise


def test_env_override_allows_black(tmp_path, monkeypatch):
    p = str(tmp_path / "black.mp4")
    _clip(p, "black")
    monkeypatch.setenv("REELLY_ALLOW_BLACK_SOURCE", "1")
    media.assert_not_black(p)  # opt-out honored
