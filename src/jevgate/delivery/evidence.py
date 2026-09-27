"""Evidence for the delivery gate: diff parsing, filtering, chunking and
compaction, test-log parsing, post-change excerpts and budget checks.

Everything here is deterministic and offline; the gate turns these results
into the state the model reads.  Token counts use
:func:`jevgate.textstats.tokens` with ``kind="code"`` (``ceil(len/3)``).
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable, Iterable

from ..textstats import tokens

MAX_BLOCK_LINES = 200
MAX_NAMES = 300
TAIL_LINES = 40
_MARKER_ALLOWANCE = 16  # tokens reserved for a "[... N lines omitted ...]" line


class EvidenceError(RuntimeError):
    """Evidence could not be gathered, read or fitted into its budget."""


# ---------------------------------------------------------------------------
# Diff parsing

DEFAULT_LOCKFILES: tuple[str, ...] = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
    "Pipfile.lock", "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock",
)

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
_GENERATED_RE = re.compile(r"generated|do not edit", re.IGNORECASE)


@dataclass
class Hunk:
    """One ``@@`` hunk: its header line and the body lines that follow it."""

    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """The hunk as it appeared in the diff (header plus body)."""
        return "\n".join([self.header, *self.lines])


@dataclass
class FileDiff:
    """One file's part of a unified diff."""

    path: str
    old_path: str | None
    status: str  # added | modified | deleted | renamed | binary
    patch: str
    binary: bool
    hunks: list[Hunk]
    tokens: int

    @property
    def header(self) -> str:
        """The lines before the first hunk (``diff --git``, ``---``, ``+++`` ...)."""
        out: list[str] = []
        for line in self.patch.split("\n"):
            if _HUNK_RE.match(line):
                break
            out.append(line)
        return "\n".join(out)


