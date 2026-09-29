"""Every delivery fixture is well formed: its patch parses, its ticket has
acceptance bullets, its logs parse, and the gate runs over it with AI disabled."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jevgate.client import TypeSafeClient
from jevgate.config import Config
from jevgate.context import ContextPack
from jevgate.delivery.evidence import parse_diff, parse_test_log, read_excerpt
from jevgate.delivery.gate import DeliveryInputs, check
from jevgate.markdown import parse_ticket
from jevgate.runs import Run

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CASES = sorted(p for p in (FIXTURES / "deliveries").iterdir() if p.is_dir())
EXPECTED_CASES = {
    "accept-01", "unmet-ac-01", "unproven-nologs-01", "unproven-unrelated-tests-01", "defect-01", "duplicate-01",
    "scope-creep-01", "over-engineered-01", "failing-tests-01", "rule-violation-01", "convention-01",
    "unclear-ac-01", "gather-resolved-02", "command-proven-01", "command-unproven-01", "command-proven-02", "command-differs-01",
}
VERDICTS = {"accept", "revise", "unproven", "uncertain", "gather"}


def test_every_planned_case_exists():
    assert {c.name for c in CASES} == EXPECTED_CASES


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_fixture_is_well_formed(case: Path):
    files = parse_diff((case / "change.patch").read_text(encoding="utf-8"))
    assert files and all(f.hunks and f.status == "modified" and f.path.startswith("src/ledger/") for f in files)
    ticket = parse_ticket((case / "ticket.md").read_text(encoding="utf-8"))
    assert ticket["title"] and ticket["why"] and ticket["what"] and len(ticket["acceptance"]) >= 1
    logs = [parse_test_log(str(p), p.read_text(encoding="utf-8")) for p in sorted((case / "tests").glob("*.log"))] if (case / "tests").is_dir() else []
    for log in logs:
        assert log.tool == "pytest" and log.passed is not None and log.names
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    assert expected["verdict"] in VERDICTS
    assert set(expected) >= {"verdict", "failing_gates", "finding_files", "gather"}
    assert all(isinstance(expected[k], list) for k in ("failing_gates", "finding_files", "gather"))
    changed = {f.path for f in files}
    assert set(expected["finding_files"]) <= changed
    for spec in expected.get("files", []):
        excerpt = read_excerpt(case, spec)
        assert excerpt["text"].strip()
    if case.name == "failing-tests-01":
        assert any((log.failed or 0) > 0 for log in logs) and expected["verdict"] == "revise"
    elif logs:
        assert all(not log.failed and not log.errors for log in logs)
    if expected["verdict"] == "unproven" and case.name.endswith("nologs-01"):
        assert not logs


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_gate_runs_with_ai_disabled(case: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    pack = ContextPack.load(FIXTURES / "vault", project="ledger")
    ticket = parse_ticket((case / "ticket.md").read_text(encoding="utf-8"))
    logs = [parse_test_log(str(p), p.read_text(encoding="utf-8")) for p in sorted((case / "tests").glob("*.log"))] if (case / "tests").is_dir() else []
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    inputs = DeliveryInputs(
        ticket={k: ticket[k] for k in ("title", "why", "what", "acceptance")},
        diff_text=(case / "change.patch").read_text(encoding="utf-8"),
        logs=logs,
        files_after=[read_excerpt(case, spec) for spec in expected.get("files", [])],
    )
    run = Run(tmp_path / "runs", "r1", cache_dir=tmp_path / "cache")
    client = TypeSafeClient(enabled=False, cache_dir=run.cache_dir, audit_path=run.audit_path)
    report = check(inputs, pack, client, Config(), run)
    if case.name == "failing-tests-01":
        assert report.outcome == "revise" and report.exit_code == 1
    else:
        assert report.outcome == "uncertain" and report.exit_code == 3
        assert {f.severity for f in report.findings} <= {"warn"}
    assert report.usage["requests"] == 0 and (run.dir / "round-1.md").is_file()
    assert report.to_markdown().startswith("# jevgate delivery — ")
