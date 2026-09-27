"""Optional Linear adapter: fetch an issue as a ticket dict, create one from a draft.

GraphQL over ``urllib`` only. The personal API key goes in the ``Authorization``
header raw (no ``Bearer``), as Linear requires for personal keys, and is never
echoed in an error. Redirects are refused so the key only ever reaches
``ENDPOINT``. 429 and 5xx responses are retried with 1s/2s/4s backoff.

Importing this module never fails (stdlib only), so :mod:`jevgate.cli` can
import :class:`LinearError` at module top and map it to exit code 4.

CLI::

    jevgate linear get DIY-17 [--json]
    jevgate linear create <draft.md|draft.json> --team KEY [--project NAME] [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from . import __version__
from .markdown import parse_ticket, render_ticket

ENDPOINT = "https://api.linear.app/graphql"
KEY_FILE = Path.home() / ".config" / "linear" / "api-key"
# Personal keys look like ``lin_api_...``; OAuth tokens are long opaque strings.
KEY_PATTERN = re.compile(r"lin_api_[A-Za-z0-9]+|[A-Za-z0-9_\-]{20,}")
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
ERROR_BODY_CHARS = 300
ATTEMPTS = 4  # sleeps 1s, 2s, 4s between the four tries

EXIT_OK = 0
EXIT_ERROR = 4


class LinearError(RuntimeError):
    """A Linear request that could not be made or did not succeed. Never carries the key."""


class RedirectRefused(urllib.error.URLError):
    """The endpoint answered with a redirect; following it could leak the key."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so the API key only ever goes to ENDPOINT."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise RedirectRefused(f"Refusing HTTP {code} redirect from the Linear endpoint")


# --------------------------------------------------------------------------- #
# Key and transport
# --------------------------------------------------------------------------- #


