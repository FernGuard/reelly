"""overlays._composite encodes exactly like the deliverable it composites over.

The gfx pass once encoded at medium/crf 18 over a burned base at
veryfast/crf 20-21: a quality inversion that spent time on detail the base
never had. Both passes (and the outro) now read config.final_encode_args, so
they cannot drift apart again.
"""
from reelly import audio_post, config, finalize, overlays


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


def _encode(cmd):
    i = cmd.index("-c:v")
    return cmd[i:i + len(config.final_encode_args())]


def test_gfx_encode_matches_deliverable_encode(tmp_path, monkeypatch):
    gfx = _run_composite(tmp_path, monkeypatch)
    burn = []
    monkeypatch.setattr(finalize.subprocess, "run",
                        lambda cmd, **k: burn.append(list(cmd)))
    finalize._burn_pass("src.mp4", str(tmp_path / "b.mp4"), [])
    assert _encode(gfx) == config.final_encode_args()
    assert _encode(burn[-1]) == config.final_encode_args()


def test_burn_runs_at_the_output_frame_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("REELLY_FPS", "60")
    burn = []
    monkeypatch.setattr(finalize.subprocess, "run",
                        lambda cmd, **k: burn.append(list(cmd)))
    finalize._burn_pass("src.mp4", str(tmp_path / "b.mp4"), [])
    assert burn[-1][burn[-1].index("-r") + 1] == "60"


def test_output_fps_order_and_guard(monkeypatch):
    monkeypatch.delenv("REELLY_FPS", raising=False)
    from reelly import brandkit
    monkeypatch.setattr(brandkit, "brand", lambda name=None: {})
    assert config.output_fps() == 30                  # public default
    monkeypatch.setattr(brandkit, "brand", lambda name=None: {"fps": 60})
    assert config.output_fps() == 60                  # brand kit
    monkeypatch.setenv("REELLY_FPS", "30")
    assert config.output_fps() == 30                  # env beats the kit
    monkeypatch.setenv("REELLY_FPS", "47")
    assert config.output_fps() == 30                  # odd rates refused
