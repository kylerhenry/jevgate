"""``jevgate delivery check``: judge a change against its ticket."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..cli import EXIT_ERROR, add_common, emit, load_config, make_client, make_run
from ..config import Config
from ..context import ContextPack
from .evidence import EvidenceError
from .gate import DeliveryError, build_inputs, check


def register(subparsers: Any) -> None:
    """Add the ``delivery`` command group and its ``check`` subcommand."""
    parser = subparsers.add_parser("delivery", help="judge a change against its ticket")
    parser.set_defaults(func=lambda args: _help(parser))
    sub = parser.add_subparsers(dest="delivery_command", metavar="<subcommand>")

    checker = sub.add_parser("check", help="review a diff against a ticket's acceptance bullets")
    source = checker.add_mutually_exclusive_group(required=True)
    source.add_argument("--ticket", metavar="F", help="ticket draft (.md or .json)")
    source.add_argument("--from-linear", dest="from_linear", metavar="ID", help="fetch the ticket from Linear (e.g. DIY-17)")
    checker.add_argument("--repo", metavar="DIR", default=".", help="repository the diff and --files are read from (default: .)")
    diff = checker.add_mutually_exclusive_group(required=True)
    diff.add_argument("--base", metavar="REF", help="git base ref; with --head the merge-base diff, alone the working tree")
    diff.add_argument("--diff-file", dest="diff_file", metavar="F", help="a unified diff to review instead of git")
    checker.add_argument("--head", metavar="REF", help="git head ref (requires --base)")
    checker.add_argument("--test-log", dest="test_log", action="append", default=[], metavar="F",
                         help="verbatim test runner output (repeatable)")
    checker.add_argument("--files", action="append", default=[], metavar="PATH[:START-END]",
                         help="post-change file excerpt for the whole-change question (repeatable)")
    checker.add_argument("--no-tests-ok", dest="no_tests_ok", action="store_true",
                         help="accept a change that is not proven by test output")
    checker.add_argument("--project", metavar="NAME", help="context pack project scope (#jevgate/project/<NAME>)")
    checker.add_argument("--context-json", dest="context_json", metavar="F", help="prebuilt context pack as JSON")
    add_common(checker)
    checker.set_defaults(func=cmd_check, _parser=checker)


def _help(parser: argparse.ArgumentParser) -> int:
    parser.print_help()
    return EXIT_ERROR


def load_delivery_pack(args: Any, cfg: Config, repo: Path) -> ContextPack | None:
    """The pack from ``--context-json``, ``--context-dir`` or config ``context.dir``; None when nothing names one."""
    context = cfg.context or {}
    max_items = cfg.max_items if cfg.max_items is not None else 16
    kwargs = {
        "project": getattr(args, "project", None) or context.get("project"),
        "follow_links": int(context.get("follow_links", 1)),
        "context_budget": int(context.get("context_budget", cfg.context_budget)),
        "max_items": max_items,
    }
    context_json = getattr(args, "context_json", None)
    if context_json:
        path = Path(context_json)
        if not path.is_file():
            raise FileNotFoundError(f"context pack not found: {path}")
        return ContextPack.from_dict(json.loads(path.read_text(encoding="utf-8")), **kwargs)
    explicit = getattr(args, "context_dir", None)
    directory = explicit or context.get("dir")
    if not directory:
        print("jevgate: warning: no context pack (pass --context-dir or set context.dir); "
              "architecture, component and convention questions are skipped", file=sys.stderr)
        return None
    path = Path(directory).expanduser()
    if not explicit and not path.is_absolute():
        path = repo / path
    return ContextPack.load(path, areas=context.get("areas") or None, **kwargs)


def cmd_check(args: argparse.Namespace) -> int:
    """Run the delivery gate and print its report; the exit code is the verdict's."""
    if getattr(args, "head", None) and not getattr(args, "base", None):
        args._parser.error("--head requires --base")
    repo = Path(getattr(args, "repo", None) or ".")
    try:
        cfg = load_config(args, repo)
        run = make_run(args, "delivery")
        client = make_client(args, cfg, run)
        pack = load_delivery_pack(args, cfg, repo)
        inputs = build_inputs(args, cfg)
        report = check(inputs, pack, client, cfg, run)
    except (DeliveryError, EvidenceError, ValueError) as error:
        print(f"jevgate: error: {error}", file=sys.stderr)
        return EXIT_ERROR
    return emit(report, args)


__all__ = ["register", "cmd_check", "load_delivery_pack"]