def load_linear_key() -> str:
    """Return the Linear key from ``LINEAR_API_KEY`` or ``~/.config/linear/api-key``.

    Validated by shape only. Raises :class:`LinearError` when no key is found or
    the key is malformed; the message never contains the key.
    """
    key = os.environ.get("LINEAR_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        lines = KEY_FILE.read_text(encoding="utf-8").splitlines()
        key = lines[0].strip() if lines else ""
    if not key:
        raise LinearError("No Linear key in LINEAR_API_KEY or ~/.config/linear/api-key")
    if not KEY_PATTERN.fullmatch(key):
        raise LinearError("Linear key is malformed; refusing to send it")
    return key


def _sleep(seconds: float) -> None:
    """Backoff hook; tests patch it to avoid waiting."""
    time.sleep(seconds)


def _open(request: urllib.request.Request, timeout: float) -> bytes:
    """Transport hook: POST the request without following redirects, return the body."""
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        return response.read()


def _error_detail(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8", errors="replace")[:ERROR_BODY_CHARS]
    except Exception:  # pragma: no cover - a body that cannot be read
        return ""


# --------------------------------------------------------------------------- #
# GraphQL documents
# --------------------------------------------------------------------------- #

ISSUE_FIELDS = """
    id identifier title description url branchName
    state { name }
    team { id key }
    labels { nodes { name } }
    project { name }
"""

ISSUE_QUERY = f"""
query JevgateIssue($id: String!) {{
  issue(id: $id) {{ {ISSUE_FIELDS} }}
}}
"""

TEAM_QUERY = """
query JevgateTeam($key: String!) {
  teams(filter: { key: { eq: $key } }) { nodes { id key } }
}
"""

PROJECT_QUERY = """
query JevgateProject($name: String!) {
  projects(filter: { name: { eq: $name } }) {
    nodes { id name teams { nodes { id } } }
  }
}
"""

CREATE_MUTATION = """
mutation JevgateIssueCreate($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier url } }
}
"""

UPDATE_MUTATION = """
mutation JevgateIssueUpdate($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success issue { id identifier url } }
}
"""

COMMENTS_QUERY = """
query JevgateComments($id: String!) {
  issue(id: $id) {
    comments { nodes { body createdAt user { name } } }
  }
}
"""


class Linear:
    """Minimal Linear GraphQL client over ``urllib``."""

    def __init__(self, key: str, *, endpoint: str = ENDPOINT, timeout: float = 30) -> None:
        self.key = key
        self.endpoint = endpoint
        self.timeout = timeout

    # -- transport ---------------------------------------------------------

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        """POST one GraphQL document and return its ``data`` object.

        Retries 429/5xx, connection errors and timeouts with 1s/2s/4s backoff;
        any other HTTP status raises :class:`LinearError` at once with the
        first 300 characters of the body. GraphQL ``errors`` raise
        :class:`LinearError` with their messages joined.
        """
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps({"query": query, "variables": variables or {}}, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": self.key,
                "Content-Type": "application/json",
                "User-Agent": f"jevgate/{__version__}",
            },
        )
        last = "Linear request failed"
        for attempt in range(ATTEMPTS):
            try:
                payload = json.loads(_open(request, self.timeout))
                break
            except urllib.error.HTTPError as error:
                detail = _error_detail(error)
                last = f"Linear HTTP {error.code}: {detail}".rstrip(": ")
                if error.code not in RETRY_STATUS:
                    raise LinearError(last) from None
            except RedirectRefused as error:
                raise LinearError(str(error.reason)) from None
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                reason = getattr(error, "reason", None) or error
                last = f"Linear request failed: {type(error).__name__}: {str(reason)[:ERROR_BODY_CHARS]}"
            if attempt + 1 < ATTEMPTS:
                _sleep(2**attempt)
        else:
            raise LinearError(f"{last} (after {ATTEMPTS} attempts)")
        if not isinstance(payload, dict):
            raise LinearError("Linear returned a non-object response")
        errors = payload.get("errors")
        if errors:
            messages = [str(e.get("message") if isinstance(e, dict) else e) for e in errors]
            raise LinearError("Linear GraphQL: " + "; ".join(messages))
        data = payload.get("data")
        if not isinstance(data, dict):
            raise LinearError("Linear response has no data object")
        return data

    # -- reads --------------------------------------------------------------

    def get_issue(self, identifier: str) -> dict:
        """Fetch one issue by identifier (``DIY-17``) or UUID as a flat dict."""
        node = self.graphql(ISSUE_QUERY, {"id": identifier}).get("issue")
        if not node:
            raise LinearError(f"issue {identifier} not found")
        return _flatten_issue(node)

    def team_id(self, key: str) -> str:
        """Resolve a team key (``DIY``) to its UUID."""
        nodes = _nodes(self.graphql(TEAM_QUERY, {"key": key}).get("teams"))
        for node in nodes:
            if node.get("key") == key and node.get("id"):
                return str(node["id"])
        raise LinearError(f"team {key} not found")

    def project_id(self, name: str, team_id: str | None = None) -> str | None:
        """Resolve a project name to its UUID, preferring one attached to ``team_id``."""
        nodes = _nodes(self.graphql(PROJECT_QUERY, {"name": name}).get("projects"))
        matches = [n for n in nodes if n.get("name") == name and n.get("id")]
        if team_id:
            for node in matches:
                if team_id in {t.get("id") for t in _nodes(node.get("teams"))}:
                    return str(node["id"])
        return str(matches[0]["id"]) if matches else None

    def list_comments(self, issue_id: str) -> list[dict]:
        """Return ``[{body, user, created}]`` for an issue (identifier or UUID), oldest first as served."""
        issue = self.graphql(COMMENTS_QUERY, {"id": issue_id}).get("issue")
        if not issue:
            raise LinearError(f"issue {issue_id} not found")
        out: list[dict] = []
        for node in _nodes(issue.get("comments")):
            user = node.get("user") or {}
            out.append(
                {
                    "body": node.get("body") or "",
                    "user": user.get("name") if isinstance(user, dict) else None,
                    "created": node.get("createdAt"),
                }
            )
        return out

    # -- writes -------------------------------------------------------------

    def create_issue(
        self,
        team_id: str,
        title: str,
        description: str,
        *,
        project_id: str | None = None,
        label_ids: list[str] | None = None,
    ) -> dict:
        """Create an issue and return ``{identifier, url, id}``."""
        payload: dict[str, Any] = {"teamId": team_id, "title": title, "description": description}
        if project_id:
            payload["projectId"] = project_id
        if label_ids:
            payload["labelIds"] = list(label_ids)
        result = self.graphql(CREATE_MUTATION, {"input": payload}).get("issueCreate") or {}
        return _mutation_result(result, "issueCreate")

    def update_issue(self, id: str, *, title: str | None = None, description: str | None = None) -> dict:
        """Update an issue's title and/or description; returns ``{identifier, url, id}``."""
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if not payload:
            raise LinearError("update_issue: nothing to update")
        result = self.graphql(UPDATE_MUTATION, {"id": id, "input": payload}).get("issueUpdate") or {}
        return _mutation_result(result, "issueUpdate")


def _nodes(connection: Any) -> list[dict]:
    if isinstance(connection, dict) and isinstance(connection.get("nodes"), list):
        return [n for n in connection["nodes"] if isinstance(n, dict)]
    return []


def _flatten_issue(node: dict) -> dict:
    team = node.get("team") or {}
    state = node.get("state") or {}
    project = node.get("project") or {}
    return {
        "id": node.get("id"),
        "identifier": node.get("identifier"),
        "title": node.get("title") or "",
        "description": node.get("description") or "",
        "url": node.get("url"),
        "team_key": team.get("key"),
        "team_id": team.get("id"),
        "state": state.get("name"),
        "labels": [str(n.get("name")) for n in _nodes(node.get("labels")) if n.get("name")],
        "project": project.get("name") or None,
        "branch": node.get("branchName"),
    }


def _mutation_result(result: dict, name: str) -> dict:
    issue = result.get("issue") or {}
    if not result.get("success") or not issue.get("id"):
        raise LinearError(f"Linear {name} did not succeed")
    return {"identifier": issue.get("identifier"), "url": issue.get("url"), "id": issue.get("id")}


# --------------------------------------------------------------------------- #
# Issue <-> ticket
# --------------------------------------------------------------------------- #


def issue_to_ticket(issue: dict) -> dict:
    """Parse a flat issue (from :meth:`Linear.get_issue`) into the ticket dict shape.

    Adds ``linear: {identifier, url, team, project}``. The description is
    parsed as the body under a ``# <title>`` heading, so Linear's separately
    stored title lands in ``ticket["title"]``.
    """
    title = str(issue.get("title") or "").strip()
    description = str(issue.get("description") or "")
    ticket = parse_ticket(f"# {title}\n\n{description}")
    if not ticket.get("title"):
        ticket["title"] = title
    ticket["linear"] = {
        "identifier": issue.get("identifier"),
        "url": issue.get("url"),
        "team": issue.get("team_key"),
        "project": issue.get("project"),
    }
    return ticket


def acceptance_required(ticket: dict) -> None:
    """Raise :class:`LinearError` unless the ticket has ``## Acceptance`` bullets (delivery gate input)."""
    if not ticket.get("acceptance"):
        ident = (ticket.get("linear") or {}).get("identifier") or ticket.get("title") or "<untitled>"
        raise LinearError(f"issue {ident} has no '## Acceptance' section with bullets")


def ticket_to_description(ticket: dict) -> str:
    """Render a ticket as a Linear description: :func:`render_ticket` minus the ``# Title`` line."""
    lines = render_ticket(ticket).splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines = lines[1:]
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _load_draft(path: Path) -> dict:
    """Read a ticket draft: ``.json`` as the ticket dict, anything else as Markdown."""
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, dict):
            raise LinearError(f"{path}: JSON draft must be an object")
        return data
    return parse_ticket(text)


