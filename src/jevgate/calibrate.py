"""``jevgate calibrate``: sweep gate thresholds over labelled fixtures.

Every fixture case is run through its gate exactly as the gate's CLI would run
it, but with a client that answers from a directory of cached responses
(``--responses``; ``--refresh`` fetches the missing ones live). The stored
probabilities in each report are then re-read at every candidate threshold and
``unclear_at`` and compared with the labels in ``expected.json``.

Pure parts: :func:`sweep` (arithmetic over stored readings), :func:`agreement`
(route/verdict, gather and ask agreement at current defaults), :func:`render`.
I/O parts: :func:`case_inputs`, :func:`run_case`, :func:`cmd_calibrate`.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence

from jevgate.cli import EXIT_ERROR, EXIT_OK, load_config
from jevgate.client import TypeSafeClient
from jevgate.config import Config
from jevgate.context import ContextPack
from jevgate.report import Report
from jevgate.runs import Run

KINDS = ("ticket", "delivery")
DEFAULT_THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)
DEFAULT_UNCLEAR_ATS = (0.3, 0.4, 0.5)
DEFAULT_RESPONSES = Path("fixtures") / "responses"
DEFAULT_CONTEXT_DIR = Path("fixtures") / "vault"
DEFAULT_PROJECT = "ledger"
ITEM_SEPARATOR = ":"
CACHE_MISS = "cache miss: "
CASE_FILE = {"ticket": "draft.md", "delivery": "ticket.md"}
OUTCOME_KEY = {"ticket": "route", "delivery": "verdict"}
FAIL_STATUS, UNCLEAR_STATUS, PASS_STATUS = "fail", "unclear", "pass"

# Label families in expected.json that stand for gate families: ticket gather
# labels name context areas, and ``reuse_missed`` is composed from ``overlap``.
TICKET_LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "reuse_missed": ("overlap",),
    "architecture": ("arch_rule", "placement", "parallel_mechanism"),
    "components": ("overlap", "uses", "reuse_missed"),
    "decisions": ("decision", "simpler_alternative"),
    "data": ("data_ownership",),
    "interfaces": ("interface_compat",),
    "constraints": ("constraint",),
}
LABEL_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {"ticket": TICKET_LABEL_ALIASES, "delivery": {}}


# ---------------------------------------------------------------------------
# Pure: re-reading stored readings


def family_of(gate_id: str) -> str:
    """``arch_rule`` for ``arch_rule:R03``; the id itself when it has no item."""
    return gate_id.split(ITEM_SEPARATOR, 1)[0]


def direction_of(readings: Iterable[Mapping[str, Any]]) -> str:
    """``pass`` when the gated probability is P(pass), ``fire`` when it is P(fail).

    Noul and level kinds carry the direction in their ``kind``; a choice gate
    reveals it through which side ``p`` equals. Majority vote across readings.
    """
    votes: Counter[str] = Counter()
    for reading in readings:
        kind = reading.get("kind")
        if kind in ("pass", "level"):
            votes["pass"] += 1
        elif kind == "fire":
            votes["fire"] += 1
        elif kind == "choice":
            p, p_pass, p_fail = reading.get("p"), reading.get("p_pass"), reading.get("p_fail")
            if p is None or p_pass == p_fail:
                continue
            votes["pass" if p == p_pass else "fire"] += 1
    return "fire" if votes["fire"] > votes["pass"] else "pass"


def restatus(reading: Mapping[str, Any], threshold: float, unclear_at: float, direction: str) -> str:
    """The status ``reading`` would have had at ``threshold`` / ``unclear_at``."""
    if reading.get("status") == "na":
        return "na"
    p_pass, p_fail = reading.get("p_pass"), reading.get("p_fail")
    if p_pass is None or p_fail is None:
        return "unknown"
    if reading.get("kind") == "choice" and float(reading.get("p_unclear") or 0.0) >= unclear_at:
        return UNCLEAR_STATUS
    if direction == "fire":
        return FAIL_STATUS if float(p_fail) >= threshold else PASS_STATUS
    return PASS_STATUS if float(p_pass) >= threshold else FAIL_STATUS


def family_status(readings: Iterable[Mapping[str, Any]], threshold: float, unclear_at: float, direction: str) -> str:
    """Worst recomputed status across one family's readings (fail > unclear > pass); ``unknown`` when none answered."""
    statuses = {restatus(r, threshold, unclear_at, direction) for r in readings}
    for status in (FAIL_STATUS, UNCLEAR_STATUS, PASS_STATUS):
        if status in statuses:
            return status
    return "unknown"


