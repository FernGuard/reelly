"""The `reveal` command: ordered cards -> gacha-style reveal. The full render is
exercised end-to-end by hand; here we guard the module contract and the CLI wiring
(a designed-card sequence path distinct from generative `motion`)."""
import pytest

from reelly import reveal


def test_reveal_needs_at_least_two_cards(tmp_path):
    with pytest.raises(ValueError):
        reveal.build(["only.png"], str(tmp_path / "out.mp4"))


def test_reveal_is_registered_in_the_cli():
    from reelly import cli
    ap = cli.build_parser() if hasattr(cli, "build_parser") else None
    # the subcommand must exist so `reelly reveal ...` dispatches
    import argparse
    src = open(cli.__file__).read()
    assert '"reveal"' in src and "reveal.build(" in src