def cmd_get(args: argparse.Namespace) -> int:
    """``linear get <identifier> [--json]``: print the issue as ticket Markdown or JSON."""
    api = Linear(load_linear_key())
    ticket = issue_to_ticket(api.get_issue(args.identifier))
    if args.json:
        print(json.dumps(ticket, indent=2, ensure_ascii=False))
    else:
        print(render_ticket(ticket), end="")
    return EXIT_OK


def cmd_create(args: argparse.Namespace) -> int:
    """``linear create <draft> --team KEY [--project NAME] [--dry-run] [--json]``."""
    ticket = _load_draft(Path(args.draft))
    meta = ticket.get("linear") if isinstance(ticket.get("linear"), dict) else {}
    team_key = args.team or meta.get("team")
    project_name = args.project or meta.get("project")
    title = str(ticket.get("title") or "").strip()
    if not title:
        raise LinearError(f"{args.draft}: draft has no title")
    if not team_key:
        raise LinearError("no team: pass --team KEY (or set linear.team in a JSON draft)")
    description = ticket_to_description(ticket)
    if args.dry_run:
        if args.json:
            record = {"dry_run": True, "team": team_key, "project": project_name, "title": title, "description": description}
            print(json.dumps(record, indent=2, ensure_ascii=False))
        else:
            print(f"team: {team_key}")
            print(f"project: {project_name or '-'}")
            print(f"title: {title}")
            print()
            print(description, end="")
        return EXIT_OK
    api = Linear(load_linear_key())
    team_id = api.team_id(team_key)
    project_id = None
    if project_name:
        project_id = api.project_id(project_name, team_id)
        if project_id is None:
            raise LinearError(f"project {project_name!r} not found")
    result = api.create_issue(team_id, title, description, project_id=project_id)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"{result['identifier']} {result['url']}")
    return EXIT_OK


def register(subparsers: Any) -> None:
    """Add the ``linear`` subcommand with ``get`` and ``create`` actions."""
    parser = subparsers.add_parser("linear", help="Read or create Linear issues from ticket drafts")
    parser.set_defaults(func=lambda args: (parser.print_help(), EXIT_ERROR)[1])
    actions = parser.add_subparsers(dest="linear_command", metavar="<action>")

    get = actions.add_parser("get", help="Print an issue as ticket Markdown (or JSON)")
    get.add_argument("identifier", help="Issue identifier such as DIY-17")
    get.add_argument("--json", action="store_true", help="Print the ticket dict as JSON")
    get.set_defaults(func=cmd_get)

    create = actions.add_parser("create", help="Create an issue from a ticket draft")
    create.add_argument("draft", help="Ticket draft (.md or .json)")
    create.add_argument("--team", metavar="KEY", help="Team key such as DIY")
    create.add_argument("--project", metavar="NAME", help="Project name to attach the issue to")
    create.add_argument("--dry-run", action="store_true", help="Print what would be sent; needs no key")
    create.add_argument("--json", action="store_true", help="Print the result as JSON")
    create.set_defaults(func=cmd_create)
