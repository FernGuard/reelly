"""The configuration surface: brand.json schema 1 (validated, migrated) and
one registry for every REELLY_* environment setting."""
import json
import os
import re

import pytest

from reelly import brandkit, config, settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_setting_the_code_reads_is_registered():
    names = set()
    for dp, _, files in os.walk(os.path.join(ROOT, "reelly")):
        for f in files:
            if f.endswith(".py"):
                src = open(os.path.join(dp, f), encoding="utf-8").read()
                names |= set(re.findall(r'["\'](REELLY_[A-Z0-9_]+)["\']', src))
    names -= {"REELLY_EXIT"}
    missing = sorted(n for n in names if n not in settings.NAMES and not n.endswith("_"))
    assert not missing, f"add these to reelly/settings.py: {missing}"
    for name, group, kind, default, level, help_ in settings.SETTINGS:
        assert level in ("public", "advanced") and help_ and group


def test_the_settings_doc_is_generated_and_current():
    doc = os.path.join(ROOT, "docs", "SETTINGS.md")
    assert open(doc, encoding="utf-8").read() == settings.markdown(), \
        "regenerate: python -m reelly.settings --markdown > docs/SETTINGS.md"


def test_one_parser_for_on_off(monkeypatch):
    for v, want in (("1", True), ("ON", True), ("yes", True), ("true", True),
                    ("0", False), ("off", False), ("No", False), ("false", False)):
        monkeypatch.setenv("REELLY_FX_LITE", v)
        assert config.env_bool("REELLY_FX_LITE") is want
    monkeypatch.setenv("REELLY_FX_LITE", "maybe")
    assert config.env_bool("REELLY_FX_LITE", True) is True


OLD = {"name": "g", "accent": "#a87cf0", "fps": 60, "reframe": "full_width",
       "outro_fx": "smoke", "copy": {"cta": "Wishlist now", "clip_len": [25, 30]},
       "fx": {"outro": "smoke-endcard", "ambient": ["smoke-ambient", "embers-ambient"],
              "text": "smoke-aura", "text_motion": "blocklock", "text_exit": "petals"}}


def test_an_old_kit_is_refused_with_directions_and_migrates_cleanly():
    errs, _ = brandkit.check(OLD)
    assert any('"fx" moved to effects' in e for e in errs)
    assert any('"copy.clip_len" moved to video.clip_len' in e for e in errs)
    new = brandkit.migrate(OLD)
    assert brandkit.check(new) == ([], [])
    assert new["schema"] == 1 and new["video"] == {"fps": 60, "reframe": "full_width",
                                                   "clip_len": [25, 30]}
    assert new["effects"] == {"endcard": "smoke", "ambient": ["smoke", "embers"], "text": "smoke"}
    assert new["text"] == {"motion": "blocklock", "exit": "petals"}
    assert "clip_len" not in new["copy"] and new["copy"]["cta"] == "Wishlist now"


def test_typos_fail_at_second_zero():
    errs, _ = brandkit.check({"schema": 1, "effects": {"ambient": ["smoke", "embrs"]},
                              "text": {"exits": "petals"}, "video": {"fps": 45}})
    assert any('no preset "embrs"' in e for e in errs)
    assert any('unknown key "text.exits"' in e for e in errs)
    assert any('"video.fps"' in e for e in errs)
    errs, _ = brandkit.check({"schema": 1, "effects": {"ambient": [
        {"preset": "embers", "params": {"u_densty": 0.4}}]}})
    assert any("unknown parameter" in e for e in errs)


def test_cli_checks_and_migrates_a_kit(tmp_path, monkeypatch):
    d = tmp_path / "kit" / "brands" / "g"
    d.mkdir(parents=True)
    (d / "brand.json").write_text(json.dumps(OLD))
    from reelly import cli
    monkeypatch.setenv("REELLY_BRANDKIT", str(tmp_path / "kit"))
    with pytest.raises(SystemExit) as e:
        cli.main(["brand", "check", "g"])
    assert e.value.code == 1
    cli.main(["brand", "migrate", "g", "--write"])
    assert (d / "brand.json.bak").exists()
    assert json.loads((d / "brand.json").read_text())["schema"] == 1
    with pytest.raises(SystemExit) as e:
        cli.main(["brand", "check", "g"])
    assert e.value.code == 0
    monkeypatch.setenv("REELLY_BRAND", "g")
    (d / "brand.json").write_text(json.dumps({"schema": 1, "effects": {"endcard": "lazers"}}))
    with pytest.raises(SystemExit) as e:
        brandkit.require_valid()
    assert "lazers" in str(e.value)


def test_models_are_pinned_and_verified():
    from reelly import models
    for k, m in models.MODELS.items():
        assert re.fullmatch(r"[0-9a-f]{64}", m["sha256"]) and m["url"].startswith("https://")
        assert "/main/" not in m["url"]                       # a pinned revision
        assert m["env"] in settings.NAMES


def test_prompts_name_the_footage_by_content_type(monkeypatch):
    from reelly import sight
    monkeypatch.setenv("REELLY_CONTENT_TYPE", "gameplay")
    assert "video game" in sight.subject()["source"]
    monkeypatch.setenv("REELLY_CONTENT_TYPE", "podcast")
    s = sight.subject()
    assert "game" not in s["source"] + s["reel"] + s["look"] + s["footage"]


def test_the_example_brand_kit_is_valid():
    data = json.load(open(os.path.join(ROOT, "examples", "brandkit", "demo", "brand.json"),
                          encoding="utf-8"))
    assert brandkit.check(data) == ([], [])


def test_local_only_stops_every_outbound_path(monkeypatch, tmp_path):
    from reelly import control, direct, face, models, sight
    monkeypatch.setenv("REELLY_LOCAL_ONLY", "1")
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_x")
    monkeypatch.delenv("REELLY_VISION_URL", raising=False)
    assert direct.resolve_brain("auto") is None and not direct.cli_ready("codex")
    assert direct._ask_cli("codex", "hi", "p", "label") is None
    assert config.get_key("huggingface") is None and config.paid_blocked("fal-ai")
    assert sight.resolve_backend() is None
    monkeypatch.setattr(face, "MESH_MODEL", str(tmp_path / "none.task"))
    assert face._ensure_model() is False
    assert control.verify_events("v.mp4", [{"t": 1.0}], (0, 0, 1, 1), str(tmp_path), "c") == []
    with pytest.raises(SystemExit):
        models.fetch("depth")
