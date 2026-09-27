"""Context pack: the architecture and knowledge notes the gates feed to Jev.

A pack is a folder of Markdown notes (an Obsidian vault or any subfolder of
one). Every note resolves to one *area* and may yield *items* (rules, layers,
constraints, conventions, components, decisions) that questions cite by note
and line. Resolution order, first hit wins:

1. tags ``#jevgate/<area>``, ``#jevgate/component``, ``#jevgate/decision``
   (frontmatter ``tags:`` or inline tokens outside code);
2. frontmatter ``area:`` / ``kind:``;
3. the ``areas`` glob mapping from ``jevgate.json`` plus heading fallbacks
   (``## Rules``, ``## Constraints``, ``## Conventions``, ``## Layers``,
   ``## Alternatives``, ``## Provides``, ``## Interface``, ``## Decision``);
4. the parent folder name (``components``, ``decisions``/``adr``, ...);
5. ``general``.

``#jevgate/project/<name>`` scopes a note to one project; ``#jevgate/ignore``
drops a note (or, on a line with text, that line). Nothing here talks to the
network; :meth:`ContextPack.from_dict` accepts a prebuilt pack for producers
that are not vaults.
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import markdown as md
from .textstats import tokens

AREAS: tuple[str, ...] = (
    "architecture",
    "components",
    "decisions",
    "data",
    "interfaces",
    "constraints",
    "conventions",
    "glossary",
)
GENERAL = "general"
TEXT_AREAS = ("data", "interfaces", "glossary")
ITEM_KINDS: tuple[str, ...] = ("rule", "constraint", "convention", "layer", "component", "decision")
LINE_KINDS: tuple[str, ...] = ("rule", "constraint", "convention", "layer")
NOTE_KINDS: tuple[str, ...] = ("component", "decision")
ITEM_AREA = {
    "rule": "architecture",
    "layer": "architecture",
    "constraint": "constraints",
    "convention": "conventions",
    "component": "components",
    "decision": "decisions",
}
AREA_ITEM_KINDS = {
    "architecture": ("rule", "layer"),
    "constraints": ("constraint",),
    "conventions": ("convention",),
    "components": ("component",),
    "decisions": ("decision",),
}
ID_PREFIX = {"rule": "R", "constraint": "C", "convention": "V", "layer": "L"}
HEADING_KINDS = {"rules": "rule", "constraints": "constraint", "conventions": "convention", "layers": "layer"}
FOLDER_AREAS = {**{a: a for a in AREAS}, "component": "components", "decision": "decisions", "adr": "decisions", "adrs": "decisions"}
TAG = md.TAG_PREFIX  # "jevgate/"
_KNOWN_TAG_RE = re.compile(
    r"^jevgate/(?:" + "|".join(AREAS) + r"|component|decision|rule|constraint|convention|layer|ignore|applies/[\w.*-]+|project/[\w.-]+)$"
)
_STOPWORDS = frozenset(
    "the and for that with this from into are was not but have has will should must when then than its our your "
    "use used using via all any can one two new add also each per only over under out more some such them they "
    "their which what why how who where about after before between does did done been being were would could may "
    "might into onto upon here there".split()
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_STATUS_LINE_RE = re.compile(r"^\**status\**\s*[:：]\s*\**\s*([^*\n]+?)\s*\**\s*$", re.I)


def slug(text: str) -> str:
    """Lowercase ``text`` and replace runs of non-alphanumerics with ``-``."""
    return _SLUG_RE.sub("-", text.lower()).strip("-") or "note"


def _tok(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) >= 3 and w not in _STOPWORDS}


def _as_list(value) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in re.split(r"[,\s]+", str(value)) if part.strip()]


def _norm_tags(value) -> list[str]:
    return [t.lstrip("#").strip() for t in _as_list(value) if t.lstrip("#").strip()]


def _applies_glob(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if "*" in value or "/" in value or "?" in value:
        return value
    return "*" + value if value.startswith(".") else f"*.{value}"


def _clean_line(line: str) -> str:
    """Prose as sent to the model: tags stripped, wikilinks reduced to their label."""
    line = md.strip_tags(line)
    return re.sub(r"\[\[([^\]|#]*)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]", lambda m: (m.group(2) or m.group(1)).strip(), line)


def _item_text(raw: str) -> str:
    stripped = md._BULLET_RE.sub(lambda m: m.group(3), raw)
    stripped = md._HEADING_RE.sub(lambda m: m.group(2), stripped)
    return re.sub(r"\s+", " ", _clean_line(stripped)).strip()


def _first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text):
        lines = [l for l in block.splitlines() if l.strip() and not md._HEADING_RE.match(l)]
        if lines:
            return re.sub(r"\s+", " ", " ".join(_clean_line(l) for l in lines)).strip()
    return ""


def _glob_match(rel: str, pattern: str) -> bool:
    candidates = {pattern, pattern.replace("**/", ""), pattern.replace("/**", "")}
    return any(fnmatch.fnmatchcase(rel, p) for p in candidates)


@dataclass
class Note:
    """One Markdown note of the pack.

    ``body`` is the text as it would be sent: frontmatter removed, ignored
    lines dropped, ``#jevgate/...`` tags stripped. ``resolved_by`` names the
    rule that assigned ``area`` (``tag:#jevgate/architecture``,
    ``frontmatter:area``, ``glob:<pattern>``, ``folder:<name>``,
    ``default:general``, ``dict:<area>``).
    """

    title: str
    path: str
    area: str
    kind: str
    frontmatter: dict
    body: str
    tags: list[str]
    links: list[str]
    project: str | None
    resolved_by: str
    ignored: bool
    reason: str = ""
    aliases: list[str] = field(default_factory=list)
    kind_by: str = ""

    def search_text(self) -> str:
        return " ".join([self.title, self.path, " ".join(self.aliases), self.body])


@dataclass
class Item:
    """One citeable unit extracted from a note.

    ``kind`` is one of ``rule``, ``constraint``, ``convention``, ``layer``,
    ``component`` or ``decision``. ``note`` is the note's path (its title for
    packs built from a dict) and ``line`` the 1-based source line.
    """

    id: str
    kind: str
    text: str
    note: str
    line: int
    tags: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    resolved_by: str = ""

    @property
    def area(self) -> str:
        return ITEM_AREA[self.kind]

    def search_text(self) -> str:
        parts = [self.text]
        for value in self.meta.values():
            if isinstance(value, list):
                parts += [str(v) for v in value]
            elif value:
                parts.append(str(value))
        return " ".join(parts)

    def applies_to(self, path: str) -> bool:
        """True when the item's ``applies_to`` globs are empty or match ``path``."""
        patterns = [p for p in _as_list(self.meta.get("applies_to")) if p]
        if not patterns:
            return True
        name = Path(path).name
        return any(fnmatch.fnmatchcase(path, p) or fnmatch.fnmatchcase(name, p) for p in patterns)


