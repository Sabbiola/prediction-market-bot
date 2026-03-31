from __future__ import annotations

from collections.abc import Sequence


def build_parser():
    from prediction_market_bot.cli.parser import build_parser as _build_parser

    return _build_parser()


def main(argv: Sequence[str] | None = None) -> int:
    from prediction_market_bot.cli.app import main as _main

    return _main(argv)


__all__ = ["build_parser", "main"]
