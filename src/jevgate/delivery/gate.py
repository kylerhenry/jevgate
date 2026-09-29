"""The delivery gate: inputs, rules, per-file and whole-change requests,
aggregation of readings, findings, gather list and verdict.

Policy (never a holistic question):

* rules first — ``diff_empty`` and ``tests_failing`` fail without an API
  call; ``no_test_logs``, ``unreviewed_file``, ``untracked_files`` and
  ``missing_context_area`` warn;
* one request per reviewable chunk of each kept file, and one per chunk of
  the whole change (whole hunks grouped under the state budget, never
  compacted); readings per chunk, worst chunk wins, worst file wins per
  file gate; change gates combine per family (``_read_change_jobs``);
* verdict: ``revise`` on any fail, else ``gather`` when a reading is
  unclear, else ``uncertain`` when a reading is unknown, else ``accept``
  when proven (or ``--no-tests-ok``), else ``unproven``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..cli import DELIVERY_EXITS
from ..client import Job, TypeSafeClient
from ..config import Config
from ..context import ContextPack
from ..linear import Linear, issue_to_ticket, load_linear_key
from ..markdown import parse_ticket
from ..questions import Gate, Reading, read, to_questions
from ..report import Finding, Report, sort_findings
from ..runs import Run, delta
from ..textstats import tokens
from . import rubric
from .evidence import (
    Chunk,
    EvidenceError,
    FileDiff,
    TestLog,
    assert_budget,
    change_summary,
    chunk_change,
    chunk_file,
    evidence_summary,
    files_after_state,
    filter_files,
    git_diff,
    ignored,
    outline_diff,
    parse_diff,
    parse_test_log,
    read_excerpt,
    tests_state,
    untracked_files,
)
from .schema import dedupe_bullets

CONTEXT_AREAS = ("architecture", "components", "conventions")
WARNED_DROP_REASONS = ("too_large", "generated")
SKIPPED_REASONS = ("deleted", "not_source")  # no per-file questions, but still in the whole-change diff
_STATUS_RANK = {"fail": 4, "unclear": 3, "unknown": 2, "pass": 1, "na": 0}
_MIN_DIFF_BUDGET = 2000
_MAX_CHANGE_CHUNKS = 8  # more means the diff is judged in slivers; exit 4 instead
TICKET_KEYS = ("title", "why", "what")


class DeliveryError(ValueError):
    """The inputs cannot be assembled (bad ticket, no acceptance bullets, ...)."""


# ---------------------------------------------------------------------------
# Inputs


@dataclass
class DeliveryInputs:
    """Everything the gate judges, already read from disk, git or Linear."""

    ticket: dict
    diff_text: str
    files_after: list[dict] = field(default_factory=list)
    logs: list[TestLog] = field(default_factory=list)
    commands: list[dict] = field(default_factory=list)  # {path, text}: captured output of a command a bullet names
    repo: Path | None = None
    base: str | None = None
    head: str | None = None
    no_tests_ok: bool = False
    all_items: bool = False
    untracked: list[str] = field(default_factory=list)
    ticket_source: str = ""


def normalise_ticket(ticket: Any, source: str = "ticket") -> dict:
    """``{title, why, what, acceptance}`` with deduplicated bullets; raises without bullets."""
    if isinstance(ticket, dict) and "ticket" in ticket and "title" not in ticket:
        ticket = ticket["ticket"]
    if not isinstance(ticket, dict):
        raise DeliveryError(f"{source}: expected a ticket object")
    out = {key: str(ticket.get(key) or "").strip() for key in TICKET_KEYS}
    acceptance = ticket.get("acceptance") or []
    if isinstance(acceptance, str):
        acceptance = [acceptance]
    out["acceptance"] = dedupe_bullets(list(acceptance))
    if ticket.get("linear"):
        out["linear"] = ticket["linear"]
    if not out["acceptance"]:
        raise DeliveryError(f"{source}: the ticket has no acceptance bullets, so there is nothing to verify")
    return out


def _load_ticket(args: Any) -> tuple[dict, str]:
    path_text = getattr(args, "ticket", None)
    identifier = getattr(args, "from_linear", None)
    if path_text:
        path = Path(path_text)
        if not path.is_file():
            raise FileNotFoundError(f"ticket not found: {path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".json":
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise DeliveryError(f"{path}: not valid JSON ({exc})") from exc
            return normalise_ticket(data, str(path)), str(path)
        return normalise_ticket(parse_ticket(text), str(path)), str(path)
    if identifier:
        issue = Linear(load_linear_key()).get_issue(identifier)
        return normalise_ticket(issue_to_ticket(issue), identifier), f"linear:{identifier}"
    raise DeliveryError("a ticket source is required: --ticket F or --from-linear ID")


def _load_diff(args: Any, repo: Path) -> tuple[str, list[str]]:
    diff_file = getattr(args, "diff_file", None)
    if diff_file:
        path = Path(diff_file)
        if not path.is_file():
            raise FileNotFoundError(f"diff file not found: {path}")
        return path.read_text(encoding="utf-8", errors="replace"), []
    base = getattr(args, "base", None)
    if not base:
        raise DeliveryError("a diff source is required: --base REF [--head REF] or --diff-file F")
    head = getattr(args, "head", None)
    text = git_diff(repo, base, head)
    untracked = untracked_files(repo) if not head else []
    return text, untracked


def build_inputs(args: Any, cfg: Config) -> DeliveryInputs:
    """Read the ticket, the diff, the test logs and the ``--files`` excerpts named by ``args``."""
    repo = Path(getattr(args, "repo", None) or ".")
    ticket, source = _load_ticket(args)
    diff_text, untracked = _load_diff(args, repo)
    logs = []
    for log_path in getattr(args, "test_log", None) or []:
        path = Path(log_path)
        if not path.is_file():
            raise FileNotFoundError(f"test log not found: {path}")
        logs.append(parse_test_log(str(path), path.read_text(encoding="utf-8", errors="replace")))
    files_after = [read_excerpt(repo, spec) for spec in getattr(args, "files", None) or []]
    commands = []
    for out_path in getattr(args, "command_output", None) or []:
        path = Path(out_path)
        if not path.is_file():
            raise FileNotFoundError(f"command output not found: {path}")
        commands.append({"path": str(path), "text": path.read_text(encoding="utf-8", errors="replace")})
    return DeliveryInputs(
        ticket=ticket,
        diff_text=diff_text,
        files_after=files_after,
        logs=logs,
        commands=commands,
        repo=repo,
        base=getattr(args, "base", None),
        head=getattr(args, "head", None),
        no_tests_ok=bool(getattr(args, "no_tests_ok", False)),
        all_items=bool(getattr(args, "all_items", False)) or cfg.max_items is None,
        untracked=untracked,
        ticket_source=source,
    )


# ---------------------------------------------------------------------------
# Rules (no API call)


def _log_failures(logs: list[TestLog]) -> list[str]:
    names: list[str] = []
    for log in logs:
        if (log.failed or 0) > 0 or (log.errors or 0) > 0:
            names += log.failing or [f"{log.path}: {log.summary or 'failures reported'}"]
    return names


def rule_findings(inputs: DeliveryInputs, files: list[FileDiff], dropped: list[dict], pack: ContextPack | None) -> list[Finding]:
    """The rule findings: ``diff_empty`` and ``tests_failing`` fail; the rest warn."""
    findings: list[Finding] = []
    if not files:
        findings.append(Finding(
            id="rule:diff_empty", source="rule", severity="fail", gate="diff_empty",
            message="The diff contains no file changes.",
            hint="Point --base/--head or --diff-file at the change that implements the ticket; an empty diff cannot satisfy any acceptance bullet.",
        ))
    failing = _log_failures(inputs.logs)
    if failing:
        shown = ", ".join(failing[:8]) + (" ..." if len(failing) > 8 else "")
        findings.append(Finding(
            id="rule:tests_failing", source="rule", severity="fail", gate="tests_failing",
            location={"tests": failing[:20]},
            message=f"The test output reports failures: {shown}.",
            hint="Make every test pass (fix the change or the test it broke), re-run the suite and supply the fresh output with --test-log.",
        ))
    if not inputs.logs:
        findings.append(Finding(
            id="rule:no_test_logs", source="rule", severity="warn", gate="no_test_logs",
            message="No test output was supplied, so nothing can be proven.",
            hint="Run the test suite that covers the change and pass its verbatim output with --test-log F (or --no-tests-ok to accept unproven).",
        ))
    for record in dropped:
        if record.get("dropped_reason") in WARNED_DROP_REASONS:
            findings.append(Finding(
                id=f"rule:unreviewed_file:{record['path']}", source="rule", severity="warn", gate="unreviewed_file",
                location={"file": record["path"], "reason": record["dropped_reason"]},
                message=f"{record['path']} was not reviewed ({record['dropped_reason']}).",
                hint="Split an oversized file change into smaller commits or review that file by hand; generated files are skipped on purpose.",
            ))
    nameless = [log.path for log in inputs.logs if not log.names]
    if nameless:
        shown = ", ".join(nameless[:8]) + (" ..." if len(nameless) > 8 else "")
        findings.append(Finding(
            id="rule:test_log_has_no_names", source="rule", severity="warn", gate="test_log_has_no_names",
            location={"logs": nameless[:20]},
            message=f"The test output names no test ({shown}), so ac_proven and tests_exercise_change cannot pass on it.",
            hint="Run the suite so the log names each test (pytest -v or -rA, go test -v, jest --verbose; cargo test already does) and pass that output with --test-log.",
        ))
    if inputs.untracked:
        shown = ", ".join(inputs.untracked[:8]) + (" ..." if len(inputs.untracked) > 8 else "")
        findings.append(Finding(
            id="rule:untracked_files", source="rule", severity="warn", gate="untracked_files",
            location={"files": inputs.untracked[:50]},
            message=f"Untracked files are not part of the diff: {shown}.",
            hint="git add the new files (or commit and pass --head) so the diff includes them; otherwise they are invisible to the review.",
        ))
    for area in CONTEXT_AREAS:
        if pack is None or not pack.has(area):
            findings.append(Finding(
                id=f"rule:missing_context_area:{area}", source="rule", severity="warn", gate="missing_context_area",
                location={"area": area},
                message=f"No {area} notes in the context pack; the {', '.join(_families_for(area))} questions were skipped.",
                hint=_missing_area_hint(area),
            ))
    return findings


def _families_for(area: str) -> list[str]:
    return [family for family, needs in rubric.FAMILY_NEEDS.items() if needs == area]


def _missing_area_hint(area: str) -> str:
    if area == "architecture":
        return "Add an architecture note tagged #jevgate/architecture with `#jevgate/rule` bullets (or run `jevgate context init`) and pass --context-dir."
    if area == "components":
        return "Add one note per existing module tagged #jevgate/component with `path` and `provides` (or run `jevgate context init`) and pass --context-dir."
    return "Add a conventions note tagged #jevgate/conventions with `#jevgate/convention` bullets (add #jevgate/applies/py to scope one to a file type) and pass --context-dir."


def missing_area_gathers(pack: ContextPack | None) -> list[dict]:
    """Gather entries for absent areas (listed as optional; they never change the verdict)."""
    out = []
    for area in CONTEXT_AREAS:
        if pack is None or not pack.has(area):
            out.append({
                "area": area,
                "missing": f"the context pack has no {area} notes",
                "for": _families_for(area),
            })
    return out


# ---------------------------------------------------------------------------
# Requests


def split_reviewable(kept: list[FileDiff], source_globs: list[str]) -> tuple[list[FileDiff], list[dict]]:
    """Files that get the per-file questions, and records ``{path, status,
    dropped_reason, tokens}`` for the ones that do not: ``deleted`` files and
    files matching none of ``source_globs`` (``not_source``). Both kinds stay in
    the whole-change diff; they only skip the per-file questions."""
    reviewed: list[FileDiff] = []
    skipped: list[dict] = []
    for file in kept:
        reason = "deleted" if file.status == "deleted" else None if ignored(file.path, source_globs) else "not_source"
        if reason:
            skipped.append({"path": file.path, "status": file.status, "dropped_reason": reason, "tokens": file.tokens})
        else:
            reviewed.append(file)
    return reviewed, skipped


def _ticket_state(ticket: dict) -> dict:
    return {**{k: ticket.get(k, "") for k in TICKET_KEYS}, "acceptance": list(ticket.get("acceptance") or [])}


def _rows(gates: list[Gate], family: str, keys: tuple[str, ...]) -> list[dict]:
    return [{k: g.item[k] for k in keys if k in g.item} for g in gates if g.family == family and g.item]


def file_state(ticket: dict, file: FileDiff, chunk: Chunk, others: list[str], gates: list[Gate]) -> dict:
    """The per-file request state: the ticket, this chunk, the other changed paths
    and only the context items the gates were built from."""
    state: dict = {
        "ticket": _ticket_state(ticket),
        "file": {"path": chunk.path, "status": file.status, "patch": chunk.patch},
        "other_changed_files": others,
    }
    if chunk.total > 1 or chunk.truncated:
        state["file"]["chunk"] = {"index": chunk.index, "total": chunk.total, "truncated": chunk.truncated}
    rules = _rows(gates, "arch_rule", ("id", "text", "note", "line"))
    if rules:
        state["architecture"] = {"rules": rules}
    conventions = _rows(gates, "convention", ("id", "text", "note", "applies_to"))
    if conventions:
        state["conventions"] = conventions
    components = _rows(gates, "dup", ("id", "name", "path", "provides", "interface", "aliases"))
    if components:
        state["components"] = components
    return state


def _chunk_tag(path: str, index: int) -> str:
    return f"file:{path}#{index}"


@dataclass
class _FileJob:
    file: FileDiff
    chunk: Chunk
    gates: list[Gate]
    job: Job


def _file_jobs(inputs: DeliveryInputs, kept: list[FileDiff], all_paths: list[str], chunks: dict[str, list[Chunk]],
               pack: ContextPack | None, cfg: Config) -> list[_FileJob]:
    jobs: list[_FileJob] = []
    acceptance = inputs.ticket["acceptance"]
    for file in kept:
        query = f"{file.path}\n{file.patch}"
        gates = rubric.file_gates(file.path, pack, cfg, acceptance, query)
        others = [p for p in all_paths if p != file.path]
        for chunk in chunks[file.path]:
            state = file_state(inputs.ticket, file, chunk, others, gates)
            questions = to_questions(gates)
            assert_budget(state, questions)
            jobs.append(_FileJob(file, chunk, gates, Job(_chunk_tag(file.path, chunk.index), state, questions)))
    return jobs


def _change_tag(chunk: Chunk) -> str:
    """``change`` for a diff that went whole in one request, else ``change#<i>``."""
    return "change" if chunk.total == 1 and not chunk.truncated else f"change#{chunk.index}"