def label_matches(family: str, labels: Iterable[str], aliases: Mapping[str, Sequence[str]]) -> bool:
    """True when a label is ``family``, an item of it, or an alias that stands for it."""
    for label in labels:
        head = family_of(str(label))
        if head == family or family in aliases.get(head, ()):
            return True
    return False


def _answered(readings: Iterable[Mapping[str, Any]]) -> bool:
    return any(r.get("p") is not None for r in readings)


def family_index(readings_by_case: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, list[dict]]]:
    """``{family: {case: [readings]}}`` over every non-info reading."""
    index: dict[str, dict[str, list[dict]]] = {}
    for case, readings in readings_by_case.items():
        for gate_id, reading in readings.items():
            if reading.get("kind") == "info":
                continue
            index.setdefault(family_of(gate_id), {}).setdefault(case, []).append(dict(reading))
    return index


def _mode(values: Iterable[Any], fallback: Any) -> Any:
    counts = Counter(v for v in values if v is not None)
    return counts.most_common(1)[0][0] if counts else fallback


def _stricter(direction: str):
    """Sort key: among equal agreements the stricter threshold wins."""
    return (lambda row: -row["threshold"]) if direction == "pass" else (lambda row: row["threshold"])


def pick_best(rows: Sequence[Mapping[str, Any]], direction: str, default: float) -> float:
    """Highest agreement; ties go to the stricter threshold (higher for pass-kind, lower for fire-kind)."""
    scored = [row for row in rows if row.get("agreement") is not None]
    if not scored:
        return default
    top = max(row["agreement"] for row in scored)
    ties = [row for row in scored if row["agreement"] == top]
    return float(min(ties, key=_stricter(direction))["threshold"])


def _sweep_family(family: str, per_case: Mapping[str, list[dict]], labels_by_case: Mapping[str, Mapping[str, Any]],
                  thresholds: Iterable[float], unclear_at: float, aliases: Mapping[str, Sequence[str]]) -> dict:
    everything = [r for rs in per_case.values() for r in rs]
    direction = direction_of(everything)
    default = float(_mode((r.get("threshold") for r in everything), 0.85))
    answered = {case: rs for case, rs in per_case.items() if _answered(rs)}
    failing = {c for c in answered if label_matches(family, labels_by_case.get(c, {}).get("failing_gates") or [], aliases)}
    n_passing = len(answered) - len(failing)
    rows = []
    for threshold in sorted({round(float(t), 4) for t in thresholds} | {default}):
        fired = {c for c, rs in answered.items() if family_status(rs, threshold, unclear_at, direction) == FAIL_STATUS}
        hits, false = len(fired & failing), len(fired - failing)
        agreement = round((hits + n_passing - false) / len(answered), 4) if answered else None
        rows.append({
            "threshold": threshold, "fires_failing": hits, "n_failing": len(failing),
            "fires_passing": false, "n_passing": n_passing, "agreement": agreement, "default": threshold == default,
        })
    return {
        "kind": _mode((r.get("kind") for r in everything), "choice"), "direction": direction, "default": default,
        "asked": len(answered), "unanswered": len(per_case) - len(answered), "labelled_failing": sorted(failing),
        "rows": rows, "best": pick_best(rows, direction, default),
    }


