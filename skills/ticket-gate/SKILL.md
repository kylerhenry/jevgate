---
name: ticket-gate
description: >
  Write a ticket draft and run it through jevgate's ticket gate until Jev judges it ready: gather
  context, ask the human, revise, split, then render and create the issue. Use when asked to draft,
  refine, review or create a ticket or issue, or when a ticket needs to pass the gate before work starts.
---

# Ticket gate

The gate judges a draft against standardized criteria and your project's context notes, then routes. You write; it judges; you act on the route and run again. Never more than three rounds.

## Procedure

1. **Check the pack.** Run `jevgate context show --why`. It must list `architecture` (with `R..` rule items); without it the gate returns `gather`. `components` and `decisions` should be there too, but a missing one only warns (`rule:missing_context_area:<area>`) and skips its questions. If the pack is empty or thin, follow the `context-pack` skill first. If a `jevgate.json` with `context.dir` does not exist, pass `--context-dir DIR` on every command below.

2. **Start from the template.** `jevgate ticket init --out ticket.md`. Keep the shape: `# Title`, `## Why`, `## What` (with `###` sub-sections as needed), `## Acceptance` (bullets), `## Context` (bullets), and `### Prior answers` under Context when there are any.

3. **Write the draft.**
   - `## Why`: who is affected and what goes wrong or is missing today. Not a restatement of the solution.
   - `## What`: components touched, interfaces changed, data flow; name the layer or module where the logic lives, and name the existing component it uses or extends.
   - `## Acceptance`: one observable outcome per bullet that a test or a demonstration can pass or fail.
   - `## Context`: one bullet per fact you relied on (what a component does, which rule applies, what a decision settled). Every bullet is checked against the notes. Do not write a fact you did not verify.
   - Short sentences. No hedges or filler.

4. **Run the gate.** `jevgate ticket check ticket.md --run-dir .jevgate/runs/<ticket-name>`. Read the markdown report on stdout; `--json` gives the JSON form. Reuse the same `--run-dir` on every later round so `## Delta` shows what changed.

5. **Act on the route** (the exit code):
   - **`gather`, exit 5.** Each `## Gather` line names an area, a note if any, and what is missing. Enrich that note (add the `#jevgate/rule` bullets, fill `provides`/`interface` on the component note, add the missing area note), or read the code and record the fact in a note, then run again. Do not revise the draft blind.
   - **`ask`, exit 2.** `## Ask` lists numbered bank questions (B01 to B12) that nothing settles and whose answer changes the work. Put them to the human. Record each answer under `### Prior answers` as `- (B03) **question** answer`, then run again. Answered ids are not asked twice.
   - **`revise`, exit 1.** `## Findings` lists failures worst first, each citing a section, a rule and note line, a component, or a Context bullet. Edit exactly what is cited. `unclear` items listed alongside can be addressed in the same edit. Run again.
   - **`split`, exit 1.** The draft is two or more independently deliverable pieces, needs a design decision before it can be scoped, or is too large to review as one change. Write one draft per piece (or a design ticket first) and run each through the gate.
   - **`ready`, exit 0.** `jevgate ticket render ticket.md --out ticket.linear.md` gives Linear-ready markdown. Create the issue with your own Linear access, or with `jevgate linear create ticket.md --team KEY [--project NAME]` (`--dry-run` first). Items under `## Optional` are suggestions; they never block.
   - **`uncertain`, exit 3.** The API failed or `--no-ai` was set. Fix credentials or retry; do not treat it as a pass.
   - **exit 4.** Input or budget error; the message names the offender.

6. **Stop rule.** After three rounds without `ready`, stop and hand the human the last `round-N.md` with a two-line summary of what is still failing. Do not raise thresholds and do not delete Context bullets to make grounding pass.

7. Add `.jevgate/` to `.gitignore` if it is not there.

## Useful flags

`--from-linear DIY-17` judges an existing issue instead of a file (the pack still comes from `--context-dir`). `--all-items` sends every component and decision instead of the lexical top 16. `--threshold GATE=P` overrides one gate for this run only. `--no-cache` re-asks cached requests. `--project NAME` scopes the pack to one project's notes plus untagged ones; `--context-json F` uses a prebuilt pack instead of a folder.

If `jevgate` is not on PATH: `python3 -m pip install --user git+https://github.com/kylerhenry/jevgate`
