# jevgate, for agents

`jevgate` is a command-line tool with two validation gates backed by TypeSafe's Jev model. The **ticket gate** judges a ticket draft (scope, ambiguity, architecture fit, reuse, grounding against your notes) and routes you to gather context, ask a human, revise, split, or mark it ready. The **delivery gate** judges a code change plus a fresh test log against the ticket's acceptance bullets and returns accept, revise, gather, unproven or uncertain. Jev answers narrow typed questions with probabilities; code applies thresholds and decides the route. You, the driving agent, do the writing and the fixing. Details: `README.md`.

## Install

```
python3 -m pip install --user git+https://github.com/kylerhenry/jevgate
```

Needs `TYPESAFE_API_KEY` (or `~/.config/typesafe/api-key`). Optional: `LINEAR_API_KEY`.

## Skills

Read the one that matches the task before starting it.

- `skills/context-pack/SKILL.md`: build or repair the context pack (a folder of tagged markdown notes, ideally an Obsidian vault with `#jevgate/...` tags). Use first on any repository that has no pack, and whenever a gate returns `gather`.
- `skills/ticket-gate/SKILL.md`: write a ticket and run it through `jevgate ticket check` until it is `ready`. Use when asked to draft, refine or create a ticket or issue.
- `skills/delivery-gate/SKILL.md`: run the tests, run `jevgate delivery check` against the ticket, act on the verdict. Use when a change is finished and before it is declared done, opened as a PR or merged.

In Claude Code the plugin also exposes `/ticket-gate` and `/delivery-gate`, which run the same procedures.

## The loop, in ten lines

1. Make sure a context pack exists: `jevgate context show --why` lists notes per area. If it is empty, follow `context-pack` first.
2. Draft the ticket from `jevgate ticket init --out ticket.md`; put every fact you relied on in `## Context` as its own bullet.
3. `jevgate ticket check ticket.md --run-dir .jevgate/runs/<name>`; read the report on stdout.
4. Exit 5 (`gather`): enrich the named note or add the named tag, run again. Exit 2 (`ask`): get the human's answers, add them under `### Prior answers` with the bank id, run again.
5. Exit 1 (`revise` or `split`): edit what the findings cite, or write one draft per piece; run again.
6. Exit 0 (`ready`): `jevgate ticket render ticket.md` and create the issue.
7. Do the work. Then run the project's test suite fresh and save its full output: `pytest -q > tests.log 2>&1` or the project's equivalent.
8. `jevgate delivery check --ticket ticket.md --base main --test-log tests.log --run-dir .jevgate/runs/<name>-delivery`.
9. Exit 1: fix what the findings cite, worst first. Exit 5: run again with `--files <paths named>`. Exit 2 (`unproven`): add or run the missing tests. Exit 0: done.
10. Stop after three rounds of either gate and hand the last report to a human. Add `.jevgate/` to `.gitignore`.

## Rules

- Never edit, weaken, skip or delete a test to satisfy the delivery gate. If a test is wrong, say so in the report you hand to the human, with the reason, and leave the gate unpassed.
- Never write a test log by hand or trim one. The gate records the log's hash.
- Never fabricate a Context bullet. If you did not verify a fact in the notes or the code, it does not go in `## Context`.
- Do not raise a threshold to get a pass. Thresholds live in `jevgate.json` and belong to the human.