def _expected_state(family: str, labels: Mapping[str, Any], aliases: Mapping[str, Sequence[str]]) -> str:
    if label_matches(family, labels.get("failing_gates") or [], aliases):
        return FAIL_STATUS
    if label_matches(family, labels.get("gather") or [], aliases):
        return UNCLEAR_STATUS
    return PASS_STATUS


def _sweep_unclear(index: Mapping[str, Mapping[str, list[dict]]], families: Mapping[str, dict],
                   labels_by_case: Mapping[str, Mapping[str, Any]], unclear_ats: Iterable[float], default: float,
                   aliases: Mapping[str, Sequence[str]]) -> dict:
    """Three-way agreement (fail/unclear/pass vs failing/gather/other) for choice families at their default thresholds."""
    pairs = []
    for family, info in families.items():
        if info["kind"] != "choice":
            continue
        for case, readings in index[family].items():
            if _answered(readings):
                expected = _expected_state(family, labels_by_case.get(case, {}), aliases)
                pairs.append((readings, info["direction"], info["default"], expected))
    rows = []
    for unclear_at in sorted({round(float(u), 4) for u in unclear_ats} | {default}):
        agree = gather_hits = n_gather = other_unclear = 0
        for readings, direction, threshold, expected in pairs:
            got = family_status(readings, threshold, unclear_at, direction)
            agree += got == expected
            if expected == UNCLEAR_STATUS:
                n_gather += 1
                gather_hits += got == UNCLEAR_STATUS
            else:
                other_unclear += got == UNCLEAR_STATUS
        rows.append({
            "unclear_at": unclear_at, "unclear_gather": gather_hits, "n_gather": n_gather,
            "unclear_other": other_unclear, "n_other": len(pairs) - n_gather,
            "agreement": round(agree / len(pairs), 4) if pairs else None, "default": unclear_at == default,
        })
    scored = [row for row in rows if row["agreement"] is not None]
    best = default
    if scored:
        top = max(row["agreement"] for row in scored)
        best = min(row["unclear_at"] for row in scored if row["agreement"] == top)  # ties: more cautious
    return {"default": default, "pairs": len(pairs), "rows": rows, "best": best}


def snippet(families: Mapping[str, Mapping[str, Any]], unclear: Mapping[str, Any]) -> dict:
    """A ``jevgate.json`` fragment naming only what differs from the current defaults."""
    changed = {name: info["best"] for name, info in families.items() if info["best"] != info["default"]}
    out: dict[str, Any] = {}
    if changed:
        out["thresholds"] = changed
    if unclear.get("best") is not None and unclear["best"] != unclear.get("default"):
        out["unclear_at"] = unclear["best"]
    return out


def sweep(readings_by_case: Mapping[str, Mapping[str, Mapping[str, Any]]],
          labels_by_case: Mapping[str, Mapping[str, Any]],
          families: Sequence[str] | None = None,
          thresholds: Iterable[float] = DEFAULT_THRESHOLDS,
          unclear_ats: Iterable[float] = DEFAULT_UNCLEAR_ATS,
          *, unclear_default: float = 0.50,
          aliases: Mapping[str, Sequence[str]] | None = None) -> dict:
    """Re-read every stored reading at every candidate and count agreement with the labels.

    ``readings_by_case`` is ``{case: Report.readings}``; ``labels_by_case`` is
    ``{case: expected.json}`` (``failing_gates`` and ``gather`` are used). A
    family counts as asked in a case when at least one of its readings has a
    probability. Returns ``families``, ``unclear``, ``snippet`` and the
    ``unknown_families`` that were requested but never asked.
    """
    aliases = dict(aliases or {})
    index = family_index(readings_by_case)
    requested = list(families) if families else sorted(index)
    unknown = [name for name in requested if name not in index]
    thresholds, unclear_ats = list(thresholds), list(unclear_ats)
    swept = {
        name: _sweep_family(name, index[name], labels_by_case, thresholds, unclear_default, aliases)
        for name in requested if name in index
    }
    unclear = _sweep_unclear(index, swept, labels_by_case, unclear_ats, unclear_default, aliases)
    return {
        "cases": len(readings_by_case), "families": swept, "unclear": unclear,
        "snippet": snippet(swept, unclear), "unknown_families": unknown,
    }


