"""Run directories: one folder per gate run holding a report per round, the
bank-question ledger and the request audit log.

Layout of ``<root>/<run_id>/``: ``round-N.json``, ``round-N.md``,
``ledger.json`` (bank question ids already put to the human) and
``requests.jsonl`` (the client's audit log). Reusing a run directory starts the
next round.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .report import Report

ROUND_RE = re.compile(r"^round-(\d+)\.json$")
LEDGER_NAME = "ledger.json"
AUDIT_NAME = "requests.jsonl"
DEFAULT_ROOT = Path(".jevgate") / "runs"


def default_run_id(gate: str, when: datetime | None = None) -> str:
    """``<YYYYMMDD-HHMMSS>-<gate>`` in local time."""
    when = when or datetime.now()
    return f"{when.strftime('%Y%m%d-%H%M%S')}-{gate}"


class Run:
    """One run directory. Created on construction."""

    def __init__(self, root: Path, run_id: str | None = None, *, gate: str = "run", cache_dir: Path | None = None) -> None:
        self.root = Path(root)
        self.run_id = run_id or default_run_id(gate)
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def at(cls, path: Path, *, cache_dir: Path | None = None) -> "Run":
        """A run whose directory is exactly ``path`` (what ``--run-dir`` names)."""
        path = Path(path)
        return cls(path.parent, path.name, cache_dir=cache_dir)

    @property
    def dir(self) -> Path:
        return self.root / self.run_id

    @property
    def audit_path(self) -> Path:
        return self.dir / AUDIT_NAME

    @property
    def ledger_path(self) -> Path:
        return self.dir / LEDGER_NAME

    # -- rounds -------------------------------------------------------------

    def rounds(self) -> list[int]:
        """Round numbers already written, ascending."""
        numbers = []
        for path in self.dir.iterdir():
            match = ROUND_RE.match(path.name)
            if match:
                numbers.append(int(match.group(1)))
        return sorted(numbers)

    def next_round(self) -> int:
        rounds = self.rounds()
        return (rounds[-1] + 1) if rounds else 1

    def round_paths(self, number: int) -> tuple[Path, Path]:
        return self.dir / f"round-{number}.json", self.dir / f"round-{number}.md"

    def write(self, report: Report) -> tuple[Path, Path]:
        """Write ``round-N.json`` and ``round-N.md``; fills ``report.round``/``run_id`` when unset."""
        if not report.round:
            report.round = self.next_round()
        if not report.run_id:
            report.run_id = self.run_id
        json_path, md_path = self.round_paths(report.round)
        json_path.write_text(json.dumps(report.to_json(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        md_path.write_text(report.to_markdown(), encoding="utf-8")
        return json_path, md_path

    def load(self, number: int) -> Report | None:
        json_path, _ = self.round_paths(number)
        if not json_path.is_file():
            return None
        return Report.from_json(json.loads(json_path.read_text(encoding="utf-8")))

    def previous(self) -> Report | None:
        """The latest report written so far, or None on a fresh run."""
        rounds = self.rounds()
        return self.load(rounds[-1]) if rounds else None

    # -- ledger -------------------------------------------------------------

    def ledger(self) -> set[str]:
        """Bank question ids already put to the human in this run."""
        if not self.ledger_path.is_file():
            return set()
        try:
            data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        asked = data.get("asked") if isinstance(data, dict) else data
        return {str(x) for x in asked or []}

    def add_ledger(self, ids: Iterable[str]) -> set[str]:
        asked = self.ledger() | {str(x) for x in ids}
        self.ledger_path.write_text(json.dumps({"asked": sorted(asked)}, indent=2) + "\n", encoding="utf-8")
        return asked


def delta(prev: Report | None, cur: Report) -> dict:
    """Finding ids resolved since ``prev``, new in ``cur``, and present in both."""
    before = {f.id for f in prev.findings} if prev else set()
    after = {f.id for f in cur.findings}
    return {
        "resolved": sorted(before - after),
        "new": sorted(after - before),
        "unchanged": sorted(before & after),
    }
