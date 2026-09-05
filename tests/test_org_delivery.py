"""Org pass (reviewer 2026-08-13): one clean delivery directory and fewer files.
Intermediates live in _work/ (a sibling of deliverables/, not inside it) and are
deleted after a successful render; a manifest summarizes what shipped.
"""
import json
import os

from reelly import motion


def _project(tmp_path):
    root = tmp_path / "motion-x"
    for d in ("deliverables/final", "type", "_work", "qc", "source"):
        (root / d).mkdir(parents=True, exist_ok=True)
    # intermediates
    (root / "_work" / "cut_01_base.mp4").write_bytes(b"BASE")
    (root / "_work" / "cut_01_base.mp4.occ.json").write_text("{}")
    (root / "_work" / "cut_01_0.png").write_bytes(b"LAYER")
    (root / "type" / "hook.png.raw.png").write_bytes(b"RAW")
    (root / "type" / "hook.png.key").write_text("k")
    # keepers
    (root / "deliverables" / "final" / "cut_01.mp4").write_bytes(b"FINAL")
    (root / "type" / "hook.png").write_bytes(b"HOOK")
    (root / "type" / "hook.png.override.json").write_text("{}")
    (root / "source" / "keyart.png").write_bytes(b"SRC")
    return root


PLAN = {
    "hook": {"text": "This tiny cat"}, "payoff": {"text": "takes the lead"},
    "cta": "Play on ExampleBrand", "caption": "cap",
    "provenance": {"source_image": "keyart.png", "model": "seedance", "real_art": False},
    "design_gate": {"result": "pass"}, "style_gate": {"result": "fail"},
    "band_gate": {"result": "pass"},
}


def test_manifest_summarizes_what_shipped(tmp_path):
    root = _project(tmp_path)
    motion._write_manifest(str(root), PLAN)
    m = json.loads((root / "deliverables" / "manifest.json").read_text())
    assert m["cut"] == "final/cut_01.mp4"
    assert m["hook"] == "This tiny cat" and m["payoff"] == "takes the lead"
    assert m["cta"] == "Play on ExampleBrand"
    assert m["source_image"] == "keyart.png" and m["model"] == "seedance"
    assert m["design_gate"] == "pass" and m["style_gate"] == "fail"


def test_cleanup_removes_intermediates_keeps_deliverable_and_provenance(tmp_path):
    root = _project(tmp_path)
    motion._cleanup_work(str(root))
    # gone: the whole _work/ dir, raw lettering, cache keys
    assert not (root / "_work").exists()
    assert not (root / "type" / "hook.png.raw.png").exists()
    assert not (root / "type" / "hook.png.key").exists()
    # kept: deliverable, shipped lettering, human override, source
    assert (root / "deliverables" / "final" / "cut_01.mp4").exists()
    assert (root / "type" / "hook.png").exists()
    assert (root / "type" / "hook.png.override.json").exists()
    assert (root / "source" / "keyart.png").exists()


def test_deliverables_holds_only_the_cut_and_manifest_after_a_run(tmp_path):
    """The delivery directory is clean: final/ + manifest.json, no _work sibling
    inside it."""
    root = _project(tmp_path)
    motion._write_manifest(str(root), PLAN)
    motion._cleanup_work(str(root))
    entries = sorted(os.listdir(root / "deliverables"))
    assert entries == ["final", "manifest.json"]
    assert "_work" not in os.listdir(root / "deliverables")
