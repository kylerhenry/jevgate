"""Question primitives, gates and readings.

A ``Noul``, ``Choice`` or ``Score`` is the typed question sent to Jev. A ``Gate``
wraps one question with the policy that turns its answer into a ``Reading``:
which side of the answer is gated, the threshold, and which options mean
"unclear" or "not applicable". Policy stays here, in code; the model only
supplies probabilities.

Gate ids use ``:`` between a family and an item id (``arch_rule:R03``,
``dup:cache-helper``, ``ac_met:2``) so thresholds can be overridden per family.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any, Iterable, Mapping, Union

KINDS = ("pass", "fire", "level", "choice", "info")
STATUSES = ("pass", "fail", "unclear", "na", "unknown")
BORDERLINE_MARGIN = 0.05
ITEM_SEPARATOR = ":"


@dataclass(frozen=True)
class Noul:
    """A yes/no question answered with a probability that the statement holds."""

    instructions: str
    true: str | dict | None = None
    false: str | dict | None = None

    def to_api(self) -> dict:
        question: dict = {"type": "noul", "instructions": self.instructions}
        criteria = {k: v for k, v in (("true", self.true), ("false", self.false)) if v is not None}
        if criteria:
            question["criteria"] = criteria
        return question


@dataclass(frozen=True)
class Choice:
    """One option out of several; the answer carries a probability per option."""

    instructions: str
    options: Mapping[str, Union[str, dict, None]]

    def to_api(self) -> dict:
        return {"type": "choice", "instructions": self.instructions, "criteria": dict(self.options)}


@dataclass(frozen=True)
class Score:
    """An ordinal level 0..n-1 with a description per level."""

    instructions: str
    levels: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "levels", tuple(self.levels))

    def to_api(self) -> dict:
        return {"type": "score", "instructions": self.instructions, "criteria": list(self.levels)}


Question = Union[Noul, Choice, Score]


@dataclass(frozen=True)
class Gate:
    """A question plus the policy that reads its answer.

    kind:
      pass    Noul; passes when P ≥ threshold
      fire    Noul; fails when P ≥ threshold
      level   Score; passes when ΣP(acceptable levels) ≥ threshold
      choice  Choice; exactly one of pass_options / fail_options is the gated side
      info    any; never fails, the reading only reports the probability
    needs: context areas whose notes must be in the state for this question.
    item:   for per-item gates, the item the question was built from (for reports).
    """

    id: str
    question: Question
    kind: str
    needs: frozenset[str] = frozenset()
    threshold: float = 0.85
    acceptable: frozenset[int] = frozenset()
    pass_options: tuple[str, ...] = ()
    fail_options: tuple[str, ...] = ()
    unclear_options: tuple[str, ...] = ("unclear",)
    na_options: tuple[str, ...] = ("not_applicable",)
    unclear_at: float = 0.50
    item: dict | None = None
    hint: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"gate {self.id!r}: unknown kind {self.kind!r}")
        object.__setattr__(self, "needs", frozenset(self.needs))
        object.__setattr__(self, "acceptable", frozenset(int(x) for x in self.acceptable))
        for name in ("pass_options", "fail_options", "unclear_options", "na_options"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.kind == "choice" and bool(self.pass_options) == bool(self.fail_options):
            raise ValueError(f"gate {self.id!r}: a choice gate sets exactly one of pass_options/fail_options")
        if self.kind == "level" and not self.acceptable:
            raise ValueError(f"gate {self.id!r}: a level gate needs acceptable levels")

    @property
    def family(self) -> str:
        """The id before the first ``:`` (``arch_rule`` for ``arch_rule:R03``)."""
        return self.id.split(ITEM_SEPARATOR, 1)[0]

    @property
    def item_id(self) -> str | None:
        """The id after the first ``:``, or None for a gate without an item."""
        parts = self.id.split(ITEM_SEPARATOR, 1)
        return parts[1] if len(parts) == 2 else None


@dataclass
class Reading:
    """What one gate says about one answer.

    ``p`` is the gated probability, the number compared with ``threshold``
    (``None`` when there was no usable answer). ``p_pass`` / ``p_fail`` are the
    two sides of that comparison so reports can show either.
    """

    gate_id: str
    kind: str
    status: str
    threshold: float
    p: float | None = None
    p_pass: float | None = None
    p_fail: float | None = None
    p_unclear: float | None = None
    borderline: bool = False
    level: int | None = None
    legend: str | None = None
    probabilities: dict | None = None
    confidence: float | None = None
    choice: str | None = None
    item: dict | None = None

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def _round(value: float) -> float:
    return round(float(value), 4)


def _legend(criteria: Any, key: Any) -> str | None:
    if isinstance(criteria, dict):
        text = criteria.get(key)
    elif isinstance(criteria, (list, tuple)) and isinstance(key, int) and 0 <= key < len(criteria):
        text = criteria[key]
    else:
        return None
    if isinstance(text, dict):
        text = text.get("description") or text.get("text")
    return text if isinstance(text, str) else None


def _sum(probabilities: dict, keys: Iterable[str]) -> float:
    return float(sum(probabilities.get(k, 0.0) for k in keys))


def read(gate: Gate, answer: dict | None) -> Reading:
    """Turn a validated answer (or None) into a Reading according to the gate's kind."""
    reading = Reading(gate_id=gate.id, kind=gate.kind, status="unknown", threshold=gate.threshold, item=gate.item)
    if not isinstance(answer, dict):
        return reading
    confidence = answer.get("confidence")
    if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
        reading.confidence = _round(confidence)

    if gate.kind in ("pass", "fire", "info"):
        p = answer.get("noul")
        if not isinstance(p, (int, float)) or isinstance(p, bool):
            return reading
        p = _round(p)
        reading.p = p
        if gate.kind == "fire":
            reading.p_pass, reading.p_fail = _round(1 - p), p
            reading.status = "fail" if p >= gate.threshold else "pass"
        else:
            reading.p_pass, reading.p_fail = p, _round(1 - p)
            reading.status = "pass" if gate.kind == "info" or p >= gate.threshold else "fail"
        reading.p_unclear = 0.0
        if gate.kind != "info":
            reading.borderline = abs(p - gate.threshold) < BORDERLINE_MARGIN
        return reading

    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict):
        return reading
    reading.probabilities = {k: _round(v) for k, v in probabilities.items()}

    if gate.kind == "level":
        score = answer.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            # The API returns the probability-weighted level as a float; the
            # nearest level gives the reader a label to go with the number.
            levels = gate.question.to_api().get("criteria") or []
            reading.level = max(0, min(len(levels) - 1, round(score))) if levels else round(score)
            reading.legend = _legend(levels, reading.level)
        p = _round(_sum(probabilities, (str(i) for i in gate.acceptable)))
        reading.p, reading.p_pass, reading.p_fail, reading.p_unclear = p, p, _round(1 - p), 0.0
        reading.status = "pass" if p >= gate.threshold else "fail"
        reading.borderline = abs(p - gate.threshold) < BORDERLINE_MARGIN
        return reading

    # choice
    choice = answer.get("choice")
    if isinstance(choice, str):
        reading.choice = choice
        reading.legend = _legend(gate.question.to_api().get("criteria"), choice)
    p_na = _sum(probabilities, gate.na_options)
    p_unclear = _round(_sum(probabilities, gate.unclear_options))
    reading.p_unclear = p_unclear
    if gate.pass_options:
        p = _round(_sum(probabilities, gate.pass_options))
        reading.p, reading.p_pass, reading.p_fail = p, p, _round(1 - p)
        decided = "pass" if p >= gate.threshold else "fail"
    else:
        p = _round(_sum(probabilities, gate.fail_options))
        reading.p, reading.p_pass, reading.p_fail = p, _round(1 - p), p
        decided = "fail" if p >= gate.threshold else "pass"
    if p_na >= 0.5:
        reading.status = "na"
    elif p_unclear >= gate.unclear_at:
        reading.status = "unclear"
    else:
        reading.status = decided
        reading.borderline = abs(p - gate.threshold) < BORDERLINE_MARGIN
    return reading


def group_by_needs(gates: Iterable[Gate]) -> dict[frozenset, list[Gate]]:
    """Group gates by the context areas they need: one request per distinct slice."""
    groups: dict[frozenset, list[Gate]] = {}
    for gate in gates:
        groups.setdefault(frozenset(gate.needs), []).append(gate)
    return groups


def with_overrides(gates: Iterable[Gate], overrides: Mapping[str, float]) -> list[Gate]:
    """Apply threshold overrides by exact id, else by family (the id before ``:``)."""
    result = []
    for gate in gates:
        threshold = overrides.get(gate.id, overrides.get(gate.family))
        result.append(gate if threshold is None else replace(gate, threshold=float(threshold)))
    return result


def to_questions(gates: Iterable[Gate]) -> dict[str, dict]:
    """The API question dict for a list of gates, keyed by gate id."""
    return {gate.id: gate.question.to_api() for gate in gates}


__all__ = [
    "Noul", "Choice", "Score", "Question", "Gate", "Reading", "read",
    "group_by_needs", "with_overrides", "to_questions", "KINDS", "STATUSES", "ITEM_SEPARATOR",
]
