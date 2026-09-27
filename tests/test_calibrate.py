"""The calibrate command: sweep arithmetic on synthetic readings, rendering, the
CLI end to end over ticket fixtures with canned answers, and graceful
degradation. No network."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from jevgate import calibrate as cal
from jevgate import cli
from jevgate import client as client_module
from jevgate.config import Config
from jevgate.report import Report
from jevgate.ticket.schema import load_ticket

from conftest import TEST_KEY
from test_ticket_fixtures import pack_for
from test_ticket_gate import FIXTURES, TICKETS, prime, set_choice

# -- synthetic readings ---------------------------------------------------------


def reading(gate_id: str, kind: str, p: float, *, threshold: float = 0.85, direction: str = "pass",
            p_unclear: float = 0.0, status: str | None = None) -> dict:
    """A stored reading shaped like ``Reading.to_dict()``."""
    fire = kind == "fire" or (kind == "choice" and direction == "fire")
    p_pass, p_fail = (round(1 - p, 4), p) if fire else (p, round(1 - p, 4))
    if status is None:
        if kind == "choice" and p_unclear >= 0.4:
            status = "unclear"
        elif fire:
            status = "fail" if p >= threshold else "pass"
        else:
            status = "pass" if p >= threshold else "fail"
    return {"gate_id": gate_id, "kind": kind, "status": status, "threshold": threshold,
            "p": p, "p_pass": p_pass, "p_fail": p_fail, "p_unclear": p_unclear, "borderline": False}


def synthetic() -> tuple[dict, dict]:
    """Four cases over five families: pass, fire, level, a per-item fire-kind choice, and an info gate."""
    rule = dict(kind="choice", direction="fire", threshold=0.60)
    readings = {
        "A": {"clear": reading("clear", "pass", 0.9), "risk": reading("risk", "fire", 0.2, threshold=0.6),
              "depth": reading("depth", "level", 0.75, threshold=0.7),
              "rule:R01": reading("rule:R01", p=0.3, p_unclear=0.1, **rule), "rule:R02": reading("rule:R02", p=0.2, **rule),
              "note": reading("note", "info", 0.5)},
        "B": {"clear": reading("clear", "pass", 0.7), "risk": reading("risk", "fire", 0.65, threshold=0.6),
              "depth": reading("depth", "level", 0.5, threshold=0.7), "rule:R01": reading("rule:R01", p=0.7, **rule)},
        "C": {"clear": reading("clear", "pass", 0.8), "risk": reading("risk", "fire", 0.7, threshold=0.6),
              "depth": reading("depth", "level", 0.9, threshold=0.7),
              "rule:R01": reading("rule:R01", p=0.2, p_unclear=0.45, **rule)},
        "D": {"clear": reading("clear", "pass", 0.95), "risk": reading("risk", "fire", 0.1, threshold=0.6),
              "rule:R01": reading("rule:R01", p=0.55, **rule), "rule:R02": reading("rule:R02", p=0.1, **rule)},
    }
    labels = {
        "A": {"route": "ready", "failing_gates": [], "gather": []},
        "B": {"route": "revise", "failing_gates": ["clear", "depth", "rule:R01"], "gather": []},
        "C": {"route": "revise", "failing_gates": ["risk"], "gather": ["rule"]},
        "D": {"route": "ready", "failing_gates": [], "gather": []},
    }
    return readings, labels


def rows_by_threshold(info: dict) -> dict[float, dict]:
    return {row["threshold"]: row for row in info["rows"]}


def test_sweep_counts_fires_and_agreement_per_family():
    readings, labels = synthetic()
    result = cal.sweep(readings, labels)
    assert set(result["families"]) == {"clear", "risk", "depth", "rule"}  # info gates are never swept
    assert result["cases"] == 4 and result["unknown_families"] == []

    clear = result["families"]["clear"]
    assert clear["kind"] == "pass" and clear["direction"] == "pass" and clear["default"] == 0.85
    assert clear["asked"] == 4 and clear["labelled_failing"] == ["B"]
    rows = rows_by_threshold(clear)
    assert rows[0.8] == {"threshold": 0.8, "fires_failing": 1, "n_failing": 1, "fires_passing": 0, "n_passing": 3,
                         "agreement": 1.0, "default": False}
    assert rows[0.85]["default"] is True and rows[0.85]["fires_passing"] == 1 and rows[0.85]["agreement"] == 0.75
    assert rows[0.95]["fires_passing"] == 2 and rows[0.95]["agreement"] == 0.5
    assert clear["best"] == 0.8

    risk = result["families"]["risk"]
    assert risk["direction"] == "fire" and risk["default"] == 0.6
    rows = rows_by_threshold(risk)
    assert rows[0.6]["fires_failing"] == 1 and rows[0.6]["fires_passing"] == 1 and rows[0.6]["agreement"] == 0.75
    assert rows[0.7]["agreement"] == 1.0 and rows[0.8]["fires_failing"] == 0
    assert risk["best"] == 0.7


def test_best_threshold_ties_go_to_the_stricter_value_per_kind():
    readings, labels = synthetic()
    result = cal.sweep(readings, labels)
    depth = result["families"]["depth"]  # level: asked in 3 cases, 0.6 and 0.7 both perfect -> higher wins
    assert depth["asked"] == 3 and depth["unanswered"] == 0
    rows = rows_by_threshold(depth)
    assert rows[0.6]["agreement"] == rows[0.7]["agreement"] == 1.0 and rows[0.5]["agreement"] == round(2 / 3, 4)
    assert depth["best"] == 0.7 and depth["default"] == 0.7

    rule = result["families"]["rule"]  # fire-kind choice, per item: 0.6 and 0.7 both perfect -> lower wins
    assert rule["direction"] == "fire" and rule["labelled_failing"] == ["B"]
    rows = rows_by_threshold(rule)
    assert rows[0.5]["fires_passing"] == 1 and rows[0.5]["agreement"] == 0.75  # D fires at 0.55; C is unclear, not a fire
    assert rows[0.6]["agreement"] == rows[0.7]["agreement"] == 1.0
    assert rule["best"] == 0.6

    assert cal.pick_best([{"threshold": 0.6, "agreement": 0.9}, {"threshold": 0.8, "agreement": 0.9}], "pass", 0.5) == 0.8
    assert cal.pick_best([{"threshold": 0.6, "agreement": 0.9}, {"threshold": 0.8, "agreement": 0.9}], "fire", 0.5) == 0.6
    assert cal.pick_best([{"threshold": 0.6, "agreement": None}], "fire", 0.5) == 0.5


def test_unclear_sweep_and_snippet_list_only_changes():
    readings, labels = synthetic()
    result = cal.sweep(readings, labels)
    unclear = result["unclear"]
    assert unclear["default"] == 0.4 and unclear["pairs"] == 4  # only the choice family, asked in four cases
    rows = {row["unclear_at"]: row for row in unclear["rows"]}
    assert rows[0.3]["unclear_gather"] == 1 and rows[0.3]["n_gather"] == 1 and rows[0.3]["unclear_other"] == 0
    assert rows[0.3]["agreement"] == rows[0.4]["agreement"] == 1.0 and rows[0.5]["agreement"] == 0.75
    assert rows[0.4]["default"] is True
    assert unclear["best"] == 0.3  # ties go to the more cautious (lower) value
    assert result["snippet"] == {"thresholds": {"clear": 0.8, "risk": 0.7}, "unclear_at": 0.3}

    same = cal.sweep(readings, labels, ["depth", "rule"], unclear_ats=[0.4])
    assert same["snippet"] == {} and list(same["families"]) == ["depth", "rule"]


def test_sweep_honours_family_subset_defaults_and_aliases():
    readings, labels = synthetic()
    result = cal.sweep(readings, labels, ["clear", "mystery"], thresholds=[0.75], unclear_ats=[0.4])
    assert result["unknown_families"] == ["mystery"] and list(result["families"]) == ["clear"]
    assert [row["threshold"] for row in result["families"]["clear"]["rows"]] == [0.75, 0.85]  # the default is always swept

    aliased = dict(labels)
    aliased["B"] = {"failing_gates": ["reuse_missed:cache-helper"], "gather": []}
    via_alias = cal.sweep(readings, aliased, ["clear"], aliases={"reuse_missed": ("clear",)})
    assert via_alias["families"]["clear"]["labelled_failing"] == ["B"]
    assert cal.sweep(readings, aliased, ["clear"])["families"]["clear"]["labelled_failing"] == []


def test_restatus_and_direction_edge_cases():
    assert cal.restatus({"status": "na", "kind": "choice", "p_pass": 0.1, "p_fail": 0.9}, 0.5, 0.4, "fire") == "na"
    assert cal.restatus({"status": "unknown", "kind": "pass", "p_pass": None, "p_fail": None}, 0.5, 0.4, "pass") == "unknown"
    assert cal.restatus(reading("x", "choice", 0.2, p_unclear=0.5), 0.5, 0.5, "pass") == "unclear"
    assert cal.restatus(reading("x", "choice", 0.2, p_unclear=0.5), 0.5, 0.6, "pass") == "fail"
    assert cal.direction_of([reading("x", "choice", 0.9, direction="pass")]) == "pass"
    assert cal.direction_of([reading("x", "choice", 0.9, direction="fire")]) == "fire"
    assert cal.direction_of([reading("x", "choice", 0.5)]) == "pass"  # a 50/50 answer carries no vote
    assert cal.family_status([], 0.5, 0.4, "pass") == "unknown"
    unanswered = cal.sweep({"A": {"clear": {"gate_id": "clear", "kind": "pass", "status": "unknown", "threshold": 0.85,
                                            "p": None, "p_pass": None, "p_fail": None, "p_unclear": None}}}, {"A": {}})
    assert unanswered["families"]["clear"]["asked"] == 0 and unanswered["families"]["clear"]["unanswered"] == 1
    assert unanswered["families"]["clear"]["best"] == 0.85 and unanswered["snippet"] == {}


# -- agreement at defaults and rendering --------------------------------------------


def make_case(name: str, outcome: str, expected: dict, *, gather=(), asks=(), misses=()) -> cal.CaseResult:
    report = Report(gate="ticket", outcome=outcome, exit_code=0, gather=list(gather), asks=list(asks))
    return cal.CaseResult(name=name, report=report, expected=expected, misses=list(misses))


def test_agreement_matches_routes_gathers_and_asks():
    cases = [
        make_case("one", "ready", {"route": "ready", "failing_gates": [], "gather": [], "asks": []}),
        make_case("two", "gather", {"route": "gather", "gather": ["components:event-bus"]},
                  gather=[{"area": "components", "item": "event-bus", "for": ["overlap:event-bus"]}]),
        make_case("three", "gather", {"route": "gather", "gather": ["architecture"]},
                  gather=[{"area": "architecture", "item": None, "for": ["arch_rule", "placement"]}]),
        make_case("four", "revise", {"route": "ask", "asks": ["B03", "B04"]}, asks=[{"id": "B03"}]),
        make_case("five", "gather", {"verdict": "gather", "gather": ["ac_met:1"]},
                  gather=[{"area": "code", "for": ["ac_met:1"]}]),
    ]
    result = cal.agreement(cases, "ticket")
    assert [(r["case"], r["ok"]) for r in result["outcomes"]["rows"]] == [("one", True), ("two", True), ("three", True), ("four", False), ("five", True)]
    assert result["outcomes"]["agree"] == 4 and result["outcomes"]["n"] == 5
    gathers = {r["case"]: r for r in result["gather"]["rows"]}
    assert set(gathers) == {"two", "three", "five"} and all(r["ok"] for r in gathers.values())
    assert gathers["two"]["got"] == ["components:event-bus"] and gathers["three"]["got"] == ["architecture [arch_rule, placement]"]
    assert result["asks"]["rows"] == [{"case": "four", "expected": ["B03", "B04"], "got": ["B03"], "ok": False}]
    assert cal.agreement(cases[-1:], "delivery")["outcome_key"] == "verdict"


def test_render_marks_defaults_and_lists_every_section():
    readings, labels = synthetic()
    swept = cal.sweep(readings, labels, ["clear", "rule", "ghost"])
    cases = [make_case(name, labels[name]["route"], labels[name], misses=[{"tag": "slice-a", "sha": "abc123def4567890"}] if name == "A" else ())
             for name in readings]
    text = cal.render(cal.build_result("ticket", cases, swept, fixtures_dir="fx", responses_dir="resp"))
    assert text.startswith("# jevgate calibrate — ticket\n\nfixtures: fx (4 cases); responses: resp")
    assert "### clear (pass, passes when P ≥ t; default 0.85; asked in 4 cases; labelled failing: B)" in text
    assert "| 0.85 * | 1/1 | 1/3 | 0.75 |" in text and "| 0.80 | 1/1 | 0/3 | 1.00 |" in text and "best: 0.80" in text
    assert "### rule (choice, fires when P ≥ t; default 0.60" in text and "best: 0.60 (unchanged)" in text
    assert "## unclear_at sweep" in text and "| 0.40 * | 1/1 | 0/3 | 1.00 |" in text
    assert "## Route agreement at current defaults" in text and "agreement: 4/4" in text
    assert "## Gather agreement (expected ⊆ report)" in text and "| C | rule | - | NO |" in text
    assert '"thresholds": {\n    "clear": 0.8\n  }' in text and '"unclear_at": 0.3' in text
    assert "## Cache misses (1) — run again with --refresh to fetch them\n\n- A: slice-a (abc123def456)" in text
    assert "Unknown --gate families (never asked in any case): ghost" in text
    assert text.rstrip().endswith("## Limitations\n\nauthored dev set, not held-out; 4 cases; thresholds are defaults, not truths")

    bare = cal.render(cal.sweep({"A": {}}, {"A": {}}))
    assert "(no answered readings: nothing to sweep" in bare and "(current defaults are already the best on this set)" in bare


# -- end to end over ticket fixtures ---------------------------------------------------

CASES = ("ready-01", "rule-violation-01", "thin-component-note-01")


class Dispatch:
    """Fake transport answering per case: by the draft title found in the state, else with passing answers."""

    def __init__(self) -> None:
        self.by_title: dict[str, dict] = {}
        self.fallback: dict[str, dict] = {}
        self.bodies: list[dict] = []

    def __call__(self, body: dict, key: str, *, timeout: float = 60, attempts: int = 4) -> dict:
        assert key == TEST_KEY
        self.bodies.append(body)
        blob = json.dumps(body["state"], ensure_ascii=False)
        answers = next((a for title, a in self.by_title.items() if title in blob), self.fallback)
        out = {}
        for qid, question in body["questions"].items():
            if qid in answers:
                answer = dict(answers[qid])
                answer.setdefault("type", question["type"])
                out[qid] = answer
        return {"answers": out, "usage": {"input_tokens": 100, "output_tokens": 10}}


@pytest.fixture
def dispatch(monkeypatch: pytest.MonkeyPatch) -> Dispatch:
    fake = Dispatch()
    monkeypatch.setenv("TYPESAFE_API_KEY", TEST_KEY)
    monkeypatch.setattr(client_module, "call_api", fake)
    for case in CASES:
        ticket = load_ticket(TICKETS / case / "draft.md")
        holder = SimpleNamespace()
        gates = prime(holder, ticket, pack_for(case), Config())
        if case == "rule-violation-01":
            set_choice(holder, "arch_rule:R01", gates, gates["arch_rule:R01"].fail_options[0])
        fake.by_title[ticket["title"]] = holder.answers
        fake.fallback.update(holder.answers)
    return fake


@pytest.fixture
def small_fixtures(tmp_path: Path) -> Path:
    root = tmp_path / "fixtures" / "tickets"
    for case in CASES:
        shutil.copytree(TICKETS / case, root / case)
    return root


def run_json(argv: list[str], capsys) -> tuple[int, dict]:
    code = cli.main(argv)
    out = capsys.readouterr().out
    return code, (json.loads(out) if out else {})


def test_refresh_populates_responses_then_cache_only_reproduces_them(dispatch, small_fixtures, tmp_path, capsys):
    responses = tmp_path / "responses"
    common = ["calibrate", "ticket", str(small_fixtures), "--responses", str(responses),
              "--context-dir", str(FIXTURES / "vault"), "--json"]

    code, first = run_json([*common, "--refresh"], capsys)
    assert code == 0 and dispatch.bodies and sorted(responses.glob("*.json"))
    assert first["cache_misses"] == [] and first["errors"] == [] and first["unknown_families"] == []
    assert first["case_names"] == list(CASES) and first["responses"] == str(responses)
    outcomes = {r["case"]: r for r in first["outcomes"]["rows"]}
    assert outcomes["ready-01"] == {"case": "ready-01", "expected": "ready", "got": "ready", "ok": True}
    assert outcomes["rule-violation-01"] == {"case": "rule-violation-01", "expected": "revise", "got": "revise", "ok": True}
    assert outcomes["thin-component-note-01"]["expected"] == "gather" and outcomes["thin-component-note-01"]["ok"] is False
    arch = first["families"]["arch_rule"]
    assert arch["direction"] == "fire" and arch["labelled_failing"] == ["rule-violation-01"] and arch["asked"] == 3
    default_row = next(r for r in arch["rows"] if r["default"])
    assert default_row["threshold"] == 0.6 and default_row["fires_failing"] == 1 and default_row["fires_passing"] == 0
    assert first["families"]["design_unambiguous"]["default"] == 0.85 and "uses" not in first["families"]
    assert first["limitations"] == "authored dev set, not held-out; 3 cases; thresholds are defaults, not truths"
    assert all(entry["sha"] == path.stem for path in responses.glob("*.json") for entry in [json.loads(path.read_text())])

    calls = len(dispatch.bodies)
    code, second = run_json(common, capsys)
    assert code == 0 and len(dispatch.bodies) == calls
    assert second["cache_misses"] == [] and second["families"] == first["families"]
    assert second["outcomes"] == first["outcomes"] and second["gather"] == first["gather"] and second["snippet"] == first["snippet"]

    empty = [*common[:4], str(tmp_path / "empty"), *common[5:]]
    code, third = run_json(empty, capsys)
    assert code == 0 and len(dispatch.bodies) == calls
    assert third["cache_misses"] and all(len(m["sha"]) == 64 for m in third["cache_misses"])
    assert third["families"] and all(info["asked"] == 0 for info in third["families"].values())
    assert third["snippet"] == {} and all(r["got"] == "uncertain" for r in third["outcomes"]["rows"])


def test_markdown_output_out_file_and_gate_subset(dispatch, small_fixtures, tmp_path, capsys):
    responses = tmp_path / "responses"
    base = ["calibrate", "ticket", str(small_fixtures), "--responses", str(responses), "--context-dir", str(FIXTURES / "vault")]
    assert cli.main([*base, "--refresh", "--gate", "arch_rule", "--thresholds", "0.5,0.7", "--unclear-at", "0.3"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("# jevgate calibrate — ticket\n") and "### arch_rule (choice, fires when P ≥ t; default 0.60" in text
    assert "| 0.60 * |" in text and "| 0.50 |" in text and "| 0.70 |" in text and "### design_unambiguous" not in text
    assert "| 0.30 |" in text and "| 0.40 * |" in text
    assert "| rule-violation-01 | revise | revise | yes |" in text and "run again with --refresh" not in text

    out = tmp_path / "result.md"
    assert cli.main([*base, "--out", str(out)]) == 0
    assert capsys.readouterr().out == "" and out.read_text().startswith("# jevgate calibrate — ticket")


def test_unknown_gate_family_is_reported_and_exits_4(canned, small_fixtures, tmp_path, capsys):
    argv = ["calibrate", "ticket", str(small_fixtures), "--responses", str(tmp_path / "none"),
            "--context-dir", str(FIXTURES / "vault"), "--gate", "nope", "--gate", "ac_testable"]
    assert cli.main(argv) == 4
    captured = capsys.readouterr()
    assert "unknown --gate families: nope" in captured.err
    assert "Unknown --gate families (never asked in any case): nope" in captured.out and "### ac_testable" in captured.out
    assert canned.calls == 0


def test_missing_fixtures_dir_or_cases_exits_4(tmp_path, capsys):
    assert cli.main(["calibrate", "ticket", str(tmp_path / "nowhere")]) == 4
    assert "fixtures dir not found" in capsys.readouterr().err
    (tmp_path / "empty").mkdir()
    assert cli.main(["calibrate", "delivery", str(tmp_path / "empty")]) == 4
    assert "no delivery cases under" in capsys.readouterr().err


def test_missing_delivery_gate_degrades_with_a_message(monkeypatch, tmp_path, capsys):
    monkeypatch.setitem(sys.modules, "jevgate.delivery.gate", None)
    argv = ["calibrate", "delivery", str(FIXTURES / "deliveries"), "--responses", str(tmp_path / "none")]
    assert cli.main(argv) == 4
    assert "the delivery gate is not available yet" in capsys.readouterr().err


def test_delivery_cache_only_degrades_to_cache_misses(canned, tmp_path, capsys):
    pytest.importorskip("jevgate.delivery.gate")
    argv = ["calibrate", "delivery", str(FIXTURES / "deliveries"), "--responses", str(tmp_path / "none"), "--json"]
    code, result = run_json(argv, capsys)
    assert code == 0 and canned.calls == 0
    assert result["kind"] == "delivery" and result["outcome_key"] == "verdict" and len(result["case_names"]) >= 10
    assert result["cache_misses"] and result["errors"] == []
    assert all(info["asked"] == 0 for info in result["families"].values())
    verdicts = {r["case"]: r["got"] for r in result["outcomes"]["rows"]}
    assert verdicts["failing-tests-01"] == "revise" and verdicts["accept-01"] == "uncertain"
    assert cli.main(argv[:-1]) == 0 and "## Cache misses (" in capsys.readouterr().out
