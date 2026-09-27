"""Cheap prose statistics: token estimates, readability signals, hedge words.

These feed the rule layer of the ticket gate (numbers and thresholds live in
code; the model never sees them). Everything is a heuristic over plain text.
"""

from __future__ import annotations

import math
import re

from . import markdown as md

DEFAULT_HEDGES: tuple[str, ...] = (
    "leverage",
    "robust",
    "seamless",
    "seamlessly",
    "utilize",
    "utilise",
    "comprehensive",
    "holistic",
    "streamline",
    "ensure that",
    "in order to",
    "it is important to note",
    "it's worth noting",
    "various",
    "cutting-edge",
    "state-of-the-art",
    "delve",
)

LONG_WORD_LETTERS = 9

_WORD_RE = re.compile(r"[A-Za-z0-9][\w'’-]*")
_BULLET_LINE_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_HEADING_LINE_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bvs\.)(?<=[.!?])[\"')\]]*\s+(?=[A-Z0-9\"'(\[`*_])"
)


def tokens(text: str, kind: str = "prose") -> int:
    """Estimate the model tokens of ``text``: ``len/4`` for prose, ``len/3`` for code."""
    if not text:
        return 0
    divisor = 3 if kind == "code" else 4
    return math.ceil(len(text) / divisor)


def _prose_lines(text: str) -> tuple[list[str], int, int]:
    """Return cleaned non-empty prose lines plus (bullet lines, non-empty lines)."""
    lines: list[str] = []
    bullet_count = 0
    non_empty = 0
    in_fence = False
    for raw in text.splitlines():
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence or not raw.strip():
            continue
        non_empty += 1
        if _BULLET_LINE_RE.match(raw):
            bullet_count += 1
        cleaned = _BULLET_LINE_RE.sub("", raw)
        cleaned = _HEADING_LINE_RE.sub("", cleaned)
        cleaned = md._CODE_SPAN_RE.sub(" code ", cleaned)
        cleaned = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), cleaned)
        cleaned = cleaned.replace("**", "").strip()
        if cleaned:
            lines.append(cleaned)
    return lines, bullet_count, non_empty


def _hedge_pattern(phrase: str) -> re.Pattern:
    escaped = re.escape(phrase.strip())
    if " " in phrase or "-" in phrase or "'" in phrase:
        return re.compile(r"(?<![\w-])" + escaped + r"(?![\w-])", re.IGNORECASE)
    return re.compile(r"(?<![\w-])" + escaped + r"\w*(?![\w-])", re.IGNORECASE)


def find_hedges(text: str, hedges: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Return every hedge/LLM-ism occurrence (canonical phrase) in reading order."""
    phrases = list(hedges) if hedges is not None else list(DEFAULT_HEDGES)
    matches: list[tuple[int, int, str]] = []
    for phrase in phrases:
        if not phrase.strip():
            continue
        for match in _hedge_pattern(phrase).finditer(text):
            matches.append((match.start(), -(match.end() - match.start()), -len(phrase), phrase))
    matches.sort()
    found: list[str] = []
    last_end = -1
    for start, neg_len, _, phrase in matches:
        end = start - neg_len
        if start < last_end:
            continue
        found.append(phrase)
        last_end = end
    return found


def stats(text: str, hedges: list[str] | tuple[str, ...] | None = None) -> dict:
    """Readability signals for a block of Markdown prose.

    Returns ``sentences``, ``words``, ``avg_sentence_words``, ``long_word_share``
    (words with at least nine letters), ``hedges`` (occurrences found) and
    ``bullet_ratio`` (bullet lines over non-empty lines). Fenced code is
    skipped; inline code counts as one word.
    """
    lines, bullet_count, non_empty = _prose_lines(text)
    sentences = 0
    words = 0
    long_words = 0
    for line in lines:
        for piece in _SENTENCE_SPLIT_RE.split(line):
            piece_words = _WORD_RE.findall(piece)
            if not piece_words:
                continue
            sentences += 1
            words += len(piece_words)
            long_words += sum(1 for w in piece_words if len(re.sub(r"[^A-Za-z]", "", w)) >= LONG_WORD_LETTERS)
    return {
        "sentences": sentences,
        "words": words,
        "avg_sentence_words": round(words / sentences, 2) if sentences else 0.0,
        "long_word_share": round(long_words / words, 3) if words else 0.0,
        "hedges": find_hedges(md.mask_code(text), hedges),
        "bullet_ratio": round(bullet_count / non_empty, 3) if non_empty else 0.0,
    }


def ticket_prose(ticket: dict) -> str:
    """The text the rule checks run over: why, what and the acceptance bullets."""
    parts = [str(ticket.get("why") or ""), str(ticket.get("what") or "")]
    parts += [f"- {b}" for b in ticket.get("acceptance") or []]
    return "\n".join(p for p in parts if p)


def rule_findings(
    ticket: dict,
    *,
    max_avg_sentence_words: float = 25,
    max_long_word_share: float = 0.20,
    max_hedges: int = 3,
    hedges: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    """Warn-level findings from the numeric readability rules.

    Each finding is a plain dict ``{id, severity, message, hint, stats}`` with
    ids ``rule:long_sentences``, ``rule:dense_words`` and ``rule:hedges``.
    """
    text = ticket_prose(ticket)
    numbers = stats(text, hedges)
    findings: list[dict] = []
    if numbers["sentences"] and numbers["avg_sentence_words"] > max_avg_sentence_words:
        findings.append(
            {
                "id": "rule:long_sentences",
                "severity": "warn",
                "message": (
                    f"Sentences average {numbers['avg_sentence_words']} words "
                    f"(limit {max_avg_sentence_words})."
                ),
                "hint": "Split long sentences; one fact or step per sentence or bullet.",
                "stats": numbers,
            }
        )
    if numbers["words"] and numbers["long_word_share"] > max_long_word_share:
        findings.append(
            {
                "id": "rule:dense_words",
                "severity": "warn",
                "message": (
                    f"{numbers['long_word_share'] * 100:.0f}% of words have "
                    f"{LONG_WORD_LETTERS}+ letters (limit {max_long_word_share * 100:.0f}%)."
                ),
                "hint": "Prefer short concrete words; name files, commands and values instead of describing them.",
                "stats": numbers,
            }
        )
    if len(numbers["hedges"]) > max_hedges:
        listed = ", ".join(sorted(set(numbers["hedges"])))
        findings.append(
            {
                "id": "rule:hedges",
                "severity": "warn",
                "message": f"{len(numbers['hedges'])} hedge or filler phrases (limit {max_hedges}): {listed}.",
                "hint": "Delete the filler or replace it with the specific claim it stands in for.",
                "stats": numbers,
            }
        )
    return findings
