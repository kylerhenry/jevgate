"""Ticket input: loading, validation, normalisation and the fragments the gate sends.

A ticket is ``{title, why, what, acceptance: [str], context_bullets: [str],
prior_answers: [{id?, question, answer}], labels?, estimate?, linear?}``. The
Markdown form is Kyle's template (:data:`TEMPLATE_MD`); the JSON form is the
same dict (:data:`TEMPLATE_JSON`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..markdown import parse_ticket

REQUIRED = ("title", "why", "what", "acceptance")
LIST_FIELDS = ("acceptance", "context_bullets", "labels")

_BULLET_PREFIX = re.compile(r"^(?:[-*•]\s*|\d+[.)]\s*|\[[ xX]\]\s*)+")
_PATH_RE = re.compile(r"(?<![\w/`])(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]*[A-Za-z0-9_](?:\.[A-Za-z0-9]+)?")
_BACKTICK_RE = re.compile(r"`([^`\n]{1,120})`")
_MODULE_RE = re.compile(r"\b(?:[a-z_][a-z0-9_]+\.){1,}[a-z_][a-z0-9_]+\b")

TEMPLATE_MD = """# <Title: the change in one line>

## Why

<Who is affected and what goes wrong or is missing today. One short paragraph.>

## What

<The approach: which components change, which interfaces, how data flows.
Name paths and components as they appear in the context notes.>

### Behaviour

<What the user or caller observes after the change. Plain sentences.>

## Acceptance

- <An observable outcome a test, command or demonstration can pass or fail.>
- <One outcome per bullet.>

## Context

- <A fact the draft relies on, as stated in the notes or the code: path, component, decision.>

## Prior answers

<!-- Answers a human gave to clarifying questions, one bullet each, written as
"dash, space, (bank id), bold question, answer", for example:
    (B03) **How will it be verified: which test, command or observation?** With pytest tests/test_importer.py.
Delete this comment once a real answer is added. -->
"""

TEMPLATE_JSON = {
    "title": "",
    "why": "",
    "what": "",
    "acceptance": [],
    "context_bullets": [],
    "prior_answers": [],
    "labels": [],
    "estimate": None,
}


def bullet_key(text: str) -> str:
    """Comparison key for a bullet: markers stripped, lower case, whitespace
    collapsed, trailing punctuation dropped."""
    body = _BULLET_PREFIX.sub("", str(text).strip())
    body = re.sub(r"\s+", " ", body).strip().lower()
    return body.rstrip(".;:!")


def dedupe_bullets(bullets: Any) -> list[str]:
    """Drop empty and repeated bullets (by :func:`bullet_key`), keeping order."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in bullets or []:
        text = re.sub(r"\s+", " ", str(raw)).strip()
        key = bullet_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def load_ticket(path_or_text: str | Path) -> dict:
    """Load a draft from a ``.md`` or ``.json`` path, or parse Markdown text.

    The result carries ``source``: the path, or ``"text"`` for inline Markdown.
    """
    candidate = Path(str(path_or_text))
    is_path = False
    try:
        is_path = len(str(path_or_text)) < 4096 and candidate.is_file()
    except (OSError, ValueError):
        is_path = False
    if is_path:
        raw = candidate.read_text(encoding="utf-8", errors="replace")
        if candidate.suffix.lower() == ".json":
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError(f"{candidate}: a ticket JSON file must hold an object")
            ticket = dict(data)
            if "context_bullets" not in ticket and "context" in ticket:
                ticket["context_bullets"] = ticket.pop("context")
        else:
            ticket = parse_ticket(raw)
        ticket["source"] = str(candidate)
        return ticket
    if "\n" not in str(path_or_text) and str(path_or_text).endswith((".md", ".json")):
        raise FileNotFoundError(f"ticket draft not found: {path_or_text}")
    ticket = parse_ticket(str(path_or_text))
    ticket["source"] = "text"
    return ticket


