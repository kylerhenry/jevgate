"""The README and the delivery skill document what the delivery gate reports:
whole-change chunking, the ``rule:evidence_truncated`` finding and the chunk cap."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
SKILL = (ROOT / "skills" / "delivery-gate" / "SKILL.md").read_text(encoding="utf-8")


def test_docs_name_the_truncation_finding():
    assert README.count("rule:evidence_truncated") >= 1
    assert SKILL.count("rule:evidence_truncated") >= 1


def test_readme_state_budget_row_describes_chunking_not_compaction():
    (row,) = [line for line in README.splitlines() if line.startswith("| `state_budget` |")]
    assert "chunked at hunk boundaries" in row and "never compacted" in row and "exit 4" in row
    assert "compacted to fit" not in README


def test_skill_explains_partial_readings_and_the_chunk_cap():
    assert "partial: true" in SKILL and "change too large" in SKILL and "more than 8" in SKILL
