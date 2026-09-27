"""The ticket policy: rules first, then one request per state slice, then a route.

Nothing here asks the model a holistic question. Jev answers the narrow
catalog questions in :mod:`rubric`; this module reads them, composes the
derived findings (missed reuse, placement mismatch, ungrounded claims, the
split signal) and picks the route in a fixed order::

    split → gather (only when nothing fails) → ask (fails and asks)
          → revise (fails) → uncertain (no usable answers) → ready
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..cli import TICKET_EXITS
from ..client import Job, TypeSafeClient
from ..config import Config
from ..context import ContextPack
from ..questions import Gate, Reading, group_by_needs, read, to_questions
from ..report import Finding, Report, sort_findings
from ..runs import Run, delta
from ..textstats import rule_findings, stats, ticket_prose
from .clarify import prior_answer_ids, select_asks
from .rubric import OPTIONAL_AREAS, SPLIT_OPTIONS, all_gates, claim_notes
from .schema import draft_state, normalise_ticket, schema_problems, stated_locations, ticket_query_text

SPECIAL_SLICES = frozenset({"index", "claims"})
REQUIRED_AREAS = ("architecture", "components", "decisions")
WARN_AREAS = ("architecture", "components", "decisions", "data", "interfaces", "constraints")
AREA_UNLOCKS = {
    "architecture": ("arch_rule", "placement", "parallel_mechanism"),
    "components": ("overlap", "uses", "reuse_missed"),
    "decisions": ("decision", "simpler_alternative"),
    "data": ("data_ownership",),
    "interfaces": ("interface_compat",),
    "constraints": ("constraint",),
}
AREA_FIX = {
    "architecture": "add a note tagged #jevgate/architecture with #jevgate/rule bullets and #jevgate/layer headings",
    "components": "add one note per component tagged #jevgate/component with path, provides and interface",
    "decisions": "add ADR notes tagged #jevgate/decision with ## Decision and ## Alternatives",
    "data": "add a note tagged #jevgate/data describing stores, ownership and migrations",
    "interfaces": "add a note tagged #jevgate/interfaces listing public contracts and compatibility rules",
    "constraints": "add a note tagged #jevgate/constraints with #jevgate/constraint bullets",
}
NO_FINDING_FAMILIES = frozenset({"bank", "uses", "split"})

REUSE_OVERLAP_AT = 0.60
REUSE_USES_AT = 0.40
PLACEMENT_AT = 0.60
UNGROUNDED_AT = 0.60
EFFORT_SPLIT_AT = 0.50
EFFORT_TOP_LEVEL = "3"


# ----------------------------------------------------------------- rules


def _schema_findings(ticket: dict) -> list[Finding]:
    findings = []
    for field, message in schema_problems(ticket):
        findings.append(
            Finding(
                id=f"rule:missing_{field}",
                source="rule",
                severity="fail",
                gate=f"missing_{field}",
                location={"section": field},
                message=message,
                hint=f"Add the {field} section: " + {
                    "title": "one line naming the change.",
                    "why": "who is affected and what goes wrong today.",
                    "what": "the approach, components and interfaces.",
                    "acceptance": "observable outcomes as bullets, one per check.",
                }.get(field, "see `jevgate ticket init` for the template."),
            )
        )
    return findings


def _text_findings(ticket: dict, cfg: Config) -> tuple[list[Finding], dict]:
    numbers = stats(ticket_prose(ticket), cfg.hedges)
    findings = []
    for raw in rule_findings(ticket, hedges=cfg.hedges):
        findings.append(
            Finding(
                id=raw["id"],
                source="rule",
                severity=raw.get("severity", "warn"),
                gate=raw["id"].split(":", 1)[1],
                location={"section": "body"},
                message=raw.get("message", ""),
                hint=raw.get("hint", ""),
            )
        )
    return findings, numbers


def _missing_areas(pack: ContextPack | None) -> tuple[list[dict], list[Finding]]:
    """Gather entries for required areas and warn findings for every missing area."""
    gathers: list[dict] = []
    findings: list[Finding] = []
    for area in WARN_AREAS:
        if pack is not None and pack.has(area):
            continue
        missing = f"the context pack has no {area} notes: {AREA_FIX[area]}"
        if pack is None:
            missing = f"no context pack was loaded (pass --context-dir or set context.dir); {area} is needed: {AREA_FIX[area]}"
        findings.append(
            Finding(
                id=f"rule:missing_context_area:{area}",
                source="rule",
                severity="warn",
                gate=f"missing_context_area:{area}",
                location={"area": area},
                message=f"No {area} notes; the {', '.join(AREA_UNLOCKS[area])} question(s) were skipped.",
                hint=AREA_FIX[area],
            )
        )
        if area in REQUIRED_AREAS:
            gathers.append({"area": area, "note": None, "item": None, "missing": missing, "for": list(AREA_UNLOCKS[area])})
    return gathers, findings


# ----------------------------------------------------------------- state


def _slice_tag(needs: frozenset) -> str:
    return "ticket:" + ("+".join(sorted(needs)) or "draft")


def _merge_evidence(evidence: dict, sent: dict) -> None:
    context = evidence.setdefault("context", {})
    for area, detail in sent.items():
        rows = context.setdefault(area, [])
        for name in list(detail.get("notes") or []) + list(detail.get("items") or []):
            if name not in rows:
                rows.append(name)
        if detail.get("truncated"):
            truncated = evidence.setdefault("truncated", [])
            if area not in truncated:
                truncated.append(area)


def _state(needs: frozenset, ticket: dict, pack: ContextPack | None, query: str, cfg: Config, evidence: dict) -> dict:
    """The state one slice is asked over: the draft plus exactly the needed areas."""
    if needs == frozenset({"index"}):
        return {
            "draft": draft_state(ticket),
            "prior_answers": list(ticket.get("prior_answers") or []),
            "pack_index": pack.index() if pack is not None else [],
        }
    if needs == frozenset({"claims"}):
        claims = list(ticket.get("context_bullets") or [])
        notes = claim_notes(pack, claims, k=cfg.max_items, budget=cfg.context_budget) if pack is not None else []
        evidence.setdefault("context", {}).setdefault("claims", []).extend(n["path"] for n in notes)
        return {"claims": claims, "notes": notes}
    state: dict = {"draft": draft_state(ticket)}
    if pack is not None and needs:
        state.update(pack.slice(set(needs), query, all_items=cfg.max_items is None))
        _merge_evidence(evidence, pack.last_evidence)
    return state


def _applicable(needs: frozenset, pack: ContextPack | None) -> bool:
    required = set(needs) - OPTIONAL_AREAS - SPECIAL_SLICES
    if not required:
        return True
    return pack is not None and all(pack.has(a) for a in required)


# ----------------------------------------------------------------- readings → findings


def _area_of(gate: Gate) -> str:
    if gate.item and gate.item.get("area"):
        return str(gate.item["area"])
    for area in sorted(gate.needs):
        if area not in SPECIAL_SLICES:
            return area
    return "draft"


def _location(gate: Gate) -> dict:
    family, item = gate.family, gate.item or {}
    if family == "ac_testable":
        return {"criterion": item.get("index")}
    if family == "claim":
        return {"context": item.get("index")}
    if family in ("overlap", "uses", "reuse_missed"):
        return {"component": item.get("id")}
    if family == "decision":
        return {"decision": item.get("id")}
    if family == "constraint":
        return {"constraint": item.get("id")}
    if family == "arch_rule":
        return {"section": "what", "rule": item.get("id")}
    if family == "why_is_a_problem":
        return {"section": "why"}
    if family == "title_matches_body":
        return {"section": "title"}
    if family in ("language_unambiguous", "readability"):
        return {"section": "body"}
    return {"section": "what"}


def _cites(gate: Gate) -> dict | None:
    item = gate.item or {}
    if item.get("note"):
        return {"note": item["note"], "line": item.get("line")}
    return None


def _message(gate: Gate, reading: Reading) -> str:
    label = gate.family.replace("_", " ")
    if reading.status == "unclear":
        return f"{label}: the notes do not settle it (P(unclear) = {reading.p_unclear})."
    if gate.kind == "level":
        return f"{label}: judged level {reading.level} ({reading.legend}); P(acceptable) = {reading.p}."
    if gate.kind == "choice":
        return f"{label}: judged '{reading.choice}' ({reading.legend}); P = {reading.p}."
    return f"{label}: P(true) = {reading.p}, below {reading.threshold}."


def _missing_text(gate: Gate, reading: Reading) -> str:
    item = gate.item or {}
    family = gate.family
    where = f"{item.get('note')}:{item.get('line')}" if item.get("note") else "the notes"
    if family == "arch_rule":
        return f"architecture rule {item.get('id')} ({where}) is too thin to judge against the What: say what it forbids or requires and which components it covers"
    if family == "placement":
        return "architecture has no clear place for this kind of logic: add a #jevgate/layer heading whose responsibility covers it"
    if family == "parallel_mechanism":
        return "architecture does not describe its mechanisms (extension points, adapters, pipelines, hooks) well enough to compare with the What: describe them in the architecture note"
    if family == "boundary_crossing":
        return "architecture does not state its boundaries: add #jevgate/rule bullets for the allowed dependency directions"
    if family == "overlap":
        return f"component {item.get('name') or item.get('id')} ({where}) lacks provides/interface detail: fill `provides` and `interface` so overlap with the What can be judged"
    if family == "decision":
        return f"decision {item.get('title') or item.get('id')} ({where}) lacks a clear decision or alternatives: fill ## Decision and ## Alternatives"
    if family == "simpler_alternative":
        return "components and decisions do not describe existing mechanisms well enough to judge whether a simpler route exists: add provides/interface to component notes"
    if family == "data_ownership":
        return "data notes do not say which component owns the data the What touches or the migration path: add ownership and migration policy"
    if family == "interface_compat":
        return "interface notes do not list the interface the What changes or its compatibility rule: add it"
    if family == "constraint":
        return f"constraint {item.get('id')} ({where}) is too vague to judge against the What: state the measurable rule"
    return f"{_area_of(gate)} notes lack the detail needed to answer {gate.id}"


def _gather_entry(gate: Gate, reading: Reading) -> dict:
    item = gate.item or {}
    return {
        "area": _area_of(gate),
        "note": item.get("note"),
        "item": item.get("id"),
        "missing": _missing_text(gate, reading),
        "for": [gate.id],
    }


def _finding(gate: Gate, reading: Reading, severity: str) -> Finding:
    return Finding(
        id=f"jev:{gate.id}",
        source="jev",
        severity=severity,
        gate=gate.id,
        p=reading.p,
        threshold=reading.threshold,
        borderline=reading.borderline,
        level=reading.level,
        location=_location(gate),
        cites=_cites(gate),
        message=_message(gate, reading),
        hint=gate.hint,
    )


# ----------------------------------------------------------------- composed findings


def _prob(reading: Reading | None, key: str) -> float | None:
    if reading is None or not reading.probabilities:
        return None
    value = reading.probabilities.get(key)
    return float(value) if value is not None else None


def _reuse(gates: dict[str, Gate], readings: dict[str, Reading], cfg: Config) -> tuple[list[dict], list[Finding]]:
    overlap_at = float(cfg.thresholds.get("reuse_missed", REUSE_OVERLAP_AT))
    uses_at = float(cfg.thresholds.get("reuse_uses", REUSE_USES_AT))
    rows: list[dict] = []
    findings: list[Finding] = []
    for gate_id, gate in gates.items():
        if gate.family != "overlap":
            continue
        cid = gate.item_id
        overlap, uses = readings.get(gate_id), readings.get(f"uses:{cid}")
        overlap_p, uses_p = _prob(overlap, "provides_it"), (uses.p if uses is not None else None)
        if overlap is None or overlap.status == "unknown" or uses is None or uses.status == "unknown":
            verdict = "unknown"
        elif overlap.status == "unclear":
            verdict = "unclear"
        elif overlap_p is not None and overlap_p >= overlap_at and uses_p is not None and uses_p <= uses_at:
            verdict = "missed"
        elif overlap_p is not None and overlap_p >= overlap_at:
            verdict = "uses"
        else:
            verdict = "unrelated"
        rows.append({"component": cid, "overlap_p": overlap_p, "uses_p": uses_p, "verdict": verdict})
        if verdict == "missed":
            name = (gate.item or {}).get("name") or cid
            findings.append(
                Finding(
                    id=f"jev:reuse_missed:{cid}",
                    source="jev",
                    severity="fail",
                    gate=f"reuse_missed:{cid}",
                    p=overlap_p,
                    threshold=overlap_at,
                    borderline=abs(overlap_p - overlap_at) < 0.05,
                    location={"component": cid},
                    cites=_cites(gate),
                    message=f"Component {name} already provides what the What builds (P = {overlap_p}) and the draft does not use or mention it (P(uses) = {uses_p}).",
                    hint=gate.hint,
                )
            )
    return rows, findings


def _layer_of(component: Any, layers: list[dict]) -> str | None:
    names = [str(component.meta.get("name", component.text)).lower()]
    names += [str(a).lower() for a in component.meta.get("aliases") or []]
    for layer in layers:
        text = f"{layer.get('responsibility', '')} {layer['name']}".lower()
        if any(n and n in text for n in names):
            return layer["id"]
    path = str(component.meta.get("path") or "").lower()
    for layer in layers:
        word = str(layer["name"]).split()[0].lower()
        if word and f"/{word}/" in "/" + path:
            return layer["id"]
    return None


def _stated_place(ticket: dict, pack: ContextPack, layers: list[dict]) -> tuple[str | None, str | None]:
    """(layer id, evidence) for the place the What names, or (None, None)."""
    what = str(ticket.get("what") or "").lower()
    tokens = stated_locations(ticket)
    for layer in layers:
        name = str(layer["name"]).lower()
        if name and name in what:
            return layer["id"], layer["name"]
    for component in pack.items("component"):
        name = str(component.meta.get("name", component.text))
        path = str(component.meta.get("path") or "")
        aliases = [str(a).lower() for a in component.meta.get("aliases") or []]
        hit = None
        for token in tokens:
            low = token.lower()
            if path and (low == path.lower() or low in path.lower() or path.lower() in low):
                hit = token
            elif low == name.lower() or low in aliases:
                hit = token
            if hit:
                break
        if hit is None and name.lower() in what:
            hit = name
        if hit is not None:
            layer = _layer_of(component, layers)
            if layer:
                return layer, hit
    for token in tokens:
        for layer in layers:
            word = str(layer["name"]).split()[0].lower()
            if word and f"/{word}/" in "/" + token.lower() + "/":
                return layer["id"], token
    return None, None


def _placement(ticket: dict, pack: ContextPack | None, gates: dict[str, Gate], readings: dict[str, Reading], cfg: Config) -> tuple[dict, Finding | None]:
    gate, reading = gates.get("placement"), readings.get("placement")
    if gate is None or reading is None or pack is None:
        return {}, None
    layers = [
        {"id": i.id, "name": i.meta.get("name", i.text), "responsibility": i.meta.get("responsibility", ""), "note": i.note, "line": i.line}
        for i in pack.items("layer")
    ]
    names = {l["id"]: l["name"] for l in layers}
    at = float(cfg.thresholds.get("placement_mismatch", PLACEMENT_AT))
    expected_id, expected_p = None, None
    if reading.status in ("pass", "fail") and reading.choice and reading.choice in names:
        p = _prob(reading, reading.choice)
        if p is not None and p >= at:
            expected_id, expected_p = reading.choice, p
    stated_id, evidence = _stated_place(ticket, pack, layers)
    summary = {
        "expected": names.get(expected_id, "new_component" if reading.choice == "new_component" else None),
        "stated": names.get(stated_id),
        "evidence": evidence,
        "p": expected_p,
    }
    if expected_id and stated_id and expected_id != stated_id:
        layer = next(l for l in layers if l["id"] == expected_id)
        finding = Finding(
            id="jev:placement_mismatch",
            source="jev",
            severity="fail",
            gate="placement_mismatch",
            p=expected_p,
            threshold=at,
            borderline=abs(expected_p - at) < 0.05,
            location={"section": "what"},
            cites={"note": layer["note"], "line": layer["line"]},
            message=f"The architecture puts this logic in the {names[expected_id]} (P = {expected_p}); the What places it in the {names[stated_id]} ({evidence}).",
            hint=f"Move the logic to the {names[expected_id]} as the architecture note assigns it, or say in the What why it belongs in the {names[stated_id]}.",
        )
        return summary, finding
    return summary, None


def _architecture(gates: dict[str, Gate], readings: dict[str, Reading], placement: dict) -> dict | None:
    rule_gates = [g for g in gates.values() if g.family == "arch_rule"]
    mechanism = readings.get("parallel_mechanism")
    if not rule_gates and mechanism is None and not placement:
        return None
    buckets: dict[str, list[str]] = {"complies": [], "violates": [], "na": [], "unclear": []}
    cited: list[dict] = []
    for gate in rule_gates:
        reading = readings.get(gate.id)
        rid = gate.item_id or gate.id
        if reading is None or reading.status == "unknown":
            continue
        key = {"pass": "complies", "fail": "violates", "na": "na", "unclear": "unclear"}[reading.status]
        buckets[key].append(rid)
        if reading.status in ("fail", "unclear"):
            cited.append({"id": rid, **(_cites(gate) or {})})
    summary: dict = {"rules": {**buckets, "cited": cited}, "placement": placement}
    if mechanism is not None and mechanism.status != "unknown":
        summary["mechanism"] = {"choice": mechanism.choice, "status": mechanism.status, "p": mechanism.p}
    boundary = readings.get("boundary_crossing")
    if boundary is not None and boundary.status != "unknown":
        summary["boundary"] = {"choice": boundary.choice, "status": boundary.status, "p": boundary.p}
    return summary


def _split(readings: dict[str, Reading], cfg: Config) -> dict | None:
    split = readings.get("split")
    if split is not None and split.status == "fail":
        return {"signal": "split", "choice": split.choice, "legend": split.legend, "p": split.p}
    effort = readings.get("effort")
    at = float(cfg.thresholds.get("effort_split", EFFORT_SPLIT_AT))
    p3 = _prob(effort, EFFORT_TOP_LEVEL)
    if p3 is not None and p3 >= at:
        return {"signal": "effort", "level": effort.level, "legend": effort.legend, "p": p3}
    return None


def _about_codebase(claim: str, pack: ContextPack | None) -> bool:
    if stated_locations({"what": claim}):
        return True
    low = claim.lower()
    if pack is not None:
        for component in pack.items("component"):
            names = [str(component.meta.get("name", component.text))] + [str(a) for a in component.meta.get("aliases") or []]
            if any(n and n.lower() in low for n in names):
                return True
        for layer in pack.items("layer"):
            if str(layer.meta.get("name", layer.text)).lower() in low:
                return True
    return bool(re.search(r"\b(module|function|class|file|table|endpoint|service|handler|store|import)\b", low))


def _ungrounded(gates: dict[str, Gate], readings: dict[str, Reading], pack: ContextPack | None, cfg: Config) -> list[Finding]:
    at = float(cfg.thresholds.get("ungrounded", UNGROUNDED_AT))
    findings = []
    for gate in gates.values():
        if gate.family != "claim":
            continue
        reading = readings.get(gate.id)
        p = _prob(reading, "not_covered")
        if reading is None or reading.status == "fail" or p is None or p < at:
            continue
        claim = str((gate.item or {}).get("text") or "")
        about_code = _about_codebase(claim, pack)
        findings.append(
            Finding(
                id=f"jev:ungrounded:{gate.item_id}",
                source="jev",
                severity="fail" if about_code else "warn",
                gate=f"ungrounded:{gate.item_id}",
                p=p,
                threshold=at,
                borderline=abs(p - at) < 0.05,
                location={"context": (gate.item or {}).get("index")},
                message=f"No note supports the Context claim «{claim}» (P(not covered) = {p})"
                + ("; it is a claim about the codebase, so it must be backed by a note." if about_code else "."),
                hint="Add the fact to the relevant note (or cite the note that states it), or drop the claim.",
            )
        )
    return findings


# ----------------------------------------------------------------- route


def _route(split: dict | None, fails: list[Finding], gathers: list[dict], asks: list[dict], unknown: bool) -> str:
    if split:
        return "split"
    if gathers and not fails:
        return "gather"
    if fails and asks:
        return "ask"
    if fails:
        return "revise"
    if unknown:
        return "uncertain"
    return "ready"


def _summary(route: str, fails: list[Finding], gathers: list[dict], asks: list[dict], unknown: int, split: dict | None) -> str:
    bits = [f"Route: {route}"]
    if split:
        bits.append(f"split signal from {split['signal']} ({split.get('choice') or split.get('legend')}, p {split['p']})")
    if fails:
        bits.append(f"{len(fails)} failing gate(s): " + ", ".join(f.gate or f.id for f in sort_findings(fails)[:6]))
    if gathers:
        bits.append(f"{len(gathers)} gather item(s) in " + ", ".join(sorted({g['area'] for g in gathers})))
    if asks:
        bits.append(f"{len(asks)} question(s) for a human: " + ", ".join(a["id"] for a in asks))
    if unknown:
        bits.append(f"{unknown} gate(s) without a usable answer")
    return "; ".join(bits) + "."


def check(ticket: dict, pack: ContextPack | None, client: TypeSafeClient, cfg: Config, run: Run) -> Report:
    """Judge ``ticket`` against ``pack`` and write the round's report to ``run``."""
    ticket = normalise_ticket(ticket)
    evidence: dict = {"context": {}}
    findings: list[Finding] = _schema_findings(ticket)
    text_findings, numbers = _text_findings(ticket, cfg)
    findings += text_findings
    rules = {"stats": numbers, "findings": [f.id for f in findings]}
    schema_fails = [f for f in findings if f.severity == "fail"]

    if schema_fails:
        report = Report(
            gate="ticket", outcome="revise", exit_code=TICKET_EXITS["revise"], model=client.model,
            summary=_summary("revise", schema_fails, [], [], 0, None) + " No question was asked.",
            findings=sort_findings(findings), rules=rules, evidence=evidence, usage=client.usage.to_dict(),
        )
        report.delta = delta(run.previous(), report)
        run.write(report)
        return report

    gathers, area_findings = _missing_areas(pack)
    findings += area_findings
    prior_ids = prior_answer_ids(ticket)
    gates_list = all_gates(ticket, pack, cfg, prior_ids, run.ledger())
    gates = {g.id: g for g in gates_list}
    query = ticket_query_text(ticket)

    jobs: list[Job] = []
    tags: dict[str, list[Gate]] = {}
    for needs, group in group_by_needs(gates_list).items():
        if not _applicable(needs, pack):
            continue
        tag = _slice_tag(needs)
        tags[tag] = group
        jobs.append(Job(tag=tag, state=_state(needs, ticket, pack, query, cfg, evidence), questions=to_questions(group)))
    evidence["slices"] = [job.tag for job in jobs]
    answers = client.ask_many(jobs) if jobs else {}

    readings: dict[str, Reading] = {}
    for tag, group in tags.items():
        got = answers.get(tag) or {}
        for gate in group:
            readings[gate.id] = read(gate, got.get(gate.id))

    for gate_id, reading in readings.items():
        gate = gates[gate_id]
        if reading.status == "fail" and gate.family not in NO_FINDING_FAMILIES:
            findings.append(_finding(gate, reading, "fail"))
        elif reading.status == "unclear":
            findings.append(_finding(gate, reading, "unclear"))
            gathers.append(_gather_entry(gate, reading))

    reuse_rows, reuse_findings = _reuse(gates, readings, cfg)
    findings += reuse_findings
    placement, placement_finding = _placement(ticket, pack, gates, readings, cfg)
    if placement_finding is not None:
        findings.append(placement_finding)
    findings += _ungrounded(gates, readings, pack, cfg)
    architecture = _architecture(gates, readings, placement)
    split = _split(readings, cfg)
    if split:
        source = readings["split"] if split["signal"] == "split" else readings["effort"]
        findings.append(
            Finding(
                id="jev:split", source="jev", severity="fail", gate="split", p=split["p"],
                threshold=source.threshold if split["signal"] == "split" else float(cfg.thresholds.get("effort_split", EFFORT_SPLIT_AT)),
                level=split.get("level"), location={"section": "what"},
                message=f"Split signal: {split.get('legend') or split.get('choice')} (p {split['p']}).",
                hint=gates["split"].hint if split["signal"] == "split" else gates["effort"].hint,
            )
        )

    fails = [f for f in findings if f.severity == "fail"]
    unknown = sum(1 for r in readings.values() if r.status == "unknown")
    asks = select_asks(readings.values(), cfg.max_asks)
    route = _route(split, fails, gathers, asks, unknown > 0)
    optional = {"asks": [], "gather": []}
    if route == "ready":
        optional = {"asks": asks, "gather": gathers}
        asks, gathers = [], []
    if route == "ask":
        run.add_ledger(a["id"] for a in asks)

    report = Report(
        gate="ticket",
        outcome=route,
        exit_code=TICKET_EXITS[route],
        model=client.model,
        summary=_summary(route, fails, gathers, asks, unknown, split),
        findings=sort_findings(findings),
        readings={gid: r.to_dict() for gid, r in readings.items()},
        asks=asks,
        gather=gathers,
        optional=optional,
        split=split,
        architecture=architecture,
        reuse=reuse_rows,
        rules=rules,
        evidence=evidence,
        usage=client.usage.to_dict(),
    )
    report.delta = delta(run.previous(), report)
    run.write(report)
    return report


__all__ = ["check", "REQUIRED_AREAS", "AREA_UNLOCKS"]