@dataclass
class ChangePlan:
    """The whole-change states, one per chunk, with the diff budget they were cut to."""

    states: list[tuple[dict, Chunk]]
    budget: int
    fixed: int


def plan_change(inputs: DeliveryInputs, kept: list[FileDiff], dropped: list[dict], cfg: Config) -> ChangePlan:
    """Build the whole-change states ``{ticket, diff, files_after, tests}``.

    The fixed part (ticket, excerpts, tests) is sized first; the diff gets
    ``state_budget`` less that, floored at ``_MIN_DIFF_BUDGET``.  A diff that
    fits goes whole in one state, byte-identical to a run before chunking
    existed.  A larger diff is split by :func:`chunk_change` at hunk
    boundaries, every chunk carrying ``chunk`` and a ``diff_outline`` of the
    whole diff; past ``_MAX_CHANGE_CHUNKS`` chunks :class:`EvidenceError` is
    raised before any request."""
    ticket = _ticket_state(inputs.ticket)
    if inputs.logs:
        tests = tests_state(inputs.logs, cfg.tests_budget)
    else:
        tests = {"tool": "none", "summary": "no test output was supplied"}
    files_after = files_after_state(inputs.files_after, cfg.file_budget) if inputs.files_after else []
    fixed = tokens(json.dumps({"ticket": ticket, "files_after": files_after, "tests": tests}, ensure_ascii=False), "code")
    budget = max(cfg.state_budget - fixed, _MIN_DIFF_BUDGET)

    def state(diff: str) -> dict:
        return {"ticket": ticket, "diff": diff, "files_after": files_after, "tests": tests}

    if not kept:
        diff = "\n".join(f"{d['path']} ({d['status']}, not reviewed: {d['dropped_reason']})" for d in dropped) or "(empty)"
        return ChangePlan([(state(diff), Chunk("change", 1, 1, diff, False, tokens(diff, "code")))], budget, fixed)
    chunks = chunk_change(kept, budget)
    if len(chunks) == 1 and not chunks[0].truncated:
        return ChangePlan([(state(chunks[0].patch), chunks[0])], budget, fixed)
    outline = outline_diff(kept)
    chunks = chunk_change(kept, max(budget - tokens(outline + "\n", "code"), 1))
    if len(chunks) > _MAX_CHANGE_CHUNKS:
        full_tokens = tokens("\n".join(file.patch for file in kept), "code")
        raise EvidenceError(
            f"change too large: {len(chunks)} chunks of {budget} tokens ({full_tokens} diff tokens, {fixed} fixed); "
            "split the change or supply fewer --files"
        )
    states: list[tuple[dict, Chunk]] = []
    for chunk in chunks:
        view = state(chunk.patch)
        view["chunk"] = {"index": chunk.index, "total": chunk.total, "truncated": chunk.truncated}
        view["diff_outline"] = outline
        states.append((view, chunk))
    return ChangePlan(states, budget, fixed)


