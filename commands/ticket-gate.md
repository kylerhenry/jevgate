---
description: Draft or refine a ticket and run it through jevgate's ticket gate until it is ready
argument-hint: "[path/to/ticket.md | DIY-17 | a one-line description of the change]"
allowed-tools: Bash(jevgate:*), Bash(python3:*), Read, Edit, Write
---

Follow the procedure in the `ticket-gate` skill (`skills/ticket-gate/SKILL.md`) for `$ARGUMENTS`: if it is an existing draft, run `jevgate ticket check` on it; if it is a Linear identifier, use `--from-linear`; if it is a description, start from `jevgate ticket init` and write the draft with a `## Context` bullet for every fact relied on. Check the pack with `jevgate context show --why` first, act on each route (`gather`, `ask`, `revise`, `split`, `ready`) exactly as the skill says, reuse one `--run-dir` across rounds, stop after three rounds and hand the last report to the user, and finish a `ready` draft with `jevgate ticket render`.