# ---------------------------------------------------------------------------
# Pure: agreement at current defaults


@dataclass
class CaseResult:
    """One fixture case after its gate ran: the report, the labels and what was asked."""

    name: str
    report: Report
    expected: dict
    asked: set[str] = field(default_factory=set)
    misses: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _gather_entries(report: Report) -> list[dict]:
    """The blocking gather entries; ``optional.gather`` is advisory and not compared with labels."""
    return [e for e in report.gather if isinstance(e, dict)]


def gather_labels(report: Report) -> set[str]:
    """Every label a gather entry answers to: its area, ``area:item``, its gate ids and their families."""
    labels: set[str] = set()
    for entry in _gather_entries(report):
        area, item = entry.get("area"), entry.get("item")
        if area:
            labels.add(str(area))
            if item:
                labels.add(f"{area}{ITEM_SEPARATOR}{item}")
        for gate_id in entry.get("for") or []:
            labels.add(str(gate_id))
            labels.add(family_of(str(gate_id)))
    return labels


def gather_summary(report: Report) -> list[str]:
    """One short label per gather entry, for display."""
    out = []
    for entry in _gather_entries(report):
        area, item, for_ids = entry.get("area"), entry.get("item"), entry.get("for") or []
        text = f"{area}{ITEM_SEPARATOR}{item}" if item else str(area or "?")
        if for_ids and not item:
            text += f" [{', '.join(str(g) for g in for_ids)}]"
        out.append(text)
    return out


def ask_ids(report: Report) -> list[str]:
    """Bank ids the report asks (blocking asks only; ``optional.asks`` is advisory)."""
    return [str(a.get("id")) for a in report.asks if isinstance(a, dict) and a.get("id")]


def agreement(cases: Sequence[CaseResult], kind: str) -> dict:
    """Route/verdict, gather and ask agreement of each case's report with its labels."""
    key = OUTCOME_KEY.get(kind, "route")
    outcomes, gathers, asks = [], [], []
    for case in cases:
        expected = case.expected.get(key) or case.expected.get("route") or case.expected.get("verdict")
        outcomes.append({"case": case.name, "expected": expected, "got": case.report.outcome, "ok": expected == case.report.outcome})
        want = [str(x) for x in case.expected.get("gather") or []]
        got = gather_summary(case.report)
        if want or got:
            gathers.append({"case": case.name, "expected": want, "got": got, "ok": set(want) <= gather_labels(case.report)})
        want_asks = [str(x) for x in case.expected.get("asks") or []]
        got_asks = ask_ids(case.report)
        if want_asks or got_asks:
            asks.append({"case": case.name, "expected": want_asks, "got": got_asks, "ok": set(want_asks) <= set(got_asks)})

    def block(rows: list[dict]) -> dict:
        return {"rows": rows, "agree": sum(1 for r in rows if r["ok"]), "n": len(rows)}

    return {"outcome_key": key, "outcomes": block(outcomes), "gather": block(gathers), "asks": block(asks)}


def limitations(n_cases: int) -> str:
    return f"authored dev set, not held-out; {n_cases} cases; thresholds are defaults, not truths"


def build_result(kind: str, cases: Sequence[CaseResult], swept: Mapping[str, Any], *,
                 fixtures_dir: str = "", responses_dir: str = "") -> dict:
    """The full calibration result: sweep, agreement at defaults, cache misses, limitations."""
    misses = [{"case": c.name, **m} for c in cases for m in c.misses]
    errors = [f"{c.name}: {e}" for c in cases for e in c.errors]
    return {
        "kind": kind, "fixtures": fixtures_dir, "responses": responses_dir,
        "case_names": [c.name for c in cases], **dict(swept), **agreement(cases, kind),
        "cache_misses": misses, "errors": errors, "limitations": limitations(len(cases)),
    }


