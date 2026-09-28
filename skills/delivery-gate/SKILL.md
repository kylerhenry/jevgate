---
name: delivery-gate
description: >
  Run a finished code change through jevgate's delivery gate: fresh test log plus diff judged against the
  ticket's acceptance bullets, then act on accept, revise, gather or unproven. Use when a change is complete
  and before it is declared done, pushed as a PR or merged.
---

# Delivery gate

The gate reads the ticket's `## Acceptance` bullets back, diffs the change, and asks Jev per file (defects, duplication, over-engineering, edge cases, rules, conventions) and per acceptance bullet (met, proven by a passing test). The test log is evidence. You may update a test when the behaviour it checks changes, but never leave one proving less than it did to satisfy the gate.

## Procedure

1. **Have the ticket.** The same `ticket.md` the ticket gate passed, or `--from-linear DIY-17`. The context pack must be the one the ticket gate used (`jevgate context show --why` to confirm; `--context-dir DIR` if `jevgate.json` does not set it).

2. **Run the tests fresh, verbose, and keep the whole output.** Run the project's own suite now, after the last edit, in a mode that prints every test's name, and redirect everything to a file: `pytest -v > tests.log 2>&1` (or `-rA`), `npx jest --verbose > tests.log 2>&1`, `go test -v ./... > tests.log 2>&1`, `cargo test > tests.log 2>&1` (cargo names tests by default). The log must name the tests: `ac_proven` and `tests_exercise_change` cannot pass on a summary-only log (`pytest -q` prints dots), and such a log produces `rule:test_log_has_no_names`; the report's `evidence.tests[].names_count` shows how many names were parsed. Do not trim it, paste it from memory, or reuse an old run. The gate records the file's sha256.

3. **Run the gate.**
   ```
   jevgate delivery check --ticket ticket.md --base main --test-log tests.log --run-dir .jevgate/runs/<ticket-name>-delivery
   ```
   `--base main` diffs `main...HEAD`; add `--head <ref>` to judge a committed range instead of the working tree; `--diff-file change.patch` judges a patch file instead. Repeat `--test-log` for several logs. Per-file questions run on source files only (`source_globs` in `jevgate.json`, code suffixes by default); deleted and non-source files stay in the whole-change diff and are listed in `## Evidence` as `deleted` / `not_source`. `--repo DIR` if you are not in the repository root. Reuse `--run-dir` every round.

4. **Act on the verdict** (the exit code):
   - **`revise`, exit 1.** `## Findings` lists failures worst first. Each names the file, the hunk or chunk, and the rule (with note and line), component, convention or acceptance criterion it concerns. Fix the code that is cited, starting at the top. Then go back to step 2: run the tests again, new log, run the gate again.
   - **`gather`, exit 5.** A question could not be decided from the diff alone: `ac_met` or `correctness_defect` depends on code outside it, or `dup` could not tell whether a file re-implements a component. The finding says what was undecidable and names the paths (for `ac_met`, the files `touches_ac` marked as contributing; when none, the outcome is produced outside the diff and you name the file that produces it). Run again adding `--files PATH` or `--files PATH:START-END` for each path (repeatable); for a `dup` gather, add the behaviour to the component note's `provides`/`interface` instead if the note is what is thin. Nothing else changes.
   - **`unproven`, exit 2.** No failures, but some acceptance bullet has no passing test that plainly covers it, or the tests shown do not exercise the changed paths, or no log was given. Add the missing test (a test that checks the bullet's observable outcome), or run the existing one that does, then step 2 again. `--no-tests-ok` is only for changes with nothing to test, such as documentation, and must be justified in your hand-off.
   - **`accept`, exit 0.** Every acceptance bullet is met and proven, the tests exercise the change, and no finding fired. Report the run dir and the log hash in your hand-off.
   - **`uncertain`, exit 3.** API failure or `--no-ai`; not a pass. Fix and retry.
   - **exit 4.** Empty diff, oversized request, missing key; the message names it.

5. **Stop rule.** Three rounds without `accept`: stop, hand the human the last `round-N.md` and say what is still failing and why.

## Never

- Change a test when the behaviour it checks has changed, and say what changed. The line is whether it still proves what it was written to prove: never leave one proving less to make a finding go away, by weakening an assertion, narrowing a case, skipping or deleting. A test that silently stops covering its case is a defect like any other, and a harder one to see. If a test itself is wrong, say so in the hand-off with the reason and leave the gate unpassed.
- Never hand-write or edit a test log.
- Never widen the ticket's acceptance bullets to match what was built. If the ticket was wrong, that is a new ticket-gate round, with the human.
- Never raise a threshold with `--threshold` to get an accept.

## Useful flags

`--all-items` sends every component and rule instead of the lexical top 16 (use when a `dup_` or `arch_rule` you expected did not appear in `## Evidence`). `--json` for the JSON report. `--no-cache` re-asks cached requests. `--project NAME` scopes the pack to one project's notes plus untagged ones; `--context-json F` uses a prebuilt pack instead of a folder. `jevgate report <run-dir> [--round N]` reprints a stored round.

If `jevgate` is not on PATH: `python3 -m pip install --user git+https://github.com/kylerhenry/jevgate`
