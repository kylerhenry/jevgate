"""Findings and the report both gates write: JSON for tools, Markdown for the driving agent.

Finding ids are stable across rounds (``jev:<gate id>`` or ``rule:<name>``) so
``runs.delta`` can say what was resolved. ``Finding.p`` is the gated
probability, the number that was compared with ``threshold``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any, Iterable

from . import CATALOG_VERSION, __version__

SEVERITIES = ("fail", "unclear", "warn")
_SEVERITY_RANK = {name: rank for rank, name in enumerate(SEVERITIES)}
SOURCES = ("rule", "jev")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Finding:
    """One thing the reader must act on, from a rule or from a Jev reading."""

    id: str
    source: str = "jev"
    severity: str = "fail"
    gate: str | None = None
    p: float | None = None
    threshold: float | None = None
    borderline: bool = False
    level: int | None = None
    location: dict = field(default_factory=dict)
    cites: dict | None = None
    message: str = ""
    hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Finding":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


def sort_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Worst first: fail → unclear → warn, then gated probability ascending (None last), then id."""
    def key(finding: Finding):
        p = finding.p
        return (
            _SEVERITY_RANK.get(finding.severity, len(SEVERITIES)),
            p is None,
            p if p is not None else 0.0,
            finding.id,
        )
    return sorted(findings, key=key)


def _empty_optional() -> dict:
    return {"asks": [], "gather": []}


@dataclass
class Report:
    """Everything one round produced. ``outcome`` is the ticket route or delivery verdict."""

    gate: str
    outcome: str
    exit_code: int
    run_id: str = ""
    round: int = 0
    created: str = field(default_factory=_now)
    model: str = "jev-latest"
    jevgate: str = __version__
    catalog: str = CATALOG_VERSION
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    readings: dict[str, dict] = field(default_factory=dict)
    asks: list[dict] = field(default_factory=list)
    gather: list[dict] = field(default_factory=list)
    optional: dict = field(default_factory=_empty_optional)
    split: dict | None = None
    architecture: dict | None = None
    reuse: list[dict] = field(default_factory=list)
    rules: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    delta: dict | None = None

    # -- JSON ---------------------------------------------------------------

    def to_json(self) -> dict:
        """Plain dicts and lists, ready for ``json.dumps``."""
        data = {f.name: getattr(self, f.name) for f in fields(self)}
        data["findings"] = [f.to_dict() for f in sort_findings(self.findings)]
        return data

    @classmethod
    def from_json(cls, data: dict) -> "Report":
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["findings"] = [
            f if isinstance(f, Finding) else Finding.from_dict(f) for f in data.get("findings") or []
        ]
        kwargs.setdefault("gate", "ticket")
        kwargs.setdefault("outcome", "uncertain")
        kwargs.setdefault("exit_code", 3)
        if kwargs.get("optional") is None:
            kwargs["optional"] = _empty_optional()
        return cls(**kwargs)

    # -- Markdown -----------------------------------------------------------

    def title(self) -> str:
        return f"# jevgate {self.gate} — {self.outcome.upper()} (round {self.round}, exit {self.exit_code})"

    def default_summary(self) -> str:
        counts = {s: 0 for s in SEVERITIES}
        for finding in self.findings:
            counts[finding.severity] = counts.get(finding.severity, 0) + 1
        parts = [f"{n} {name}" for name, n in counts.items() if n]
        findings = ", ".join(parts) if parts else "no findings"
        bits = [f"{len(self.findings)} finding(s) ({findings})" if self.findings else "No findings"]
        if self.gather:
            bits.append(f"{len(self.gather)} gather item(s)")
        if self.asks:
            bits.append(f"{len(self.asks)} question(s) for a human")
        if self.split:
            bits.append("a split is suggested")
        usage = self.usage or {}
        if usage.get("requests") is not None:
            bits.append(f"{usage.get('requests', 0)} request(s), {usage.get('cached', 0)} cached")
        if usage.get("errors"):
            bits.append(f"{len(usage['errors'])} API error(s)")
        return "; ".join(bits) + "."

    def to_markdown(self) -> str:
        out = [self.title(), "", self.summary or self.default_summary(), ""]
        if self.split:
            out += ["Split: " + _inline(self.split), ""]
        _section(out, "Findings", [_finding_lines(f) for f in sort_findings(self.findings)])
        _section(out, "Gather", [_gather_line(g) for g in self.gather])
        _section(out, "Ask", [f"{i}. {_ask_text(a)}" for i, a in enumerate(self.asks, 1)])
        optional = []
        for i, ask in enumerate((self.optional or {}).get("asks") or [], 1):
            optional.append(f"- ask {i}: {_ask_text(ask)}")
        for item in (self.optional or {}).get("gather") or []:
            optional.append(_gather_line(item))
        _section(out, "Optional", optional)
        _section(out, "Architecture", _architecture_lines(self.architecture))
        _section(out, "Reuse", _reuse_lines(self.reuse))
        _section(out, "Evidence", _evidence_lines(self.evidence))
        _section(out, "Usage", _usage_lines(self.usage))
        _section(out, "Delta", _delta_lines(self.delta))
        while out and out[-1] == "":
            out.pop()
        return "\n".join(out) + "\n"


# -- markdown helpers ---------------------------------------------------------