# ---------------------------------------------------------------------------
# Pure: rendering


def _pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2f}"


def _family_lines(name: str, info: Mapping[str, Any]) -> list[str]:
    verb = "fires when P ≥ t" if info["direction"] == "fire" else "passes when P ≥ t"
    header = (f"### {name} ({info['kind']}, {verb}; default {info['default']:.2f}; asked in {info['asked']} cases"
              + (f", {info['unanswered']} unanswered" if info.get("unanswered") else "")
              + f"; labelled failing: {', '.join(info['labelled_failing']) or 'none'})")
    if not info["asked"]:
        return [header, "", "(no answered readings for this family; nothing to sweep)", ""]
    lines = [header, "", "| threshold | fires on labelled-failing | fires on labelled-passing | agreement |",
             "|---|---|---|---|"]
    for row in info["rows"]:
        mark = " *" if row["default"] else ""
        lines.append(f"| {row['threshold']:.2f}{mark} | {row['fires_failing']}/{row['n_failing']} "
                     f"| {row['fires_passing']}/{row['n_passing']} | {_pct(row['agreement'])} |")
    lines += ["", f"best: {info['best']:.2f}" + (" (unchanged)" if info["best"] == info["default"] else ""), ""]
    return lines


def _unclear_lines(unclear: Mapping[str, Any]) -> list[str]:
    lines = [f"## unclear_at sweep (choice families at their default thresholds; {unclear['pairs']} case×family pairs)", "",
             "| unclear_at | unclear on labelled-gather | unclear on other | agreement |", "|---|---|---|---|"]
    for row in unclear["rows"]:
        mark = " *" if row["default"] else ""
        lines.append(f"| {row['unclear_at']:.2f}{mark} | {row['unclear_gather']}/{row['n_gather']} "
                     f"| {row['unclear_other']}/{row['n_other']} | {_pct(row['agreement'])} |")
    lines += ["", f"best: {unclear['best']:.2f}" + (" (unchanged)" if unclear["best"] == unclear["default"] else ""), ""]
    return lines


def _rows_lines(title: str, block: Mapping[str, Any], columns: tuple[str, str]) -> list[str]:
    lines = [f"## {title}", ""]
    if not block["rows"]:
        return lines + ["(nothing to compare)", ""]
    lines += [f"| case | {columns[0]} | {columns[1]} | ok |", "|---|---|---|---|"]
    for row in block["rows"]:
        want = ", ".join(row["expected"]) if isinstance(row["expected"], list) else row["expected"]
        got = ", ".join(row["got"]) if isinstance(row["got"], list) else row["got"]
        lines.append(f"| {row['case']} | {want or '-'} | {got or '-'} | {'yes' if row['ok'] else 'NO'} |")
    return lines + ["", f"agreement: {block['agree']}/{block['n']}", ""]


