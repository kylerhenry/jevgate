"""The clarifying-question bank: fixed questions a human may need to answer.

Each bank question becomes a *fire* gate: Jev says how likely it is that the
question is unanswered and that its answer would change the What or the
Acceptance. Questions already answered (``prior_answers``) or already put to
the human in this run (the ledger) are not asked again.
"""

from __future__ import annotations

import re
from typing import Iterable

from ..questions import Gate, Noul, Reading

BANK: list[tuple[str, str]] = [
    ("B01", "What existing behaviour must not change?"),
    ("B02", "Where in the codebase should this live?"),
    ("B03", "How will it be verified: which test, command or observation?"),
    ("B04", "What happens when the operation fails or the input is malformed?"),
    ("B05", "Which existing data, configs, callers or clients must keep working?"),
    ("B06", "What volume, size or rate must it handle?"),
    ("B07", "Who triggers or consumes this, and from where?"),
    ("B08", "How does it reach production: deploy, migration, flag, ordering?"),
    ("B09", "What must exist or be decided before work starts?"),
    ("B10", "What data or permissions does it touch, and who may use it?"),
    ("B11", "What signal shows it is done and can be closed?"),
    ("B12", "What related work is deliberately excluded?"),
]
BANK_QUESTIONS: dict[str, str] = dict(BANK)
BANK_FAMILY = "bank"
DEFAULT_THRESHOLD = 0.70

_TRUE = "Nothing settles it, and the answer changes what gets built."
_FALSE = "It is already settled, or the answer would not change what gets built."


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", str(text).lower()).strip()


def bank_id(question: str) -> str | None:
    """The bank id whose wording matches ``question`` (ignoring case and punctuation)."""
    wanted = _norm(question)
    for bid, text in BANK:
        if wanted and _norm(text) == wanted:
            return bid
    return None


def prior_answer_ids(ticket: dict) -> set[str]:
    """Bank ids settled by the draft's prior answers: explicit ids and matched wording."""
    ids: set[str] = set()
    for entry in ticket.get("prior_answers") or []:
        if not isinstance(entry, dict):
            continue
        explicit = str(entry.get("id") or "").strip().upper()
        if explicit in BANK_QUESTIONS:
            ids.add(explicit)
            continue
        matched = bank_id(str(entry.get("question") or ""))
        if matched:
            ids.add(matched)
    return ids


def bank_instructions(question: str, data_note: str) -> str:
    return (
        f"Clarifying question: «{question}». Is it unanswered by the draft, the prior answers and the "
        f"note index, and would different answers lead to a different What or Acceptance?" + data_note
    )


def bank_gates(prior_answer_ids: Iterable[str], ledger: Iterable[str], threshold: float = DEFAULT_THRESHOLD) -> list[Gate]:
    """One fire gate per bank question not in ``prior_answer_ids`` or ``ledger``."""
    from .rubric import DATA_NOTE  # local import: rubric imports this module

    skip = {str(x).upper() for x in prior_answer_ids} | {str(x).upper() for x in ledger}
    gates = []
    for bid, question in BANK:
        if bid in skip:
            continue
        gates.append(
            Gate(
                id=f"{BANK_FAMILY}:{bid}",
                question=Noul(bank_instructions(question, DATA_NOTE), true=_TRUE, false=_FALSE),
                kind="fire",
                needs=frozenset({"index"}),
                threshold=threshold,
                item={"id": bid, "question": question},
                hint="Put the human's answer under Prior answers as `- (%s) **%s** <answer>` and fold it into the What or Acceptance." % (bid, question),
            )
        )
    return gates


def select_asks(readings: Iterable[Reading], cap: int) -> list[dict]:
    """The fired bank readings, highest probability first, at most ``cap``."""
    fired = [r for r in readings if r.gate_id.startswith(BANK_FAMILY + ":") and r.status == "fail"]
    fired.sort(key=lambda r: (-(r.p or 0.0), r.gate_id))
    asks = []
    for reading in fired[: max(0, int(cap))]:
        bid = reading.gate_id.split(":", 1)[1]
        asks.append({"id": bid, "question": BANK_QUESTIONS.get(bid, bid), "p": reading.p})
    return asks


__all__ = ["BANK", "BANK_QUESTIONS", "bank_id", "prior_answer_ids", "bank_gates", "select_asks"]