def _problems(ticket: Any) -> list[tuple[str, str]]:
    """``(field, message)`` pairs for what is wrong with a ticket's shape."""
    if not isinstance(ticket, dict):
        return [("ticket", "the ticket must be an object")]
    found: list[tuple[str, str]] = []
    for field in ("title", "why", "what"):
        value = ticket.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            found.append((field, f"{field} is missing"))
        elif not isinstance(value, str):
            found.append((field, f"{field} must be text"))
    acceptance = ticket.get("acceptance")
    if acceptance is None or (isinstance(acceptance, list) and not dedupe_bullets(acceptance)):
        found.append(("acceptance", "acceptance has no bullets"))
    elif not isinstance(acceptance, list):
        found.append(("acceptance", "acceptance must be a list of bullets"))
    for field in ("context_bullets", "labels"):
        value = ticket.get(field)
        if value is not None and not isinstance(value, list):
            found.append((field, f"{field} must be a list"))
    prior = ticket.get("prior_answers")
    if prior is not None and not isinstance(prior, list):
        found.append(("prior_answers", "prior_answers must be a list of {id?, question, answer}"))
    elif isinstance(prior, list) and any(not isinstance(p, dict) for p in prior):
        found.append(("prior_answers", "every prior answer must be an object with question and answer"))
    return found


def validate_ticket(ticket: Any) -> list[str]:
    """Plain-English problems with the ticket's shape; empty when it is usable."""
    return [message for _, message in _problems(ticket)]


def schema_problems(ticket: Any) -> list[tuple[str, str]]:
    """Like :func:`validate_ticket` but keeps the field each problem is about."""
    return _problems(ticket)


def _prior(entry: Any) -> dict:
    if not isinstance(entry, dict):
        return {"question": str(entry).strip(), "answer": ""}
    out = {"question": str(entry.get("question") or "").strip(), "answer": str(entry.get("answer") or "").strip()}
    if entry.get("id"):
        out = {"id": str(entry["id"]).strip().upper(), **out}
    return out


def normalise_ticket(ticket: dict) -> dict:
    """A copy with stripped text, deduped bullets and every optional field present."""
    out = dict(ticket or {})
    for field in ("title", "why", "what"):
        out[field] = re.sub(r"[ \t]+\n", "\n", str(out.get(field) or "")).strip()
    out["acceptance"] = dedupe_bullets(out.get("acceptance") if isinstance(out.get("acceptance"), list) else [])
    context = out.get("context_bullets") if isinstance(out.get("context_bullets"), list) else []
    out["context_bullets"] = dedupe_bullets(context)
    out["prior_answers"] = [_prior(p) for p in (out.get("prior_answers") or []) if p]
    labels = out.get("labels")
    out["labels"] = [str(l).strip() for l in labels if str(l).strip()] if isinstance(labels, list) else []
    estimate = out.get("estimate")
    out["estimate"] = estimate if isinstance(estimate, (int, str)) and not isinstance(estimate, bool) else None
    return out


def ticket_query_text(ticket: dict) -> str:
    """Title + What + acceptance: the text lexical selection is run against."""
    parts = [str(ticket.get("title") or ""), str(ticket.get("what") or "")]
    parts += [str(b) for b in ticket.get("acceptance") or []]
    return "\n".join(p for p in parts if p)


def draft_state(ticket: dict) -> dict:
    """The ``draft`` fragment every request carries."""
    return {
        "title": ticket.get("title") or "",
        "why": ticket.get("why") or "",
        "what": ticket.get("what") or "",
        "acceptance": list(ticket.get("acceptance") or []),
        "context": list(ticket.get("context_bullets") or []),
        "labels": list(ticket.get("labels") or []),
        "estimate": ticket.get("estimate"),
    }


def stated_locations(ticket: dict) -> list[str]:
    """Paths, backticked identifiers and dotted module names the What mentions."""
    text = str(ticket.get("what") or "")
    found: list[str] = []
    for match in _BACKTICK_RE.finditer(text):
        found.append(match.group(1).strip())
    plain = _BACKTICK_RE.sub(" ", text)
    found += [m.group(0) for m in _PATH_RE.finditer(plain)]
    found += [m.group(0) for m in _MODULE_RE.finditer(plain) if not m.group(0).endswith(".")]
    seen: set[str] = set()
    out: list[str] = []
    for token in found:
        token = token.strip().rstrip(".,;:")
        if token and token not in seen and not token.startswith("http"):
            seen.add(token)
            out.append(token)
    return out


__all__ = [
    "TEMPLATE_MD", "TEMPLATE_JSON", "REQUIRED", "load_ticket", "validate_ticket", "schema_problems",
    "normalise_ticket", "ticket_query_text", "draft_state", "stated_locations", "dedupe_bullets", "bullet_key",
]
