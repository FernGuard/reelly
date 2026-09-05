"""Public-release config: keys from env / ~/.reelly, never from the repo."""
import json
import os
import stat

import pytest

from reelly import config


SECRET = "sk-test-do-not-commit-this-value-xyz"


def test_env_key_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("FAL_KEY", "fal-test-key")
    assert config.get_key("google-genai") == SECRET
    assert config.provider_key("openai") == "openai-test-key"
    assert config.have_key("fal-ai")


def test_user_config_json(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HOME", str(tmp_path))
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "config.json").write_text(json.dumps({
        "gemini_api_key": SECRET,
    }))
    assert config.get_key("google-genai") == SECRET


def test_missing_key_names_the_variable_and_url(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HOME", str(tmp_path / "empty"))
    for var in config.PROVIDERS["openai"]["env"]:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(config.MissingKeyError) as exc:
        config.provider_key("openai")
    msg = str(exc.value)
    assert "OPENAI_API_KEY" in msg
    assert "https://platform.openai.com/api-keys" in msg
    assert SECRET not in msg


def test_missing_key_does_not_read_asset_bot(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "HOME", str(tmp_path / "empty"))
    bot_dir = tmp_path / ".asset-bot"
    bot_dir.mkdir()
    (bot_dir / "config.json").write_text(json.dumps({
        "asset-providers": {"fal-ai": {"apiKey": "legacy-fal-key"}}
    }))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.delenv("FAL_API_KEY", raising=False)
    assert config.get_key("fal-ai") is None


def test_doctor_never_prints_secrets(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(config, "HOME", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("FAL_KEY", SECRET)
    config.doctor()
    out = capsys.readouterr().out
    assert SECRET not in out
    assert "sk-test" not in out


def test_write_user_config_is_owner_only(tmp_path):
    path = tmp_path / "config.json"
    config.write_user_config({"openai_api_key": "x"}, path=str(path))
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_ffmpeg_uses_path_not_only_homebrew(monkeypatch):
    monkeypatch.setenv("FFMPEG", "/custom/ffmpeg")
    assert config.find_ffmpeg() == "/custom/ffmpeg"
