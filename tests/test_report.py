"""Finding order, Markdown sections and the JSON round trip."""

from __future__ import annotations

import json

from jevgate import CATALOG_VERSION, __version__
from jevgate.report import Finding, Report, sort_findings


def finding(id, severity="fail", p=None, **kw):
    return Finding(id=id, severity=severity, p=p, **kw)


def test_sort_findings_worst_first():
    findings = [
        finding("rule:missing_why", "warn", None, source="rule"),
        finding("jev:ac_met:2", "unclear", 0.3),
        finding("jev:b", "fail", 0.6),
        finding("jev:a", "fail", 0.2),
        finding("jev:c", "fail", None),
        finding("jev:d", "unclear", 0.1),
        finding("jev:e", "warn", 0.9),
    ]
    assert [f.id for f in sort_findings(findings)] == [
        "jev:a", "jev:b", "jev:c", "jev:d", "jev:ac_met:2", "jev:e", "rule:missing_why"]


def full_report():
    return Report(
        gate="ticket", outcome="revise", exit_code=1, run_id="20260927-120000-ticket", round=2,
        findings=[
            finding("jev:design_unambiguous", "fail", 0.62, gate="design_unambiguous", threshold=0.85,
                    message="Where the logic lives is open.", hint="Name the module.", location={"section": "what"}),
            finding("rule:hedges", "warn", source="rule", message="4 hedges", location={"section": "why"}),
            finding("jev:arch_rule:R03", "unclear", 0.35, gate="arch_rule:R03", threshold=0.6, borderline=False,
                    cites={"note": "architecture.md", "line": 12}, message="Rule R03 may not apply."),
        ],
        readings={"design_unambiguous": {"status": "fail", "p": 0.62}},
        asks=[{"id": "B03", "question": "Which queue is used?", "p": 0.8}],
        gather=[{"area": "components", "note": "cache-helper.md", "missing": "lacks `provides`", "for": ["reuse:cache-helper"]}],
        optional={"asks": [{"id": "B04", "question": "Is retry needed?", "p": 0.7}], "gather": []},
        split={"choice": "split_independent", "p": 0.7},
        architecture={"rules": {"complies": ["R01"], "violates": [], "na": ["R02"], "unclear": ["R03"], "cited": []},
                      "placement": {"expected": "worker", "stated": "api"}, "mechanism": "queue"},
        reuse=[{"component": "cache-helper", "overlap_p": 0.8, "uses_p": 0.1, "verdict": "reuse_missed"}],
        rules={"stats": {"avg_sentence_words": 12}},
        evidence={"context": {"architecture": [{"title": "architecture.md"}], "glossary": []},
                  "files": [{"path": "src/x.py"}, {"path": "big.py", "dropped_reason": "over budget"}],
                  "tests": [{"path": "pytest.log"}], "compacted": True},
        usage={"requests": 3, "cached": 1, "input_tokens": 1200, "output_tokens": 30, "cost_usd": 0.0000504,
               "errors": ["slice-b: TypeSafe HTTP 503"]},
        delta={"resolved": ["jev:why_is_a_problem"], "new": ["jev:arch_rule:R03"], "unchanged": ["jev:design_unambiguous"]},
    )


def test_markdown_sections_in_order():
    md = full_report().to_markdown()
    assert md.startswith("# jevgate ticket — REVISE (round 2, exit 1)\n")
    headings = [line for line in md.splitlines() if line.startswith("## ")]
    assert headings == ["## Findings", "## Gather", "## Ask", "## Optional", "## Architecture",
                        "## Reuse", "## Evidence", "## Usage", "## Delta"]
    body = md.split("\n")
    assert "3 finding(s) (2 fail" not in md  # one fail, one unclear, one warn
    assert "1 fail, 1 unclear, 1 warn" in md and "a split is suggested" in md
    # findings worst first with probability, threshold, message and hint
    first = body.index("- **jev:design_unambiguous** [fail] (section=what) — p 0.62 vs threshold 0.85 — Where the logic lives is open.")
    assert body[first + 1] == "  hint: Name the module."
    assert body.index("- **jev:arch_rule:R03** [unclear] (cites architecture.md:12) — p 0.35 vs threshold 0.60 — Rule R03 may not apply.") > first
    assert body.index("- **rule:hedges** [warn] (section=why) — 4 hedges") > first
    assert "- **components** (cache-helper.md): lacks `provides` (for: reuse:cache-helper)" in body
    assert "1. Which queue is used? (B03, p 0.80)" in body
    assert "- ask 1: Is retry needed? (B04, p 0.70)" in body
    assert "- rules complies: R01" in body and "- placement: expected worker, stated api" in body
    assert "| cache-helper | 0.80 | 0.10 | reuse_missed |" in body
    assert "- context architecture: architecture.md" in body and "- context glossary: (nothing)" in body
    assert "- files: src/x.py, big.py (dropped: over budget)" in body
    assert "- diff was compacted to fit the state budget" in body
    assert "- requests: 3 (1 cached), input tokens 1200, output tokens 30, cost $0.0001" in body
    assert "- error: slice-b: TypeSafe HTTP 503" in body
    assert "- resolved: jev:why_is_a_problem" in body and "- new: jev:arch_rule:R03" in body
    assert md.endswith("\n") and not md.endswith("\n\n")


def test_markdown_omits_empty_sections():
    report = Report(gate="delivery", outcome="accept", exit_code=0, round=1)
    md = report.to_markdown()
    assert md.startswith("# jevgate delivery — ACCEPT (round 1, exit 0)\n")
    assert "## " not in md
    assert "No findings." in md
    report.summary = "All criteria proven."
    assert "All criteria proven." in report.to_markdown()
    report.usage = {"requests": 0, "cached": 2}
    assert "## Usage" in report.to_markdown()


def test_json_round_trip():
    report = full_report()
    data = report.to_json()
    assert data["jevgate"] == __version__ and data["catalog"] == CATALOG_VERSION
    assert data["outcome"] == "revise" and data["exit_code"] == 1
    assert [f["id"] for f in data["findings"]][0] == "jev:design_unambiguous"
    assert isinstance(data["findings"][0], dict) and data["findings"][0]["location"] == {"section": "what"}
    text = json.dumps(data)
    back = Report.from_json(json.loads(text))
    assert back.to_json() == data
    assert back.findings[0] == report.findings[0]
    assert back.optional == report.optional and back.delta == report.delta


def test_from_json_tolerates_missing_and_unknown_keys():
    back = Report.from_json({"gate": "ticket", "outcome": "ready", "exit_code": 0, "future_key": 1, "optional": None})
    assert back.findings == [] and back.optional == {"asks": [], "gather": []}
    assert Report.from_json({}).outcome == "uncertain"
    assert Finding.from_dict({"id": "x", "bogus": 1}).id == "x"