def commands_state(commands: list[dict], budget: int) -> list[dict]:
    """The ``commands`` fragment: ``{path, text}`` per captured command output,
    trimmed to ``budget`` tokens the way ``files_after`` excerpts are.  ``path``
    is the file's name only, so the state (and its cache hash) does not depend
    on where the file was read from."""
    return [{"path": Path(item["path"]).name, "text": item["text"]} for item in files_after_state(commands, budget)]


def change_state(inputs: DeliveryInputs, kept: list[FileDiff], dropped: list[dict], cfg: Config) -> list[tuple[dict, Chunk]]:
    """The whole-change states within ``state_budget``, one per chunk (see :func:`plan_change`)."""
    return plan_change(inputs, kept, dropped, cfg).states


@dataclass
class _ChangeJob:
    chunk: Chunk
    gates: list[Gate]
    job: Job


COMMANDS_TAG = "commands"


def commands_job(inputs: DeliveryInputs, gates: list[Gate], cfg: Config) -> _ChangeJob | None:
    """The one request for the ``command_proven`` gates: the ticket and the
    captured command outputs, nothing else, so the change requests stay as they
    are for every ticket and the proof of a command is read from its output
    alone.  ``None`` when no bullet is command-backed."""
    asked = [g for g in gates if g.family == "command_proven"]
    if not asked:
        return None
    state = {"ticket": _ticket_state(inputs.ticket), "commands": commands_state(inputs.commands, cfg.tests_budget)}
    questions = to_questions(asked)
    assert_budget(state, questions)
    chunk = Chunk(COMMANDS_TAG, 1, 1, "", False, tokens(json.dumps(state, ensure_ascii=False), "code"))
    return _ChangeJob(chunk, asked, Job(COMMANDS_TAG, state, questions))


