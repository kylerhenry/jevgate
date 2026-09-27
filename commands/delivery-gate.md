---
description: Run a finished change through jevgate's delivery gate against its ticket, with a fresh test log
argument-hint: "[--ticket path/to/ticket.md | --from-linear DIY-17] [--base main] [--test-log tests.log]"
allowed-tools: Bash(jevgate:*), Bash(python3:*), Read, Edit, Write
---

Follow the procedure in the `delivery-gate` skill (`skills/delivery-gate/SKILL.md`) with `$ARGUMENTS`: run the project's test suite fresh and verbose so the log names every test (`pytest -v`, `go test -v`, `jest --verbose`; `cargo test` already does) and save its complete output to a log file, then run `jevgate delivery check` with the ticket (`--ticket` or `--from-linear`), `--base` (default `main`) and `--test-log`, and act on the verdict as the skill says: `revise` means fix the cited code and re-run tests and gate, `gather` means re-run with `--files` for the named paths, `unproven` means add or run the missing tests, `accept` means report the run dir and log hash. Reuse one `--run-dir`, stop after three rounds and hand the last report to the user, and never edit a test or a log to satisfy the gate.
