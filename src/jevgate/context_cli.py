"""``jevgate context init`` and ``jevgate context show``.

``init`` writes a tagged skeleton pack for a repository; ``show`` prints what
:class:`~jevgate.context.ContextPack` would send per area and, with ``--why``,
which resolution rule assigned every note and item. Both return an exit code
(0 ok, 4 error) and never call the network.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

from . import markdown as md
from .context import AREAS, ContextPack, slug
from .textstats import tokens

EXIT_OK = 0
EXIT_ERROR = 4

SEED_FILES = ("CLAUDE.md", "CONTRIBUTING.md", "AGENTS.md")
_CONSTRAINT_WORDS = (
    "never",
    "must not",
    "do not",
    "don't",
    "forbidden",
    "only ",
    "no network",
    "stdlib",
    "python 3",
    "require",
    "security",
    "secret",
    "performance",
    "timeout",
    "memory",
    "budget",
    "limit",
)
_SKIP_MODULES = {"setup", "conftest", "__main__", "test", "tests", "docs", "build", "dist", "node_modules", "venv"}
_CODE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".go", ".rs", ".rb", ".java", ".kt", ".c", ".cc", ".cpp", ".h", ".sh"}
_LIST_AREAS = ("components", "decisions", "constraints", "conventions")


def register(subparsers) -> None:
    """Attach the ``context`` command group to an argparse ``subparsers`` object."""
    parser = subparsers.add_parser("context", help="build and inspect the context pack")
    sub = parser.add_subparsers(dest="context_command", metavar="<init|show>")

    init = sub.add_parser("init", help="write tagged skeleton notes for a repository")
    init.add_argument("--repo", default=".", help="repository to scan (default: .)")
    init.add_argument("--out", default="docs/context", help="output folder; a relative path is under --repo (default: docs/context)")
    init.add_argument("--project", default=None, help="scope every note with #jevgate/project/NAME")
    init.add_argument("--force", action="store_true", help="overwrite existing notes")
    init.set_defaults(func=cmd_init)

    show = sub.add_parser("show", help="print what would be sent per area")
    show.add_argument("--context-dir", default=None, help="pack folder (default: context.dir from jevgate.json, else docs/context)")
    show.add_argument("--context-json", default=None, help="a prebuilt pack in the from_dict shape instead of a folder")
    show.add_argument("--config", default=None, help="jevgate.json to read context settings from (default: ./jevgate.json if present)")
    show.add_argument("--project", default=None, help="load notes scoped to this project")
    show.add_argument("--area", default=None, help="only this area")
    show.add_argument("--draft", default=None, help="ticket draft used as the selection query")
    show.add_argument("--why", action="store_true", help="print which rule resolved each note and item")
    show.add_argument("--json", action="store_true", help="machine-readable output")
    show.set_defaults(func=cmd_show)

    parser.set_defaults(func=lambda args: (parser.print_help(), EXIT_ERROR)[1])


# --------------------------------------------------------------------------- #
# context init
# --------------------------------------------------------------------------- #


def _docstring_first_line(path: Path) -> str:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError, OSError):
        return ""
    doc = ast.get_docstring(tree) or ""
    return doc.strip().splitlines()[0].strip() if doc.strip() else ""


def _python_units(folder: Path, prefix: str, descend: bool) -> list[tuple[str, str, str]]:
    """``(name, path, provides)`` for packages and modules directly under ``folder``."""
    units: list[tuple[str, str, str]] = []
    if not folder.is_dir():
        return units
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith((".", "_")) or entry.name in _SKIP_MODULES or entry.name.startswith("test"):
            continue
        rel = f"{prefix}{entry.name}"
        if entry.is_dir() and (entry / "__init__.py").exists():
            units.append((entry.name, rel, _docstring_first_line(entry / "__init__.py")))
            if descend:
                for name, sub_rel, provides in _python_units(entry, rel + "/", False):
                    units.append((f"{entry.name}/{name}", sub_rel, provides))
        elif entry.is_dir() and prefix and not entry.name.endswith((".egg-info", ".dist-info")):
            if any(child.suffix in _CODE_SUFFIXES for child in entry.iterdir() if child.is_file()):
                units.append((entry.name, rel, ""))
        elif entry.is_file() and entry.suffix == ".py" and entry.stem not in _SKIP_MODULES:
            units.append((entry.stem, rel, _docstring_first_line(entry)))
    return units


def find_components(repo: Path) -> list[tuple[str, str, str]]:
    """Top-level packages/modules under ``src/``, else the repo root, else ``lib/``."""
    for base, prefix in ((repo / "src", "src/"), (repo, ""), (repo / "lib", "lib/")):
        units = _python_units(base, prefix, descend=True)
        if units:
            return units
    return []


def seed_bullets(repo: Path) -> tuple[list[str], list[str], list[str]]:
    """Split the bullets of CLAUDE.md/CONTRIBUTING.md/AGENTS.md into (constraints, conventions, sources)."""
    constraints: list[str] = []
    conventions: list[str] = []
    sources: list[str] = []
    for name in SEED_FILES:
        path = repo / name
        if not path.is_file():
            continue
        sources.append(name)
        _, body, _ = md.parse_frontmatter_ex(path.read_text(encoding="utf-8", errors="replace"))
        for bullet in md.bullets(body):
            if len(bullet) > 300 or len(bullet) < 12:
                continue
            lowered = bullet.lower()
            (constraints if any(w in lowered for w in _CONSTRAINT_WORDS) else conventions).append(bullet)
            if len(constraints) + len(conventions) >= 40:
                break
    return constraints, conventions, sources


def _frontmatter(tags: list[str], **fields: object) -> str:
    lines = ["---", f"tags: [{', '.join(tags)}]"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
        else:
            text = str(value).replace('"', "'")
            lines.append(f'{key}: "{text}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def skeleton_files(repo: Path, project: str | None) -> dict[str, str]:
    """The relative path -> content map ``context init`` writes."""
    scope = [f"jevgate/project/{project}"] if project else []
    name = project or repo.resolve().name
    files: dict[str, str] = {}

    files["architecture.md"] = _frontmatter(["jevgate/architecture", *scope]) + (
        f"# {name} architecture\n\n"
        "Replace this paragraph with how the system is put together: the layers, where each kind of logic lives, "
        "and the extension points a change should use. Link component notes with wikilinks.\n\n"
        "## Layers\n\n"
        "### Interface layer #jevgate/layer\n\nHandlers, CLI and rendering: parses input, calls services, formats output. (replace me)\n\n"
        "### Service layer #jevgate/layer\n\nUse cases and validation. (replace me)\n\n"
        "### Storage layer #jevgate/layer\n\nPersistence; the only layer that knows the store. (replace me)\n\n"
        "## Rules\n\n"
        "- Interface code never calls the store directly; it goes through a service. (replace me) #jevgate/rule\n"
        "- Cross-cutting side effects go through the existing hook or event mechanism, not inline calls. (replace me) #jevgate/rule\n"
    )

    components = find_components(repo)
    for comp_name, path, provides in components:
        note_name = slug(comp_name)
        files[f"components/{note_name}.md"] = _frontmatter(
            ["jevgate/component", *scope],
            path=path,
            provides=provides or "(replace me: one sentence on what this component provides)",
            interface="(replace me: the public functions or commands)",
            aliases=[],
        ) + (
            f"# {comp_name}\n\n"
            f"Source: `{path}`.\n\n"
            "## Provides\n\n"
            f"{provides or '(replace me)'}\n\n"
            "## Interface\n\n(replace me)\n"
        )
    if not components:
        files["components/example.md"] = _frontmatter(
            ["jevgate/component", *scope], path="src/example.py", provides="(replace me)", interface="(replace me)", aliases=[]
        ) + "# example\n\nNo packages or modules were found under src/, the repo root or lib/; describe your components by hand.\n"

    files["decisions/0001-template.md"] = _frontmatter(["jevgate/decision", *scope], status="proposed") + (
        "# ADR 0001: (replace me with the decision title)\n\n"
        "## Context\n\n(what forced the decision)\n\n"
        "## Decision\n\n(what was decided, in one paragraph)\n\n"
        "## Alternatives\n\n- (rejected option 1 and why)\n- (rejected option 2 and why)\n\n"
        "## Consequences\n\n(what becomes easier or harder)\n"
    )

    constraints, conventions, sources = seed_bullets(repo)
    seeded = f"Seeded from {', '.join(sources)}; edit freely." if sources else "Nothing to seed from (no CLAUDE.md, CONTRIBUTING.md or AGENTS.md)."
    files["constraints.md"] = _frontmatter(["jevgate/constraints", *scope]) + (
        "# Constraints\n\n"
        f"Non-functional rules: performance, security, environment, forbidden dependencies. {seeded}\n\n"
        "## Constraints\n\n"
        + "".join(f"- {b} #jevgate/constraint\n" for b in constraints)
        + ("" if constraints else "- (replace me: e.g. stdlib only at runtime) #jevgate/constraint\n")
    )
    files["conventions.md"] = _frontmatter(["jevgate/conventions", *scope]) + (
        "# Conventions\n\n"
        f"Code style, naming, error handling, logging, testing. Add `#jevgate/applies/py` to scope a bullet to one file type. {seeded}\n\n"
        "## Conventions\n\n"
        + "".join(f"- {b} #jevgate/convention\n" for b in conventions)
        + ("" if conventions else "- (replace me: e.g. type hints and docstrings on public functions) #jevgate/convention #jevgate/applies/py\n")
    )
    files["data.md"] = _frontmatter(["jevgate/data", *scope]) + "# Data\n\nData model, stores, schemas, ownership and migration policy. (replace me)\n"
    files["interfaces.md"] = _frontmatter(["jevgate/interfaces", *scope]) + "# Interfaces\n\nExternal and public contracts: APIs, CLI surfaces, message topics, compatibility rules. (replace me)\n"
    files["glossary.md"] = _frontmatter(["jevgate/glossary", *scope]) + "# Glossary\n\n- **term**: definition (replace me)\n"
    return files


def cmd_init(args: argparse.Namespace) -> int:
    """Write the skeleton pack; existing files are kept unless ``--force``."""
    repo = Path(args.repo).expanduser()
    if not repo.is_dir():
        print(f"error: repo not found: {repo}", file=sys.stderr)
        return EXIT_ERROR
    out = Path(args.out).expanduser()
    if not out.is_absolute():
        out = repo / out
    written = 0
    skipped: list[str] = []
    for rel, content in skeleton_files(repo, args.project).items():
        target = out / rel
        if target.exists() and not getattr(args, "force", False):
            skipped.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written += 1
        print(f"wrote {target}")
    for rel in skipped:
        print(f"kept  {out / rel} (exists; use --force to overwrite)")
    print(f"{written} note(s) written to {out}; fill the '(replace me)' markers, then run: jevgate context show --context-dir {out} --why")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# context show
# --------------------------------------------------------------------------- #


def _read_context_config(path: str | None) -> dict:
    candidate = Path(path) if path else Path("jevgate.json")
    if not candidate.is_file():
        return {}
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"warning: could not read {candidate}: {exc}", file=sys.stderr)
        return {}
    return data.get("context") or {} if isinstance(data, dict) else {}


def load_pack(args: argparse.Namespace) -> ContextPack:
    """Build the pack from ``--context-json``, ``--context-dir`` or the config."""
    cfg = _read_context_config(getattr(args, "config", None))
    kwargs = {
        "project": getattr(args, "project", None) or cfg.get("project"),
        "follow_links": int(cfg.get("follow_links", 1)),
        "context_budget": int(cfg.get("context_budget", 8000)),
        "max_items": int(cfg.get("max_items", 16)),
    }
    if getattr(args, "context_json", None):
        data = json.loads(Path(args.context_json).read_text(encoding="utf-8"))
        return ContextPack.from_dict(data, **kwargs)
    directory = getattr(args, "context_dir", None) or cfg.get("dir") or "docs/context"
    return ContextPack.load(Path(directory).expanduser(), areas=cfg.get("areas") or None, **kwargs)


def draft_query(path: str | None) -> str:
    """The selection query built from a ticket draft (title, why, what, bullets)."""
    if not path:
        return ""
    ticket = md.parse_ticket(Path(path).read_text(encoding="utf-8", errors="replace"))
    parts = [ticket["title"], ticket["why"], ticket["what"], *ticket["acceptance"], *ticket["context_bullets"]]
    return "\n".join(p for p in parts if p)


def cmd_show(args: argparse.Namespace) -> int:
    """Print the notes and items per area, their token counts and (``--why``) the resolution."""
    try:
        pack = load_pack(args)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    query = draft_query(getattr(args, "draft", None))
    areas = [args.area] if getattr(args, "area", None) else pack.areas()
    fragment = pack.slice(set(areas), query)
    report = {"areas": {}, "warnings": pack.warnings}
    for area in areas:
        notes = pack.notes(area)
        items = pack.items(area)
        sent = pack.last_evidence.get(area, {})
        report["areas"][area] = {
            "present": pack.has(area),
            "notes": [{"path": n.path, "title": n.title, "tokens": tokens(n.body)} for n in notes],
            "items": [{"id": i.id, "kind": i.kind, "text": i.text, "note": i.note, "line": i.line, "meta": i.meta, "sent": i.id in sent.get("items", [])} for i in items],
            "sent": sent,
            "slice_tokens": tokens(json.dumps(fragment.get(area, {}))),
        }
    if getattr(args, "why", False):
        report["explain"] = pack.explain()
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
        return EXIT_OK

    for area, info in report["areas"].items():
        if not info["present"]:
            print(f"{area}: missing (questions needing it are skipped; add a note tagged #jevgate/{area})")
            continue
        if area == "general":
            print(f"general: {len(info['notes'])} note(s) (index only; never sent to a question)")
            for note in info["notes"]:
                print(f"  note  {note['path']}  {note['tokens']} tokens")
            continue
        print(f"{area}: {len(info['notes'])} note(s), {len(info['items'])} item(s); slice {info['slice_tokens']} tokens" + (" (truncated)" if info["sent"].get("truncated") else ""))
        for note in info["notes"]:
            if area in _LIST_AREAS:
                marker = "items only"
            else:
                marker = "sent" if note["path"] in info["sent"].get("notes", []) else "not sent (over budget)"
            print(f"  note  {note['path']}  {note['tokens']} tokens  [{marker}]")
        for item in info["items"]:
            flag = "sent" if item["sent"] else "not sent"
            text = item["text"] if len(item["text"]) <= 90 else item["text"][:87] + "..."
            print(f"  {item['id']:<6} {text}  [{item['note']}:{item['line']}] [{flag}]")
    if getattr(args, "why", False):
        print("\nresolution:")
        for entry in report["explain"]:
            status = f"  skipped: {entry['reason']}" if entry["ignored"] else ""
            print(f"  {entry['path']}  area={entry['area']} via {entry['resolved_by']}  kind={entry['kind']} via {entry['kind_by']}  tags={','.join(entry['tags']) or '-'}{status}")
            for item in entry["items"]:
                print(f"    {item['id']:<6} {item['kind']} line {item['line']} via {item['resolved_by']}")
    if report["warnings"]:
        print("\nwarnings:")
        for warning in report["warnings"]:
            print(f"  - {warning}")
    return EXIT_OK