def render(result: Mapping[str, Any]) -> str:
    """The Markdown calibration report for one :func:`build_result` (or bare :func:`sweep`) result."""
    kind = result.get("kind", "")
    n_cases = result.get("cases", len(result.get("case_names") or []))
    lines = [f"# jevgate calibrate — {kind}".rstrip(" —"), ""]
    if result.get("fixtures") or result.get("responses"):
        lines += [f"fixtures: {result.get('fixtures')} ({n_cases} cases); responses: {result.get('responses')}", ""]
    lines += ["## Threshold sweep per family", ""]
    families = result.get("families") or {}
    if not families:
        lines += ["(no answered readings: nothing to sweep; run with --refresh to fetch responses)", ""]
    for name, info in families.items():
        lines += _family_lines(name, info)
    if result.get("unclear"):
        lines += _unclear_lines(result["unclear"])
    if "outcomes" in result:
        label = result.get("outcome_key", "route")
        lines += _rows_lines(f"{label.capitalize()} agreement at current defaults", result["outcomes"], ("expected", "got"))
        lines += _rows_lines("Gather agreement (expected ⊆ report)", result["gather"], ("expected", "got"))
        lines += _rows_lines("Asks agreement (expected ⊆ report)", result["asks"], ("expected", "got"))
    lines += ["## jevgate.json snippet", ""]
    fragment = result.get("snippet") or {}
    if fragment:
        lines += ["```json", json.dumps(fragment, indent=2), "```", ""]
    else:
        lines += ["(current defaults are already the best on this set)", ""]
    misses = result.get("cache_misses") or []
    if misses:
        lines += [f"## Cache misses ({len(misses)}) — run again with --refresh to fetch them", ""]
        lines += [f"- {m['case']}: {m.get('tag')} ({str(m.get('sha'))[:12]})" for m in misses] + [""]
    if result.get("errors"):
        lines += ["## Errors", ""] + [f"- {e}" for e in result["errors"]] + [""]
    if result.get("unknown_families"):
        lines += [f"## Unknown --gate families (never asked in any case): {', '.join(result['unknown_families'])}", ""]
    lines += ["## Limitations", "", result.get("limitations") or limitations(n_cases), ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# I/O: building and running cases


@dataclass
class CaseInputs:
    """What one case's gate needs: the labels, the config, the pack and the gate-specific inputs."""

    name: str
    case_dir: Path
    expected: dict
    cfg: Config
    pack: ContextPack | None
    ticket: dict | None = None
    inputs: Any = None


def gate_module(kind: str) -> ModuleType:
    """The gate module for ``kind``, imported lazily; raises ImportError when it is not there yet."""
    return importlib.import_module(f"jevgate.{kind}.gate")


def repo_root(fixtures_dir: Path) -> Path:
    """The directory that ``fixtures/`` lives in, or the cwd when ``fixtures_dir`` is elsewhere."""
    fixtures_dir = Path(fixtures_dir).resolve()
    return fixtures_dir.parent.parent if fixtures_dir.parent.name == "fixtures" else Path.cwd()


def resolve_path(text: str | Path, case_dir: Path, root: Path) -> Path:
    """Relative paths in a fixture resolve against the case dir, then the repo root, then the cwd."""
    path = Path(text).expanduser()
    if path.is_absolute():
        return path
    for base in (case_dir, root, Path.cwd()):
        if (base / path).exists():
            return base / path
    return root / path


def find_cases(kind: str, fixtures_dir: Path) -> list[Path]:
    """Case directories under ``fixtures_dir`` that carry ``expected.json`` and the gate's input file."""
    fixtures_dir = Path(fixtures_dir)
    if not fixtures_dir.is_dir():
        raise FileNotFoundError(f"fixtures dir not found: {fixtures_dir}")
    wanted = CASE_FILE[kind]
    return sorted(p for p in fixtures_dir.iterdir() if p.is_dir() and (p / "expected.json").is_file() and (p / wanted).is_file())


def _pack_kwargs(cfg: Config, project: str | None) -> dict:
    context = cfg.context or {}
    return {
        "project": project or context.get("project") or DEFAULT_PROJECT,
        "follow_links": int(context.get("follow_links", 1)),
        "context_budget": int(context.get("context_budget", cfg.context_budget)),
        "max_items": cfg.max_items if cfg.max_items is not None else 16,
    }


def load_case_pack(case_dir: Path, expected: Mapping[str, Any], args: Any, cfg: Config, root: Path) -> ContextPack:
    """The case's own ``context_json``/``context_dir`` when given, else ``--context-dir`` (default fixtures/vault)."""
    kwargs = _pack_kwargs(cfg, getattr(args, "project", None))
    if expected.get("context_json"):
        path = resolve_path(expected["context_json"], case_dir, root)
        return ContextPack.from_dict(json.loads(path.read_text(encoding="utf-8")), **kwargs)
    directory = expected.get("context_dir") or getattr(args, "context_dir", None) or str(DEFAULT_CONTEXT_DIR)
    areas = (cfg.context or {}).get("areas") or None
    return ContextPack.load(resolve_path(directory, case_dir, root), areas=areas, **kwargs)


def case_inputs(kind: str, case_dir: Path, args: Any, *, root: Path | None = None) -> CaseInputs:
    """Build one case's inputs exactly as ``jevgate <kind> check`` would from the case's files."""
    case_dir = Path(case_dir)
    root = root if root is not None else repo_root(case_dir.parent)
    expected = json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))
    cfg = load_config(args, root)
    pack = load_case_pack(case_dir, expected, args, cfg, root)
    built = CaseInputs(name=case_dir.name, case_dir=case_dir, expected=expected, cfg=cfg, pack=pack)
    if kind == "ticket":
        from jevgate.ticket.schema import load_ticket

        built.ticket = load_ticket(case_dir / CASE_FILE[kind])
        return built
    tests_dir = case_dir / "tests"
    logs = sorted(str(p) for p in tests_dir.glob("*.log")) if tests_dir.is_dir() else []
    namespace = argparse.Namespace(
        ticket=str(case_dir / CASE_FILE[kind]), from_linear=None, diff_file=str(case_dir / "change.patch"),
        base=None, head=None, repo=str(case_dir), test_log=logs, files=list(expected.get("files") or []),
        no_tests_ok=False, all_items=False,
    )
    built.inputs = gate_module(kind).build_inputs(namespace, cfg)
    return built