def _change_jobs(plan: ChangePlan, gates: list[Gate]) -> list[_ChangeJob]:
    """One job per change chunk.  Every chunk gets the change gates except
    ``ac_proven``, which reads only the tests fragment and is asked once, in
    the first chunk, and ``command_proven``, which has its own request
    (:func:`commands_job`)."""
    jobs: list[_ChangeJob] = []
    for state, chunk in plan.states:
        change = [g for g in gates if g.family != "command_proven"]
        asked = change if chunk.index == 1 else [g for g in change if g.family != "ac_proven"]
        questions = to_questions(asked)
        assert_budget(state, questions)
        jobs.append(_ChangeJob(chunk, asked, Job(_change_tag(chunk), state, questions)))
    return jobs


# ---------------------------------------------------------------------------
# Aggregation


def worse(a: Reading, b: Reading) -> Reading:
    """The worse of two readings of the same gate: fail > unclear > unknown >
    pass > na, then the larger failing probability (info gates: the larger p)."""
    if a.kind == "info":
        return a if (a.p or 0.0) >= (b.p or 0.0) else b
    key_a = (_STATUS_RANK.get(a.status, 0), a.p_fail or 0.0)
    key_b = (_STATUS_RANK.get(b.status, 0), b.p_fail or 0.0)
    return a if key_a >= key_b else b