class ContextPack:
    """The loaded notes and items, with selection, slicing and explanation."""

    def __init__(
        self,
        *,
        project: str | None = None,
        follow_links: int = 1,
        context_budget: int = 8000,
        max_items: int = 16,
    ) -> None:
        self.project = project
        self.follow_links = follow_links
        self.context_budget = context_budget
        self.max_items = max_items
        self.warnings: list[str] = []
        self.last_evidence: dict[str, dict] = {}
        self._notes: list[Note] = []
        self._items: list[Item] = []
        self._index: dict[str, Note] = {}
        self._finished = False

    # ------------------------------------------------------------------ load
    @classmethod
    def load(
        cls,
        dir: Path | str,
        *,
        project: str | None = None,
        areas: dict[str, list[str]] | None = None,
        follow_links: int = 1,
        context_budget: int = 8000,
        max_items: int = 16,
    ) -> "ContextPack":
        """Load every ``*.md`` under ``dir`` (hidden folders skipped)."""
        root = Path(dir).expanduser()
        if not root.is_dir():
            raise FileNotFoundError(f"context dir not found: {root}")
        pack = cls(project=project, follow_links=follow_links, context_budget=context_budget, max_items=max_items)
        paths = sorted(
            p for p in root.rglob("*.md") if not any(part.startswith(".") for part in p.relative_to(root).parts)
        )
        for path in paths:
            pack._add_file(root, path, areas or {})
        pack._finish()
        return pack

    @classmethod
    def from_dict(
        cls,
        d: dict,
        *,
        project: str | None = None,
        follow_links: int = 1,
        context_budget: int = 8000,
        max_items: int = 16,
    ) -> "ContextPack":
        """Build a pack from ``{"<area>": [{"title", "text", "meta", "items": [...]}]}``.

        Items are ``{id?, kind?, text, line?, meta?, tags?}``; ``kind`` defaults
        to the area's item kind. A component or decision entry without
        ``items`` yields one item from its ``meta``. Ids are numbered when absent.
        """
        pack = cls(project=project, follow_links=follow_links, context_budget=context_budget, max_items=max_items)
        for area, entries in (d or {}).items():
            if area not in AREAS and area != GENERAL:
                pack.warnings.append(f"unknown area '{area}' in pack dict; loaded as general")
                area = GENERAL
            for position, entry in enumerate(entries or [], start=1):
                meta = dict(entry.get("meta") or {})
                title = str(entry.get("title") or meta.get("title") or f"{area}-{position}")
                text = str(entry.get("text") or "")
                kind = meta.get("kind") or ("component" if area == "components" else "decision" if area == "decisions" else "note")
                note = Note(
                    title=title,
                    path=str(meta.get("note") or title),
                    area=area,
                    kind=kind,
                    frontmatter=meta,
                    body=text,
                    tags=_norm_tags(meta.get("tags")),
                    links=md.wikilinks(text),
                    project=meta.get("project"),
                    resolved_by=f"dict:{area}",
                    ignored=False,
                    aliases=_as_list(meta.get("aliases")),
                    kind_by="dict:kind" if meta.get("kind") else f"area:{area}",
                )
                pack._notes.append(note)
                items = entry.get("items")
                if items is None and kind in NOTE_KINDS:
                    items = [pack._note_item_dict(note)]
                for raw in items or []:
                    item_kind = raw.get("kind") or AREA_ITEM_KINDS.get(area, ("rule",))[0]
                    if item_kind not in ITEM_KINDS:
                        pack.warnings.append(f"{title}: unknown item kind '{item_kind}' skipped")
                        continue
                    pack._items.append(
                        Item(
                            id=str(raw.get("id") or ""),
                            kind=item_kind,
                            text=str(raw.get("text") or ""),
                            note=note.path,
                            line=int(raw.get("line") or 0),
                            tags=_norm_tags(raw.get("tags")),
                            meta=dict(raw.get("meta") or {}),
                            resolved_by="dict:item",
                        )
                    )
        pack._finish()
        return pack

    def _note_item_dict(self, note: Note) -> dict:
        meta = note.frontmatter
        if note.kind == "component":
            item_meta = {
                "name": note.title,
                "path": str(meta.get("path") or ""),
                "provides": str(meta.get("provides") or ""),
                "interface": str(meta.get("interface") or ""),
                "aliases": _as_list(meta.get("aliases")),
            }
            return {"id": slug(note.title), "kind": "component", "text": item_meta["provides"] or _first_paragraph(note.body), "meta": item_meta}
        item_meta = {
            "title": note.title,
            "status": str(meta.get("status") or ""),
            "decision": str(meta.get("decision") or ""),
            "alternatives": _as_list(meta.get("alternatives")),
        }
        return {"id": slug(note.title), "kind": "decision", "text": item_meta["decision"] or _first_paragraph(note.body), "meta": item_meta}

    def _add_file(self, root: Path, path: Path, areas: dict[str, list[str]]) -> None:
        text = path.read_text(encoding="utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        frontmatter, body, fm_warnings = md.parse_frontmatter_ex(text)
        self.warnings += [f"{rel}: {w}" for w in fm_warnings]
        offset = len(text.splitlines()) - len(body.splitlines())
        body_lines = body.splitlines()
        fm_tags = _norm_tags(frontmatter.get("tags"))
        inline = md.inline_tags(body)
        all_tags: list[str] = []
        for tag in fm_tags + [t for _, t in inline]:
            if tag not in all_tags:
                all_tags.append(tag)
        for tag in all_tags:
            if tag.startswith(TAG) and not _KNOWN_TAG_RE.match(tag):
                self.warnings.append(f"{rel}: unknown tag #{tag}")

        h1 = ""
        h1_line = 0
        for number, raw in enumerate(body_lines, start=1):
            match = md._HEADING_RE.match(raw)
            if match and len(match.group(1)) == 1:
                h1 = md.strip_tags(match.group(2))
                h1_line = number
                break
        title = str(frontmatter.get("title") or h1 or path.stem).strip()
        aliases = _as_list(frontmatter.get("aliases"))
        for extra in (path.stem, h1):
            if extra and extra != title and extra not in aliases:
                aliases.append(extra)

        # Which inline tags apply to the note and which to a single line.
        note_tags = list(fm_tags)
        line_tags: dict[int, list[str]] = {}
        ignored_lines: set[int] = set()
        for number, tag in inline:
            name = tag[len(TAG):] if tag.startswith(TAG) else ""
            line_has_text = bool(_item_text(body_lines[number - 1]))
            if name == "ignore":
                if line_has_text:
                    ignored_lines.add(number)
                elif tag not in note_tags:
                    note_tags.append(tag)
            elif name in LINE_KINDS or name.startswith("applies/"):
                line_tags.setdefault(number, []).append(tag)
            elif tag not in note_tags:
                note_tags.append(tag)

        project = None
        for tag in note_tags:
            if tag.startswith(TAG + "project/"):
                project = tag[len(TAG + "project/"):]
                break
        if project is None and frontmatter.get("project"):
            project = str(frontmatter["project"]).strip()

        area, kind, resolved_by, kind_by = self._resolve(rel, note_tags, frontmatter, areas)

        ignored = False
        reason = ""
        if TAG + "ignore" in note_tags:
            ignored, reason = True, "tag:#jevgate/ignore"
        elif str(frontmatter.get("ignore", "")).strip().lower() in ("true", "yes", "1"):
            ignored, reason = True, "frontmatter:ignore"
        elif project is not None and self.project is not None and project != self.project:
            ignored, reason = True, f"project:{project} (loading project {self.project or 'none'})"

        clean_lines = [_clean_line(raw) for number, raw in enumerate(body_lines, start=1) if number not in ignored_lines]
        clean_body = re.sub(r"\n{3,}", "\n\n", "\n".join(clean_lines)).strip("\n")
        note = Note(
            title=title,
            path=rel,
            area=area,
            kind=kind,
            frontmatter=frontmatter,
            body=clean_body,
            tags=all_tags,
            links=md.wikilinks(body),
            project=project,
            resolved_by=resolved_by,
            ignored=ignored,
            reason=reason,
            aliases=aliases,
            kind_by=kind_by,
        )
        self._notes.append(note)
        if ignored:
            return
        if tokens(clean_body) > self.context_budget:
            self.warnings.append(f"{rel}: {tokens(clean_body)} tokens exceeds context_budget {self.context_budget}; it will be truncated")
        self._extract_items(note, body, body_lines, offset, line_tags, ignored_lines, h1_line)

    def _resolve(self, rel: str, note_tags: list[str], frontmatter: dict, areas: dict[str, list[str]]):
        area = kind = None
        resolved_by = kind_by = ""
        for tag in note_tags:
            name = tag[len(TAG):] if tag.startswith(TAG) else ""
            if name in AREAS and area is None:
                area, resolved_by = name, f"tag:#{tag}"
            elif name in NOTE_KINDS and kind is None:
                kind, kind_by = name, f"tag:#{tag}"
        if area is None and kind is not None:
            area, resolved_by = ITEM_AREA[kind], kind_by
        fm_area = str(frontmatter.get("area") or "").strip().lower()
        fm_kind = str(frontmatter.get("kind") or "").strip().lower()
        if area is None and fm_area:
            if fm_area in AREAS or fm_area == GENERAL:
                area, resolved_by = fm_area, "frontmatter:area"
            else:
                self.warnings.append(f"{rel}: unknown frontmatter area '{fm_area}'")
        if kind is None and fm_kind in NOTE_KINDS:
            kind, kind_by = fm_kind, "frontmatter:kind"
            if area is None:
                area, resolved_by = ITEM_AREA[kind], kind_by
        if area is None:
            for candidate, patterns in areas.items():
                for pattern in _as_list(patterns):
                    if _glob_match(rel, pattern):
                        area, resolved_by = candidate, f"glob:{pattern}"
                        break
                if area is not None:
                    break
        if area is None:
            parts = rel.split("/")
            if len(parts) > 1 and parts[-2].lower() in FOLDER_AREAS:
                area, resolved_by = FOLDER_AREAS[parts[-2].lower()], f"folder:{parts[-2]}"
        if area is None:
            area, resolved_by = GENERAL, "default:general"
        if kind is None:
            kind = "component" if area == "components" else "decision" if area == "decisions" else "note"
            kind_by = f"area:{area}"
        return area, kind, resolved_by, kind_by

    def _extract_items(
        self,
        note: Note,
        body: str,
        body_lines: list[str],
        offset: int,
        line_tags: dict[int, list[str]],
        ignored_lines: set[int],
        h1_line: int,
    ) -> None:
        rel = note.path
        fm = note.frontmatter
        sections = md.parse_sections_ex(body)
        by_key = {s.key: s for s in sections if s.level == 2}
        taken: set[int] = set()
        note_applies = [_applies_glob(v) for v in _as_list(fm.get("applies_to"))]

        def responsibility_after(line_no: int, end: int) -> str:
            for raw in body_lines[line_no : end - 1]:
                if raw.strip() and not md._HEADING_RE.match(raw):
                    return re.sub(r"\s+", " ", _clean_line(raw)).strip()
            return ""

        def add_line_item(kind: str, number: int, resolved_by: str, tags: list[str], section_end: int | None = None) -> None:
            raw = body_lines[number - 1]
            text = _item_text(raw)
            if not text:
                return
            meta: dict = {}
            applies = [_applies_glob(t[len(TAG + "applies/"):]) for t in tags if t.startswith(TAG + "applies/")]
            if kind == "convention":
                meta["applies_to"] = applies or list(note_applies)
            elif applies:
                meta["applies_to"] = applies
            if kind == "layer":
                if md._HEADING_RE.match(raw):
                    name = text
                    desc = responsibility_after(number, section_end or len(body_lines) + 1)
                else:
                    name, desc = _split_layer(text)
                meta = {"name": name, "responsibility": desc, **meta}
            self._items.append(Item("", kind, text, rel, number + offset, list(tags), meta, resolved_by))
            taken.add(number)

        # 1. tagged lines
        for number in sorted(line_tags):
            if number in ignored_lines:
                continue
            tags = line_tags[number]
            kinds = [k for k in LINE_KINDS if TAG + k in tags]
            if not kinds:
                continue
            end = None
            for section in sections:
                if section.line == number:
                    end = section.end
            add_line_item(kinds[0], number, f"tag:#jevgate/{kinds[0]}", tags, end)

        # 2. heading fallbacks, only in a note of the matching area (never in general)
        for key, kind in HEADING_KINDS.items():
            section = by_key.get(key)
            if section is None or note.area != ITEM_AREA[kind]:
                continue
            resolved_by = f"heading:## {section.heading}"
            if kind == "layer":
                subs = [s for s in sections if s.level == 3 and section.start <= s.line < section.end]
                if subs:
                    for sub in subs:
                        if sub.line not in taken and sub.line not in ignored_lines:
                            add_line_item("layer", sub.line, resolved_by, [], sub.end)
                    continue
            for number, _ in md.bullets_ex(section.text, section.start):
                if number not in taken and number not in ignored_lines:
                    add_line_item(kind, number, resolved_by, [])

        # 3. area-wide bullets for constraints/conventions notes with nothing else
        if note.area in ("constraints", "conventions") and not any(i.note == rel for i in self._items):
            kind = AREA_ITEM_KINDS[note.area][0]
            for number, _ in md.bullets_ex(body, 1):
                if number not in ignored_lines:
                    add_line_item(kind, number, f"area:{note.area} bullets", [])

        # 4. component / decision notes are one item each
        if note.kind in NOTE_KINDS:
            section_text = {s.key: s.text.strip() for s in sections if s.level == 2}
            if note.kind == "component":
                meta = {
                    "name": note.title,
                    "path": str(fm.get("path") or ""),
                    "provides": _first_paragraph(str(fm.get("provides") or section_text.get("provides", ""))),
                    "interface": str(fm.get("interface") or _section_plain(section_text.get("interface", ""))),
                    "aliases": _as_list(fm.get("aliases")),
                }
                text = meta["provides"] or _first_paragraph(note.body)
            else:
                status = str(fm.get("status") or "").strip()
                if not status:
                    for raw in body_lines:
                        match = _STATUS_LINE_RE.match(raw.strip())
                        if match:
                            status = match.group(1).strip()
                            break
                meta = {
                    "title": note.title,
                    "status": status,
                    "decision": str(fm.get("decision") or _section_plain(section_text.get("decision", ""))),
                    "alternatives": _as_list(fm.get("alternatives")) or [_clean_line(b) for b in md.bullets(section_text.get("alternatives", ""))],
                }
                text = meta["decision"] or _first_paragraph(note.body)
            self._items.append(Item(slug(note.title), note.kind, text, rel, (h1_line or 1) + offset, [], meta, note.kind_by))

    def _finish(self) -> None:
        self._index = {}
        for note in self._notes:
            for key in [note.title, Path(note.path).stem, *note.aliases]:
                self._index.setdefault(key.strip().lower(), note)
        counters: dict[str, int] = {}
        seen_ids: set[str] = set()
        for item in self._items:
            if not item.id:
                counters[item.kind] = counters.get(item.kind, 0) + 1
                item.id = f"{ID_PREFIX.get(item.kind, item.kind[0].upper())}{counters[item.kind]:02d}"
            base = item.id
            suffix = 2
            while item.id in seen_ids:
                item.id = f"{base}-{suffix}"
                suffix += 1
            if item.id != base:
                self.warnings.append(f"{item.note}: duplicate item id '{base}' renamed to '{item.id}'")
            seen_ids.add(item.id)
        for note in self._notes:
            if note.ignored:
                continue
            for link in note.links:
                if self.resolve(link) is None:
                    self.warnings.append(f"{note.path}: unresolved wikilink [[{link}]]")
        self._finished = True

    # ---------------------------------------------------------------- queries
    def resolve(self, link: str) -> Note | None:
        """Resolve a wikilink target by title, file stem or alias (case-insensitive)."""
        return self._index.get(link.split("#", 1)[0].split("|", 1)[0].strip().lower())

    def all_notes(self) -> list[Note]:
        """Every note read, including ignored and project-skipped ones."""
        return list(self._notes)

    def areas(self) -> list[str]:
        """The areas present (notes or items), in canonical order."""
        present = {n.area for n in self._notes if not n.ignored} | {i.area for i in self._items}
        return [a for a in (*AREAS, GENERAL) if a in present]

    def has(self, area: str) -> bool:
        return area in self.areas()

    def notes(self, area: str) -> list[Note]:
        return [n for n in self._notes if n.area == area and not n.ignored]

    def items(self, area_or_kind: str) -> list[Item]:
        """Items of one kind, or every item whose kind belongs to the area."""
        if area_or_kind in ITEM_KINDS:
            return [i for i in self._items if i.kind == area_or_kind]
        kinds = AREA_ITEM_KINDS.get(area_or_kind, ())
        return [i for i in self._items if i.kind in kinds]

    def applicable(self, path: str, area_or_kind: str = "convention") -> list[Item]:
        """Items whose ``applies_to`` globs are empty or match ``path``."""
        return [i for i in self.items(area_or_kind) if i.applies_to(path)]

    def select_items(self, kind: str, query_text: str, k: int | None) -> list[Item]:
        """Rank items of ``kind`` (or area) by lexical overlap with ``query_text``.

        Ties keep note order; ``k=None`` returns all. Tokens come from the item
        text and its meta (path, aliases, provides, ...).
        """
        query = _tok(query_text or "")
        candidates = self.items(kind)
        ranked = sorted(enumerate(candidates), key=lambda pair: (-len(query & _tok(pair[1].search_text())), pair[0]))
        selected = [item for _, item in ranked]
        return selected if k is None else selected[:k]

    def select_notes(self, area: str, query_text: str, k: int | None) -> list[Note]:
        """Rank the notes of ``area`` by lexical overlap with ``query_text``."""
        query = _tok(query_text or "")
        candidates = self.notes(area)
        ranked = sorted(enumerate(candidates), key=lambda pair: (-len(query & _tok(pair[1].search_text())), pair[0]))
        selected = [note for _, note in ranked]
        return selected if k is None else selected[:k]

    def linked(self, notes: list[Note], hops: int | None = None) -> list[Note]:
        """Notes reachable from ``notes`` through wikilinks within ``hops`` (excluding the inputs)."""
        hops = self.follow_links if hops is None else hops
        seen = {id(n) for n in notes}
        frontier = list(notes)
        found: list[Note] = []
        for _ in range(max(hops, 0)):
            next_frontier: list[Note] = []
            for note in frontier:
                for link in note.links:
                    target = self.resolve(link)
                    if target is None or target.ignored or id(target) in seen:
                        continue
                    seen.add(id(target))
                    found.append(target)
                    next_frontier.append(target)
            frontier = next_frontier
        return found

    # ------------------------------------------------------------------ slice
    def slice(self, needs: set[str], query_text: str, *, all_items: bool = False) -> dict:
        """The state fragment for the areas in ``needs``, each within ``context_budget``.

        Shapes: ``architecture: {overview, rules, layers}``; ``components``,
        ``decisions``, ``constraints``, ``conventions``: lists; ``data``,
        ``interfaces``, ``glossary``: ``{overview}``. Text areas end with
        ``[... truncated ...]`` and ``truncated: True`` when cut; list areas
        drop trailing items. :attr:`last_evidence` records what was sent.
        """
        k = None if all_items else self.max_items
        result: dict = {}
        self.last_evidence = {}
        for area in AREAS:
            if area not in needs or not self.has(area):
                continue
            evidence = {"notes": [], "items": [], "tokens": 0, "truncated": False}
            if area == "architecture":
                fragment: dict = {"overview": ""}
                fragment["rules"] = self._fit_list(
                    [{"id": i.id, "text": i.text, "note": i.note, "line": i.line} for i in self._ordered(self.select_items("rule", query_text, k))],
                    self.context_budget // 2,
                    evidence,
                )
                fragment["layers"] = self._fit_list(
                    [{"id": i.id, "name": i.meta.get("name", i.text), "responsibility": i.meta.get("responsibility", "")} for i in self.items("layer")],
                    self.context_budget // 4,
                    evidence,
                )
                remaining = self.context_budget - tokens(json.dumps(fragment)) - 8
                notes = self.select_notes(area, query_text, None)
                fragment["overview"], truncated = self._fit_text(notes + self.linked(notes), remaining, evidence)
                if truncated:
                    fragment["truncated"] = True
                result[area] = fragment
            elif area in TEXT_AREAS:
                notes = self.select_notes(area, query_text, None)
                overview, truncated = self._fit_text(notes + self.linked(notes), self.context_budget - 8, evidence)
                fragment = {"overview": overview}
                if truncated:
                    fragment["truncated"] = True
                result[area] = fragment
            elif area == "components":
                items = self._with_linked_items(self.select_items("component", query_text, k), "component")
                rows = [
                    {"id": i.id, "name": i.meta.get("name", i.text), "path": i.meta.get("path", ""), "provides": i.meta.get("provides", i.text), "interface": i.meta.get("interface", ""), "aliases": list(i.meta.get("aliases", []))}
                    for i in items
                ]
                result[area] = self._fit_list(rows, self.context_budget, evidence)
            elif area == "decisions":
                items = self._with_linked_items(self.select_items("decision", query_text, k), "decision")
                rows = [
                    {"id": i.id, "title": i.meta.get("title", i.text), "status": i.meta.get("status", ""), "decision": i.meta.get("decision", i.text), "alternatives": list(i.meta.get("alternatives", []))}
                    for i in items
                ]
                result[area] = self._fit_list(rows, self.context_budget, evidence)
            else:  # constraints, conventions
                kind = AREA_ITEM_KINDS[area][0]
                rows = []
                for item in self._ordered(self.select_items(kind, query_text, k)):
                    row = {"id": item.id, "text": item.text, "note": item.note}
                    if item.meta.get("applies_to"):
                        row["applies_to"] = list(item.meta["applies_to"])
                    rows.append(row)
                result[area] = self._fit_list(rows, self.context_budget, evidence)
            evidence["tokens"] = tokens(json.dumps(result[area]))
            self.last_evidence[area] = evidence
        return result

    def _ordered(self, items: list[Item]) -> list[Item]:
        """Restore note order for a selected subset (stable reading order for the model)."""
        position = {id(i): n for n, i in enumerate(self._items)}
        return sorted(items, key=lambda i: position.get(id(i), 0))

    def _with_linked_items(self, selected: list[Item], kind: str) -> list[Item]:
        by_note = {n.path: n for n in self._notes}
        notes = [by_note[i.note] for i in selected if i.note in by_note]
        linked_paths = {n.path for n in self.linked(notes)}
        extra = [i for i in self.items(kind) if i.note in linked_paths and i not in selected]
        return self._ordered(list(selected) + extra)

    def _fit_list(self, rows: list[dict], budget: int, evidence: dict) -> list[dict]:
        kept: list[dict] = []
        for row in rows:
            if tokens(json.dumps(kept + [row])) > budget:
                evidence["truncated"] = True
                break
            kept.append(row)
            evidence["items"].append(row["id"])
        return kept

    def _fit_text(self, notes: list[Note], budget: int, evidence: dict) -> tuple[str, bool]:
        marker = "\n[... truncated ...]"
        size = lambda s: tokens(json.dumps(s))  # noqa: E731 - budget applies to the JSON form
        text = ""
        truncated = False
        for note in notes:
            body = note.body.strip()
            block = body if body.startswith("# ") else f"# {note.title}\n\n{body}".strip()
            joiner = "\n\n" if text else ""
            if size(text + joiner + block) <= budget:
                text = text + joiner + block
                evidence["notes"].append(note.path)
                continue
            cut = block[: max(budget * 4 - len(json.dumps(text)) - len(marker) - 32, 0)]
            while cut and size(text + joiner + cut + marker) > budget:
                cut = cut[: int(len(cut) * 0.9)]
            if len(cut) > 200:
                text = text + joiner + cut.rstrip() + marker
                evidence["notes"].append(note.path)
            elif text:
                text += marker
            truncated = True
            break
        if truncated:
            evidence["truncated"] = True
        return text, truncated

    # ------------------------------------------------------------ reporting
    def index(self) -> list[dict]:
        """``[{title, area, first_line}]`` for every loaded note (the clarifying bank's index)."""
        out = []
        for note in self._notes:
            if note.ignored:
                continue
            first = ""
            for raw in note.body.splitlines():
                if raw.strip() and not md._HEADING_RE.match(raw):
                    first = raw.strip()[:160]
                    break
            out.append({"title": note.title, "area": note.area, "first_line": first})
        return out

    def explain(self) -> list[dict]:
        """Per note: how its area and kind were resolved and which items it yielded."""
        out = []
        for note in self._notes:
            out.append(
                {
                    "path": note.path,
                    "title": note.title,
                    "area": note.area,
                    "kind": note.kind,
                    "resolved_by": note.resolved_by,
                    "kind_by": note.kind_by,
                    "ignored": note.ignored,
                    "reason": note.reason,
                    "project": note.project,
                    "tags": list(note.tags),
                    "tokens": tokens(note.body),
                    "items": [
                        {"id": i.id, "kind": i.kind, "line": i.line, "resolved_by": i.resolved_by}
                        for i in self._items
                        if i.note == note.path
                    ],
                }
            )
        return out

    def to_dict(self) -> dict:
        """The ``from_dict`` shape of this pack (what ``--context-json`` accepts)."""
        out: dict[str, list[dict]] = {}
        for note in self._notes:
            if note.ignored:
                continue
            items = [
                {"id": i.id, "kind": i.kind, "text": i.text, "line": i.line, "meta": i.meta}
                for i in self._items
                if i.note == note.path
            ]
            meta = {"kind": note.kind, "note": note.path}
            if note.aliases:
                meta["aliases"] = list(note.aliases)
            out.setdefault(note.area, []).append({"title": note.title, "text": note.body, "meta": meta, "items": items})
        return out


def _split_layer(text: str) -> tuple[str, str]:
    for sep in (" — ", " – ", ": ", " - "):
        if sep in text:
            name, desc = text.split(sep, 1)
            return name.strip(), desc.strip()
    return text, ""


def _section_plain(text: str) -> str:
    lines = [_clean_line(l) for l in text.splitlines() if l.strip()]
    return re.sub(r"[ \t]+", " ", "\n".join(lines)).strip()
