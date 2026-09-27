"""``jevgate ticket`` subcommands: ``check``, ``init`` and ``render``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..cli import EXIT_ERROR, EXIT_OK, add_common, emit, load_config, make_client, make_run
from ..context import ContextPack
from ..context_cli import load_pack
from ..linear import Linear, issue_to_ticket, load_linear_key
from ..markdown import render_ticket
from .gate import check
from .schema import TEMPLATE_JSON, TEMPLATE_MD, load_ticket, normalise_ticket, validate_ticket


def _pack_or_none(args: argparse.Namespace) -> ContextPack | None:
    """The pack from the flags or config; None (with a warning) when nothing is configured."""
    try:
        return load_pack(args)
    except FileNotFoundError as error:
        if getattr(args, "context_dir", None) or getattr(args, "context_json", None):
            raise
        print(f"warning: {error}; running without a context pack (architecture, reuse and decision questions are skipped)", file=sys.stderr)
        return None


def cmd_check(args: argparse.Namespace) -> int:
    cfg = load_config(args)
    if getattr(args, "from_linear", None):
        issue = Linear(load_linear_key()).get_issue(args.from_linear)
        ticket = issue_to_ticket(issue)
        ticket["source"] = f"linear:{args.from_linear}"
    elif args.draft:
        ticket = load_ticket(args.draft)
    else:
        print("jevgate: error: give a draft path or --from-linear ID", file=sys.stderr)
        return EXIT_ERROR
    pack = _pack_or_none(args)
    run = make_run(args, "ticket")
    client = make_client(args, cfg, run)
    report = check(ticket, pack, client, cfg, run)
    return emit(report, args)


def cmd_init(args: argparse.Namespace) -> int:
    out = Path(args.out or ("ticket.json" if args.json else "ticket.md"))
    if out.exists() and not args.force:
        print(f"jevgate: error: {out} exists (use --force to overwrite)", file=sys.stderr)
        return EXIT_ERROR
    text = json.dumps(TEMPLATE_JSON, indent=2) + "\n" if args.json else TEMPLATE_MD
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}")
    return EXIT_OK


def cmd_render(args: argparse.Namespace) -> int:
    ticket = normalise_ticket(load_ticket(args.draft))
    for problem in validate_ticket(ticket):
        print(f"warning: {problem}", file=sys.stderr)
    text = render_ticket(ticket)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text, end="")
    return EXIT_OK


def register(subparsers: Any) -> None:
    parser = subparsers.add_parser("ticket", help="ticket gate: check a draft, write a template, render for Linear")
    sub = parser.add_subparsers(dest="ticket_command", metavar="<subcommand>")

    check_parser = sub.add_parser("check", help="judge a draft and route the next step")
    check_parser.add_argument("draft", nargs="?", metavar="<draft.md|draft.json>")
    check_parser.add_argument("--from-linear", metavar="ID", help="load the draft from a Linear issue (needs LINEAR_API_KEY)")
    check_parser.add_argument("--project", metavar="NAME", help="context project scope (#jevgate/project/<name>)")
    check_parser.add_argument("--context-json", metavar="F", help="prebuilt pack JSON instead of --context-dir")
    add_common(check_parser)
    check_parser.set_defaults(func=cmd_check)

    init_parser = sub.add_parser("init", help="write the ticket template")
    init_parser.add_argument("--out", metavar="F", help="output path (default ticket.md or ticket.json)")
    init_parser.add_argument("--json", action="store_true", help="write the JSON template")
    init_parser.add_argument("--force", action="store_true", help="overwrite an existing file")
    init_parser.set_defaults(func=cmd_init)

    render_parser = sub.add_parser("render", help="render a draft as Linear-ready Markdown")
    render_parser.add_argument("draft", metavar="<draft.md|draft.json>")
    render_parser.add_argument("--out", metavar="F", help="write here instead of stdout")
    render_parser.set_defaults(func=cmd_render)

    def _help(args: argparse.Namespace) -> int:
        parser.print_help()
        return EXIT_ERROR

    parser.set_defaults(func=_help)


__all__ = ["register", "cmd_check", "cmd_init", "cmd_render"]