@dataclass
class _FileReading:
    reading: Reading
    gate: Gate
    path: str
    chunk: int
    partial: bool = False  # the reading comes from a truncated chunk


def _read_file_jobs(jobs: list[_FileJob], results: dict[str, dict]) -> dict[str, dict[str, _FileReading]]:
    """Worst reading per gate per file across that file's chunks."""
    by_file: dict[str, dict[str, _FileReading]] = {}
    for item in jobs:
        answers = results.get(item.job.tag) or {}
        slot = by_file.setdefault(item.file.path, {})
        for gate in item.gates:
            reading = read(gate, answers.get(gate.id))
            current = slot.get(gate.id)
            if current is None or worse(current.reading, reading) is reading:
                slot[gate.id] = _FileReading(reading, gate, item.file.path, item.chunk.index, item.chunk.truncated)
    return by_file


def _worst_across_files(by_file: dict[str, dict[str, _FileReading]]) -> dict[str, _FileReading]:
    worst: dict[str, _FileReading] = {}
    for readings in by_file.values():
        for gate_id, entry in readings.items():
            current = worst.get(gate_id)
            if current is None or worse(current.reading, entry.reading) is entry.reading:
                worst[gate_id] = entry
    return worst


@dataclass
class _ChangeReading:
    """A change gate's combined reading across chunks: the deciding chunk,
    whether it was truncated, and every chunk's reading."""

    reading: Reading
    gate: Gate
    chunk: int
    partial: bool
    per_chunk: dict[int, tuple[Reading, bool]]


def _better_of(gate: Gate, a: tuple[int, Reading, bool], b: tuple[int, Reading, bool]) -> tuple[int, Reading, bool]:
    """Combine two chunks' readings of one change gate.  ``ac_met`` is met when
    any chunk shows it met (a chunk without the bullet's code answers unclear),
    so a passing chunk wins and the higher P(met) breaks ties; every other
    family takes the worst reading, as file gates do."""
    if gate.family == "ac_met":
        a_pass, b_pass = a[1].status == "pass", b[1].status == "pass"
        if a_pass and b_pass:
            return a if (a[1].p or 0.0) >= (b[1].p or 0.0) else b
        if a_pass or b_pass:
            return a if a_pass else b
    return a if worse(a[1], b[1]) is a[1] else b


def _read_change_jobs(gates: list[Gate], jobs: list[_ChangeJob], results: dict[str, dict]) -> dict[str, _ChangeReading]:
    """Combined reading per change gate across the chunks it was asked in."""
    out: dict[str, _ChangeReading] = {}
    for gate in gates:
        entries: list[tuple[int, Reading, bool]] = []
        for item in jobs:
            if gate.id not in item.job.questions:
                continue
            answers = results.get(item.job.tag) or {}
            entries.append((item.chunk.index, read(gate, answers.get(gate.id)), item.chunk.truncated))
        if not entries:
            entries = [(1, read(gate, None), False)]
        best = entries[0]
        for entry in entries[1:]:
            best = _better_of(gate, best, entry)
        out[gate.id] = _ChangeReading(best[1], gate, best[0], best[2], {i: (r, t) for i, r, t in entries})
    return out