def run_case(kind: str, built: CaseInputs, responses_dir: Path, run_root: Path, *, refresh: bool = False) -> CaseResult:
    """Run the gate over ``built`` with a client that answers from ``responses_dir`` (live on misses with ``refresh``)."""
    run = Run(run_root, built.name, cache_dir=responses_dir)
    client = TypeSafeClient(
        enabled=True, cache_dir=responses_dir, audit_path=run.audit_path,
        workers=built.cfg.workers, use_cache=True, cache_only=not refresh,
    )
    gate = gate_module(kind)
    if kind == "ticket":
        report = gate.check(built.ticket, built.pack, client, built.cfg, run)
    else:
        report = gate.check(built.inputs, built.pack, client, built.cfg, run)
    asked = {qid for record in client.records for qid in record.get("question_ids") or []}
    misses = [{"tag": r.get("tag"), "sha": r.get("sha")} for r in client.records if str(r.get("error") or "").startswith(CACHE_MISS)]
    errors = [str(r["error"]) for r in client.records if r.get("error") and not str(r["error"]).startswith(CACHE_MISS)]
    return CaseResult(name=built.name, report=report, expected=built.expected, asked=asked, misses=misses, errors=errors)


def calibrate(kind: str, fixtures_dir: Path, args: Any) -> dict:
    """Run every case and sweep; the I/O composition behind ``cmd_calibrate``."""
    fixtures_dir = Path(fixtures_dir)
    cases = find_cases(kind, fixtures_dir)
    if not cases:
        raise FileNotFoundError(f"no {kind} cases under {fixtures_dir} (need <case>/expected.json and <case>/{CASE_FILE[kind]})")
    gate_module(kind)
    root = repo_root(fixtures_dir)
    responses_dir = resolve_path(getattr(args, "responses", None) or DEFAULT_RESPONSES, fixtures_dir, root)
    results: list[CaseResult] = []
    with tempfile.TemporaryDirectory(prefix="jevgate-calibrate-") as tmp:
        for case_dir in cases:
            built = case_inputs(kind, case_dir, args, root=root)
            results.append(run_case(kind, built, responses_dir, Path(tmp) / "runs", refresh=bool(getattr(args, "refresh", False))))
    unclear_default = float(built.cfg.unclear_at)
    swept = sweep(
        {r.name: r.report.readings for r in results}, {r.name: r.expected for r in results},
        list(getattr(args, "gate", None) or []) or None,
        getattr(args, "thresholds", None) or DEFAULT_THRESHOLDS, getattr(args, "unclear_at", None) or DEFAULT_UNCLEAR_ATS,
        unclear_default=unclear_default, aliases=LABEL_ALIASES.get(kind, {}),
    )
    return build_result(kind, results, swept, fixtures_dir=str(fixtures_dir), responses_dir=str(responses_dir))


