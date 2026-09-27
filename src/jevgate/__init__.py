"""jevgate: Jev-backed validation gates for tickets and deliveries.

Two pipelines share one core:

* ``jevgate ticket`` judges a ticket draft against standardized criteria and
  routes the driving agent to gather context, ask, revise, split, or proceed.
* ``jevgate delivery`` judges a code change and its test logs against the
  ticket's acceptance criteria and says where changes are still needed.

Policy lives in code; the model (TypeSafe's Jev) answers narrow typed
questions over scoped state and never decides the route on its own.
"""

__version__ = "0.1.0"

# Bump whenever any question wording, option set or level text changes so the
# request cache never serves an answer to a question that no longer exists.
CATALOG_VERSION = "2026-09-27.1"