def _truncation_finding(tag: str, chunk: Chunk) -> Finding:
    """A ``warn`` that names what a truncated chunk did not show the model."""
    parts = [f"{cut['path']} {cut['header']}".strip() + f": {cut['lines_sent']} of {cut['lines']} lines sent" for cut in chunk.cuts]
    location = {"chunk": chunk.index} if chunk.path == "change" else {"file": chunk.path, "chunk": chunk.index}
    return Finding(
        id=f"rule:evidence_truncated:{tag}", source="rule", severity="warn", gate="evidence_truncated", location=location,
        message="The model saw only part of this chunk (" + "; ".join(parts) + "); its readings are partial.",
        hint="Split the hunk into smaller commits or pass --files for the code that was cut; a pass read on a partial chunk is not proof.",
    )


def contributing_files(by_file: dict[str, dict[str, _FileReading]], index: int) -> list[str]:
    """Files whose ``touches_ac:<index>`` reading is at or above the contributes threshold."""
    gate_id = f"touches_ac:{index}"
    out = []
    for path, readings in by_file.items():
        entry = readings.get(gate_id)
        if entry and entry.reading.p is not None and entry.reading.p >= entry.gate.threshold:
            out.append(path)
    return out


def _cites(gate: Gate) -> dict | None:
    if gate.item and gate.item.get("note"):
        return {"note": gate.item["note"], "line": gate.item.get("line")}
    return None


def _hint(gate: Gate, path: str) -> str:
    return gate.hint.replace("{file}", path)


def _file_message(gate: Gate, reading: Reading, path: str) -> str:
    legend = reading.legend or reading.choice or ""
    family = gate.family
    if family == "dup":
        return f"{path} re-implements what component {gate.item['name']} already provides."
    if family == "over_engineered":
        return f"{path} is more general, layered or configurable than the ticket needs."
    if family == "correctness_defect":
        return f"{path} contains a defect visible in the diff."
    if family == "arch_rule":
        return f"{path} violates architecture rule {gate.item['id']}: «{gate.item['text']}»."
    if family == "convention":
        return f"{path} breaks convention {gate.item['id']}: «{gate.item['text']}»."
    if family == "edge_cases":
        return f"{path} leaves boundary or failure cases unhandled" + (f" ({legend})" if legend else "") + "."
    return f"{path}: {gate.id} did not pass."


def _file_gather(gate: Gate, path: str) -> dict:
    family = gate.family
    if family == "correctness_defect":
        return {"area": "code", "missing": f"supply --files {path} (and the callee its changed logic depends on) so the change can be judged in context", "for": [f"{gate.id}:{path}"]}
    if family == "dup":
        return {"area": "components", "note": gate.item["note"], "item": gate.item["id"],
                "missing": (f"could not tell whether {path} re-implements {gate.item['name']}: the note's `provides`/`interface` "
                            f"do not describe the behaviour touched here; add it to {gate.item['note']}, or pass --files {path} "
                            "if the hunk shows too little of the file"),
                "for": [f"{gate.id}:{path}"]}
    if family == "arch_rule":
        return {"area": "architecture", "note": gate.item["note"], "item": gate.item["id"],
                "missing": f"rule {gate.item['id']} is not specific enough to judge {path}; say which layer or module it binds in the note",
                "for": [f"{gate.id}:{path}"]}
    if family == "convention":
        return {"area": "conventions", "note": gate.item["note"], "item": gate.item["id"],
                "missing": f"convention {gate.item['id']} is not specific enough to judge {path}; add an example or an applies_to scope in the note",
                "for": [f"{gate.id}:{path}"]}
    return {"area": "ticket", "missing": f"the ticket does not say enough to judge {gate.id} for {path}; state the intended scope in What or supply --files {path}", "for": [f"{gate.id}:{path}"]}


def _file_findings(by_file: dict[str, dict[str, _FileReading]]) -> tuple[list[Finding], list[dict]]:
    findings: list[Finding] = []
    gathers: list[dict] = []
    for path, readings in by_file.items():
        for gate_id, entry in readings.items():
            gate, reading = entry.gate, entry.reading
            if gate.kind == "info" or reading.status in ("pass", "na", "unknown"):
                continue
            unclear = reading.status == "unclear"
            finding = Finding(
                id=f"jev:{gate_id}:{path}", source="jev", severity="unclear" if unclear else "fail",
                gate=gate.family, p=reading.p_unclear if unclear else reading.p,
                threshold=gate.unclear_at if unclear else reading.threshold, borderline=reading.borderline,
                level=reading.level, location={"file": path, "chunk": entry.chunk}, cites=_cites(gate),
                hint=_hint(gate, path),
            )
            if reading.status == "fail":
                finding.message = _file_message(gate, reading, path)
            else:
                gather = _file_gather(gate, path)
                finding.message = f"{path}: {gate.family} could not be decided ({gather['missing']})."
                finding.hint = gather["missing"]
                gathers.append(gather)
            findings.append(finding)
    return findings, gathers


def _ac_location(gate: Gate, by_file: dict[str, dict[str, _FileReading]]) -> dict:
    index = gate.item["criterion"]
    return {"criterion": index, "text": gate.item["text"], "files": contributing_files(by_file, index)}


