"""Untagged runs build gfx only; explicit account/flag/delivery.json win."""
import json
import os
import tempfile

from reelly import accounts


def _root(delivery=None):
    d = tempfile.mkdtemp()
    if delivery is not None:
        json.dump(delivery, open(os.path.join(d, "delivery.json"), "w"))
    return d


def test_untagged_project_builds_gfx_only():
    root = _root()
    prof = accounts.for_project(root)
    assert prof["explicit"] is False
    assert accounts.variants_for(root, prof) == ["gfx"]


def test_explicit_account_flag_keeps_profile_variants():
    root = _root()
    prof = accounts.for_project(root, cli_account="creator")
    assert prof["explicit"] is True
    assert accounts.variants_for(root, prof) == list(accounts.VARIANTS)


def test_delivery_json_account_is_explicit():
    root = _root({"account": "creator"})
    prof = accounts.for_project(root)
    assert prof["explicit"] is True
    assert accounts.variants_for(root, prof) == list(accounts.VARIANTS)


def test_cli_variants_beat_default():
    root = _root()
    prof = accounts.for_project(root)
    assert accounts.variants_for(root, prof, "plain,gfx") == ["plain", "gfx"]


def test_delivery_json_variants_beat_default():
    root = _root({"variants": ["plain"]})
    prof = accounts.for_project(root)
    assert accounts.variants_for(root, prof) == ["plain"]


def test_run_account_still_gfx_only():
    root = _root()
    prof = accounts.for_project(root, cli_account="managed")
    assert accounts.variants_for(root, prof) == ["gfx"]
