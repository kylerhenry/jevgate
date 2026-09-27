"""The delivery gate: rules, per-file and whole-change requests, aggregation,
gather, verdicts, cache behaviour across rounds. The transport is the ``canned``
fake; ``Script`` fills its answers per request from each question's criteria so
answers can differ per file and per chunk."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

import pytest

from jevgate import client as client_module
from jevgate.client import TypeSafeClient
from jevgate.config import Config
from jevgate.context import ContextPack
from jevgate.delivery import rubric
from jevgate.delivery.evidence import parse_test_log, read_excerpt
from jevgate.delivery.gate import DeliveryInputs, check
from jevgate.report import Report
from jevgate.runs import Run

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
CASES = FIXTURES / "deliveries"

GOOD = {
    "dup": "unrelated", "over_engineered": "no", "correctness_defect": "no_defect_visible",
    "arch_rule": "complies", "convention": "follows", "edge_cases": 2, "touches_ac": 0.9,
    "ac_met": "met", "ac_proven": "passing_test_covers", "scope_creep": "none", "tests_exercise_change": "covers",
}


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))


@pytest.fixture
def pack() -> ContextPack:
    return ContextPack.load(FIXTURES / "vault", project="ledger")


def build_answer(question: dict, value, p: float) -> dict:
    """A valid answer to ``question``: ``value`` with probability ``p``, the rest spread evenly."""
    qtype = question["type"]
    if qtype == "noul":
        return {"type": "noul", "noul": float(value if isinstance(value, float) else p)}
    criteria = question["criteria"]
    keys = list(criteria) if isinstance(criteria, dict) else [str(i) for i in range(len(criteria))]
    chosen = str(value)
    rest = (1 - p) / max(len(keys) - 1, 1)
    probabilities = {k: (p if k == chosen else rest) for k in keys}
    if qtype == "choice":
        return {"type": "choice", "choice": chosen, "probabilities": probabilities, "confidence": p}
    return {"type": "score", "score": int(value), "probabilities": probabilities, "confidence": p}


class Script:
    """Fills ``canned.answers`` for every question of each request before the
    canned transport answers it. Rules match a gate id or family, optionally
    restricted to one file path and chunk index; the last match wins."""

    def __init__(self, canned, monkeypatch: pytest.MonkeyPatch) -> None:
        self.canned = canned
        self.rules: list[tuple[Callable[[str, dict], bool], object, float]] = []
        self._lock = threading.Lock()  # requests run in parallel threads; answers are filled per request
        monkeypatch.setattr(client_module, "call_api", self)

    def on(self, target: str, value, p: float = 0.95, *, path: str | None = None, chunk: int | None = None) -> "Script":
        def match(qid: str, state: dict) -> bool:
            if qid != target and qid.split(":", 1)[0] != target:
                return False
            file = state.get("file") or {}
            if path is not None and file.get("path") != path:
                return False
            if chunk is not None and (file.get("chunk") or {}).get("index", 1) != chunk:
                return False
            return True
        self.rules.append((match, value, p))
        return self

    def lookup(self, qid: str, state: dict) -> tuple[object, float]:
        for match, value, p in reversed(self.rules):
            if match(qid, state):
                return value, p
        family = qid.split(":", 1)[0]
        value = GOOD[family]
        return value, (value if isinstance(value, float) else 0.95)

    def __call__(self, body: dict, key: str, **kwargs) -> dict:
        with self._lock:
            for qid, question in body["questions"].items():
                value, p = self.lookup(qid, body["state"])
                self.canned.answers[qid] = build_answer(question, value, p)
            return self.canned(body, key, **kwargs)


def load_case(name: str) -> dict:
    case = CASES / name
    from jevgate.markdown import parse_ticket
    ticket = parse_ticket((case / "ticket.md").read_text(encoding="utf-8"))
    logs = []
    if (case / "tests").is_dir():
        logs = [parse_test_log(str(p), p.read_text(encoding="utf-8")) for p in sorted((case / "tests").glob("*.log"))]
    expected = json.loads((case / "expected.json").read_text(encoding="utf-8"))
    files_after = [read_excerpt(case, spec) for spec in expected.get("files", [])]
    return {
        "ticket": {k: ticket[k] for k in ("title", "why", "what", "acceptance")},
        "diff": (case / "change.patch").read_text(encoding="utf-8"),
        "logs": logs, "expected": expected, "files_after": files_after, "dir": case,
    }


def inputs_for(name: str, **overrides) -> DeliveryInputs:
    case = load_case(name)
    kwargs = dict(ticket=case["ticket"], diff_text=case["diff"], logs=case["logs"], files_after=case["files_after"])
    kwargs.update(overrides)
    return DeliveryInputs(**kwargs)


def run_gate(inputs: DeliveryInputs, pack: ContextPack | None, tmp_path: Path, *, cfg: Config | None = None,
             enabled: bool = True, run_id: str = "r1") -> Report:
    run = Run(tmp_path / "runs", run_id, cache_dir=tmp_path / "cache")
    client = TypeSafeClient(enabled=enabled, cache_dir=run.cache_dir, audit_path=run.audit_path)
    return check(inputs, pack, client, cfg or Config(), run)


def bodies_for(canned, tag_prefix: str) -> list[dict]:
    """Request bodies whose per-file state path starts with ``tag_prefix`` (or every whole-change body for ``change``)."""
    out = []
    for body in canned.bodies:
        state = body["state"]
        if tag_prefix == "change" and "diff" in state:
            out.append(body)
        elif "file" in state and state["file"]["path"].startswith(tag_prefix):
            out.append(body)
    return out


def finding(report: Report, prefix: str):
    return [f for f in report.findings if f.id.startswith(prefix)]


def mini_vault(root: Path) -> ContextPack:
    root.mkdir(parents=True, exist_ok=True)
    (root / "architecture.md").write_text(
        "---\ntags: [jevgate/architecture]\n---\n# Arch\n\n## Rules\n\n"
        "- Handlers never contain SQL. #jevgate/rule\n"
        "- Docs are written in English. #jevgate/rule #jevgate/applies/md\n",
        encoding="utf-8",
    )
    (root / "conventions.md").write_text(
        "---\ntags: [jevgate/conventions]\n---\n# Conventions\n\n"
        "- Python functions carry type hints. #jevgate/convention #jevgate/applies/py\n"
        "- Markdown headings are sentence case. #jevgate/convention #jevgate/applies/md\n"
        "- Commit messages are imperative. #jevgate/convention\n",
        encoding="utf-8",
    )
    (root / "cache.md").write_text(
        "---\ntags: [jevgate/component]\npath: src/cache.py\nprovides: A TTL cache.\n---\n# Cache helper\n\nA cache.\n",
        encoding="utf-8",
    )
    return ContextPack.load(root)


def synthetic_diff(path: str, hunks: int, lines: int, width: int = 40) -> str:
    head = [f"diff --git a/{path} b/{path}", "index 1111111..2222222 100644", f"--- a/{path}", f"+++ b/{path}"]
    body: list[str] = []
    start = 1
    for h in range(hunks):
        body.append(f"@@ -{start},1 +{start},{lines + 1} @@")
        body.append(" context")
        body.extend(f"+line-{h:02d}-{i:03d} " + "x" * (width - 14) for i in range(lines))
        start += 50
    return "\n".join(head + body) + "\n"


# ---------------------------------------------------------------------------
# rubric


def test_rubric_catalog_ids_thresholds_and_data_note(pack: ContextPack):
    cfg = Config()
    gates = rubric.file_gates("src/ledger/services/posting.py", pack, cfg, ["a", "b"], "post_entry period closed")
    ids = [g.id for g in gates]
    assert {"over_engineered", "correctness_defect", "edge_cases", "touches_ac:1", "touches_ac:2"} <= set(ids)
    assert "dup:cache-helper" in ids and "arch_rule:R01" in ids and "convention:V01" in ids
    assert all(g.question.to_api()["instructions"].endswith(rubric.DATA_NOTE) for g in gates)
    by_id = {g.id: g for g in gates}
    assert by_id["edge_cases"].threshold == 0.70 and by_id["edge_cases"].acceptable == {1, 2}
    assert by_id["dup:cache-helper"].fail_options == ("reimplements",) and by_id["dup:cache-helper"].threshold == 0.60
    assert by_id["arch_rule:R01"].item["note"] == "architecture.md" and by_id["arch_rule:R01"].item["line"] > 0
    assert "«Architecture rule" not in by_id["arch_rule:R01"].question.instructions
    assert by_id["arch_rule:R01"].question.instructions.startswith("Architecture rule «")
    assert by_id["touches_ac:1"].kind == "info" and by_id["touches_ac:1"].threshold == 0.50

    change = rubric.change_gates(["a", "b"], True, cfg)
    assert [g.id for g in change] == ["ac_met:1", "ac_met:2", "ac_proven:1", "ac_proven:2", "scope_creep", "tests_exercise_change"]
    assert [g.id for g in rubric.change_gates(["a"], False, cfg)] == ["ac_met:1", "scope_creep"]
    thresholds = {g.family: g.threshold for g in change}
    assert thresholds == {"ac_met": 0.90, "ac_proven": 0.90, "scope_creep": 0.60, "tests_exercise_change": 0.90}
    assert all(g.question.to_api()["instructions"].endswith(rubric.DATA_NOTE) for g in change)

    tuned = Config(thresholds={"arch_rule": 0.5, "ac_met:2": 0.7}, unclear_at=0.3)
    gates = rubric.file_gates("x.py", pack, tuned, ["a"], "")
    assert all(g.threshold == 0.5 for g in gates if g.family == "arch_rule")
    assert all(g.unclear_at == 0.3 for g in gates)
    change = {g.id: g for g in rubric.change_gates(["a", "b"], False, tuned)}
    assert change["ac_met:1"].threshold == 0.90 and change["ac_met:2"].threshold == 0.7


# ---------------------------------------------------------------------------
# verdicts


def test_accept(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch)
    report = run_gate(inputs_for("accept-01"), pack, tmp_path)
    assert report.outcome == "accept" and report.exit_code == 0 and report.gate == "delivery"
    assert not [f for f in report.findings if f.severity != "warn"]
    assert canned.calls == 5  # four files, one chunk each, plus the whole change
    assert report.usage["requests"] == 5 and report.rules["stats"]["proven"] is True
    assert {f["path"] for f in report.evidence["files"]} == {
        "src/ledger/services/posting.py", "src/ledger/api/router.py",
        "src/ledger/services/test_posting.py", "src/ledger/api/test_router.py",
    }
    assert report.readings["ac_met:1"]["status"] == "pass" and report.readings["over_engineered"]["per_file"]
    assert set(report.evidence["context"]) == {"architecture", "components", "conventions"}
    assert "R01" in report.evidence["context"]["architecture"]["items"]
    assert (tmp_path / "runs" / "r1" / "round-1.json").is_file() and report.round == 1
    assert report.delta == {"resolved": [], "new": [], "unchanged": []}


def test_revise_with_file_located_finding_worst_first(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch).on("correctness_defect", "defect_visible", 0.9, path="src/ledger/services/reports.py") \
        .on("ac_met:2", "not_met", 0.8)
    report = run_gate(inputs_for("defect-01"), pack, tmp_path)
    assert report.outcome == "revise" and report.exit_code == 1
    severities = [f.severity for f in report.findings]
    assert severities == sorted(severities, key=("fail", "unclear", "warn").index)
    assert report.findings[0].severity == "fail"
    defect = finding(report, "jev:correctness_defect:")
    assert len(defect) == 1
    assert defect[0].location == {"file": "src/ledger/services/reports.py", "chunk": 1}
    assert "src/ledger/services/reports.py" in defect[0].hint and defect[0].message
    ac = finding(report, "jev:ac_met:2")[0]
    assert ac.location["criterion"] == 2 and ac.location["text"].startswith("Entries posted on")
    assert set(ac.location["files"]) == {"src/ledger/services/reports.py", "src/ledger/services/test_reports.py"}


def test_gather_with_files_hint(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch).on("ac_met:1", "unclear", 0.8)
    report = run_gate(inputs_for("unclear-ac-01"), pack, tmp_path)
    assert report.outcome == "gather" and report.exit_code == 5
    assert len(report.gather) == 1
    entry = report.gather[0]
    assert entry["for"] == ["ac_met:1"] and "pass --files src/ledger/services/periods.py" in entry["missing"]
    assert "src/ledger/services/periods.py" in entry["missing"]
    unclear = finding(report, "jev:ac_met:1")
    assert unclear and unclear[0].severity == "unclear" and "--files" in unclear[0].hint


def test_unproven_without_logs_and_no_tests_ok(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch)
    report = run_gate(inputs_for("unproven-nologs-01"), pack, tmp_path)
    assert report.outcome == "unproven" and report.exit_code == 2
    assert [f.id for f in report.findings if f.source == "rule"] == ["rule:no_test_logs"]
    change = bodies_for(canned, "change")[0]
    assert set(change["questions"]) == {"ac_met:1", "ac_met:2", "ac_met:3", "scope_creep"}
    assert change["state"]["tests"]["tool"] == "none"
    report = run_gate(inputs_for("unproven-nologs-01", no_tests_ok=True), pack, tmp_path, run_id="r2")
    assert report.outcome == "accept" and report.exit_code == 0


def test_unproven_when_tests_do_not_cover_the_change(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch).on("ac_proven", "no_relevant_test", 0.9).on("tests_exercise_change", "unrelated", 0.9)
    report = run_gate(inputs_for("unproven-unrelated-tests-01"), pack, tmp_path)
    assert report.outcome == "unproven" and report.exit_code == 2
    warns = {f.id for f in report.findings if f.severity == "warn"}
    assert {"jev:ac_proven:1", "jev:ac_proven:2", "jev:ac_proven:3", "jev:tests_exercise_change"} <= warns
    assert not [f for f in report.findings if f.severity == "fail"]
    assert "--test-log" in finding(report, "jev:tests_exercise_change")[0].hint
    tests = bodies_for(canned, "change")[0]["state"]["tests"]
    assert tests["tool"] == "pytest" and tests["counts"]["passed"] == 5 and tests["names"]


def test_failing_tests_rule_makes_no_api_call(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch)
    report = run_gate(inputs_for("failing-tests-01"), pack, tmp_path)
    assert report.outcome == "revise" and report.exit_code == 1
    assert canned.calls == 0 and report.usage["requests"] == 0
    fail = finding(report, "rule:tests_failing")[0]
    assert fail.severity == "fail" and "test_api_returns_409_for_closed_period" in fail.message
    assert report.readings == {} and report.rules["findings"] == ["rule:tests_failing"]


def test_diff_empty(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch)
    report = run_gate(inputs_for("accept-01", diff_text=""), pack, tmp_path)
    assert report.outcome == "revise" and canned.calls == 0
    assert [f.id for f in report.findings if f.severity == "fail"] == ["rule:diff_empty"]


def test_no_ai_is_uncertain(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch)
    report = run_gate(inputs_for("accept-01"), pack, tmp_path, enabled=False)
    assert report.outcome == "uncertain" and report.exit_code == 3 and canned.calls == 0
    assert report.readings["ac_met:1"]["status"] == "unknown"
    assert not [f for f in report.findings if f.severity != "warn"]


def test_missing_pack_warns_but_completes(canned, monkeypatch, tmp_path):
    Script(canned, monkeypatch)
    report = run_gate(inputs_for("accept-01"), None, tmp_path)
    assert report.outcome == "accept"
    warned = {f.id for f in report.findings if f.gate == "missing_context_area"}
    assert warned == {f"rule:missing_context_area:{a}" for a in ("architecture", "components", "conventions")}
    assert [g["area"] for g in report.optional["gather"]] == ["architecture", "components", "conventions"]
    assert report.gather == [] and report.evidence["context"] == {}
    for body in bodies_for(canned, "src/"):
        families = {qid.split(":", 1)[0] for qid in body["questions"]}
        assert families == {"over_engineered", "correctness_defect", "edge_cases", "touches_ac"}
        assert not ({"architecture", "conventions", "components"} & set(body["state"]))


# ---------------------------------------------------------------------------
# state shapes


def test_per_file_state_has_only_that_file_and_applicable_items(canned, monkeypatch, tmp_path):
    pack = mini_vault(tmp_path / "vault")
    diff = synthetic_diff("src/app.py", 1, 3) + synthetic_diff("docs/guide.md", 1, 3)
    ticket = {"title": "t", "why": "w", "what": "x", "acceptance": ["one", "two"]}
    Script(canned, monkeypatch)
    cfg = Config(source_globs=["*.py", "*.md"])  # markdown is reviewed here on purpose: the applies/md items
    report = run_gate(DeliveryInputs(ticket=ticket, diff_text=diff), pack, tmp_path, cfg=cfg)
    assert report.outcome == "unproven"
    py = bodies_for(canned, "src/app.py")[0]
    md = bodies_for(canned, "docs/guide.md")[0]
    assert py["state"]["file"] == {"path": "src/app.py", "status": "modified", "patch": py["state"]["file"]["patch"]}
    assert "docs/guide.md" not in py["state"]["file"]["patch"] and py["state"]["other_changed_files"] == ["docs/guide.md"]
    assert md["state"]["other_changed_files"] == ["src/app.py"]
    assert py["state"]["ticket"] == ticket
    assert [c["text"] for c in py["state"]["conventions"]] == ["Python functions carry type hints.", "Commit messages are imperative."]
    assert [c["text"] for c in md["state"]["conventions"]] == ["Markdown headings are sentence case.", "Commit messages are imperative."]
    assert [r["text"] for r in py["state"]["architecture"]["rules"]] == ["Handlers never contain SQL."]
    assert [r["text"] for r in md["state"]["architecture"]["rules"]] == ["Handlers never contain SQL.", "Docs are written in English."]
    assert [c["name"] for c in py["state"]["components"]] == ["Cache helper"]
    assert {"id", "name", "path", "provides", "interface", "aliases"} == set(py["state"]["components"][0])
    assert set(py["state"]) == {"ticket", "file", "other_changed_files", "architecture", "conventions", "components"}
    py_ids = set(py["questions"])
    assert "convention:V01" in py_ids and "convention:V02" not in py_ids and "arch_rule:R02" not in py_ids
    assert {"dup:cache-helper", "touches_ac:1", "touches_ac:2"} <= py_ids
    change = bodies_for(canned, "change")[0]["state"]
    assert set(change) == {"ticket", "diff", "files_after", "tests"} and change["diff"].count("diff --git") == 2


def test_files_excerpts_reach_the_whole_change_state(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch)
    inputs = inputs_for("gather-resolved-02")
    assert inputs.files_after and inputs.files_after[0]["range"] == [1, 34]
    report = run_gate(inputs, pack, tmp_path)
    assert report.outcome == "accept"
    change = bodies_for(canned, "change")[0]["state"]
    assert change["files_after"][0]["path"].endswith("src/ledger/storage/postgres.py")
    assert "def after_close" in change["files_after"][0]["text"]
    assert report.evidence["files_after"] == [{"path": inputs.files_after[0]["path"], "range": [1, 34]}]
    for body in bodies_for(canned, "src/"):
        assert "files_after" not in body["state"]


def test_max_aggregation_across_files(canned, monkeypatch, pack, tmp_path):
    a, b = "src/ledger/services/posting.py", "src/ledger/api/router.py"
    Script(canned, monkeypatch).on("over_engineered", "yes", 0.7, path=a).on("over_engineered", "no", 0.9, path=b) \
        .on("edge_cases", 0, 0.8, path=b)
    report = run_gate(inputs_for("accept-01"), pack, tmp_path)
    assert report.outcome == "revise"
    over = finding(report, "jev:over_engineered:")
    assert [f.location["file"] for f in over] == [a]
    reading = report.readings["over_engineered"]
    assert reading["file"] == a and reading["status"] == "fail" and reading["p"] == 0.7
    assert reading["per_file"][b]["status"] == "pass" and set(reading["per_file"]) >= {a, b}
    edge = report.readings["edge_cases"]
    assert edge["file"] == b and edge["status"] == "fail" and edge["level"] == 0
    assert [f.location["file"] for f in finding(report, "jev:edge_cases:")] == [b]


def test_worst_chunk_wins_within_a_file(canned, monkeypatch, pack, tmp_path):
    diff = synthetic_diff("src/ledger/services/big.py", 3, 30)
    ticket = {"title": "t", "why": "w", "what": "x", "acceptance": ["one"]}
    cfg = Config(file_budget=600)
    Script(canned, monkeypatch).on("correctness_defect", "defect_visible", 0.85, path="src/ledger/services/big.py", chunk=2)
    report = run_gate(DeliveryInputs(ticket=ticket, diff_text=diff, no_tests_ok=True), pack, tmp_path, cfg=cfg)
    chunks = [b for b in canned.bodies if "file" in b["state"]]
    assert len(chunks) == 3 and sorted(b["state"]["file"]["chunk"]["index"] for b in chunks) == [1, 2, 3]
    assert report.outcome == "revise"
    defect = finding(report, "jev:correctness_defect:")[0]
    assert defect.location == {"file": "src/ledger/services/big.py", "chunk": 2} and defect.p == 0.85
    assert report.evidence["files"][0]["chunks"] == 3


def test_unclear_beats_pass_but_not_fail(canned, monkeypatch, pack, tmp_path):
    a, b = "src/ledger/services/posting.py", "src/ledger/api/router.py"
    Script(canned, monkeypatch).on("arch_rule:R01", "unclear", 0.7, path=a).on("arch_rule:R01", "complies", 0.95, path=b)
    report = run_gate(inputs_for("accept-01"), pack, tmp_path)
    assert report.outcome == "gather"
    assert report.readings["arch_rule:R01"]["status"] == "unclear" and report.readings["arch_rule:R01"]["file"] == a
    entry = report.gather[0]
    assert entry["area"] == "architecture" and entry["item"] == "R01" and entry["note"] == "architecture.md"
    assert entry["for"] == [f"arch_rule:R01:{a}"]
    assert finding(report, f"jev:arch_rule:R01:{a}")[0].cites == {"note": "architecture.md", "line": finding(report, f"jev:arch_rule:R01:{a}")[0].cites["line"]}

    Script(canned, monkeypatch).on("arch_rule:R01", "unclear", 0.7, path=a).on("arch_rule:R01", "violates", 0.8, path=b)
    report = run_gate(inputs_for("accept-01"), pack, tmp_path / "second")  # fresh cache: file B is re-asked
    assert report.outcome == "revise"
    assert report.readings["arch_rule:R01"]["status"] == "fail" and report.readings["arch_rule:R01"]["file"] == b
    assert {f.severity for f in finding(report, "jev:arch_rule:R01:")} == {"fail", "unclear"}
    violated = finding(report, f"jev:arch_rule:R01:{b}")[0]
    assert violated.cites["note"] == "architecture.md" and "architecture.md" in violated.hint and b in violated.hint


def test_rule_warnings_for_untracked_and_unreviewed_files(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch)
    generated = (
        "diff --git a/gen/api_pb.py b/gen/api_pb.py\nnew file mode 100644\n--- /dev/null\n+++ b/gen/api_pb.py\n"
        "@@ -0,0 +1,2 @@\n+# Generated by protoc. DO NOT EDIT!\n+x = 1\n"
    )
    inputs = inputs_for("accept-01", untracked=["notes.txt"])
    inputs.diff_text += generated
    report = run_gate(inputs, pack, tmp_path)
    ids = {f.id for f in report.findings if f.source == "rule"}
    assert {"rule:untracked_files", "rule:unreviewed_file:gen/api_pb.py"} <= ids
    assert report.outcome == "accept"
    dropped = [f for f in report.evidence["files"] if f.get("dropped_reason")]
    assert dropped == [{"path": "gen/api_pb.py", "status": "added", "tokens": dropped[0]["tokens"], "chunks": 0, "truncated": False, "dropped_reason": "generated"}]
    assert not bodies_for(canned, "gen/")


# ---------------------------------------------------------------------------
# rounds, cache, big diffs


def test_second_round_reasks_only_the_changed_file(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch)
    first = run_gate(inputs_for("accept-01"), pack, tmp_path)
    assert first.outcome == "accept" and canned.calls == 5 and first.usage["cached"] == 0

    inputs = inputs_for("accept-01")
    inputs.diff_text = inputs.diff_text.replace(
        "+        raise PeriodClosedError(f\"period {entry.period_id} is closed\")",
        "+        raise PeriodClosedError(f\"period {entry.period_id} is closed for posting\")",
    )
    second = run_gate(inputs, pack, tmp_path)
    assert second.round == 2 and second.outcome == "accept"
    assert canned.calls == 7  # the edited file and the whole change; three files came from the cache
    assert second.usage["cached"] == 3 and second.usage["requests"] == 2
    re_asked = {b["state"]["file"]["path"] for b in canned.bodies[5:] if "file" in b["state"]}
    assert re_asked == {"src/ledger/services/posting.py"}
    audit = [json.loads(line) for line in (tmp_path / "runs" / "r1" / "requests.jsonl").read_text().splitlines()]
    assert sum(1 for line in audit if line["cached"]) == 3
    assert second.delta == {"resolved": [], "new": [], "unchanged": []}


def test_big_diff_takes_the_compacted_path(canned, monkeypatch, pack, tmp_path):
    diff = "".join(synthetic_diff(f"src/ledger/services/mod{i}.py", 1, 60) for i in range(4))
    ticket = {"title": "t", "why": "w", "what": "x", "acceptance": ["one"]}
    cfg = Config(state_budget=3000, file_budget=12000)
    Script(canned, monkeypatch)
    report = run_gate(DeliveryInputs(ticket=ticket, diff_text=diff, no_tests_ok=True), pack, tmp_path, cfg=cfg)
    assert report.outcome == "accept" and report.evidence["compacted"] is True
    change = bodies_for(canned, "change")[0]["state"]
    assert change["diff"].count("diff --git") == 4 and "line-00-059" not in change["diff"]
    for body in bodies_for(canned, "src/"):
        assert "line-00-059" in body["state"]["file"]["patch"]
    assert "diff was compacted" in report.to_markdown()


def test_report_markdown_lists_hints(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch).on("dup:cache-helper", "reimplements", 0.9, path="src/ledger/services/reports.py")
    report = run_gate(inputs_for("duplicate-01"), pack, tmp_path)
    assert report.outcome == "revise"
    dup = finding(report, "jev:dup:cache-helper:")[0]
    assert dup.cites["note"] == "components/cache-helper.md" and dup.location["file"] == "src/ledger/services/reports.py"
    assert "src/ledger/storage/cache.py" in dup.hint
    text = report.to_markdown()
    assert "REVISE" in text and "jev:dup:cache-helper:src/ledger/services/reports.py" in text and "hint:" in text


# ---------------------------------------------------------------------------
# per-file hygiene: source files only, never the component's own path, logs must name tests


def test_deleted_and_non_source_files_skip_per_file_questions(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch)
    inputs = inputs_for("accept-01")
    deleted = (
        "diff --git a/src/ledger/services/legacy.py b/src/ledger/services/legacy.py\n"
        "deleted file mode 100644\nindex 1111111..0000000\n--- a/src/ledger/services/legacy.py\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n-def legacy():\n-    return 1\n"
    )
    inputs.diff_text += deleted + synthetic_diff("docs/guide.md", 1, 3) + synthetic_diff("tests/fixtures/all.json", 1, 3)
    report = run_gate(inputs, pack, tmp_path)
    assert report.outcome == "accept"
    reviewed = {b["state"]["file"]["path"] for b in canned.bodies if "file" in b["state"]}
    assert reviewed == {"src/ledger/services/posting.py", "src/ledger/api/router.py",
                        "src/ledger/services/test_posting.py", "src/ledger/api/test_router.py"}
    by_path = {f["path"]: f for f in report.evidence["files"]}
    assert by_path["docs/guide.md"]["dropped_reason"] == "not_source" and by_path["docs/guide.md"]["chunks"] == 0
    assert by_path["tests/fixtures/all.json"]["dropped_reason"] == "not_source"
    assert by_path["src/ledger/services/legacy.py"] == {"path": "src/ledger/services/legacy.py", "status": "deleted",
                                                        "tokens": by_path["src/ledger/services/legacy.py"]["tokens"],
                                                        "chunks": 0, "truncated": False, "dropped_reason": "deleted"}
    assert "dropped_reason" not in by_path["src/ledger/services/posting.py"]
    change = bodies_for(canned, "change")[0]["state"]["diff"]
    assert "docs/guide.md" in change and "src/ledger/services/legacy.py" in change  # still part of the whole change
    assert not [f for f in report.findings if f.id.startswith("rule:unreviewed_file")]
    assert report.rules["stats"]["files"] == 4 and report.rules["stats"]["skipped"] == 3 and report.rules["stats"]["dropped"] == 0
    # a wider source_globs brings the markdown file back
    inputs = inputs_for("accept-01")
    inputs.diff_text += synthetic_diff("docs/guide.md", 1, 3)
    report = run_gate(inputs, pack, tmp_path, cfg=Config(source_globs=["*.py", "*.md"]), run_id="r2")
    assert "dropped_reason" not in {f["path"]: f for f in report.evidence["files"]}["docs/guide.md"]
    assert bodies_for(canned, "docs/guide.md")


def test_dup_is_never_asked_for_the_components_own_path(pack: ContextPack):
    from dataclasses import replace as dc_replace

    cfg = Config()
    own = {g.id for g in rubric.file_gates("src/ledger/storage/cache.py", pack, cfg, ["a"], "cache ttl get set")}
    other = {g.id for g in rubric.file_gates("src/ledger/services/reports.py", pack, cfg, ["a"], "cache ttl get set")}
    assert "dup:cache-helper" not in own and "dup:cache-helper" in other
    assert {g for g in other if g.startswith("dup:")} - own == {"dup:cache-helper"}
    component = next(c for c in pack.items("component") if c.id == "cache-helper")
    assert component.meta["path"] == "src/ledger/storage/cache.py"
    assert rubric.under_component("src/ledger/storage/cache.py", component)
    assert rubric.under_component("./src/ledger/storage/cache.py", component)
    assert not rubric.under_component("src/ledger/storage/cache_test.py", component)
    folder = dc_replace(component, meta={**component.meta, "path": "src/ledger/storage/"})
    assert rubric.under_component("src/ledger/storage/cache.py", folder)
    assert rubric.under_component("src/ledger/storage/deep/x.py", folder)
    assert not rubric.under_component("src/ledger/storage_v2/x.py", folder)
    nopath = dc_replace(component, meta={**component.meta, "path": ""})
    assert not rubric.under_component("src/ledger/storage/cache.py", nopath)


def test_summary_only_log_warns_that_it_names_no_tests(canned, monkeypatch, pack, tmp_path):
    Script(canned, monkeypatch)
    quiet = parse_test_log("quiet.log", "============ test session starts ============\n............\n"
                                        "============ 12 passed in 0.30s ============\n")
    assert quiet.tool == "pytest" and quiet.passed == 12 and quiet.names == []
    report = run_gate(inputs_for("accept-01", logs=[quiet]), pack, tmp_path)
    warn = [f for f in report.findings if f.id == "rule:test_log_has_no_names"]
    assert warn and warn[0].severity == "warn" and warn[0].location == {"logs": ["quiet.log"]}
    assert "pytest -v" in warn[0].hint and "ac_proven" in warn[0].message
    assert report.evidence["tests"][0]["names_count"] == 0
    assert report.outcome == "accept"  # a warning never changes the verdict; the proof gates do
    full = run_gate(inputs_for("accept-01"), pack, tmp_path, run_id="r2")
    assert not [f for f in full.findings if f.id == "rule:test_log_has_no_names"]
    assert full.evidence["tests"][0]["names_count"] == 12