def _change_findings(gates: list[Gate], change: dict[str, _ChangeReading],
                     by_file: dict[str, dict[str, _FileReading]], chunked: bool = False) -> tuple[list[Finding], list[dict]]:
    findings: list[Finding] = []
    gathers: list[dict] = []
    for gate in gates:
        entry = change[gate.id]
        reading = entry.reading
        family = gate.family
        if reading.status in ("pass", "na", "unknown"):
            continue
        location = _ac_location(gate, by_file) if family in ("ac_met", "ac_proven", "command_proven") else {}
        if chunked:
            location = {**location, "chunk": entry.chunk}
        base = dict(id=f"jev:{gate.id}", source="jev", gate=family, p=reading.p, threshold=reading.threshold,
                    borderline=reading.borderline, location=location, hint=gate.hint)
        if family in rubric.PROOF_FAMILIES:
            what = "the changed code paths" if family == "tests_exercise_change" else f"criterion {gate.item['criterion']}"
            source = "command output" if family == "command_proven" else "test output"
            findings.append(Finding(severity="warn", message=f"The {source} does not prove {what}" + (f" ({reading.legend or reading.choice})" if (reading.legend or reading.choice) else "") + ".", **base))
        elif reading.status == "fail":
            if family == "ac_met":
                legend = reading.legend or reading.choice or "not met"
                message = f"Acceptance criterion {gate.item['criterion']} is not satisfied by the change ({legend})."
            else:
                message = "The diff changes things the ticket did not ask for."
            findings.append(Finding(severity="fail", message=message, **base))
        else:  # unclear
            if family == "ac_met":
                files = location.get("files") or []
                index = gate.item["criterion"]
                if files:
                    shown = " ".join(files)
                    missing = (f"criterion {index} is decided in code the diff does not show; touches_ac marked {shown} as contributing: "
                               f"pass --files {shown} (post-change excerpts of the code that produces the outcome)")
                else:
                    missing = (f"criterion {index} is decided outside the diff (no changed file was marked as contributing): "
                               "pass --files <path> for the code that produces the outcome")
                gather = {"area": "code", "missing": missing, "for": [gate.id]}
            else:
                gather = {"area": "ticket", "missing": f"the ticket does not say enough to judge {gate.id}; state the intended scope in What", "for": [gate.id]}
            gathers.append(gather)
            findings.append(Finding(severity="unclear", message=f"{gate.id} could not be decided from the diff and excerpts.",
                                    **{**base, "p": reading.p_unclear, "threshold": gate.unclear_at, "hint": gather["missing"]}))
    return findings, gathers


def _readings_dict(worst: dict[str, _FileReading], by_file: dict[str, dict[str, _FileReading]],
                   change: dict[str, _ChangeReading]) -> dict[str, dict]:
    """Readings for the report.  Every reading carries ``partial`` (it came
    from a truncated chunk); file gates add the worst file and chunk plus
    ``per_file``; change gates add the deciding chunk plus ``per_chunk``."""
    out: dict[str, dict] = {}
    for gate_id, entry in worst.items():
        per_file = {
            path: {"status": r[gate_id].reading.status, "p": r[gate_id].reading.p, "chunk": r[gate_id].chunk,
                   "partial": r[gate_id].partial}
            for path, r in by_file.items() if gate_id in r
        }
        out[gate_id] = {**entry.reading.to_dict(), "file": entry.path, "chunk": entry.chunk, "partial": entry.partial,
                        "per_file": per_file}
    for gate_id, entry in change.items():
        per_chunk = {str(i): {"status": r.status, "p": r.p, "partial": partial} for i, (r, partial) in entry.per_chunk.items()}
        out[gate_id] = {**entry.reading.to_dict(), "chunk": entry.chunk, "partial": entry.partial, "per_chunk": per_chunk}
    return out


def _context_evidence(jobs: list[_FileJob], pack: ContextPack | None) -> dict:
    if pack is None:
        return {}
    evidence: dict[str, dict] = {}
    for family, area in rubric.FAMILY_NEEDS.items():
        if not pack.has(area):
            continue
        notes: list[str] = []
        items: list[str] = []
        rows: list[dict] = []
        for job in jobs:
            for gate in job.gates:
                if gate.family == family and gate.item and gate.item["id"] not in items:
                    items.append(gate.item["id"])
                    rows.append(gate.item)
                    if gate.item.get("note") and gate.item["note"] not in notes:
                        notes.append(gate.item["note"])
        evidence[area] = {"notes": notes, "items": items, "tokens": tokens(json.dumps(rows, ensure_ascii=False)), "truncated": False}
    return evidence


# ---------------------------------------------------------------------------
# The gate


