"""Zero-paid by default: providers that bill per call stay off unless a run
opts in with --allow-paid / REELLY_ALLOW_PAID=1. The editor brain defaults to
a signed-in subscription CLI and falls back to heuristics."""
import json
import os
import subprocess

import pytest

from reelly import audio_post, cli, config, direct


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for var in ("REELLY_ALLOW_PAID", "OPENAI_API_KEY", "GEMINI_API_KEY",
                "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY", "FAL_KEY",
                "FAL_API_KEY", "ANTHROPIC_API_KEY", "HUGGINGFACE_TOKEN",
                "HF_TOKEN", "REELLY_BRAND"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "HOME", str(tmp_path))
    direct.cli_ready.cache_clear()
    yield
    getattr(direct.cli_ready, "cache_clear", lambda: None)()


# --- the gate -----------------------------------------------------------------

def test_paid_key_is_hidden_while_paid_calls_are_off(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert config.paid_allowed() is False
    assert config.get_key("openai") is None
    assert config.have_key("openai") is False


def test_allow_paid_env_reveals_the_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("REELLY_ALLOW_PAID", "1")
    assert config.get_key("openai") == "sk-test"


def test_allow_paid_in_user_config(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "fal-test")
    (tmp_path / "config.json").write_text(json.dumps({"allow_paid": True}))
    assert config.get_key("fal-ai") == "fal-test"


def test_explicit_env_off_beats_user_config(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "fal-test")
    monkeypatch.setenv("REELLY_ALLOW_PAID", "0")
    (tmp_path / "config.json").write_text(json.dumps({"allow_paid": True}))
    assert config.get_key("fal-ai") is None


def test_every_billing_provider_is_gated(monkeypatch):
    for name in ("google-genai", "openai", "fal-ai", "anthropic"):
        monkeypatch.setenv(config.PROVIDERS[name]["env"][0], "k")
        assert config.get_key(name) is None, name


def test_free_providers_are_not_gated(monkeypatch):
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_test")
    assert config.get_key("huggingface") == "hf_test"


def test_provider_key_raises_the_opt_in_message(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    with pytest.raises(config.PaidDisabledError) as e:
        config.provider_key("google-genai")
    assert "--allow-paid" in str(e.value)
    # callers that degrade on a missing key degrade the same way here
    assert isinstance(e.value, config.MissingKeyError)


def test_no_provider_is_required():
    assert not any(spec["required"] for spec in config.PROVIDERS.values())


def test_setup_reports_paid_off_and_present_keys_as_idle(monkeypatch, capsys):
    monkeypatch.setenv("FAL_KEY", "fal-test")
    config.doctor(as_json=True)
    checks = {c["id"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert checks["paid calls"]["detail"].startswith("OFF")
    assert checks["FAL_KEY"]["status"] == "skip"
    assert "idle" in checks["FAL_KEY"]["detail"]
    assert "fal-test" not in json.dumps(checks)


# --- CLI global flags -----------------------------------------------------------

def test_global_flags_work_after_the_subcommand(monkeypatch):
    rest = cli._global_flags(["cut", "proj", "--allow-paid", "--brand", "game-a",
                              "--for", "tiktok"])
    assert rest == ["cut", "proj", "--for", "tiktok"]
    assert os.environ["REELLY_ALLOW_PAID"] == "1"
    assert os.environ["REELLY_BRAND"] == "game-a"
    monkeypatch.delenv("REELLY_ALLOW_PAID")
    monkeypatch.delenv("REELLY_BRAND")


def test_brand_equals_form(monkeypatch):
    assert cli._global_flags(["finalize", "--brand=game-b", "p"]) == ["finalize", "p"]
    assert os.environ["REELLY_BRAND"] == "game-b"
    monkeypatch.delenv("REELLY_BRAND")


# --- brain selection --------------------------------------------------------------

def test_paid_brain_without_opt_in_exits_with_the_message():
    with pytest.raises(SystemExit) as e:
        direct.resolve_brain("gpt")
    assert "--allow-paid" in str(e.value)


def test_paid_brain_with_opt_in_is_kept(monkeypatch):
    monkeypatch.setenv("REELLY_ALLOW_PAID", "1")
    assert direct.resolve_brain("gemini") == "gemini"


def test_auto_uses_codex_and_never_probes_claude(monkeypatch):
    """claude's sign-in check reads the macOS keychain (a dialog from ssh),
    so auto never probes it; it must be asked for by name."""
    probed = []
    ready = {"codex": False, "claude": True}
    monkeypatch.setattr(direct, "cli_ready",
                        lambda tool: (probed.append(tool), ready[tool])[1])
    assert direct.resolve_brain("auto") is None
    assert probed == ["codex"]
    ready["codex"] = True
    assert direct.resolve_brain("auto") == "codex"
    assert direct.resolve_brain("claude") == "claude"


def test_named_cli_brain_not_signed_in_falls_back(monkeypatch):
    monkeypatch.setattr(direct, "cli_ready", lambda tool: False)
    assert direct.resolve_brain("codex") is None


def test_json_from_text_tolerates_fences_and_chatter():
    assert direct._json_from_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert direct._json_from_text('Here you go: {"hook": "x"} done') == {"hook": "x"}
    assert direct._json_from_text("no json here") is None
    assert direct._json_from_text("[1, 2]") is None


def test_codex_call_strips_api_keys_and_reads_the_reply(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ak-must-not-leak")
    import shutil
    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")
    seen = {}

    def fake_run(cmd, env=None, stdin=None, **kw):
        seen["cmd"], seen["env"], seen["stdin"] = cmd, env, stdin
        out = cmd[cmd.index("-o") + 1]
        with open(out, "w") as f:
            f.write('{"landing_ok": true, "hook": "The door opens."}')
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    added = []
    monkeypatch.setattr(direct.ledger, "add", lambda *a, **k: added.append(a))
    reply = direct._ask_cli("codex", "plan this", "proj", "label")
    assert reply == {"landing_ok": True, "hook": "The door opens."}
    assert "OPENAI_API_KEY" not in seen["env"]
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert seen["cmd"][:2] == ["/usr/bin/codex", "exec"]
    assert seen["stdin"] is subprocess.DEVNULL
    assert "-m" not in seen["cmd"]            # the CLI's own default model first
    assert added and added[0][0] == "codex-cli" and added[0][2] == 0.0


def test_claude_call_reads_the_result_envelope(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")

    def fake_run(cmd, **kw):
        body = json.dumps({"is_error": False, "result": '{"hook": "Open it."}'})
        return subprocess.CompletedProcess(cmd, 0, body, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(direct.ledger, "add", lambda *a, **k: None)
    assert direct._ask_cli("claude", "p", "proj", "l") == {"hook": "Open it."}


def test_claude_error_envelope_is_no_reply(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")
    body = json.dumps({"is_error": True, "result": "Not logged in"})
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, body, ""))
    monkeypatch.setattr(direct.ledger, "add", lambda *a, **k: None)
    assert direct._ask_cli("claude", "p", "proj", "l") is None


# --- audio degrades instead of billing ------------------------------------------------

def test_uncached_sfx_is_skipped_not_generated(monkeypatch, tmp_path):
    monkeypatch.setattr(audio_post, "SFX_DIR", str(tmp_path / "sfx"))
    monkeypatch.setattr(audio_post, "_fal",
                        lambda *a, **k: pytest.fail("FAL must not be called"))
    assert audio_post.sfx("impact") is None


def test_cached_sfx_is_still_used(monkeypatch, tmp_path):
    d = tmp_path / "sfx"
    d.mkdir()
    (d / "impact.mp3").write_bytes(b"x")
    monkeypatch.setattr(audio_post, "SFX_DIR", str(d))
    assert audio_post.sfx("impact") == str(d / "impact.mp3")


def test_music_without_kit_bed_ships_without_a_bed(monkeypatch, tmp_path):
    monkeypatch.setenv("REELLY_BRANDKIT", str(tmp_path / "kit"))
    monkeypatch.setattr(audio_post, "_fal",
                        lambda *a, **k: pytest.fail("FAL must not be called"))
    plan = {"id": "cut_01", "duration_s": 8.0, "title": "t"}
    assert audio_post._music_generate(plan, str(tmp_path / "m.mp3")) is None



def test_codex_falls_back_to_the_pinned_model_when_the_default_is_refused(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")
    monkeypatch.setattr(direct, "_CODEX_MODEL_OK", [False])
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "-m" not in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", 'ERROR: {"message": "The model requires a newer version of Codex."}')
        open(cmd[cmd.index("-o") + 1], "w").write('{"ok": true}')
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(direct.ledger, "add", lambda *a, **k: None)
    assert direct._ask_cli("codex", "p", "proj", "l") == {"ok": True}
    assert len(calls) == 2 and calls[1][calls[1].index("-m") + 1] == direct.config.CODEX_MODEL
    # the accepted model is remembered: the next call goes straight to it
    calls.clear()
    direct._ask_cli("codex", "p", "proj", "l")
    assert len(calls) == 1 and "-m" in calls[0]


def test_pinned_codex_model_env(monkeypatch):
    monkeypatch.setenv("REELLY_CODEX_MODEL", "some-model")
    assert direct._codex_models() == ["some-model"]
