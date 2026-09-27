"""Command-line entry point. Subcommands register themselves here.

Each gate module exposes ``register(subparsers)``; modules that are not
installed yet are skipped so the CLI works while the project grows. Shared
flags, client construction and report emission live here so every subcommand
behaves the same way.

Exit codes: 0 ready/accept · 1 revise/split · 2 ask/unproven · 3 uncertain
(API failure or --no-ai) · 4 error · 5 gather (more context required).
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .client import ClientError, TypeSafeClient
from .config import Config, ConfigError, apply_cli
from .config import load as load_config_files
from .report import Report
from .runs import DEFAULT_ROOT, Run

EXIT_OK = 0
EXIT_REVISE = 1
EXIT_ASK = 2
EXIT_UNCERTAIN = 3
EXIT_ERROR = 4
EXIT_GATHER = 5

TICKET_EXITS = {"ready": 0, "revise": 1, "split": 1, "ask": 2, "uncertain": 3, "gather": 5}
DELIVERY_EXITS = {"accept": 0, "revise": 1, "unproven": 2, "uncertain": 3, "gather": 5}

# Modules that may add subcommands, in the order they appear in --help.
REGISTRY = (
    "jevgate.ticket.cli",
    "jevgate.delivery.cli",
    "jevgate.context_cli",
    "jevgate.calibrate",
    "jevgate.linear",
)


def add_common(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Flags every gate subcommand shares."""
    parser.add_argument("--run-dir", metavar="DIR", help="run directory (reusing one starts the next round)")
    parser.add_argument("--json", action="store_true", help="print the JSON report instead of Markdown")
    parser.add_argument("--no-ai", action="store_true", help="never call the API; Jev readings are unknown")
    parser.add_argument("--no-cache", action="store_true", help="bypass the request cache")
    parser.add_argument("--threshold", action="append", metavar="GATE=P", default=[],
                        help="override a gate or family threshold (repeatable)")
    parser.add_argument("--config", metavar="F", help="config file applied after <repo>/jevgate.json")
    parser.add_argument("--context-dir", metavar="DIR", help="folder of context notes (overrides config)")
    parser.add_argument("--all-items", action="store_true", help="lift the per-area item cap")
    return parser


def load_config(args: Any, repo: Path | None = None) -> Config:
    """Defaults ← <repo>/jevgate.json ← --config ← flags."""
    repo = repo if repo is not None else Path(getattr(args, "repo", None) or ".")
    explicit = getattr(args, "config", None)
    cfg = load_config_files(repo, Path(explicit) if explicit else None)
    return apply_cli(cfg, args)


def make_run(args: Any, gate: str) -> Run:
    """The run directory named by ``--run-dir``, else a fresh ``.jevgate/runs/<id>``."""
    run_dir = getattr(args, "run_dir", None)
    if run_dir:
        return Run.at(Path(run_dir))
    return Run(DEFAULT_ROOT, gate=gate)


def make_client(args: Any, cfg: Config, run: Run | None) -> TypeSafeClient:
    """A client honouring --no-ai, --no-cache and the run's audit log."""
    return TypeSafeClient(
        enabled=not getattr(args, "no_ai", False),
        cache_dir=run.cache_dir if run is not None else None,
        audit_path=run.audit_path if run is not None else None,
        workers=cfg.workers,
        use_cache=not getattr(args, "no_cache", False),
    )


def emit(report: Report, args: Any) -> int:
    """Print the report (Markdown, or JSON with --json) and return its exit code."""
    if getattr(args, "json", False):
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    else:
        print(report.to_markdown(), end="")
    return int(report.exit_code)


# -- report subcommand -------------------------------------------------------


def cmd_report(args: argparse.Namespace) -> int:
    run = Run.at(Path(args.run_dir))
    rounds = run.rounds()
    if not rounds:
        raise FileNotFoundError(f"no rounds in {run.dir}")
    number = args.round if args.round is not None else rounds[-1]
    json_path, md_path = run.round_paths(number)
    if not json_path.is_file():
        raise FileNotFoundError(f"{json_path} does not exist (rounds: {', '.join(map(str, rounds))})")
    if args.json:
        print(json_path.read_text(encoding="utf-8"), end="")
    elif md_path.is_file():
        print(md_path.read_text(encoding="utf-8"), end="")
    else:
        print(run.load(number).to_markdown(), end="")
    return EXIT_OK


def register_report(subparsers: Any) -> None:
    parser = subparsers.add_parser("report", help="print a stored report")
    parser.add_argument("run_dir", metavar="<run-dir>")
    parser.add_argument("--round", type=int, metavar="N", help="round number (default: latest)")
    parser.add_argument("--json", action="store_true", help="print the JSON report")
    parser.set_defaults(func=cmd_report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jevgate", description="Jev-backed validation gates for tickets and deliveries.")
    parser.add_argument("--version", action="version", version=f"jevgate {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    for name in REGISTRY:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        register = getattr(module, "register", None)
        if register is not None:
            register(subparsers)
    register_report(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return EXIT_ERROR
    try:
        return int(func(args))
    except (ClientError, ConfigError, FileNotFoundError) as error:
        print(f"jevgate: error: {error}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