def check(inputs: DeliveryInputs, pack: ContextPack | None, client: TypeSafeClient, cfg: Config, run: Run) -> Report:
    """Judge ``inputs`` and write the round's report into ``run``."""
    files = parse_diff(inputs.diff_text)
    kept, dropped = filter_files(files, ignore=cfg.ignore, max_file_tokens=cfg.max_file_tokens)
    reviewed, skipped = split_reviewable(kept, cfg.source_globs)
    acceptance = inputs.ticket.get("acceptance") or []
    findings = rule_findings(inputs, files, dropped, pack)
    optional_gather = missing_area_gathers(pack)
    rule_fail = any(f.severity == "fail" for f in findings)
    chunks = {file.path: chunk_file(file, cfg.file_budget) for file in reviewed}
    gathers: list[dict] = []
    readings: dict[str, dict] = {}
    change_evidence: dict | None = None
    unknown = False
    proven = False
    file_jobs: list[_FileJob] = []
    stats = {"files": len(reviewed), "skipped": len(skipped), "dropped": len(dropped),
             "chunks": sum(len(c) for c in chunks.values()), "change_chunks": 0, "requests": 0}
    findings += [_truncation_finding(_chunk_tag(path, c.index), c) for path, cs in chunks.items() for c in cs if c.truncated]

    if not rule_fail:
        all_paths = [file.path for file in files]
        file_jobs = _file_jobs(inputs, reviewed, all_paths, chunks, pack, cfg)
        plan = plan_change(inputs, kept, dropped, cfg)
        gates = rubric.change_gates(acceptance, bool(inputs.logs), cfg)
        change_jobs = _change_jobs(plan, gates)
        commands = commands_job(inputs, gates, cfg)
        if commands is not None:
            change_jobs.append(commands)
        jobs = [item.job for item in file_jobs] + [item.job for item in change_jobs]
        stats["requests"] = len(jobs)
        stats["change_chunks"] = len(plan.states)
        results = client.ask_many(jobs)

        by_file = _read_file_jobs(file_jobs, results)
        worst = _worst_across_files(by_file)
        change = _read_change_jobs(gates, change_jobs, results)
        chunked = any(item.chunk.total > 1 or item.chunk.truncated for item in change_jobs)
        file_findings, file_gathers = _file_findings(by_file)
        change_findings, change_gathers = _change_findings(gates, change, by_file, chunked)
        findings += file_findings + change_findings
        findings += [_truncation_finding(item.job.tag, item.chunk) for item in change_jobs if item.chunk.truncated]
        gathers = file_gathers + change_gathers
        readings = _readings_dict(worst, by_file, change)
        unknown = any(e.reading.status == "unknown" for r in by_file.values() for e in r.values()) or any(
            e.reading.status == "unknown" for e in change.values()
        )
        proof = [change[g.id].reading for g in gates if g.family in rubric.PROOF_FAMILIES]
        proven = bool(inputs.logs) and bool(acceptance) and bool(proof) and all(r.status == "pass" for r in proof)
        change_evidence = change_summary(kept, [chunk for _, chunk in plan.states], plan.budget)

    fails = any(f.severity == "fail" for f in findings)
    if fails:
        verdict = "revise"
    elif gathers:
        verdict = "gather"
    elif unknown:
        verdict = "uncertain"
    elif proven or inputs.no_tests_ok:
        verdict = "accept"
    else:
        verdict = "unproven"

    evidence = evidence_summary(reviewed, skipped + dropped, chunks, inputs.logs, change_evidence)
    evidence["context"] = _context_evidence(file_jobs, pack)
    if inputs.files_after:
        evidence["files_after"] = [{"path": e.get("path"), "range": e.get("range")} for e in inputs.files_after]
    if inputs.commands:
        evidence["commands"] = [
            {"path": c["path"], "lines": len(c["text"].splitlines()),
             "sha256": hashlib.sha256(c["text"].encode("utf-8")).hexdigest()}
            for c in inputs.commands
        ]
    if inputs.untracked:
        evidence["untracked"] = list(inputs.untracked)
    if inputs.ticket_source:
        evidence["ticket"] = inputs.ticket_source
    report = Report(
        gate="delivery",
        outcome=verdict,
        exit_code=DELIVERY_EXITS[verdict],
        model=client.model,
        findings=sort_findings(findings),
        readings=readings,
        gather=gathers,
        optional={"asks": [], "gather": optional_gather},
        rules={"stats": {**stats, "proven": proven, "no_tests_ok": inputs.no_tests_ok},
               "findings": [f.id for f in findings if f.source == "rule"]},
        evidence=evidence,
        usage=client.usage.to_dict(),
    )
    report.delta = delta(run.previous(), report)
    run.write(report)
    return report


__all__ = [
    "DeliveryError", "DeliveryInputs", "build_inputs", "normalise_ticket", "check",
    "rule_findings", "missing_area_gathers", "split_reviewable", "file_state", "change_state", "plan_change", "ChangePlan",
    "commands_state", "commands_job", "COMMANDS_TAG",
    "worse", "contributing_files",
    "EvidenceError",
]