# ---------------------------------------------------------------------------
# CLI


def _float_list(text: str) -> list[float]:
    try:
        values = [float(part) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected comma-separated numbers, got {text!r}") from exc
    if not values or any(not 0 <= v <= 1 for v in values):
        raise argparse.ArgumentTypeError(f"every value must be within [0, 1], got {text!r}")
    return values


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Run the sweep and print (or write) the Markdown or JSON result."""
    try:
        result = calibrate(args.kind, Path(args.fixtures_dir), args)
    except ImportError as error:
        print(f"jevgate: error: the {args.kind} gate is not available yet ({error}); nothing to calibrate", file=sys.stderr)
        return EXIT_ERROR
    except (FileNotFoundError, ValueError, OSError) as error:
        print(f"jevgate: error: {error}", file=sys.stderr)
        return EXIT_ERROR
    text = json.dumps(result, indent=2, ensure_ascii=False) + "\n" if args.json else render(result)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if result.get("unknown_families"):
        print(f"jevgate: error: unknown --gate families: {', '.join(result['unknown_families'])}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def register(subparsers: Any) -> None:
    """Add the ``calibrate`` command."""
    parser = subparsers.add_parser("calibrate", help="sweep gate thresholds over labelled fixtures and cached responses")
    parser.add_argument("kind", choices=KINDS, metavar="(ticket|delivery)", help="which gate's fixtures to calibrate")
    parser.add_argument("fixtures_dir", metavar="<fixtures-dir>", help="folder of <case>/ dirs with expected.json")
    parser.add_argument("--responses", metavar="DIR", default=str(DEFAULT_RESPONSES),
                        help="request cache the cases answer from (default: fixtures/responses)")
    parser.add_argument("--gate", action="append", default=[], metavar="FAMILY", help="only sweep this family (repeatable)")
    parser.add_argument("--thresholds", type=_float_list, default=list(DEFAULT_THRESHOLDS), metavar="P,P,...",
                        help="candidate thresholds (default: 0.5,0.6,0.7,0.8,0.85,0.9,0.95)")
    parser.add_argument("--unclear-at", dest="unclear_at", type=_float_list, default=list(DEFAULT_UNCLEAR_ATS), metavar="P,P,...",
                        help="candidate unclear_at values (default: 0.3,0.4,0.5)")
    parser.add_argument("--refresh", action="store_true", help="fetch missing responses live and store them in --responses")
    parser.add_argument("--context-dir", dest="context_dir", metavar="DIR",
                        help="context notes for cases that name none (default: fixtures/vault)")
    parser.add_argument("--project", metavar="NAME", help="context pack project scope (default: ledger)")
    parser.add_argument("--config", metavar="F", help="config file applied after <repo>/jevgate.json")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    parser.add_argument("--out", metavar="F", help="write the result to F instead of stdout")
    parser.set_defaults(func=cmd_calibrate)


__all__ = [
    "register", "cmd_calibrate", "calibrate", "sweep", "render", "agreement", "build_result",
    "case_inputs", "run_case", "find_cases", "CaseInputs", "CaseResult",
    "family_of", "direction_of", "restatus", "family_status", "label_matches", "pick_best", "snippet",
    "DEFAULT_THRESHOLDS", "DEFAULT_UNCLEAR_ATS", "LABEL_ALIASES",
]
