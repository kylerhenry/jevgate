"""Every ticket fixture parses, validates, and runs offline; expected.json is well formed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jevgate.cli import TICKET_EXITS
from jevgate.config import Config
from jevgate.context import ContextPack
from jevgate.ticket.clarify import BANK_QUESTIONS
from jevgate.ticket.gate import check
from jevgate.ticket.schema import load_ticket, normalise_ticket, validate_ticket

from test_ticket_gate import FIXTURES, TICKETS, make_client, make_run, prime

ALLOWED_KEYS = {"route", "failing_gates", "gather", "asks", "context_dir", "context_json", "notes"}
CASES = sorted(p.name for p in TICKETS.iterdir() if (p / "draft.md").is_file())
EXPECTED_CASES = {
    "ready-01", "ready-02", "ambiguous-design-01", "vague-language-01", "ungrounded-01", "too-big-01",
    "design-first-01", "missing-why-01", "untestable-ac-01", "dense-prose-01", "missing-context-01",
    "rule-violation-01", "reuse-missed-01", "decision-conflict-01", "no-architecture-01", "thin-component-note-01",
}


def expected(case: str) -> dict:
    return json.loads((TICKETS / case / "expected.json").read_text())


def pack_for(case: str) -> ContextPack:
    data = expected(case)
    if data.get("context_json"):
        return ContextPack.from_dict(json.loads((TICKETS / case / data["context_json"]).read_text()), project="ledger")
    directory = Path(data.get("context_dir") or "fixtures/vault")
    if not directory.is_absolute():
        directory = FIXTURES.parent / directory
    return ContextPack.load(directory, project="ledger")


def test_all_planned_fixtures_exist():
    assert set(CASES) == EXPECTED_CASES


@pytest.mark.parametrize("case", CASES)
def test_expected_json_shape(case):
    data = expected(case)
    assert set(data) <= ALLOWED_KEYS and {"route", "failing_gates", "gather", "asks"} <= set(data)
    assert data["route"] in TICKET_EXITS
    assert all(a in BANK_QUESTIONS for a in data["asks"])
    assert all(isinstance(g, str) for g in data["failing_gates"] + data["gather"])
    if data["route"] == "ready":
        assert not data["failing_gates"] and not data["gather"] and not data["asks"]
    if data["route"] == "gather":
        assert data["gather"] and not data["failing_gates"]


@pytest.mark.parametrize("case", CASES)
def test_fixture_parses_and_validates(case):
    ticket = normalise_ticket(load_ticket(TICKETS / case / "draft.md"))
    problems = validate_ticket(ticket)
    if any(g.startswith("rule:missing_") for g in expected(case)["failing_gates"]):
        assert problems
    else:
        assert problems == [], problems
        assert ticket["title"] and ticket["acceptance"] and ticket["context_bullets"]


@pytest.mark.parametrize("case", CASES)
def test_fixture_runs_offline(canned, tmp_path, case):
    ticket = load_ticket(TICKETS / case / "draft.md")
    pack = pack_for(case)
    run = make_run(tmp_path)
    report = check(ticket, pack, make_client(tmp_path, run, enabled=False), Config(), run)
    assert report.outcome in TICKET_EXITS and report.exit_code == TICKET_EXITS[report.outcome]
    assert canned.calls == 0
    data = expected(case)
    if case == "missing-why-01":
        assert report.outcome == "revise"
    elif data["route"] == "gather" and data.get("context_dir"):
        assert report.outcome == "gather" and [g["area"] for g in report.gather] == data["gather"]
    else:
        assert report.outcome == "uncertain"
    assert (run.dir / "round-1.md").is_file()


@pytest.mark.parametrize("case", sorted(c for c in CASES if c.startswith("ready-")))
def test_ready_fixtures_pass_with_passing_answers(canned, tmp_path, case):
    ticket = load_ticket(TICKETS / case / "draft.md")
    pack = pack_for(case)
    prime(canned, ticket, pack, Config())
    run = make_run(tmp_path)
    report = check(ticket, pack, make_client(tmp_path, run), Config(), run)
    assert report.outcome == "ready", [f.id for f in report.findings]
    assert not [f for f in report.findings if f.source == "rule"]


def test_thin_component_pack_names_the_component(canned, tmp_path):
    pack = pack_for("thin-component-note-01")
    assert [i.id for i in pack.items("component")] == ["event-bus", "postgres-store"]
    ticket = load_ticket(TICKETS / "thin-component-note-01" / "draft.md")
    gates = prime(canned, ticket, pack, Config())
    assert "overlap:event-bus" in gates and "provides: Introduced by ADR 0003." in gates["overlap:event-bus"].question.instructions
