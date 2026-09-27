"""Markdown helpers shared by the ticket pipeline and the context pack.

Everything here is plain text processing on the small Markdown dialect the
gates rely on: a YAML-subset frontmatter block, ``#``-headings, bullet lists,
``[[wikilinks]]`` and inline ``#jevgate/...`` tags. No Markdown renderer is
involved and nothing here touches the network or the file system.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TAG_PREFIX = "jevgate/"

_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
_WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]|#]*?)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_KEY_VALUE_RE = re.compile(r"^([A-Za-z_][\w .-]*?)\s*:(?:\s+(.*))?$")
_DASH_ITEM_RE = re.compile(r"^\s*-\s*(.*)$")
_PRIOR_RE = re.compile(
    r"^(?:\((?P<id>[A-Za-z0-9][\w.-]*)\)\s*)?\*\*(?P<q>.+?)\*\*\s*[:\-–—]?\s*(?P<a>.*)$",
    re.S,
)


class FrontmatterError(ValueError):
    """Raised internally when the frontmatter block is outside the YAML subset."""


# --------------------------------------------------------------------------- #
# Frontmatter
# --------------------------------------------------------------------------- #


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        if value[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner
    return value


def _flow_list(inner: str) -> list[str]:
    items = re.findall(r'\s*("[^"]*"|\'[^\']*\'|[^,]+)', inner)
    return [_unquote(item) for item in items if _unquote(item) != ""]


def _parse_yaml_subset(lines: list[str]) -> dict:
    """Parse ``key: value`` scalars, flow lists and dash lists; raise otherwise."""
    data: dict = {}
    pending: str | None = None  # key whose dash items may follow
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        dash = _DASH_ITEM_RE.match(line)
        if dash and pending is not None:
            value = data[pending]
            if value == "":
                data[pending] = value = []
            if not isinstance(value, list):
                raise FrontmatterError(f"line {number}: unexpected list item")
            value.append(_unquote(dash.group(1)))
            continue
        if line[0] in " \t":
            raise FrontmatterError(f"line {number}: nested mappings are not supported")
        match = _KEY_VALUE_RE.match(line)
        if not match:
            raise FrontmatterError(f"line {number}: expected 'key: value'")
        key, value = match.group(1).strip(), (match.group(2) or "").strip()
        if value.startswith("[") and value.endswith("]"):
            data[key] = _flow_list(value[1:-1])
            pending = None
        elif value.startswith("{") or value in ("|", ">", "|-", ">-"):
            raise FrontmatterError(f"line {number}: block scalars and flow mappings are not supported")
        elif value == "":
            data[key] = ""
            pending = key
        else:
            data[key] = _unquote(value)
            pending = None
    return data


def parse_frontmatter_ex(text: str) -> tuple[dict, str, list[str]]:
    """Split a ``---`` frontmatter block from ``text``.

    Returns ``(data, body, warnings)``. The YAML subset covers ``key: value``
    scalars (quoted or bare; booleans and integers stay strings), flow lists
    ``[a, b]`` and dash lists. Anything else makes the whole block unparsable:
    the result is then ``({}, text, [reason])`` so a note is never lost over a
    frontmatter typo. Values are kept verbatim (no ``#`` comment stripping).
    """
    warnings: list[str] = []
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, warnings
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            end = index
            break
    if end is None:
        warnings.append("frontmatter: opening '---' without a closing '---'")
        return {}, text, warnings
    try:
        data = _parse_yaml_subset(lines[1:end])
    except FrontmatterError as exc:
        warnings.append(f"frontmatter: {exc}")
        return {}, text, warnings
    body = "\n".join(lines[end + 1 :])
    return data, body, warnings


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Like :func:`parse_frontmatter_ex` without the warnings list."""
    data, body, _ = parse_frontmatter_ex(text)
    return data, body


# --------------------------------------------------------------------------- #
# Sections and bullets
# --------------------------------------------------------------------------- #


@dataclass
class Section:
    """One heading with the text below it.

    ``start``/``end`` are 1-based line numbers of the body (``end`` exclusive)
    relative to the text given to :func:`parse_sections_ex`; ``line`` is the
    heading's own line (0 for the preamble before the first heading).
    """

    level: int
    heading: str
    key: str
    line: int
    start: int
    end: int
    text: str


def heading_key(heading: str) -> str:
    """Normalise a heading for dictionary lookups (tags removed, lowercased)."""
    return re.sub(r"\s+", " ", strip_tags(heading)).strip().lower()


def parse_sections_ex(text: str) -> list[Section]:
    """Return every H1-H3 section (plus the preamble) with line numbers.

    A section's text runs until the next heading of the same or a higher
    level; fenced code blocks never open a section.
    """
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []  # (line_no, level, heading)
    in_fence = False
    for number, raw in enumerate(lines, start=1):
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(raw)
        if match and len(match.group(1)) <= 3:
            headings.append((number, len(match.group(1)), match.group(2).strip()))
    sections: list[Section] = []
    first = headings[0][0] if headings else len(lines) + 1
    preamble_end = first
    sections.append(Section(0, "", "", 0, 1, preamble_end, "\n".join(lines[: preamble_end - 1])))
    for index, (line_no, level, heading) in enumerate(headings):
        end = len(lines) + 1
        for next_line, next_level, _ in headings[index + 1 :]:
            if next_level <= level:
                end = next_line
                break
        body = "\n".join(lines[line_no:end - 1])
        sections.append(Section(level, heading, heading_key(heading), line_no, line_no + 1, end, body))
    return sections


def parse_sections(text: str) -> dict[str, str]:
    """Map lowercased H2 headings to their text; H3s as ``"<h2>/<h3>"``.

    Text before the first H2 is stored under ``""``. An H2's text includes its
    H3 sub-sections (heading lines kept), so callers can treat it as prose.
    Duplicate headings are concatenated.
    """
    lines = text.splitlines()
    sections = parse_sections_ex(text)
    result: dict[str, str] = {}
    first_h2 = next((s.line for s in sections if s.level == 2), len(lines) + 1)
    result[""] = "\n".join(lines[: first_h2 - 1])
    current_h2 = ""
    for section in sections:
        if section.level == 2:
            current_h2 = section.key
            key = section.key
        elif section.level == 3:
            key = f"{current_h2}/{section.key}" if section.line > first_h2 else f"/{section.key}"
        else:
            continue
        if key in result:
            result[key] = result[key] + "\n" + section.text
        else:
            result[key] = section.text
    return result


def bullets_ex(text: str, first_line: int = 1) -> list[tuple[int, str]]:
    """Top-level bullets with their line numbers (see :func:`bullets`)."""
    out: list[tuple[int, list[str]]] = []
    current: list | None = None
    top_indent: int | None = None
    in_fence = False
    for offset, raw in enumerate(text.splitlines()):
        number = first_line + offset
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            current = None
            continue
        if in_fence:
            continue
        if not raw.strip() or _HEADING_RE.match(raw):
            current = None
            continue
        match = _BULLET_RE.match(raw)
        if match:
            indent = len(match.group(1).expandtabs(4))
            if top_indent is None or indent <= top_indent:
                top_indent = indent
                current = [number, [match.group(3).strip()]]
                out.append(current)  # type: ignore[arg-type]
            elif current is not None:
                current[1].append("; " + match.group(3).strip())
            else:
                current = [number, [match.group(3).strip()]]
                out.append(current)  # type: ignore[arg-type]
        elif current is not None:
            current[1].append(" " + raw.strip())
    result = []
    for number, parts in out:
        joined = "".join(parts)
        result.append((number, re.sub(r"\s+", " ", joined).strip()))
    return result


def bullets(section_text: str) -> list[str]:
    """Return the top-level ``-``/``*``/``+`` bullets of a section.

    Continuation lines are joined with a space and nested bullets are folded
    into their parent separated by ``;``. Numbered lists are prose, not bullets.
    """
    return [text for _, text in bullets_ex(section_text)]


# --------------------------------------------------------------------------- #
# Tickets
# --------------------------------------------------------------------------- #


def _section(sections: dict[str, str], name: str) -> str:
    """Find a section by exact key or by a ``"<name> ..."`` prefix (``What to do``)."""
    if name in sections:
        return sections[name]
    for key, value in sections.items():
        if key.startswith(name + " ") or key.startswith(name + ":"):
            return value
    return ""


def _split_prior_answers(context_text: str) -> tuple[str, str]:
    """Remove a ``### Prior answers`` sub-section from a Context section."""
    kept: list[str] = []
    prior: list[str] = []
    target: list[str] = kept
    for raw in context_text.splitlines():
        match = _HEADING_RE.match(raw)
        if match and len(match.group(1)) >= 3:
            target = prior if heading_key(match.group(2)) in ("prior answers", "prior-answers") else kept
            continue
        target.append(raw)
    return "\n".join(kept), "\n".join(prior)


def _prior_answer(bullet: str) -> dict:
    match = _PRIOR_RE.match(bullet)
    if match:
        entry = {"question": match.group("q").strip(), "answer": match.group("a").strip()}
        if match.group("id"):
            entry = {"id": match.group("id"), **entry}
        return entry
    if "? " in bullet:
        question, answer = bullet.split("? ", 1)
        return {"question": question.strip() + "?", "answer": answer.strip()}
    return {"question": bullet.strip(), "answer": ""}


def _as_list(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def parse_ticket(text: str) -> dict:
    """Parse a ticket draft in the ``# Title / ## Why / ## What / ## Acceptance / ## Context`` shape.

    ``## What`` keeps its ``###`` sub-headings as plain lines. Prior answers
    come from ``## Prior answers`` or ``### Prior answers`` under Context, as
    ``- **Q?** A`` or ``- (B03) **Q?** A``. A body without an H1 (a Linear
    description) yields an empty title. Optional frontmatter may carry
    ``title``, ``labels`` and ``estimate``.
    """
    frontmatter, body, _ = parse_frontmatter_ex(text)
    sections = parse_sections(body)
    preamble = sections.get("", "")
    title = str(frontmatter.get("title") or "").strip()
    if not title:
        for raw in preamble.splitlines():
            match = _HEADING_RE.match(raw)
            if match and len(match.group(1)) == 1:
                title = match.group(2).strip()
                break
    context_text, prior_text = _split_prior_answers(_section(sections, "context"))
    prior_text = _section(sections, "prior answers") or prior_text
    estimate_raw = str(frontmatter.get("estimate") or "").strip()
    estimate: int | str | None
    if not estimate_raw:
        estimate = None
    elif re.fullmatch(r"\d+", estimate_raw):
        estimate = int(estimate_raw)
    else:
        estimate = estimate_raw
    return {
        "title": title,
        "why": _section(sections, "why").strip(),
        "what": _section(sections, "what").strip(),
        "acceptance": bullets(_section(sections, "acceptance")),
        "context_bullets": bullets(context_text),
        "prior_answers": [_prior_answer(b) for b in bullets(prior_text)],
        "labels": _as_list(frontmatter.get("labels")),
        "estimate": estimate,
    }


def render_ticket(ticket: dict) -> str:
    """Render a ticket dict back to Linear-ready Markdown in a stable order."""
    parts = [f"# {ticket.get('title', '').strip()}", ""]
    parts += ["## Why", "", (ticket.get("why") or "").strip(), ""]
    parts += ["## What", "", (ticket.get("what") or "").strip(), ""]
    parts += ["## Acceptance", ""]
    parts += [f"- {_one_line(b)}" for b in ticket.get("acceptance") or []]
    parts.append("")
    prior = ticket.get("prior_answers") or []
    context = ticket.get("context_bullets") or []
    if context or prior:
        parts += ["## Context", ""]
        parts += [f"- {_one_line(b)}" for b in context]
        parts.append("")
    if prior:
        parts += ["### Prior answers", ""]
        for entry in prior:
            prefix = f"({entry['id']}) " if entry.get("id") else ""
            question = _one_line(entry.get("question", ""))
            answer = _one_line(entry.get("answer", ""))
            parts.append(f"- {prefix}**{question}** {answer}".rstrip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


# --------------------------------------------------------------------------- #
# Wikilinks and tags
# --------------------------------------------------------------------------- #


def mask_code(text: str) -> str:
    """Blank fenced code blocks and inline code spans, keeping line structure."""
    out: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            out.append("")
            continue
        if in_fence:
            out.append("")
            continue
        out.append(_CODE_SPAN_RE.sub(lambda m: " " * len(m.group(0)), raw))
    return "\n".join(out)


def wikilinks(text: str) -> list[str]:
    """Return the distinct ``[[targets]]`` in order (aliases and headings stripped)."""
    seen: list[str] = []
    for match in _WIKILINK_RE.finditer(mask_code(text)):
        target = match.group(1).strip()
        if target and target not in seen:
            seen.append(target)
    return seen


def _tag_re(prefix: str) -> re.Pattern:
    return re.compile(r"(?<![\w#/&])#(" + re.escape(prefix) + r"[\w/-]+)")


def inline_tags(text: str, prefix: str = TAG_PREFIX) -> list[tuple[int, str]]:
    """Return ``(line_number, tag)`` for every ``#<prefix>...`` token in prose.

    Tags inside inline code spans and fenced code blocks are ignored, as are
    URL fragments. Line numbers are 1-based; the tag is returned without ``#``.
    """
    pattern = _tag_re(prefix)
    found: list[tuple[int, str]] = []
    for number, line in enumerate(mask_code(text).splitlines(), start=1):
        for match in pattern.finditer(line):
            found.append((number, match.group(1)))
    return found


def strip_tags(line: str, prefix: str = TAG_PREFIX) -> str:
    """Remove ``#<prefix>...`` tokens from one line (code spans kept) and tidy the whitespace."""
    masked = mask_code(line) if "`" in line else line
    pieces: list[str] = []
    last = 0
    for match in _tag_re(prefix).finditer(masked):
        pieces.append(line[last : match.start()])
        last = match.end()
    pieces.append(line[last:])
    cleaned = re.sub(r"\(\s*\)", "", "".join(pieces))
    return re.sub(r"[ \t]+", " ", cleaned).strip()
