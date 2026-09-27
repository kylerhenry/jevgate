"""The ticket policy over canned answers: slices, routes, composition, rounds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jevgate.client import TypeSafeClient
from jevgate.config import Config
from jevgate.context import ContextPack
from jevgate.questions import Choice, Gate, Noul, Score
from jevgate.runs import Run
from jevgate.ticket import rubric
from jevgate.ticket.clarify import BANK, prior_answer_ids, select_asks
from jevgate.ticket.gate import check
from jevgate.ticket.schema import load_ticket, normalise_ticket

from conftest import http_error

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
TICKETS = FIXTURES / "tickets"


def ticket(name: str = "ready-01") -> dict:
    return load_ticket(TICKETS / name / "draft.md")


@pytest.fixture
def vault() -> ContextPack:
    return ContextPack.load(FIXTURES / "vault", project="ledger")


def good_answer(gate: Gate) -> dict:
    """An answer that passes ``gate`` (placement picks new_component so no mismatch can arise)."""
    question = gate.question
    if isinstance(question, Noul):
        return {"noul": 0.05 if gate.kind == "fire" else 0.95}
    if isinstance(question, Score):
        n = len(question.levels)
        best = max(gate.acceptable) if gate.acceptable else n - 1
        probabilities = {str(i): 0.01 for i in range(n)}
        probabilities[str(best)] = round(1 - 0.01 * (n - 1), 4)
        return {"score": best, "probabilities": probabilities, "confidence": 0.9}
    assert isinstance(question, Choice)
    options = list(question.options)
    if gate.id == "placement":
        pick = "new_component"
    elif gate.pass_options:
        pick = gate.pass_options[0]
    else:
        pick = next(o for o in options if o not in gate.fail_options + gate.unclear_options + gate.na_options)
    return choice(pick, options)


def choice(pick: str, options: list[str], p: float = 0.9) -> dict:
    rest = (1 - p) / (len(options) - 1)
    probabilities = {o: round(rest, 4) for o in options}
    probabilities[pick] = p
    return {"choice": pick, "probabilities": probabilities, "confidence": 0.9}


def prime(canned, draft: dict, pack: ContextPack | None, cfg: Config, ledger: set[str] = frozenset()) -> dict[str, Gate]:
    """Fill ``canned.answers`` with a passing answer for every gate the draft would ask."""
    normalised = normalise_ticket(draft)
    gates = rubric.all_gates(normalised, pack, cfg, prior_answer_ids(normalised), ledger)
    canned.answers = {g.id: good_answer(g) for g in gates}
    return {g.id: g for g in gates}


def set_choice(canned, gate_id: str, gates: dict[str, Gate], pick: str, p: float = 0.9) -> None:
    canned.answers[gate_id] = choice(pick, list(gates[gate_id].question.options), p)


def make_run(tmp_path: Path, name: str = "r1") -> Run:
    return Run(tmp_path / "runs", name)


def make_client(tmp_path: Path, run: Run, *, enabled: bool = True, use_cache: bool = True) -> TypeSafeClient:
    return TypeSafeClient(enabled=enabled, cache_dir=tmp_path / "cache", audit_path=run.audit_path, use_cache=use_cache)


def run_check(tmp_path, draft, pack, cfg=None, *, run=None, enabled=True, use_cache=True):
    cfg = cfg or Config()
    run = run or make_run(tmp_path)
    return check(draft, pack, make_client(tmp_path, run, enabled=enabled, use_cache=use_cache), cfg, run)


def body_for(canned, question_id: str) -> dict:
    for body in canned.bodies:
        if question_id in body["questions"]:
            return body
    raise AssertionError(f"no request carried {question_id}")


def finding_ids(report) -> set[str]:
    return {f.id for f in report.findings}


# ---------------------------------------------------------------- rules first


def test_rule_fail_routes_revise_without_api_call(canned, vault, tmp_path):
    report = run_check(tmp_path, ticket("missing-why-01"), vault)
    assert report.outcome == "revise" and report.exit_code == 1
    assert canned.calls == 0
    finding = next(f for f in report.findings if f.id == "rule:missing_why")
    assert finding.severity == "fail" and finding.location == {"section": "why"} and finding.hint
    assert report.readings == {} and report.rules["findings"] == ["rule:missing_why"]


def test_textstats_rules_only_warn(canned, vault, tmp_path):
    prime(canned, ticket("dense-prose-01"), vault, Config())
    report = run_check(tmp_path, ticket("dense-prose-01"), vault)
    ids = finding_ids(report)
    assert "rule:hedges" in ids
    assert all(f.severity == "warn" for f in report.findings if f.source == "rule")
    assert report.outcome == "ready"  # warns never block; the model's answers decide


# ---------------------------------------------------------------- slices


def test_one_request_per_slice_with_only_the_needed_areas(canned, vault, tmp_path):
    prime(canned, ticket(), vault, Config())
    report = run_check(tmp_path, ticket(), vault)
    assert report.outcome == "ready"
    assert canned.calls == 7
    expected = {
        "design_unambiguous": {"draft", "glossary"},
        "arch_rule:R01": {"draft", "architecture"},
        "overlap:cache-helper": {"draft", "components"},
        "decision:adr-0001-postgres-over-sqlite": {"draft", "components", "decisions"},
        "constraint:C01": {"draft", "data", "interfaces", "constraints"},
        "claim:1": {"claims", "notes"},
        "bank:B01": {"draft", "prior_answers", "pack_index"},
    }
    for qid, keys in expected.items():
        assert set(body_for(canned, qid)["state"].keys()) == keys, qid
    arch = body_for(canned, "arch_rule:R01")["state"]["architecture"]
    assert {r["id"] for r in arch["rules"]} == {"R01", "R02", "R03", "R04", "R05", "R06"}
    assert [l["id"] for l in arch["layers"]] == ["L01", "L02", "L03", "L04"]
    assert body_for(canned, "bank:B01")["state"]["pack_index"][0]["title"] == "Ledger architecture"
    assert "claims" in report.evidence["context"] and report.evidence["context"]["architecture"][0] == "architecture.md"
    assert len(report.evidence["slices"]) == 7


def test_catalog_wording_and_data_note(canned, vault, tmp_path):
    gates = prime(canned, ticket(), vault, Config())
    for gate in gates.values():
        assert gate.question.to_api()["instructions"].endswith(rubric.DATA_NOTE.strip())
    assert gates["scope_boundary"].question.levels[3].startswith("In-scope and deliberately-excluded")
    assert gates["arch_rule:R01"].question.options["unclear"] == rubric.UNCLEAR
    assert gates["arch_rule:R01"].fail_options == ("violates",) and gates["arch_rule:R01"].threshold == 0.60
    assert gates["design_unambiguous"].threshold == 0.85 and gates["why_is_a_problem"].threshold == 0.70
    assert gates["readability"].threshold == 0.60 and gates["effort"].acceptable == frozenset({0, 1, 2})
    assert gates["bank:B03"].kind == "fire" and gates["bank:B03"].threshold == 0.70
    assert set(gates["placement"].question.options) == {"L01", "L02", "L03", "L04", "new_component", "unclear"}
    assert "boundary_crossing" not in gates  # the vault has rules
    assert [bid for bid, _ in BANK] == [f"B{i:02d}" for i in range(1, 13)]


def test_threshold_overrides_apply_by_family(canned, vault, tmp_path):
    cfg = Config(thresholds={"ac_testable": 0.5, "arch_rule:R02": 0.9})
    gates = prime(canned, ticket(), vault, cfg)
    assert gates["ac_testable:1"].threshold == 0.5 and gates["arch_rule:R02"].threshold == 0.9
    assert gates["arch_rule:R01"].threshold == 0.60


def test_duplicate_bullets_ask_one_question(canned, vault, tmp_path):
    draft = ticket()
    draft["acceptance"] = ["Run `x` and see 1.", "run `x` and see 1", "- Run `x` and see 1.", "Other outcome."]
    gates = prime(canned, draft, vault, Config())
    assert [g for g in gates if g.startswith("ac_testable:")] == ["ac_testable:1", "ac_testable:2"]
    report = run_check(tmp_path, draft, vault)
    assert sorted(k for k in report.readings if k.startswith("ac_testable")) == ["ac_testable:1", "ac_testable:2"]


# ---------------------------------------------------------------- routes


def test_ready_lists_fired_asks_as_optional(canned, vault, tmp_path):
    prime(canned, ticket(), vault, Config())
    canned.answers["bank:B03"] = {"noul": 0.8}
    run = make_run(tmp_path)
    report = run_check(tmp_path, ticket(), vault, run=run)
    assert report.outcome == "ready" and report.exit_code == 0
    assert report.asks == [] and report.optional["asks"][0]["id"] == "B03"
    assert run.ledger() == set()
    assert report.architecture["rules"]["complies"] == ["R01", "R02", "R03", "R04", "R05", "R06"]
    assert report.architecture["placement"]["expected"] == "new_component"
    assert all(row["verdict"] in ("uses", "unrelated") for row in report.reuse)
    assert report.round == 1 and report.delta == {"resolved": [], "new": [], "unchanged": []}


def test_revise_when_a_gate_fails_and_nothing_to_ask(canned, vault, tmp_path):
    prime(canned, ticket(), vault, Config())
    canned.answers["design_unambiguous"] = {"noul": 0.3}
    report = run_check(tmp_path, ticket(), vault)
    assert report.outcome == "revise" and report.exit_code == 1
    finding = next(f for f in report.findings if f.id == "jev:design_unambiguous")
    assert finding.severity == "fail" and finding.p == 0.3 and finding.threshold == 0.85
    assert finding.location == {"section": "what"} and finding.hint and "below 0.85" in finding.message


def test_ask_needs_a_fail_and_caps_and_ledgers(canned, vault, tmp_path):
    cfg = Config(max_asks=2)
    prime(canned, ticket(), vault, cfg)
    canned.answers["design_unambiguous"] = {"noul": 0.3}
    for bid, p in (("B01", 0.75), ("B03", 0.95), ("B04", 0.9), ("B06", 0.5)):
        canned.answers[f"bank:{bid}"] = {"noul": p}
    run = make_run(tmp_path)
    report = run_check(tmp_path, ticket(), vault, cfg, run=run)
    assert report.outcome == "ask" and report.exit_code == 2
    assert [a["id"] for a in report.asks] == ["B03", "B04"]
    assert report.asks[0]["question"] == "How will it be verified: which test, command or observation?"
    assert run.ledger() == {"B03", "B04"}
    assert "jev:bank:B03" not in finding_ids(report)
    # next round in the same run dir: ledgered ids are not asked again
    canned.bodies.clear()
    run_check(tmp_path, ticket(), vault, cfg, run=run, use_cache=False)
    asked = set(body_for(canned, "bank:B01")["questions"])
    assert "bank:B03" not in asked and "bank:B04" not in asked and "bank:B01" in asked


def test_prior_answers_exclude_bank_questions(canned, vault, tmp_path):
    draft = ticket()
    draft["prior_answers"] = [
        {"id": "B03", "question": "How will it be verified?", "answer": "pytest"},
        {"question": "What happens when the operation fails or the input is malformed?", "answer": "exit 2"},
    ]
    assert prior_answer_ids(normalise_ticket(draft)) == {"B03", "B04"}
    prime(canned, draft, vault, Config())
    run_check(tmp_path, draft, vault)
    asked = set(body_for(canned, "bank:B01")["questions"])
    assert "bank:B03" not in asked and "bank:B04" not in asked and len(asked) == 10
    assert body_for(canned, "bank:B01")["state"]["prior_answers"][0]["id"] == "B03"


def test_gather_only_when_nothing_fails(canned, vault, tmp_path):
    gates = prime(canned, ticket(), vault, Config())
    set_choice(canned, "arch_rule:R02", gates, "unclear", 0.7)
    report = run_check(tmp_path, ticket(), vault)
    assert report.outcome == "gather" and report.exit_code == 5
    entry = report.gather[0]
    assert entry["area"] == "architecture" and entry["note"] == "architecture.md" and entry["item"] == "R02"
    assert entry["for"] == ["arch_rule:R02"] and "R02" in entry["missing"]
    unclear = next(f for f in report.findings if f.id == "jev:arch_rule:R02")
    assert unclear.severity == "unclear" and unclear.cites == {"note": "architecture.md", "line": 30}
    assert report.architecture["rules"]["unclear"] == ["R02"] and report.architecture["rules"]["cited"][0]["id"] == "R02"
    # a fail outranks gather: the unclear item is listed alongside
    canned.answers["design_unambiguous"] = {"noul": 0.2}
    report = run_check(tmp_path, ticket(), vault, run=make_run(tmp_path, "r2"), use_cache=False)
    assert report.outcome == "revise" and report.gather and report.exit_code == 1


def test_split_outranks_everything(canned, vault, tmp_path):
    gates = prime(canned, ticket(), vault, Config())
    set_choice(canned, "split", gates, "design_first", 0.7)
    canned.answers["design_unambiguous"] = {"noul": 0.2}
    set_choice(canned, "arch_rule:R01", gates, "unclear", 0.8)
    canned.answers["bank:B03"] = {"noul": 0.9}
    report = run_check(tmp_path, ticket(), vault)
    assert report.outcome == "split" and report.exit_code == 1
    assert report.split["signal"] == "split" and report.split["choice"] == "design_first"
    assert "jev:split" in finding_ids(report) and report.gather and report.asks


def test_effort_level_three_is_a_split_signal(canned, vault, tmp_path):
    prime(canned, ticket(), vault, Config())
    canned.answers["effort"] = {"score": 3, "probabilities": {"0": 0.1, "1": 0.1, "2": 0.25, "3": 0.55}, "confidence": 0.8}
    report = run_check(tmp_path, ticket(), vault)
    assert report.outcome == "split" and report.split["signal"] == "effort" and report.split["p"] == 0.55
    assert "jev:effort" in finding_ids(report)


def test_uncertain_when_answers_are_missing(canned, vault, tmp_path):
    prime(canned, ticket(), vault, Config())
    canned.errors.append(http_error(422, "bad"))
    report = run_check(tmp_path, ticket(), vault)
    assert report.outcome == "uncertain" and report.exit_code == 3
    assert any(r["status"] == "unknown" for r in report.readings.values())
    assert report.usage["errors"]


def test_no_ai_is_uncertain_without_calls(canned, vault, tmp_path):
    report = run_check(tmp_path, ticket(), vault, enabled=False)
    assert report.outcome == "uncertain" and report.exit_code == 3 and canned.calls == 0
    assert all(r["status"] == "unknown" for r in report.readings.values())


# ---------------------------------------------------------------- composition


def test_reuse_missed_and_unclear_overlap(canned, vault, tmp_path):
    gates = prime(canned, ticket("reuse-missed-01"), vault, Config())
    set_choice(canned, "overlap:cache-helper", gates, "provides_it", 0.85)
    canned.answers["uses:cache-helper"] = {"noul": 0.1}
    set_choice(canned, "overlap:event-bus", gates, "unclear", 0.6)
    report = run_check(tmp_path, ticket("reuse-missed-01"), vault)
    assert report.outcome == "revise"
    finding = next(f for f in report.findings if f.id == "jev:reuse_missed:cache-helper")
    assert finding.severity == "fail" and finding.location == {"component": "cache-helper"}
    assert finding.cites == {"note": "components/cache-helper.md", "line": 8} and "Cache helper" in finding.message
    rows = {r["component"]: r for r in report.reuse}
    assert rows["cache-helper"]["verdict"] == "missed" and rows["cache-helper"]["overlap_p"] == 0.85
    assert rows["event-bus"]["verdict"] == "unclear"
    entry = next(g for g in report.gather if g["item"] == "event-bus")
    assert entry["area"] == "components" and "lacks provides/interface" in entry["missing"]
    assert "jev:overlap:cache-helper" not in finding_ids(report)


def test_reuse_not_missed_when_draft_uses_the_component(canned, vault, tmp_path):
    gates = prime(canned, ticket(), vault, Config())
    set_choice(canned, "overlap:csv-importer", gates, "provides_it", 0.9)
    canned.answers["uses:csv-importer"] = {"noul": 0.95}
    report = run_check(tmp_path, ticket(), vault)
    assert report.outcome == "ready"
    assert next(r for r in report.reuse if r["component"] == "csv-importer")["verdict"] == "uses"


def test_placement_mismatch_compares_with_stated_location(canned, vault, tmp_path):
    gates = prime(canned, ticket(), vault, Config())
    set_choice(canned, "placement", gates, "L01", 0.8)  # draft states the importer: service layer (L02)
    report = run_check(tmp_path, ticket(), vault)
    assert report.outcome == "revise"
    finding = next(f for f in report.findings if f.id == "jev:placement_mismatch")
    assert finding.p == 0.8 and finding.cites["note"] == "architecture.md" and "Service layer" in finding.message
    assert report.architecture["placement"] == {"expected": "Interface layer", "stated": "Service layer", "evidence": "src/ledger/services/importer.py", "p": 0.8}
    set_choice(canned, "placement", gates, "L02", 0.8)
    report = run_check(tmp_path, ticket(), vault, run=make_run(tmp_path, "r2"), use_cache=False)
    assert report.outcome == "ready" and report.architecture["placement"]["expected"] == "Service layer"


def test_ungrounded_claim_is_a_fail_when_about_the_codebase(canned, vault, tmp_path):
    draft = ticket("ungrounded-01")
    gates = prime(canned, draft, vault, Config())
    set_choice(canned, "claim:2", gates, "not_covered", 0.8)
    report = run_check(tmp_path, draft, vault)
    finding = next(f for f in report.findings if f.id == "jev:ungrounded:2")
    assert finding.severity == "fail" and finding.location == {"context": 2}
    set_choice(canned, "claim:2", gates, "contradicted", 0.9)
    report = run_check(tmp_path, draft, vault, run=make_run(tmp_path, "r2"), use_cache=False)
    contradicted = next(f for f in report.findings if f.id == "jev:claim:2")
    assert contradicted.severity == "fail" and "jev:ungrounded:2" not in finding_ids(report)


def test_select_asks_orders_and_caps():
    from jevgate.questions import Reading

    readings = [
        Reading(gate_id="bank:B01", kind="fire", status="fail", threshold=0.7, p=0.75),
        Reading(gate_id="bank:B05", kind="fire", status="fail", threshold=0.7, p=0.95),
        Reading(gate_id="bank:B02", kind="fire", status="pass", threshold=0.7, p=0.2),
        Reading(gate_id="design_unambiguous", kind="pass", status="fail", threshold=0.85, p=0.1),
    ]
    assert [a["id"] for a in select_asks(readings, 4)] == ["B05", "B01"]
    assert [a["id"] for a in select_asks(readings, 1)] == ["B05"]


# ---------------------------------------------------------------- rounds and packs


def test_rounds_delta_and_ledger_in_one_run_dir(canned, vault, tmp_path):
    run = make_run(tmp_path)
    prime(canned, ticket(), vault, Config())
    canned.answers["design_unambiguous"] = {"noul": 0.3}
    canned.answers["bank:B03"] = {"noul": 0.9}
    first = run_check(tmp_path, ticket(), vault, run=run, use_cache=False)
    assert first.outcome == "ask" and first.round == 1 and run.ledger() == {"B03"}
    canned.answers["design_unambiguous"] = {"noul": 0.95}
    second = run_check(tmp_path, ticket(), vault, run=run, use_cache=False)
    assert second.outcome == "ready" and second.round == 2
    assert "jev:design_unambiguous" in second.delta["resolved"] and second.delta["new"] == []
    assert json.loads((run.dir / "round-2.json").read_text())["outcome"] == "ready"
    assert (run.dir / "ledger.json").is_file() or run.ledger() == {"B03"}


def test_missing_pack_gathers_required_areas(canned, tmp_path):
    prime(canned, ticket(), None, Config())
    report = run_check(tmp_path, ticket(), None)
    assert report.outcome == "gather" and report.exit_code == 5
    assert [g["area"] for g in report.gather] == ["architecture", "components", "decisions"]
    assert all("for" in g and g["missing"] for g in report.gather)
    assert {f.id for f in report.findings if f.severity == "warn"} >= {"rule:missing_context_area:architecture", "rule:missing_context_area:data"}
    assert canned.calls == 2
    assert set(body_for(canned, "design_unambiguous")["state"]) == {"draft"}
    assert body_for(canned, "bank:B01")["state"]["pack_index"] == []


def test_missing_area_from_a_thin_vault(canned, tmp_path):
    pack = ContextPack.load(FIXTURES / "vault-noarch", project="ledger")
    prime(canned, ticket(), pack, Config())
    report = run_check(tmp_path, ticket(), pack)
    assert report.outcome == "gather" and [g["area"] for g in report.gather] == ["architecture"]
    assert "architecture" in report.gather[0]["missing"] and report.gather[0]["for"] == ["arch_rule", "placement", "parallel_mechanism"]
    assert not any(q.startswith("arch_rule") for body in canned.bodies for q in body["questions"])
    assert report.architecture is None


def test_boundary_crossing_replaces_rules_when_none(canned, tmp_path):
    pack = ContextPack.from_dict({"architecture": [{"title": "Arch", "text": "Layers only.", "items": [{"id": "L01", "kind": "layer", "text": "Interface layer", "meta": {"name": "Interface layer"}}]}]})
    gates = prime(canned, ticket(), pack, Config())
    assert "boundary_crossing" in gates and not any(g.startswith("arch_rule") for g in gates)
    set_choice(canned, "boundary_crossing", gates, "forbidden", 0.7)
    report = run_check(tmp_path, ticket(), pack)
    assert "jev:boundary_crossing" in finding_ids(report) and report.architecture["boundary"]["choice"] == "forbidden"
