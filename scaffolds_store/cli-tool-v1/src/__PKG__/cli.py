"""Argparse wiring ONLY: parse args -> call core -> format output -> exit code."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import __version__
from .core.ops import echo_upper
from .errors import DataError, UserError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="__PKG__", description="__APP__")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    shout = sub.add_parser("shout", help="echo text in uppercase")
    shout.add_argument("text", help="text to echo")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "shout":
            print(echo_upper(args.text))
            return 0
        raise UserError(f"unknown command: {args.command}")
    except (UserError, DataError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
