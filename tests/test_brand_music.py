"""A brand's own soundtrack and SFX: picked per cut, $0, with a rights
guard until the brand marks its music cleared for marketing."""
import json
import subprocess

import pytest

from reelly import audio_post, config


@pytest.fixture
def brand(tmp_path, monkeypatch):
    d = tmp_path / "kit" / "brands" / "g"
    (d / "music").mkdir(parents=True)
    (d / "sfx").mkdir()
    for name in ("battle.wav", "story.wav"):
        subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                        "sine=f=220:d=3", str(d / "music" / name)], check=True)
    (d / "sfx" / "hit.ogg").write_bytes(b"x")
    spec = {"music": {"tracks": {"battle": "battle.wav", "story": "story.wav"},
                      "match": {"story": ["knock", "door"], "battle": ["combo", "attack"]},
                      "default": "battle", "cleared": False},
            "sfx": {"impact": "hit.ogg"}}
    (d / "brand.json").write_text(json.dumps(spec))
    monkeypatch.setenv("REELLY_BRANDKIT", str(tmp_path / "kit"))
    monkeypatch.setenv("REELLY_BRAND", "g")
    return d


def test_track_follows_what_the_cut_is_about(brand):
    p, k = audio_post.brand_track({"title": "A knock after closing"})
    assert k == "story" and p.endswith("story.wav")
    assert audio_post.brand_track({"title": "Combo payoff"})[1] == "battle"
    assert audio_post.brand_track({"title": "Something else"})[1] == "battle"   # default
    assert audio_post.brand_track({"title": "x", "music": "story"})[1] == "story"


def test_no_brand_music_no_track(monkeypatch):
    monkeypatch.delenv("REELLY_BRAND", raising=False)
    assert audio_post.brand_track({"title": "combo"}) == (None, None)


def test_music_uses_the_brand_track_with_a_rights_warning(brand, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(audio_post, "_fal", lambda *a, **k: pytest.fail("no generation"))
    out = tmp_path / "m.mp3"
    got = audio_post._music_generate({"id": "c", "title": "combo", "duration_s": 3}, str(out))
    assert got == str(out) and out.stat().st_size > 0
    assert "NOT marked cleared" in capsys.readouterr().out


def test_brand_sfx_wins(brand):
    assert audio_post.sfx("impact").endswith("hit.ogg")


def test_unsafe_file_names_are_ignored(brand):
    spec = json.loads((brand / "brand.json").read_text())
    spec["sfx"] = {"impact": "../../../etc/passwd"}
    (brand / "brand.json").write_text(json.dumps(spec))
    assert audio_post._brand_sound("sfx", "impact") is None


def test_level_mix_reaches_target_and_limits_peaks(tmp_path):
    """A quiet mix with a hard transient is lifted to target without the
    peaks going over the limit (the loudness then survives the peak guard)."""
    src = str(tmp_path / "mix.wav")
    subprocess.run([config.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc=0.1*sin(2*PI*330*t):d=6", "-f", "lavfi", "-i",
                    "aevalsrc=0.9*sin(2*PI*90*t):d=0.2", "-filter_complex",
                    "[1]adelay=3000|3000[h];[0][h]amix=inputs=2:normalize=0",
                    "-ar", "48000", src], check=True)
    before = audio_post.integrated_loudness(src)
    out = audio_post.level_mix(src, target=-14.0, peak_db=-2.0)
    after = audio_post.integrated_loudness(out)
    assert out != src and before < -18
    assert abs(after - (-14.0)) <= 0.5
    tp = audio_post.true_peak(out)
    assert tp is None or tp <= -1.0


def test_level_mix_leaves_a_mix_on_target_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_post, "integrated_loudness", lambda p: -14.2)
    assert audio_post.level_mix("x.wav") == "x.wav"
