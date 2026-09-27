"""Shape checks and normalisation for the delivery input.

The delivery input is::

    {ticket: {title, why, what, acceptance: [str]},
     diff: {base?, head?, text},
     files_after: [{path, range?, text}],
     tests: [{path, text, sha256}]}

:func:`validate_delivery` lists what is wrong with a document in plain words;
:func:`normalise_delivery` fills defaults, computes missing test digests and
dedupes acceptance bullets.  Validate first: normalisation assumes the shape
is roughly right and only tolerates missing keys.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BULLET_PREFIX = re.compile(r"^(?:[-*•]\s*|\d+[.)]\s*|\[[ xX]\]\s*)+")


def sha256_text(text: str) -> str:
    """Hex SHA-256 of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def bullet_key(text: str) -> str:
    """The comparison key for an acceptance bullet: markers stripped, lower
    case, whitespace collapsed, trailing punctuation dropped."""
    body = _BULLET_PREFIX.sub("", text.strip())
    body = re.sub(r"\s+", " ", body).strip().lower()
    return body.rstrip(".;:!")


def dedupe_bullets(bullets: list[Any]) -> list[str]:
    """Drop empty and repeated bullets (by :func:`bullet_key`), keeping order
    and the first spelling of each (list markers stripped)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in bullets:
        if not isinstance(item, str):
            continue
        key = bullet_key(item)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(_BULLET_PREFIX.sub("", item.strip()))
    return out


def validate_delivery(d: Any) -> list[str]:
    """Return human-readable problems with a delivery document; empty when valid."""
    if not isinstance(d, dict):
        return ["delivery input must be a JSON object"]
    problems: list[str] = []
    _check_ticket(d.get("ticket"), problems)
    _check_diff(d.get("diff"), problems)
    _check_files_after(d.get("files_after"), problems)
    _check_tests(d.get("tests"), problems)
    return problems


def _check_ticket(ticket: Any, problems: list[str]) -> None:
    if not isinstance(ticket, dict):
        problems.append("ticket: missing or not an object")
        return
    title = ticket.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append("ticket.title: missing or empty")
    for key in ("why", "what"):
        value = ticket.get(key)
        if value is not None and not isinstance(value, str):
            problems.append(f"ticket.{key}: must be a string")
    acceptance = ticket.get("acceptance")
    if acceptance is None:
        return
    if not isinstance(acceptance, list):
        problems.append("ticket.acceptance: must be a list of strings")
        return
    for i, item in enumerate(acceptance):
        if not isinstance(item, str):
            problems.append(f"ticket.acceptance[{i}]: must be a string")


def _check_diff(diff: Any, problems: list[str]) -> None:
    if not isinstance(diff, dict):
        problems.append("diff: missing or not an object")
        return
    if not isinstance(diff.get("text"), str):
        problems.append("diff.text: missing or not a string")
    for key in ("base", "head"):
        value = diff.get(key)
        if value is not None and not isinstance(value, str):
            problems.append(f"diff.{key}: must be a string when given")


def _check_range(value: Any, where: str, problems: list[str]) -> None:
    if value is None:
        return
    ok = (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
        and 1 <= value[0] <= value[1]
    )
    if not ok:
        problems.append(f"{where}.range: must be [start, end] with 1 <= start <= end")


def _check_files_after(files: Any, problems: list[str]) -> None:
    if files is None:
        return
    if not isinstance(files, list):
        problems.append("files_after: must be a list")
        return
    for i, item in enumerate(files):
        where = f"files_after[{i}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: must be an object")
            continue
        if not isinstance(item.get("path"), str) or not item["path"]:
            problems.append(f"{where}.path: missing or empty")
        if not isinstance(item.get("text"), str):
            problems.append(f"{where}.text: missing or not a string")
        _check_range(item.get("range"), where, problems)


def _check_tests(tests: Any, problems: list[str]) -> None:
    if tests is None:
        return
    if not isinstance(tests, list):
        problems.append("tests: must be a list")
        return
    for i, item in enumerate(tests):
        where = f"tests[{i}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: must be an object")
            continue
        if not isinstance(item.get("path"), str) or not item["path"]:
            problems.append(f"{where}.path: missing or empty")
        text = item.get("text")
        if not isinstance(text, str):
            problems.append(f"{where}.text: missing or not a string")
            continue
        digest = item.get("sha256")
        if digest is None:
            continue
        if not isinstance(digest, str) or not _HEX64.match(digest):
            problems.append(f"{where}.sha256: must be 64 hex characters")
        elif digest != sha256_text(text):
            problems.append(f"{where}.sha256: does not match the text")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _range(value: Any) -> list[int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [int(value[0]), int(value[1])]
    return None


def normalise_delivery(d: dict) -> dict:
    """Return a copy of ``d`` with defaults filled: empty ``why``/``what``,
    deduped ``acceptance``, ``diff.base``/``head`` as ``None`` when absent,
    ``files_after`` ranges as ``[start, end]`` or ``None``, and a ``sha256``
    computed for every test log that lacks one.  Extra keys are kept."""
    ticket = dict(d.get("ticket") or {})
    ticket.update(
        title=_text(ticket.get("title")),
        why=_text(ticket.get("why")),
        what=_text(ticket.get("what")),
        acceptance=dedupe_bullets(list(ticket.get("acceptance") or [])),
    )
    diff = dict(d.get("diff") or {})
    diff.update(
        base=diff.get("base") or None,
        head=diff.get("head") or None,
        text=diff.get("text") if isinstance(diff.get("text"), str) else "",
    )
    files_after = [
        {"path": str(item.get("path", "")), "range": _range(item.get("range")), "text": item.get("text") or ""}
        for item in (d.get("files_after") or [])
        if isinstance(item, dict)
    ]
    tests = []
    for item in d.get("tests") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text") if isinstance(item.get("text"), str) else ""
        tests.append({"path": str(item.get("path", "")), "text": text, "sha256": item.get("sha256") or sha256_text(text)})
    out = dict(d)
    out.update(ticket=ticket, diff=diff, files_after=files_after, tests=tests)
    return out
