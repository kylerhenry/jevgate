"""Run directories: rounds, ledger, delta."""

from __future__ import annotations

import json
import re
from datetime import datetime

from jevgate.report import Finding, Report
from jevgate.runs import Run, default_run_id, delta


def report(outcome="revise", ids=()):
    return Report(gate="ticket", outcome=outcome, exit_code=1,
                  findings=[Finding(id=i, severity="fail") for i in ids])


def test_default_run_id():
    assert default_run_id("ticket", datetime(2026, 9, 27, 13, 5, 9)) == "20260927-130509-ticket"
    assert re.fullmatch(r"\d{8}-\d{6}-delivery", default_run_id("delivery"))


def test_run_creates_dir_and_numbers_rounds(tmp_path):
    run = Run(tmp_path / "runs", "r1", cache_dir=tmp_path / "cache")
    assert run.dir == tmp_path / "runs" / "r1" and run.dir.is_dir()
    assert run.cache_dir == tmp_path / "cache"
    assert run.audit_path == run.dir / "requests.jsonl"
    assert run.previous() is None and run.next_round() == 1 and run.rounds() == []

    json_path, md_path = run.write(report(ids=["jev:a"]))
    assert (json_path.name, md_path.name) == ("round-1.json", "round-1.md")
    assert run.next_round() == 2
    stored = json.loads(json_path.read_text())
    assert stored["round"] == 1 and stored["run_id"] == "r1"
    assert md_path.read_text().startswith("# jevgate ticket — REVISE (round 1, exit 1)")

    again = Run.at(run.dir)  # reusing the directory continues the run
    assert again.run_id == "r1" and again.next_round() == 2
    second = report(outcome="ready", ids=[])
    second.exit_code = 0
    json2, _ = again.write(second)
    assert json2.name == "round-2.json" and again.rounds() == [1, 2]
    prev = again.previous()
    assert prev.round == 2 and prev.outcome == "ready"
    assert again.load(1).findings[0].id == "jev:a"
    assert again.load(9) is None


def test_explicit_round_is_kept(tmp_path):
    run = Run(tmp_path, "r")
    rep = report()
    rep.round = 5
    json_path, _ = run.write(rep)
    assert json_path.name == "round-5.json" and run.next_round() == 6


def test_default_id_when_none(tmp_path):
    run = Run(tmp_path, None, gate="delivery")
    assert run.run_id.endswith("-delivery") and run.dir.is_dir()


def test_ledger_persists(tmp_path):
    run = Run(tmp_path, "r")
    assert run.ledger() == set()
    assert run.add_ledger(["B03", "B01"]) == {"B01", "B03"}
    run.add_ledger(["B03", "B07"])
    assert Run.at(run.dir).ledger() == {"B01", "B03", "B07"}
    assert json.loads(run.ledger_path.read_text()) == {"asked": ["B01", "B03", "B07"]}


def test_delta():
    prev = report(ids=["jev:a", "jev:b", "rule:c"])
    cur = report(ids=["jev:b", "jev:d"])
    assert delta(prev, cur) == {"resolved": ["jev:a", "rule:c"], "new": ["jev:d"], "unchanged": ["jev:b"]}
    assert delta(None, cur) == {"resolved": [], "new": ["jev:b", "jev:d"], "unchanged": []}
    assert delta(prev, report()) == {"resolved": ["jev:a", "jev:b", "rule:c"], "new": [], "unchanged": []}
