"""overlays._composite encode settings match the underlying deliverable.

The gfx pass used to encode at medium/crf 18 while the burned base it
composites over was veryfast/crf 20-21 — a quality inversion costing ~3s/cut
for detail the source never had. The gfx encode must stay veryfast/crf 20.
"""
from reelly import audio_post, overlays


def _run_composite(tmp_path, monkeypatch):
    cmds = []
    monkeypatch.setattr(overlays.subprocess, "run",
                        lambda cmd, **k: cmds.append(list(cmd)))
    monkeypatch.setattr(overlays, "_render_png",
                        lambda wd, name, body: str(tmp_path / f"{name}.png"))
    monkeypatch.setattr(audio_post, "enforce_true_peak", lambda *a, **k: None)
    monkeypatch.setattr(audio_post, "enforce_loudness", lambda *a, **k: None)
    events = [{"template": "raw", "args": ["<div>x</div>"],
               "t": [0.5, 2.0], "sfx": ["pop.mp3", -14]}]
    overlays._composite("src.mp4", str(tmp_path / "out.mp4"), events,
                        str(tmp_path))
    return cmds[-1]


def test_gfx_encode_matches_deliverable_preset(tmp_path, monkeypatch):
    cmd = _run_composite(tmp_path, monkeypatch)
    assert cmd[cmd.index("-preset") + 1] == "veryfast"
    assert cmd[cmd.index("-crf") + 1] == "20"


def test_gfx_encode_never_regresses_to_medium_18(tmp_path, monkeypatch):
    cmd = _run_composite(tmp_path, monkeypatch)
    assert "medium" not in cmd
    assert "18" not in cmd
