"""Command-line entry point. Subcommands register themselves here."""

from __future__ import annotations

import argparse
import sys

from . import __version__

EXIT_ERROR = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jevgate", description=__doc__)
    parser.add_argument("--version", action="version", version=f"jevgate {__version__}")
    parser.add_subparsers(dest="command", metavar="<command>")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_ERROR
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