def parse_diff(text: str) -> list[FileDiff]:
    """Parse a unified (git) diff into one :class:`FileDiff` per file.

    Never raises: CRLF is normalised, unknown lines stay in the patch text,
    and a diff without ``diff --git`` headers is split at ``---``/``+++`` pairs.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [_parse_block(block) for block in _split_blocks(lines)]


def _split_blocks(lines: list[str]) -> list[list[str]]:
    git_mode = any(line.startswith("diff --git ") for line in lines)
    blocks: list[list[str]] = []
    for i, line in enumerate(lines):
        if _starts_file(lines, i, git_mode) or (not blocks and _HUNK_RE.match(line)):
            blocks.append([])
        if blocks:
            blocks[-1].append(line)
    return blocks


def _starts_file(lines: list[str], i: int, git_mode: bool) -> bool:
    line = lines[i]
    if git_mode:
        return line.startswith("diff --git ")
    return line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ ")


def _strip_ab(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


def _unquote(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def _git_header_paths(rest: str) -> tuple[str | None, str | None]:
    """Both paths from the text after ``diff --git``."""
    if rest.startswith('"'):
        parts = _unquote(rest)
        if len(parts) >= 2:
            return _strip_ab(parts[0]), _strip_ab(parts[-1])
        return None, None
    idx = rest.rfind(" b/")
    if idx > 0:
        return _strip_ab(rest[:idx]), _strip_ab(rest[idx + 1:])
    parts = rest.split(" ")
    if len(parts) >= 2:
        return _strip_ab(parts[0]), _strip_ab(parts[-1])
    return None, None


def _diff_path(rest: str) -> str | None:
    """The path from a ``---``/``+++`` line; ``None`` for ``/dev/null``."""
    text = rest.split("\t")[0].strip()
    if text.startswith('"'):
        parts = _unquote(text)
        text = parts[0] if parts else text
    if text == "/dev/null":
        return None
    return _strip_ab(text)


def _parse_header(lines: list[str]) -> dict:
    git_a = git_b = old = new = rename_from = rename_to = None
    status = "modified"
    binary = False
    for line in lines:
        if line.startswith("diff --git "):
            git_a, git_b = _git_header_paths(line[len("diff --git "):])
        elif line.startswith("--- "):
            old = _diff_path(line[4:])
        elif line.startswith("+++ "):
            new = _diff_path(line[4:])
        elif line.startswith("rename from "):
            rename_from, status = line[len("rename from "):], "renamed"
        elif line.startswith("rename to "):
            rename_to, status = line[len("rename to "):], "renamed"
        elif line.startswith("new file mode"):
            status = "added"
        elif line.startswith("deleted file mode"):
            status = "deleted"
        elif line.startswith("Binary files ") or line == "GIT binary patch":
            binary = True
    has_markers = any(line.startswith(("--- ", "+++ ")) for line in lines)
    if has_markers and status == "modified":
        if old is None and new is not None:
            status = "added"
        elif new is None and old is not None:
            status = "deleted"
    if binary:
        status = "binary"
    path = rename_to or new or git_b or old or git_a or "(unknown)"
    old_path = rename_from or old
    if old_path == path:
        old_path = None
    return {"path": path, "old_path": old_path, "status": status, "binary": binary}


def _parse_hunks(lines: list[str]) -> list[Hunk]:
    hunks: list[Hunk] = []
    for line in lines:
        m = _HUNK_RE.match(line)
        if m:
            hunks.append(Hunk(
                header=line,
                old_start=int(m.group(1)),
                old_count=int(m.group(2)) if m.group(2) is not None else 1,
                new_start=int(m.group(3)),
                new_count=int(m.group(4)) if m.group(4) is not None else 1,
            ))
        elif hunks:
            hunks[-1].lines.append(line)
    return hunks


def _parse_block(lines: list[str]) -> FileDiff:
    patch = "\n".join(lines)
    first_hunk = next((i for i, line in enumerate(lines) if _HUNK_RE.match(line)), len(lines))
    info = _parse_header(lines[:first_hunk])
    return FileDiff(
        path=info["path"],
        old_path=info["old_path"],
        status=info["status"],
        patch=patch,
        binary=info["binary"],
        hunks=_parse_hunks(lines[first_hunk:]),
        tokens=tokens(patch, "code"),
    )


# ---------------------------------------------------------------------------
# Git

def _run_git(repo: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise EvidenceError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace").strip() if exc.stderr else ""
        raise EvidenceError(f"git {' '.join(args)} failed: {stderr or f'exit {exc.returncode}'}") from exc
    return proc.stdout.decode("utf-8", errors="replace")


def git_diff(repo: Path, base: str, head: str | None = None) -> str:
    """The unified diff of ``base...head`` (merge-base form), or of ``base``
    against the working tree when ``head`` is ``None``."""
    target = f"{base}...{head}" if head else base
    return _run_git(repo, ["diff", "--no-color", "--no-ext-diff", "-U3", target])


def untracked_files(repo: Path) -> list[str]:
    """Paths of untracked, non-ignored files in ``repo``."""
    out = _run_git(repo, ["ls-files", "--others", "--exclude-standard"])
    return [line for line in out.splitlines() if line]


# ---------------------------------------------------------------------------
# Filtering

def ignored(path: str, patterns: Iterable[str]) -> bool:
    """Whether ``path`` matches any glob, tried against the path and every
    ``/``-suffix of it (so ``*.min.js`` and ``dist/*`` both work)."""
    parts = path.split("/")
    suffixes = ["/".join(parts[i:]) for i in range(len(parts))]
    return any(fnmatch(suffix, pattern) for pattern in patterns for suffix in suffixes)


def _looks_generated(file: FileDiff) -> bool:
    if file.status == "deleted" or not file.hunks:
        return False
    first = file.hunks[0]
    if first.new_start > 1:
        return False
    head = [line[1:] for line in first.lines if line[:1] in ("+", " ")][:5]
    return any(_GENERATED_RE.search(line) for line in head)


def _drop_reason(file: FileDiff, ignore: list[str], max_file_tokens: int) -> str | None:
    if file.binary:
        return "binary"
    if Path(file.path).name in DEFAULT_LOCKFILES:
        return "lockfile"
    if ignored(file.path, ignore):
        return "ignored"
    if _looks_generated(file):
        return "generated"
    if file.tokens > max_file_tokens:
        return "too_large"
    return None


def filter_files(
    files: list[FileDiff], *, ignore: list[str], max_file_tokens: int
) -> tuple[list[FileDiff], list[dict]]:
    """Split ``files`` into the ones worth reviewing and dropped records
    ``{path, status, dropped_reason, tokens}``.  Reasons: ``binary``,
    ``lockfile``, ``ignored`` (config globs), ``generated`` (one of the first
    five new lines says generated / do not edit), ``too_large``."""
    kept: list[FileDiff] = []
    dropped: list[dict] = []
    for file in files:
        reason = _drop_reason(file, ignore, max_file_tokens)
        if reason:
            dropped.append({"path": file.path, "status": file.status, "dropped_reason": reason, "tokens": file.tokens})
        else:
            kept.append(file)
    return kept, dropped


# ---------------------------------------------------------------------------
# Chunking and compaction

@dataclass
class Chunk:
    """A reviewable slice of one file's patch (``index`` is 1-based)."""

    path: str
    index: int
    total: int
    patch: str
    truncated: bool
    tokens: int


def _cut_lines(text: str, budget: int) -> tuple[str, bool]:
    """Keep the leading lines of ``text`` that fit ``budget`` tokens (always
    the first line) and append ``[... N lines omitted ...]`` when cut."""
    lines = text.split("\n")
    limit = max(budget - _MARKER_ALLOWANCE, 0) * 3
    kept: list[str] = []
    chars = 0
    for line in lines:
        chars += len(line) + 1
        if kept and chars > limit:
            break
        kept.append(line)
    omitted = len(lines) - len(kept)
    if omitted == 0:
        return text, False
    kept.append(f"[... {omitted} lines omitted ...]")
    return "\n".join(kept), True


def chunk_file(file: FileDiff, file_budget: int) -> list[Chunk]:
    """Split a file's patch into chunks of at most ``file_budget`` tokens.

    One chunk when the patch fits; otherwise whole hunks are grouped
    greedily, and a hunk that alone exceeds the budget becomes a truncated
    chunk.  Every chunk starts with the file header so it is a valid diff."""
    if file.tokens <= file_budget:
        return [Chunk(file.path, 1, 1, file.patch, False, file.tokens)]
    header = file.header
    if not file.hunks:
        text, cut = _cut_lines(file.patch, file_budget)
        return [Chunk(file.path, 1, 1, text, cut, tokens(text, "code"))]
    header_tokens = tokens(header + "\n", "code")
    groups: list[tuple[list[str], bool]] = []
    current: list[str] = []
    current_tokens = 0
    for hunk in file.hunks:
        text = hunk.text
        cost = tokens(text + "\n", "code")
        if header_tokens + cost > file_budget:
            if current:
                groups.append((current, False))
                current, current_tokens = [], 0
            cut, _ = _cut_lines(text, file_budget - header_tokens)
            groups.append(([cut], True))
            continue
        if current and header_tokens + current_tokens + cost > file_budget:
            groups.append((current, False))
            current, current_tokens = [], 0
        current.append(text)
        current_tokens += cost
    if current:
        groups.append((current, False))
    chunks: list[Chunk] = []
    for i, (texts, truncated) in enumerate(groups, 1):
        patch = "\n".join([header, *texts])
        chunks.append(Chunk(file.path, i, len(groups), patch, truncated, tokens(patch, "code")))
    return chunks


def _compact_file(file: FileDiff, keep: int) -> str:
    parts = [line for line in file.header.split("\n") if not line.startswith("index ")]
    for hunk in file.hunks:
        parts.append(hunk.header)
        if keep:
            parts.extend(hunk.lines[:keep])
            more = len(hunk.lines) - keep
            if more > 0:
                parts.append(f"[... {more} more lines ...]")
    return "\n".join(parts)


def compact_diff(files: list[FileDiff], budget: int) -> tuple[str, bool]:
    """The whole diff when it fits ``budget`` tokens, else a compacted view:
    file headers plus each hunk's header and first 20 lines; then headers
    only; finally a path list.  Returns ``(text, compacted)``."""
    full = "\n".join(file.patch for file in files)
    if tokens(full, "code") <= budget:
        return full, False
    for keep in (20, 0):
        text = "\n".join(_compact_file(file, keep) for file in files)
        if tokens(text, "code") <= budget:
            return text, True
    return "\n".join(f"{file.path} ({file.status})" for file in files), True


# ---------------------------------------------------------------------------
# Test logs

@dataclass
class TestLog:
    """What a test runner's output says, in one shape for every tool."""

    path: str
    tool: str  # pytest | unittest | jest | go | cargo | unknown
    passed: int | None
    failed: int | None
    skipped: int | None
    errors: int | None
    failing: list[str]
    failing_blocks: list[str]
    names: list[str]
    summary: str | None
    tail: str
    sha256: str
    tokens: int


_PYTEST_SESSION_RE = re.compile(r"^=+ test session starts =+$")
_PYTEST_SUMMARY_RE = re.compile(
    r"^=+ (.*?\b(?:passed|failed|errors?|skipped|xfailed|xpassed|no tests ran)\b.*?) =+$"
)
_PYTEST_STATUS_RE = re.compile(r"^(\S+::\S+)\s+(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b")
_PYTEST_SHORT_RE = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (\S+?)(?: - .*)?$")
_PYTEST_FAIL_HEADER_RE = re.compile(r"^_{3,} (.+?) _{3,}$")
_PYTEST_SECTION_RE = re.compile(r"^={3,} .* ={3,}$")
_COUNT_WORD_RE = re.compile(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed|ignored|total|todo)\b")

_UT_RAN_RE = re.compile(r"^Ran (\d+) tests? in")
_UT_OK_RE = re.compile(r"^OK(?: \((.*)\))?$")
_UT_FAILED_RE = re.compile(r"^FAILED \((.*)\)$")
_UT_FAIL_RE = re.compile(r"^(FAIL|ERROR): (\S+) \(([\w.]+)\)")
_UT_VERBOSE_RE = re.compile(
    r"^(\w+) \(([\w.]+)\) \.\.\. (ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)$"
)
_UT_RULE_RE = re.compile(r"^(=+|-+)$")

_JEST_TESTS_RE = re.compile(r"^\s*Tests:\s+(.*)$")
_JEST_SUITES_RE = re.compile(r"^\s*Test Suites:\s+")
_JEST_PASS_RE = re.compile(r"^\s*[✓✔√] (.+?)(?: \(\d+ ?m?s\))?$")
_JEST_FAIL_RE = re.compile(r"^\s*[✕✗×] (.+?)(?: \(\d+ ?m?s\))?$")
_JEST_SKIP_RE = re.compile(r"^\s*○ (?:skipped )?(.+?)$")
_JEST_BULLET_RE = re.compile(r"^\s*● (.+?)$")
_JEST_FILE_RE = re.compile(r"^\s*(PASS|FAIL) \S+\.(test|spec)\.[cm]?[jt]sx?\b")

_GO_RESULT_RE = re.compile(r"^(\s*)--- (PASS|FAIL|SKIP): (\S+)")
_GO_RUN_RE = re.compile(r"^=== RUN\s+(\S+)")
_GO_PKG_RE = re.compile(r"^(ok|FAIL|\?)\s+(\S+)")
_GO_PKG_TIMED_RE = re.compile(r"^(ok|FAIL)\s+\S+\s+[\d.]+s\b")

_CARGO_RESULT_RE = re.compile(r"^test result: (ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored")
_CARGO_TEST_RE = re.compile(r"^test (\S+) \.\.\. (ok|FAILED|ignored)")
_CARGO_STDOUT_RE = re.compile(r"^---- (\S+) stdout ----$")
_CARGO_RUNNING_RE = re.compile(r"^running \d+ tests?$")
_CARGO_LISTED_RE = re.compile(r"^    (\S+)$")

_UNKNOWN_NAME_RE = re.compile(r"\b(test_\w+|Test\w+)\b|\bit\(\s*[\"'](.+?)[\"']")

_SIGNATURES: dict[str, list[re.Pattern]] = {
    "pytest": [_PYTEST_SESSION_RE, _PYTEST_SUMMARY_RE, _PYTEST_STATUS_RE, _PYTEST_SHORT_RE,
               re.compile(r"short test summary info")],
    "unittest": [_UT_RAN_RE, _UT_FAIL_RE, _UT_FAILED_RE, _UT_VERBOSE_RE],
    "jest": [_JEST_TESTS_RE, _JEST_SUITES_RE, _JEST_PASS_RE, _JEST_FAIL_RE, _JEST_FILE_RE],
    "go": [_GO_RESULT_RE, _GO_RUN_RE, _GO_PKG_TIMED_RE, re.compile(r"^FAIL\s+\S+\s+\[")],
    "cargo": [_CARGO_RESULT_RE, _CARGO_TEST_RE, _CARGO_RUNNING_RE],
}


def detect_tool(lines: list[str]) -> str:
    """Guess which runner wrote ``lines`` by counting signature lines."""
    best, best_score = "unknown", 0
    for tool, patterns in _SIGNATURES.items():
        score = sum(1 for line in lines if any(p.match(line) or p.search(line) for p in patterns))
        if score > best_score:
            best, best_score = tool, score
    return best


def _unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _cap_blocks(blocks: list[str], max_lines: int) -> list[str]:
    """Keep whole blocks up to ``max_lines`` lines in total, cutting the one
    that crosses the limit with a marker line (which counts toward the cap)."""
    out: list[str] = []
    used = 0
    for block in blocks:
        lines = block.split("\n")
        if used + len(lines) <= max_lines:
            out.append(block)
            used += len(lines)
            continue
        room = max_lines - used - 1
        if room >= 0:
            out.append("\n".join(lines[:room] + [f"[... {len(lines) - room} lines omitted ...]"]))
        break
    return out


def _count_words(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in _COUNT_WORD_RE.finditer(text):
        key = m.group(2)
        if key in ("error", "errors"):
            key = "errors"
        counts[key] = counts.get(key, 0) + int(m.group(1))
    return counts


def _kv_counts(text: str) -> dict[str, int]:
    """``failures=1, errors=2`` style pairs."""
    return {m.group(1): int(m.group(2)) for m in re.finditer(r"(\w+(?: \w+)?)=(\d+)", text)}


def _parsed(
    passed: int | None = None, failed: int | None = None, skipped: int | None = None,
    errors: int | None = None, failing: list[str] | None = None, blocks: list[str] | None = None,
    names: list[str] | None = None, summary: str | None = None,
) -> dict:
    return {
        "passed": passed, "failed": failed, "skipped": skipped, "errors": errors,
        "failing": failing or [], "blocks": blocks or [], "names": names or [], "summary": summary,
    }


def _pytest_blocks(lines: list[str]) -> list[str]:
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if _PYTEST_FAIL_HEADER_RE.match(line):
            if current:
                blocks.append(current)
            current = [line]
        elif _PYTEST_SECTION_RE.match(line):
            if current:
                blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block).rstrip("\n") for block in blocks]


def _parse_pytest(lines: list[str]) -> dict:
    summary = None
    for line in lines:
        m = _PYTEST_SUMMARY_RE.match(line)
        if m and not _PYTEST_SESSION_RE.match(line):
            summary = m.group(1).strip()
    failing: list[str] = []
    names: list[str] = []
    seen_status: dict[str, str] = {}
    for line in lines:
        m = _PYTEST_STATUS_RE.match(line)
        if m:
            name, status = m.group(1), m.group(2)
        else:
            m = _PYTEST_SHORT_RE.match(line)
            if not m or m.group(2).startswith("["):
                continue
            status, name = m.group(1), m.group(2)
        names.append(name)
        seen_status.setdefault(name, status)
        if status in ("FAILED", "ERROR"):
            failing.append(name)
    if summary:
        c = _count_words(summary)
        counts = (c.get("passed", 0), c.get("failed", 0), c.get("skipped", 0), c.get("errors", 0))
    elif seen_status:
        statuses = list(seen_status.values())
        counts = (statuses.count("PASSED"), statuses.count("FAILED"), statuses.count("SKIPPED"), statuses.count("ERROR"))
    else:
        counts = (None, None, None, None)
    return _parsed(*counts, failing=failing, blocks=_pytest_blocks(lines), names=names, summary=summary)


def _ut_name(method: str, qualifier: str) -> str:
    return qualifier if qualifier.endswith("." + method) else f"{qualifier}.{method}"


def _unittest_blocks(lines: list[str]) -> list[str]:
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for i, line in enumerate(lines):
        if _UT_FAIL_RE.match(line):
            if current:
                blocks.append(current)
            current = [line]
            continue
        if current is None:
            continue
        ends_at_rule = line.startswith("=") and _UT_RULE_RE.match(line)
        before_ran = _UT_RULE_RE.match(line) and i + 1 < len(lines) and lines[i + 1].startswith("Ran ")
        if ends_at_rule or before_ran:
            blocks.append(current)
            current = None
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block).rstrip("\n") for block in blocks]


def _parse_unittest(lines: list[str]) -> dict:
    total = failed = errors = skipped = None
    summary = None
    for line in lines:
        m = _UT_RAN_RE.match(line)
        if m:
            total = int(m.group(1))
            continue
        m = _UT_OK_RE.match(line)
        if m:
            summary = line
            extra = _kv_counts(m.group(1) or "")
            failed, errors, skipped = 0, 0, extra.get("skipped", 0)
            continue
        m = _UT_FAILED_RE.match(line)
        if m:
            summary = line
            extra = _kv_counts(m.group(1))
            failed, errors, skipped = extra.get("failures", 0), extra.get("errors", 0), extra.get("skipped", 0)
    passed = None
    if total is not None and failed is not None:
        passed = max(total - failed - errors - skipped, 0)
    failing: list[str] = []
    names: list[str] = []
    for line in lines:
        m = _UT_VERBOSE_RE.match(line)
        if m:
            name = _ut_name(m.group(1), m.group(2))
            names.append(name)
            if m.group(3) in ("FAIL", "ERROR"):
                failing.append(name)
            continue
        m = _UT_FAIL_RE.match(line)
        if m:
            name = _ut_name(m.group(2), m.group(3))
            names.append(name)
            failing.append(name)
    if summary and total is not None:
        summary = f"Ran {total} tests: {summary}"
    return _parsed(passed, failed, skipped, errors, failing=failing, blocks=_unittest_blocks(lines),
                   names=names, summary=summary)


def _jest_blocks(lines: list[str]) -> list[str]:
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        bullet = _JEST_BULLET_RE.match(line)
        if bullet or _JEST_TESTS_RE.match(line) or _JEST_SUITES_RE.match(line) or line.startswith("Snapshots:"):
            if current:
                blocks.append(current)
            current = [line] if bullet and not bullet.group(1).startswith("Console") else None
        elif current is not None:
            current.append(line)
    if current:
        blocks.append(current)
    return ["\n".join(block).rstrip("\n") for block in blocks]


def _parse_jest(lines: list[str]) -> dict:
    summary = None
    for line in lines:
        m = _JEST_TESTS_RE.match(line)
        if m:
            summary = line.strip()
    names: list[str] = []
    line_status: list[str] = []
    bullets: list[str] = []
    suite_errors = 0
    for line in lines:
        for pattern, status in ((_JEST_PASS_RE, "passed"), (_JEST_FAIL_RE, "failed"), (_JEST_SKIP_RE, "skipped")):
            m = pattern.match(line)
            if m:
                names.append(m.group(1))
                line_status.append(status)
                break
        else:
            m = _JEST_BULLET_RE.match(line)
            if m:
                text = m.group(1)
                if text.startswith("Test suite failed to run"):
                    suite_errors += 1
                elif not text.startswith("Console"):
                    bullets.append(text)
    failing = list(bullets)
    for name, status in zip(names, line_status):
        if status == "failed" and not any(b == name or b.endswith(" › " + name) for b in bullets):
            failing.append(name)
    if summary:
        c = _count_words(summary)
        counts = (c.get("passed", 0), c.get("failed", 0), c.get("skipped", 0) + c.get("todo", 0))
    elif line_status:
        counts = (line_status.count("passed"), line_status.count("failed"), line_status.count("skipped"))
    else:
        counts = (None, None, None)
    errors = suite_errors if (summary or line_status or suite_errors) else None
    return _parsed(*counts, errors, failing=failing, blocks=_jest_blocks(lines), names=names, summary=summary)


def _parse_go(lines: list[str]) -> dict:
    names: list[str] = []
    failing: list[str] = []
    by_status = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    pkg_ok = pkg_fail = build_failed = 0
    blocks: list[list[str]] = []
    since_run: list[str] = []
    current: list[str] | None = None
    for line in lines:
        m = _GO_RESULT_RE.match(line)
        if m:
            indent, status, name = m.groups()
            names.append(name)
            by_status[status] += 1
            if status == "FAIL":
                failing.append(name)
            if not indent:
                if current:
                    blocks.append(current)
                current = since_run + [line] if status == "FAIL" else None
                since_run = []
            elif current is not None:
                current.append(line)
            continue
        m = _GO_RUN_RE.match(line)
        if m:
            names.append(m.group(1))
            if current:
                blocks.append(current)
                current = None
            since_run = [line]
            continue
        m = _GO_PKG_RE.match(line)
        if m:
            if current:
                blocks.append(current)
                current = None
            since_run = []
            if m.group(1) == "ok":
                pkg_ok += 1
            elif m.group(1) == "FAIL":
                pkg_fail += 1
                if "[build failed]" in line or "[setup failed]" in line:
                    build_failed += 1
            continue
        if line.startswith((" ", "\t")):
            if current is not None:
                current.append(line)
            elif since_run:
                since_run.append(line)
        elif current:
            blocks.append(current)
            current = None
    if current:
        blocks.append(current)
    has_info = any(by_status.values()) or pkg_ok or pkg_fail
    if not has_info:
        return _parsed(names=names, blocks=[])
    parts = []
    if pkg_ok or pkg_fail:
        parts.append(f"packages: {pkg_ok} ok, {pkg_fail} FAIL")
    if any(by_status.values()):
        parts.append(f"tests: {by_status['PASS']} passed, {by_status['FAIL']} failed, {by_status['SKIP']} skipped")
    return _parsed(
        by_status["PASS"] if by_status["PASS"] else None, by_status["FAIL"], by_status["SKIP"], build_failed,
        failing=failing, blocks=["\n".join(b).rstrip("\n") for b in blocks], names=names, summary="; ".join(parts),
    )


def _parse_cargo(lines: list[str]) -> dict:
    passed = failed = ignored = 0
    results: list[str] = []
    names: list[str] = []
    failing: list[str] = []
    blocks: list[list[str]] = []
    current: list[str] | None = None
    in_failure_list = False
    for line in lines:
        m = _CARGO_RESULT_RE.match(line)
        if m:
            results.append(line)
            passed += int(m.group(2))
            failed += int(m.group(3))
            ignored += int(m.group(4))
        m = _CARGO_TEST_RE.match(line)
        if m:
            names.append(m.group(1))
            if m.group(2) == "FAILED":
                failing.append(m.group(1))
        m = _CARGO_STDOUT_RE.match(line)
        if m or line == "failures:" or _CARGO_RESULT_RE.match(line):
            if current:
                blocks.append(current)
            current = [line] if m else None
            in_failure_list = line == "failures:" and not m
            continue
        if current is not None:
            current.append(line)
        elif in_failure_list:
            listed = _CARGO_LISTED_RE.match(line)
            if listed:
                failing.append(listed.group(1))
    if current:
        blocks.append(current)
    if not results and not names:
        return _parsed()
    counts = (passed, failed, ignored) if results else (None, None, None)
    return _parsed(*counts, errors=None, failing=failing, blocks=["\n".join(b).rstrip("\n") for b in blocks],
                   names=names, summary="; ".join(results) if results else None)


def _parse_unknown(lines: list[str]) -> dict:
    names = [m.group(1) or m.group(2) for line in lines for m in _UNKNOWN_NAME_RE.finditer(line)]
    return _parsed(names=names)


_PARSERS: dict[str, Callable[[list[str]], dict]] = {
    "pytest": _parse_pytest,
    "unittest": _parse_unittest,
    "jest": _parse_jest,
    "go": _parse_go,
    "cargo": _parse_cargo,
    "unknown": _parse_unknown,
}


def parse_test_log(path: str, text: str) -> TestLog:
    """Parse a runner's output (pytest, unittest, jest, go test, cargo test;
    anything else is ``unknown`` with names guessed by regex and no counts)."""
    lines = text.splitlines()
    tool = detect_tool(lines)
    result = _PARSERS[tool](lines)
    return TestLog(
        path=path,
        tool=tool,
        passed=result["passed"],
        failed=result["failed"],
        skipped=result["skipped"],
        errors=result["errors"],
        failing=_unique(result["failing"]),
        failing_blocks=_cap_blocks(result["blocks"], MAX_BLOCK_LINES),
        names=_unique(result["names"])[:MAX_NAMES],
        summary=result["summary"],
        tail="\n".join(lines[-TAIL_LINES:]),
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        tokens=tokens(text, "code"),
    )


# ---------------------------------------------------------------------------
# State fragments and budgets

def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _state_tokens(obj: Any) -> int:
    return tokens(_dumps(obj), "code")


def _sum_or_none(values: list[int | None]) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _tail_view(lines: list[str], keep: int) -> str:
    if keep >= len(lines):
        return "\n".join(lines)
    marker = f"[... {len(lines) - keep} lines trimmed ...]"
    return "\n".join([marker, *lines[len(lines) - keep:]]) if keep else marker


def _list_view(items: list[str], keep: int, what: str) -> list[str]:
    if keep >= len(items):
        return list(items)
    return items[:keep] + [f"[... {len(items) - keep} more {what} ...]"]


def _blocks_view(blocks: list[str], keep_lines: int) -> list[str]:
    """The first ``keep_lines`` lines of the failing blocks, whole blocks
    first, the crossing block cut with a marker counting everything left."""
    total = sum(block.count("\n") + 1 for block in blocks)
    if keep_lines >= total:
        return list(blocks)
    out: list[str] = []
    used = 0
    for block in blocks:
        lines = block.split("\n")
        if used + len(lines) <= keep_lines:
            out.append(block)
            used += len(lines)
            continue
        room = max(keep_lines - used - 1, 0)
        marker = f"[... {total - used - room} lines of failure output trimmed ...]"
        out.append("\n".join(lines[:room] + [marker]))
        break
    return out


def _merge_logs(logs: list[TestLog]) -> dict:
    tools = _unique(log.tool for log in logs)
    summaries = [log.summary if len(logs) == 1 else f"{log.path}: {log.summary}" for log in logs if log.summary]
    tail = logs[0].tail if len(logs) == 1 else "\n".join(f"--- {log.path} ---\n{log.tail}" for log in logs)
    return {
        "tool": "+".join(tools) if tools else "none",
        "summary": "; ".join(summaries) or None,
        "counts": {key: _sum_or_none([getattr(log, key) for log in logs]) for key in ("passed", "failed", "skipped", "errors")},
        "failing": _unique(name for log in logs for name in log.failing),
        "failing_blocks": _cap_blocks([b for log in logs for b in log.failing_blocks], MAX_BLOCK_LINES),
        "names": _unique(name for log in logs for name in log.names)[:MAX_NAMES],
        "tail": tail,
    }


def tests_state(logs: list[TestLog], budget: int) -> dict:
    """The ``tests`` fragment of the whole-change state, merged across logs
    and trimmed to ``budget`` tokens: the tail first, then names, then the
    failing blocks (each with a marker saying what was cut)."""
    state = _merge_logs(logs)
    tail_lines = state["tail"].splitlines()
    names, blocks = state["names"], state["failing_blocks"]
    keep = {"tail": len(tail_lines), "names": len(names), "blocks": sum(b.count("\n") + 1 for b in blocks)}

    def build() -> dict:
        view = dict(state)
        view["tail"] = _tail_view(tail_lines, keep["tail"])
        view["names"] = _list_view(names, keep["names"], "names")
        view["failing_blocks"] = _blocks_view(blocks, keep["blocks"])
        return view

    view = build()
    for key in ("tail", "names", "blocks"):
        while _state_tokens(view) > budget and keep[key] > 0:
            keep[key] //= 2
            view = build()
    return view


_EXCERPT_SPEC_RE = re.compile(r"^(.*?)(?::(\d+)(?:-(\d+))?)?$")


def read_excerpt(repo: Path, spec: str) -> dict:
    """Read ``PATH[:START-END]`` (1-based, inclusive) relative to ``repo``
    into ``{path, range, text}``; ``range`` is ``None`` for a whole file."""
    m = _EXCERPT_SPEC_RE.match(spec)
    path_text, start, end = (m.group(1), m.group(2), m.group(3)) if m else (spec, None, None)
    path = Path(path_text)
    if not path.is_absolute():
        path = repo / path
    if not path.is_file():
        raise EvidenceError(f"--files {spec}: no such file")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise EvidenceError(f"--files {spec}: {exc}") from exc
    if start is None:
        return {"path": path_text, "range": None, "text": text}
    lines = text.splitlines()
    first = int(start)
    last = int(end) if end else first
    if first < 1 or last < first:
        raise EvidenceError(f"--files {spec}: range must be START-END with 1 <= START <= END")
    if first > len(lines):
        raise EvidenceError(f"--files {spec}: line {first} is past the end ({len(lines)} lines)")
    last = min(last, len(lines))
    return {"path": path_text, "range": [first, last], "text": "\n".join(lines[first - 1:last])}


def files_after_state(excerpts: list[dict], budget: int) -> list[dict]:
    """The ``files_after`` fragment: ``{path, range, text}`` per excerpt,
    trimmed to ``budget`` tokens by halving the largest excerpt's lines
    (with an ``[... N lines omitted ...]`` marker) until it fits."""
    items = [{"path": e.get("path", ""), "range": e.get("range"), "text": e.get("text") or ""} for e in excerpts]
    lines = [item["text"].split("\n") for item in items]
    keep = [len(ls) for ls in lines]

    def view(i: int) -> dict:
        shown = lines[i][:keep[i]]
        if keep[i] < len(lines[i]):
            shown = shown + [f"[... {len(lines[i]) - keep[i]} lines omitted ...]"]
        return {"path": items[i]["path"], "range": items[i]["range"], "text": "\n".join(shown)}

    out = [view(i) for i in range(len(items))]
    while items and _state_tokens(out) > budget:
        largest = max(range(len(items)), key=lambda i: (keep[i] > 0, tokens(out[i]["text"], "code")))
        if keep[largest] == 0:
            break
        keep[largest] //= 2
        out = [view(i) for i in range(len(items))]
    return out


def _largest_key(state: dict) -> tuple[str, int]:
    if not state:
        return "(empty)", 0
    sizes = {str(key): _state_tokens(value) for key, value in state.items()}
    key = max(sizes, key=sizes.get)
    return key, sizes[key]


def assert_budget(state: dict, questions: dict, *, state_limit: int = 30000, request_limit: int = 60000) -> None:
    """Raise :class:`EvidenceError` when the state plus the longest question
    exceeds ``state_limit`` tokens or the whole request exceeds
    ``request_limit``; the message names the largest top-level state key."""
    state_tokens = _state_tokens(state)
    values = list(questions.values()) if isinstance(questions, dict) else list(questions or [])
    question_tokens = [tokens(_dumps(q), "prose") for q in values]
    longest = max(question_tokens, default=0)
    key, size = _largest_key(state)
    if state_tokens + longest > state_limit:
        raise EvidenceError(
            f"state too large: {state_tokens} state tokens + {longest} for the longest question "
            f"exceed {state_limit}; largest state key is '{key}' ({size} tokens)"
        )
    total = state_tokens + sum(question_tokens)
    if total > request_limit:
        raise EvidenceError(
            f"request too large: {total} tokens ({state_tokens} state + {sum(question_tokens)} across "
            f"{len(question_tokens)} questions) exceed {request_limit}; largest state key is '{key}' ({size} tokens)"
        )


def evidence_summary(
    kept: list[FileDiff], dropped: list[dict], chunks_by_path: dict[str, list[Chunk]],
    logs: list[TestLog], compacted: bool,
) -> dict:
    """The report's ``evidence`` fragment: what was reviewed, what was
    dropped and why, which test logs were read, and whether the diff had
    to be compacted."""
    files = []
    for file in kept:
        chunks = chunks_by_path.get(file.path, [])
        files.append({
            "path": file.path, "status": file.status, "tokens": file.tokens,
            "chunks": len(chunks), "truncated": any(c.truncated for c in chunks),
        })
    for record in dropped:
        files.append({
            "path": record["path"], "status": record.get("status", "modified"), "tokens": record.get("tokens", 0),
            "chunks": 0, "truncated": False, "dropped_reason": record["dropped_reason"],
        })
    tests = [
        {"path": log.path, "tool": log.tool, "passed": log.passed, "failed": log.failed,
         "skipped": log.skipped, "errors": log.errors, "names_count": len(log.names), "sha256": log.sha256}
        for log in logs
    ]
    return {"files": files, "tests": tests, "compacted": compacted}