def _section(out: list[str], heading: str, lines: list[str]) -> None:
    if not lines:
        return
    out += [f"## {heading}", ""]
    out += lines
    out.append("")


def _fmt_p(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) and not isinstance(value, bool) else "?"


def _inline(data: Any) -> str:
    if isinstance(data, dict):
        return ", ".join(f"{k}={_inline(v)}" for k, v in data.items() if v not in (None, "", [], {}))
    if isinstance(data, (list, tuple)):
        return "[" + ", ".join(_inline(v) for v in data) + "]"
    if isinstance(data, float):
        return _fmt_p(data)
    return str(data)


def _finding_lines(finding: Finding) -> str:
    where = _inline(finding.location) if finding.location else ""
    if finding.cites:
        cite = finding.cites.get("note", "")
        if finding.cites.get("line") is not None:
            cite = f"{cite}:{finding.cites['line']}"
        where = f"{where}; cites {cite}" if where else f"cites {cite}"
    head = f"- **{finding.id}** [{finding.severity}]"
    if where:
        head += f" ({where})"
    if finding.p is not None or finding.threshold is not None:
        head += f" — p {_fmt_p(finding.p)} vs threshold {_fmt_p(finding.threshold)}"
        if finding.borderline:
            head += " (borderline)"
    if finding.level is not None:
        head += f" — level {finding.level}"
    if finding.message:
        head += f" — {finding.message}"
    if finding.hint:
        head += f"\n  hint: {finding.hint}"
    return head


def _gather_line(item: dict) -> str:
    area = item.get("area", "?")
    where = " / ".join(str(item[k]) for k in ("note", "item") if item.get(k))
    line = f"- **{area}**" + (f" ({where})" if where else "")
    if item.get("missing"):
        line += f": {item['missing']}"
    if item.get("for"):
        line += f" (for: {', '.join(str(g) for g in item['for'])})"
    return line


def _ask_text(ask: dict) -> str:
    text = str(ask.get("question", ""))
    tail = []
    if ask.get("id"):
        tail.append(str(ask["id"]))
    if ask.get("p") is not None:
        tail.append(f"p {_fmt_p(ask['p'])}")
    return text + (f" ({', '.join(tail)})" if tail else "")


def _architecture_lines(arch: dict | None) -> list[str]:
    if not arch:
        return []
    lines = []
    rules = arch.get("rules") or {}
    for key in ("complies", "violates", "na", "unclear"):
        if rules.get(key):
            lines.append(f"- rules {key}: {', '.join(str(r) for r in rules[key])}")
    if rules.get("cited"):
        lines.append(f"- cited: {_inline(rules['cited'])}")
    placement = arch.get("placement") or {}
    if placement:
        lines.append(f"- placement: expected {placement.get('expected', '?')}, stated {placement.get('stated', '?')}")
    if arch.get("mechanism"):
        lines.append(f"- mechanism: {_inline(arch['mechanism'])}")
    return lines


def _reuse_lines(reuse: list[dict]) -> list[str]:
    if not reuse:
        return []
    lines = ["| component | overlap | uses | verdict |", "|---|---|---|---|"]
    for row in reuse:
        lines.append(
            f"| {row.get('component', '?')} | {_fmt_p(row.get('overlap_p'))} | "
            f"{_fmt_p(row.get('uses_p'))} | {row.get('verdict', '')} |"
        )
    return lines


def _evidence_lines(evidence: dict) -> list[str]:
    if not evidence:
        return []
    lines = []
    context = evidence.get("context") or {}
    for area, sent in context.items():
        names = [s.get("title", s.get("path", "?")) if isinstance(s, dict) else str(s) for s in sent or []]
        lines.append(f"- context {area}: {', '.join(names) if names else '(nothing)'}")
    files = evidence.get("files") or []
    if files:
        lines.append(f"- files: {', '.join(_evidence_name(f) for f in files)}")
    tests = evidence.get("tests") or []
    if tests:
        lines.append(f"- tests: {', '.join(_evidence_name(t) for t in tests)}")
    if evidence.get("compacted"):
        lines.append("- diff was compacted to fit the state budget")
    for key, value in evidence.items():
        if key not in ("context", "files", "tests", "compacted") and value not in (None, "", [], {}):
            lines.append(f"- {key}: {_inline(value)}")
    return lines


def _evidence_name(item: Any) -> str:
    if isinstance(item, dict):
        name = str(item.get("path", item.get("name", "?")))
        if item.get("dropped_reason"):
            name += f" (dropped: {item['dropped_reason']})"
        return name
    return str(item)


def _usage_lines(usage: dict) -> list[str]:
    if not usage:
        return []
    lines = [
        f"- requests: {usage.get('requests', 0)} ({usage.get('cached', 0)} cached), "
        f"input tokens {usage.get('input_tokens', 0)}, output tokens {usage.get('output_tokens', 0)}, "
        f"cost ${float(usage.get('cost_usd') or 0):.4f}"
    ]
    for error in usage.get("errors") or []:
        lines.append(f"- error: {error}")
    return lines


def _delta_lines(delta: dict | None) -> list[str]:
    if not delta:
        return []
    lines = []
    for key in ("resolved", "new", "unchanged"):
        ids = delta.get(key) or []
        if ids:
            lines.append(f"- {key}: {', '.join(str(i) for i in ids)}")
    return lines
