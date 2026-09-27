"""The ticket gate: judges a ticket draft against the catalog and routes the
driving agent to gather context, ask a human, revise, split or proceed.

Modules: :mod:`schema` (load, validate, normalise a draft), :mod:`rubric`
(the question catalog as gates), :mod:`clarify` (the clarifying-question bank),
:mod:`gate` (the policy that turns readings into a report) and :mod:`cli`.
"""

from .gate import check

__all__ = ["check"]
