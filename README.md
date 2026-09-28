# jevgate

Two validation gates for LLM-driven development, backed by [TypeSafe](https://typesafe.ai)'s Jev model. The **ticket gate** judges a ticket draft before it is created: scope, ambiguity, architecture fit, reuse, grounding. The **delivery gate** judges a code change and its test log against the ticket's acceptance criteria. Both are driven by whichever agent writes the ticket or the code: the agent drafts, jevgate judges, code decides what the agent does next. Python 3.10+, standard library only.

## How it works

Jev is a model that answers one narrow, typed question about a piece of state. Ask "Does this file's change comply with rule R03?" with the rule and the patch as state, and you get a probability for each option: `complies`, `violates`, `not_applicable`, `unclear`. Jev does not write text. jevgate asks many such questions in parallel, reads the probabilities against thresholds, and routes.

**The two gates.**

- `jevgate ticket check draft.md` reads a ticket in the `# Title / ## Why / ## What / ## Acceptance / ## Context` shape, loads your context pack (see "Setting up your context"), asks Jev about scope, design ambiguity, readability, every architecture rule, every nearby component, every recorded decision, every constraint and every Context bullet, then picks a route.
- `jevgate delivery check --ticket draft.md --base main --test-log tests.log` reads the same ticket back, diffs `base...head`, asks Jev per file about correctness, duplication, over-engineering, edge cases, rules and conventions, then asks about the whole change: is each acceptance bullet met, is it proven by a passing test, is there scope creep. Then it picks a verdict.

**Judge-then-generate.** One run is one round. The report tells the driving agent what to do next; the agent does it and runs again in the same run directory. Findings keep stable ids across rounds, so the report's `delta` shows what was resolved, what is new and what is unchanged. Three rounds is the recommended limit before a human looks.

**Routes, verdicts and exit codes.**

| exit | ticket route | delivery verdict | meaning |
|---|---|---|---|
| 0 | `ready` | `accept` | done; create the issue / merge |
| 1 | `revise`, `split` | `revise` | findings cite what to change; `split` means one draft per piece |
| 2 | `ask` | `unproven` | ticket: a human must answer the listed questions; delivery: tests do not prove the acceptance bullets |
| 3 | `uncertain` | `uncertain` | API failure or `--no-ai`; nothing was judged |
| 4 | error | error | bad input, budget exceeded, missing key |
| 5 | `gather` | `gather` | more context is required; the finding names what is missing |

**What Jev decides and what code decides.** Jev answers each narrow question with probabilities. Code owns everything else: the schema checks (missing sections), the text statistics (sentence length, hedges), the thresholds, the per-item aggregation (a rule violated in one file is a violation), the routing policy and the exit code. There is no holistic "is this ticket good?" question anywhere. Every finding names the question, the probability, the threshold and, where a note was involved, the note and line.

**Cost.** Jev is billed at $0.042 per 1M input tokens; output is free. A request is capped at 30k tokens of state. A full round is a few dozen requests, so it costs cents. Unchanged requests are cached by hash and cost nothing on the next round.

## Install

You need the `jevgate` command (Python 3.10+, no dependencies) plus, optionally, the agent-facing skills.

**pip, from git**

```
python3 -m pip install --user git+https://github.com/kylerhenry/jevgate
jevgate --version
```

**Claude Code plugin** (adds the `/ticket-gate` and `/delivery-gate` commands and the three skills; the Python package is still installed with pip as above)

```
claude plugin marketplace add kylerhenry/jevgate
claude plugin install jevgate@jevgate
```

**Codex and other agents** (portable skills; each skill ends with the pip line so the agent can install the command itself)

```
npx skills add kylerhenry/jevgate --skill ticket-gate
npx skills add kylerhenry/jevgate --skill delivery-gate
npx skills add kylerhenry/jevgate --skill context-pack
```

Codex also reads `.codex-plugin/plugin.json` from a clone.

**Plain clone**

```
git clone https://github.com/kylerhenry/jevgate
cd jevgate
PYTHONPATH=src python3 -m jevgate --help
```

## Credentials

- `TYPESAFE_API_KEY` in the environment, or the key on one line in `~/.config/typesafe/api-key`. Required for any run without `--no-ai`.
- `LINEAR_API_KEY`, only for `--from-linear` and `jevgate linear`.

Keys are read once, never printed, and never leave the machine except in requests to `api.typesafe.ai` and `api.linear.app`. Your draft, your notes, your diff and your test output go to TypeSafe as request state; nothing else is sent anywhere.

## Setting up your context

### 1. Why the gates need it

Every architecture, reuse, decision, constraint and grounding question is answered only from the notes you give jevgate. Jev is not shown your repository; it is shown your draft or diff plus the notes selected for that question. A thin pack does not produce a pass: a rule Jev cannot decide reads as `unclear`, and enough `unclear` yields the `gather` route, which tells you which note to enrich. The pack is the ceiling on what the gates can see.

A **pack** is a folder of markdown notes. Each note belongs to one **area**, and each question receives only the areas it needs:

| area | content | unlocks |
|---|---|---|
| `architecture` | layers/boundaries, where each kind of logic lives, extension points; a `## Rules` bullet list of stated invariants ("UI never touches the DB directly") | per-rule compliance, placement, parallel-mechanism, boundary questions |
| `components` | one note per existing module/service/helper: `path`, `provides`, `interface`, `aliases` (frontmatter) or a bullet inventory in one note | per-component reuse and duplication questions, placement options |
| `decisions` | ADR-style notes: decision, rationale, `alternatives` rejected, `status` | per-decision conflict and rejected-alternative questions |
| `data` | data model, stores, schemas, ownership, migration policy | data-ownership question |
| `interfaces` | external/public contracts: APIs, CLI surfaces, message topics, compatibility rules | interface-compatibility question |
| `constraints` | non-functional rules: performance, security, environment, forbidden dependencies (bullets) | per-constraint compliance |
| `conventions` | code style, naming, error handling, logging, testing; optional `applies_to: ["*.py"]` frontmatter | per-convention checks in the delivery gate |
| `glossary` | domain terms | included with language-ambiguity questions |

### 2. Supported pattern: tag your Obsidian vault

**This is the officially supported and tested way to feed the gates: an Obsidian vault (or any subfolder of one) whose notes carry tags in the `jevgate` namespace.** Point `context.dir` at it and nothing else is needed. Tags go in frontmatter `tags:` or inline as `#tag` anywhere in the body; Obsidian nested tags are used throughout.

| tag | on | meaning |
|---|---|---|
| `#jevgate/<area>` (`architecture`, `components`, `decisions`, `data`, `interfaces`, `constraints`, `conventions`, `glossary`) | a note | the whole note belongs to that area |
| `#jevgate/component` | a note | one component: `aliases`, `path`, `provides`, `interface` from frontmatter, else from `## Provides` / `## Interface` sections; implies area `components` |
| `#jevgate/decision` | a note | one ADR: `status`, `## Decision`, `## Alternatives` (bullets = rejected alternatives); implies area `decisions` |
| `#jevgate/rule` | a bullet or paragraph | one architecture rule (the line's text, minus the tag); implies area `architecture` for that item |
| `#jevgate/constraint`, `#jevgate/convention` | a bullet or paragraph | one constraint / convention item; conventions may add `#jevgate/applies/py` (file-type scope, extension after `applies/`) |
| `#jevgate/layer` | a bullet or heading in an architecture note | a placement option (name = the line's text) |
| `#jevgate/project/<name>` | a note | scopes the note to one project; loaded only when `context.project` matches; untagged notes are global (a vault usually spans many projects) |
| `#jevgate/ignore` | a note or a line | never sent |

**Resolution order.** Each note's area and each item's kind are resolved in this order; the first hit wins, and `jevgate context show --why` prints which rule assigned it:

1. tags (the table above), the supported pattern;
2. frontmatter `area:` / `kind:`;
3. `jevgate.json` `context.areas` glob mapping (`{"architecture": ["arch/*.md"], "components": ["components/**/*.md"]}`) and heading-based items (`## Rules`, `## Constraints`, `## Conventions`, `## Alternatives` bullets) for vaults that do not use tags;
4. parent folder name matching an area;
5. `general` (sent only with the clarifying bank's note index).

The normal shape is an area tag on the note plus item tags inside it: one `architecture.md` tagged `#jevgate/architecture` whose rule bullets carry `#jevgate/rule` and whose layer headings carry `#jevgate/layer`; a `components/` folder of notes each tagged `#jevgate/component`; ADRs tagged `#jevgate/decision`. `[[Wikilinks]]` between them are followed up to `context.follow_links` hops (default 1); aliases are honoured and unresolved links are listed as warnings, never errors.

**Worked example.** These three notes are `fixtures/vault/` in this repository, shown verbatim. They describe a small bookkeeping service.

`architecture.md`: the note is tagged with its area, headings carry `#jevgate/layer`, rule bullets carry `#jevgate/rule`, and one draft bullet is kept out with `#jevgate/ignore`.

````markdown
---
tags: [jevgate/architecture]
---

# Ledger architecture

Ledger is a small bookkeeping web service: a JSON API and a CLI in front of a Postgres store, with server-rendered invoices. Requests flow inward through the layers below and nothing in a lower layer imports an upper one. Component notes: [[Cache helper]], [[Postgres store]], [[API router]]; decisions: [[0001-postgres-over-sqlite]]. The [[Billing engine]] is planned and has no note yet.

## Layers

### Interface layer #jevgate/layer

HTTP handlers ([[API router]]), the [[Ledger CLI]] and the [[Invoice renderer]]: parse input, call a service, format output. No business rules.

### Service layer #jevgate/layer

Use cases such as posting entries, closing periods and importing statements ([[CSV importer]]). Owns validation and the double-entry invariant.

### Storage layer #jevgate/layer

The [[Postgres store]] and the [[Cache helper]] in front of it. The only layer that contains SQL.

### Integration layer #jevgate/layer

Outbound side effects: the [[Event bus]] for audit events, email and bank feeds.

## Rules

- Interface code never calls the store directly; it goes through a service. #jevgate/rule
- Every write to the ledger goes through `post_entry`, which enforces double entry. #jevgate/rule
- The storage layer is the only layer that contains SQL. #jevgate/rule
- Cross-cutting side effects (audit, email) are published on the event bus, not called inline. #jevgate/rule
- Reports read from the reporting views, never from the entries table. #jevgate/rule
- Money is handled as integer minor units; a float never reaches the storage layer. #jevgate/rule
- (draft) Reporting may bypass the cache while the view migration is unfinished. #jevgate/ignore

Rules are checked one by one by the ticket gate and the delivery gate.
````

`components/cache-helper.md`: one component. `path`, `provides`, `interface` and `aliases` come from frontmatter; the tag is inline in the body, which works the same as a frontmatter tag. `provides` is the sentence the reuse and duplication questions are built from, so write it as what the component does, not what it is.

````markdown
---
aliases: [cache, TTL cache]
path: src/ledger/storage/cache.py
provides: A TTL cache in front of Postgres store reads, keyed by query name and parameters, invalidated on every write through post_entry.
interface: "get(key) -> value | None; set(key, value, ttl_s=60); invalidate(prefix)"
---

# Cache helper

#jevgate/component

Wraps [[Postgres store]] reads. Chosen over a Redis sidecar in [[0001-postgres-over-sqlite]]. Services call it through the store facade; nothing above the storage layer imports it.
````

`decisions/0003-event-bus-for-audit.md`: one ADR. `## Decision` is the decision text, and each `## Alternatives` bullet is a rejected alternative the gate checks the draft against.

````markdown
---
tags: [jevgate/decision]
status: proposed
---

# ADR 0003: Event bus for audit and email

## Context

Every service was calling the mailer and writing audit rows inline, so each needed both dependencies.

## Decision

Audit records and outbound email are produced by subscribers on the in-process [[Event bus]], drained after commit; services never call the mailer or the audit table directly.

## Alternatives

- Inline calls from each service: rejected because every service would need the mailer and audit dependencies.
- A message broker such as RabbitMQ: rejected as infrastructure the deployment does not have.

## Consequences

Side effects become observable in one place; a subscriber failure is logged, not raised.
````

**Project scoping.** A vault usually holds several projects. Tag a note `#jevgate/project/<name>` and it is loaded only when `context.project` (or `--project`) is `<name>`. Untagged notes are global and always load. With no project set, every note loads, whatever its project tag (right for a single-project vault). Set a project and `context show --why` names the notes it skipped for other projects:

```
interfaces.md  area=interfaces via tag:#jevgate/interfaces  ...  skipped: project:ledger (loading project other)
```

**Keeping things out.** `#jevgate/ignore` on a note skips the whole note; on a bullet or paragraph it skips that line. Use it for scratch notes and half-written rules, as `architecture.md` above does.

**Pointing jevgate at the vault.** Put a `jevgate.json` in the repository root (copy `jevgate.json.example`) and set `context.dir` to the vault or to a subfolder of it:

```json
{
  "context": {
    "dir": "~/vault",
    "project": "ledger",
    "follow_links": 1
  }
}
```

`--context-dir DIR` on any command overrides `context.dir`. That is the whole setup: a tagged vault and one path.

### 3. Check what Jev will see

Before spending a request, print exactly what a run would send:

```
jevgate context show --why
jevgate context show --area components --draft draft.md
```

Flags: `--context-dir DIR` (default: `context.dir`, else `docs/context`), `--context-json F` (a prebuilt pack instead of a folder), `--config F`, `--project NAME`, `--area X` (one area only), `--draft F` (use a ticket draft as the selection query, so you see the items lexical selection would keep), `--why`, `--json`.

Only a missing `architecture` area (or no pack at all) forces the `gather` route; a missing `components`, `decisions`, `data`, `interfaces` or `constraints` area only adds a `rule:missing_context_area:<area>` warning and an entry under `## Optional`, so a project with no ADRs can still reach `ready`.

The first block lists, per area, the notes and items with their token counts and whether each is `[sent]` or `[items only]` (the note's items are sent, its prose is not). The `--why` block that follows says how each note and item was resolved:

```
architecture.md  area=architecture via tag:#jevgate/architecture  kind=note via area:architecture  tags=jevgate/architecture,jevgate/layer,jevgate/rule,jevgate/ignore
  L01    layer line 11 via tag:#jevgate/layer
  R01    rule line 29 via tag:#jevgate/rule
components/cache-helper.md  area=components via tag:#jevgate/component  kind=component via tag:#jevgate/component
adr/0001-postgres-over-sqlite.md  area=decisions via folder:adr  kind=decision via area:decisions
arch/architecture.md  area=general via default:general  kind=note via area:general
```

Read it as: `via tag:` is the supported path; `via folder:`, `via glob:` or `via frontmatter:` means a fallback fired; `via default:general` means nothing matched and the note is only in the clarifying bank's index. A note that should be an area note and shows `default:general` is missing its tag. Warnings at the end list unresolved wikilinks and unparsable frontmatter.

A `gather` finding maps back to this output. `rule:missing_context_area:<area>` means no note resolved to that area: add `#jevgate/<area>` to a note or fix `context.dir`/`context.project`. "architecture has no Rules section" means no `#jevgate/rule` items: tag the bullets. "component X lacks `provides`" means the component note needs a `provides` (and `interface`) line. A reuse question that came back `unclear` names the component whose note is too thin to decide.

### 4. Starting from nothing

```
jevgate context init --repo . --out ~/vault/projects/<name> --project <name>
```

This scans the repository and writes tagged skeletons for an agent or a person to fill: an `architecture.md` with example `#jevgate/layer` headings and `#jevgate/rule` bullets, one `components/<name>.md` per package or module tagged `#jevgate/component` with `path` filled in and `provides` seeded from the first docstring, `decisions/0001-template.md`, `constraints.md`, `conventions.md`, `data.md`, `interfaces.md` and `glossary.md`, with `constraints` and `conventions` seeded from an existing `CLAUDE.md`, `CONTRIBUTING.md` or `AGENTS.md` if found. Every note carries `#jevgate/project/<name>` when `--project` is given. `--out` may be relative (under `--repo`) or absolute, so it can sit inside a vault; `--force` overwrites existing notes. Replace every `(replace me)` marker, then run `jevgate context show --context-dir <out> --why`.

### 5. Other sources

The loader does not care where notes came from. All of these are fully supported by the code and covered by the tests:

- frontmatter `area: architecture` or `kind: component` / `kind: decision` on a note, for vaults that prefer keys to tags;
- a `jevgate.json` `context.areas` glob mapping plus heading-based items: `## Rules`, `## Constraints`, `## Conventions` and `## Alternatives` bullets become items without any tag;
- folder names: a note in a folder named after an area (`components/`, `decisions/`, or `adr/` for decisions) takes that area;
- `--context-json pack.json`, a prebuilt pack `{"<area>": [{"title", "text", "meta": {...}, "items": [...]}]}` for programmatic producers (an agent assembling context from code, a wiki export, a script); `fixtures/context-pack.json` is an example;
- `ContextPack.from_dict()` in Python, the library entry point the CLI itself uses.

Tags are the supported and tested way to get information in; anything that yields notes with an area works. `docs/context-setup.md` is a longer version of this section with an FAQ.

## Ticket gate

```
jevgate ticket init [--out ticket.md] [--json] [--force]
jevgate ticket check (<draft.md|draft.json> | --from-linear DIY-17) [--context-dir DIR | --context-json F] [--project NAME] [--run-dir DIR] [--json] [--no-ai] [--no-cache] [--all-items] [--threshold GATE=P]... [--config F]
jevgate ticket render <draft> [--out F]
```

**Input.** `jevgate ticket init` writes the template. A draft is markdown in this shape (JSON with the same fields also works: `title, why, what, acceptance, context_bullets, prior_answers`):

```markdown
# Title

## Why

Who is affected and what goes wrong or is missing today.

## What

The change: components touched, interfaces, data flow.

### Sub-sections are fine

## Acceptance

- One observable outcome per bullet; a test or a demonstration can pass or fail it.

## Context

- One fact the ticket relies on, per bullet. Each is checked against the notes.

### Prior answers

- (B03) **How will it be verified: which test, command or observation?** pytest tests/test_x.py, added in this ticket.
```

**The loop the driving agent follows.**

1. Build or refresh the context pack; `jevgate context show --why` should list the areas the ticket touches.
2. `jevgate ticket init --out ticket.md`, then write the draft. Put every fact you relied on in `## Context` as its own bullet.
3. `jevgate ticket check ticket.md --run-dir .jevgate/runs/<name>`. Read the markdown report on stdout (also written to the run dir).
4. Act on the route, then run the same command again. Reusing `--run-dir` increments the round and the report's `## Delta` shows what changed.
5. Stop after three rounds and hand the last report to a human.

**Routes.**

- `gather` (exit 5): the notes do not contain enough to answer some questions. Each `## Gather` line names the area, the note if any, and what is missing. Enrich the note, add the tag, or read the code and add the fact to a note. Nothing is revised blind.
- `ask` (exit 2): questions from the clarifying bank that the draft, the prior answers and the notes do not settle, and whose answer would change the What or Acceptance. Put the human's answers under `### Prior answers` with the bank id and run again. At most `max_asks` (4) per round; asked ids are remembered in the run's ledger and not asked twice.
- `revise` (exit 1): failed findings, worst first, each citing a section, a rule, a component or a Context bullet. `unclear` items are listed alongside so one revision addresses both.
- `split` (exit 1): Jev reads the draft as two or more independently deliverable pieces, or as needing a design decision first, or as too large to review as one change. Write one draft per piece.
- `ready` (exit 0): `jevgate ticket render ticket.md` produces Linear-ready markdown; create the issue with your own Linear access or `jevgate linear create`. Fired asks and unclear readings are listed under `## Optional`; they never block a passing ticket.
- `uncertain` (exit 3): the API failed or `--no-ai` was set; only the rule checks ran.

**Clarifying bank.** The standard questions the `ask` route draws from:

| id | question |
|---|---|
| B01 | What existing behaviour must not change? |
| B02 | Where in the codebase should this live? |
| B03 | How will it be verified: which test, command or observation? |
| B04 | What happens when the operation fails or the input is malformed? |
| B05 | Which existing data, configs, callers or clients must keep working? |
| B06 | What volume, size or rate must it handle? |
| B07 | Who triggers or consumes this, and from where? |
| B08 | How does it reach production: deploy, migration, flag, ordering? |
| B09 | What must exist or be decided before work starts? |
| B10 | What data or permissions does it touch, and who may use it? |
| B11 | What signal shows it is done and can be closed? |
| B12 | What related work is deliberately excluded? |

**What is judged.** Rule checks run first and cost nothing: missing title, Why, What or Acceptance is a fail; long sentences, long words and hedge words are warnings. Then, grouped by the context each needs:

- *Intrinsic* (draft only, plus glossary): is the scope boundary stated; is a design decision that changes the work left open in the What (`design_ambiguous`, fires at 0.60); does a term, pronoun or phrase have two readings that lead to different work (`language_ambiguous`, fires at 0.60); readability; does the Why name a problem rather than restate the solution; does the title match the body; should it be split; how much effort it is; is each acceptance bullet testable (`ac_testable_<i>`, passes at 0.50).
- *Architecture*: one question per `#jevgate/rule` (`arch_rule_<R>`); where the logic should live according to the layers, compared in code with where the draft says it goes (`placement_mismatch`); whether it reuses, extends or duplicates a named mechanism (`parallel_mechanism`); boundary crossing when no rules exist.
- *Reuse*: per selected component, does it already provide what the What builds, and does the draft use it. Code combines the two into `reuse_missed_<c>`.
- *Decisions*: per selected ADR, does the What follow, contradict or explicitly revisit it (`decision_<d>`); is there a simpler alternative visible in the notes that the draft does not address.
- *Data, interfaces, constraints*: data ownership, public-interface compatibility, one question per `#jevgate/constraint`.
- *Grounding*: each `## Context` bullet is a claim; the notes support it, contradict it or do not cover it (`claim_<i>`).
- *Clarifying bank*: which of B01 to B12 are unanswered and would change the work.

Pass-type gates default to 0.85 (`ac_testable` passes at 0.50, set by the fixture sweep); fire-type gates fire at 0.60. Ambiguity is judged as a problem-finding question (`design_ambiguous`, `language_ambiguous`: is something left open or double-readable?) that fails at 0.60 by default, calibrated on the fixtures; `--threshold language_ambiguous=0.40` tightens it. A choice reads as `unclear` when the unclear options reach `unclear_at` (0.50, also calibrated on the fixtures). Component, decision, rule and convention items are pre-selected in code by lexical overlap with the draft, `max_items` (16) per area; `--all-items` sends everything. `--no-cache` re-asks even when the request hash is cached.

## Delivery gate

```
jevgate delivery check (--ticket <draft.md|json> | --from-linear DIY-17) [--repo .] (--base main [--head HEAD] | --diff-file F) [--test-log F]... [--files PATH[:START-END]]... [--context-dir DIR | --context-json F] [--project NAME] [--no-tests-ok] [--run-dir DIR] [--json] [--no-ai] [--no-cache] [--all-items] [--threshold GATE=P]... [--config F]
```

**Inputs.**

- The ticket: `--ticket draft.md` (the same file the ticket gate passed) or `--from-linear DIY-17`. Its `## Acceptance` bullets are what the change is judged against; that is the link between the two gates.
- The change: `--base main` (diff `base...HEAD`; `--head` omitted means the working tree, and untracked files are a warning) or `--diff-file change.patch`. `--repo` defaults to the current directory. Binaries, lockfiles and `ignore` globs are dropped; a file over `max_file_tokens` is not reviewed and produces `rule:unreviewed_file`. Deleted files and files outside `source_globs` (code suffixes such as `*.py`, `*.ts`, `*.go`) get no per-file questions but stay in the whole-change diff; `evidence.files` lists them with `dropped_reason` `deleted` or `not_source`. A component's `dup` question is never asked for the component's own path.
- The proof: `--test-log tests.log`, repeatable. Without it, `test_log_globs` (`test*.log`, `tests*.log`, `pytest*.log`) are tried. pytest, unittest, jest, go test and cargo output is parsed for counts, failing blocks and test names; anything else is kept by regex plus the tail. The log must name the tests (`pytest -v` or `-rA`, `go test -v`, `jest --verbose`; `cargo test` already does): `ac_proven_<i>` and `tests_exercise_change` cannot pass on a summary-only log, which produces `rule:test_log_has_no_names`. The log's sha256 and the number of test names parsed (`evidence.tests[].names_count`) are recorded in the report.
- Extra evidence on a `gather` verdict: `--files PATH` or `--files PATH:START-END` includes post-change file excerpts so Jev can decide a question that depended on code outside the diff.

**Tests must be run fresh and pasted verbatim.** The delivery gate treats the log as evidence: it asks whether a named passing test covers each acceptance bullet and whether the tests exercise the changed paths. Run the project's suite verbose, so the log names every test (`pytest -v`, `go test -v`, `jest --verbose`; `cargo test` already does), redirect its output to a file, pass that file. A summary-only log (`pytest -q` prints dots) cannot prove anything. Do not trim it, do not write it by hand. Update a test when the behaviour it checks changes, but never leave one proving less than it did to make the gate pass. `--no-tests-ok` lets a change with no test log be accepted; use it only for changes that genuinely have nothing to test, such as documentation.

**Verdicts.**

- `revise` (exit 1): a rule failed (empty diff, failing tests) or a per-file finding fired: a visible defect, duplicated component behaviour, over-engineering, a rule or convention broken, edge cases left to chance, scope creep. Each finding names the file, the rule or component or criterion, and a probability, worst first.
- `gather` (exit 5): `ac_met_<i>`, `correctness_defect` or `dup_<c>` came back `unclear`. The finding says what was undecidable and names the paths to pass with `--files`: for `ac_met_<i>` the files `touches_ac` marked as contributing (or, when none, that the outcome is produced outside the diff); for `dup_<c>` the file and component whose `provides`/`interface` do not describe the behaviour touched.
- `unproven` (exit 2): nothing failed, but not every acceptance bullet has a passing test that covers it, or the tests shown do not exercise the change, or no log was given. Add or run tests.
- `accept` (exit 0): all `ac_met_<i>` pass at 0.85 and all `ac_proven_<i>` at 0.90, `tests_exercise_change` passes, a log is present (or `--no-tests-ok`).
- `uncertain` (exit 3): API failure or `--no-ai`.

**What is judged.** Per changed file: `dup_<c>` per selected component, `over_engineered`, `correctness_defect`, `arch_rule_<R>` per applicable rule, `convention_<k>` per convention whose `applies_to` matches the file, `edge_cases`, and `touches_ac_<i>`. Large files are split at hunk boundaries and the worst chunk wins. On the whole change: `ac_met_<i>`, `ac_proven_<i>`, `scope_creep`, `tests_exercise_change`. Pass-type delivery gates default to 0.90.

## Configuration

`jevgate.json` in the repository root; `--config F` points elsewhere. Copy `jevgate.json.example`, which documents every key. Unknown keys are an error.

| key | meaning |
|---|---|
| `thresholds` | per-gate probability thresholds, keyed by family or id (see below) |
| `unclear_at` | a choice gate reads as `unclear` (route `gather`) when P(unclear options) is at least this; default 0.50, calibrated on the fixtures |
| `context.dir` | folder of markdown notes (an Obsidian vault or a subfolder); `--context-dir` overrides |
| `context.project` | load only notes tagged `#jevgate/project/<this>` plus untagged notes; `null` (the default) loads every note |
| `context.areas` | glob mapping for vaults without tags, e.g. `{"architecture": ["arch/*.md"]}` |
| `context.follow_links` | wikilink hops to follow from a selected note; 0 disables |
| `context.context_budget` | token cap per area in a request state (default 8000) |
| `context.max_items` | items kept per area after lexical selection (default 16); `null` = all |
| `ignore` | glob patterns of changed files never sent for review (lockfiles by default) |
| `source_globs` | suffix globs (like `ignore`) of the changed files that get the per-file questions; default `*.py *.js *.ts *.tsx *.jsx *.go *.rs *.java *.kt *.rb *.php *.c *.cc *.cpp *.h *.hpp *.cs *.swift *.sh *.sql`. Other files and deleted files stay in the whole-change diff and appear in `evidence.files` as `not_source` / `deleted` |
| `test_log_globs` | where `delivery check` looks for test logs when `--test-log` is not given |
| `hedges` | words and phrases counted as hedges / LLM-isms by the readability rules |
| `state_budget` | token cap for one request state (default 24000) |
| `file_budget` | token cap per file chunk (default 12000); larger files split at hunk boundaries |
| `tests_budget` | token cap for parsed test output in the whole-change request (default 6000) |
| `context_budget` | default per-area cap when `context.context_budget` is unset |
| `max_file_tokens` | files above this are not reviewed and produce `rule:unreviewed_file` (default 40000) |
| `max_items` | default per-area item cap when `context.max_items` is unset; `--all-items` lifts it |
| `max_asks` | bank questions put to the human per round (default 4) |
| `workers` | parallel requests to the TypeSafe API (default 4) |

**Thresholds.** `--threshold GATE=P` on the command line, repeatable, or the `thresholds` object in the file. A family name applies to every instance of a question (`ac_testable`, `arch_rule`, `decision`); an id pins one instance (`ac_testable:2`, `arch_rule:R03`). Defaults: ticket pass gates 0.85 (`ac_testable` 0.50), delivery pass gates 0.90 (`ac_met` 0.85), fire gates 0.60, level gates 0.70 (readability 0.60).

## Calibration

<!-- verify: ticket/delivery flags -->

```
jevgate calibrate (ticket|delivery) <fixtures-dir> [--gate ID]... [--thresholds 0.5,0.6,0.7,0.8,0.85,0.9,0.95] [--refresh] [--json]
```

The default thresholds are starting points, not truths: 0.85 and 0.90 on the gates that name them are chosen values; `ac_testable` (0.50), `ac_met` (0.85), the two ambiguity gates (fire at 0.60) and `unclear_at` (0.50) were set by the fixture calibration recorded in `docs/calibration.md`; the other fire and level defaults are guesses until measured. A default is adopted from the fixture sweeps only when the family has labelled failures and the new value still catches all of them. `jevgate calibrate` prints the evidence. `calibrate` runs every labelled fixture (`fixtures/tickets/<case>/{draft.md, expected.json}`, `fixtures/deliveries/<case>/{ticket.md, change.patch, tests/*.log, expected.json}`) and, per gate, reports how each threshold in the sweep would have scored against the labels, so you can see where a threshold should sit for your consequences. `fixtures/responses/<sha>.json` is a checked-in request cache, so calibration and the test suite run offline; `--refresh` re-asks the API and rewrites it. Add your own labelled cases in the same layout to calibrate on your project's tickets.

## Linear (optional)

Set `LINEAR_API_KEY`. The core never needs Linear; the adapter is for teams that keep tickets there.

```
jevgate linear get DIY-17 [--json]                     # print the issue as ticket markdown
jevgate linear create ticket.md --team DIY [--project NAME] [--dry-run] [--json]
jevgate ticket check --from-linear DIY-17               # judge an existing issue
jevgate delivery check --from-linear DIY-17 --base main --test-log tests.log
```

`--dry-run` prints what would be sent and needs no key. `--from-linear` supplies only the ticket; the context pack still comes from `--context-dir`/`--context-json` or `jevgate.json`. Reading Linear comments back as prior answers is not implemented yet.

## Run directories and reports

Every `check` writes a run directory, default `.jevgate/runs/<YYYYMMDD-HHMMSS>-<gate>/`, containing `round-N.json` and `round-N.md` per round and `requests.jsonl`, one line per API request: `{ts, tag, sha, cached, question_ids, usage, error}`. Stdout is the markdown report (`--json` for the JSON one); the markdown has sections `Findings`, `Gather`, `Ask`, `Optional`, `Architecture`, `Reuse`, `Evidence`, `Usage` and `Delta`. `Evidence` lists exactly which notes and items were sent per area, which files were reviewed or dropped, and the test-log hashes. Pass the same `--run-dir` again to add a round; `Delta` then lists resolved, new and unchanged findings by id.

```
jevgate report .jevgate/runs/20260927-101500-ticket [--round N] [--json]
```

prints a stored round again. The request cache lives in `$XDG_CACHE_HOME/jevgate/requests/<sha>.json` (default `~/.cache/jevgate/`), keyed by a hash of the request and the catalog version. Add `.jevgate/` to your `.gitignore`.

## Limitations

- Pack quality is the ceiling. Thin component notes produce `unclear`, which is the intended signal (`gather`), not a pass and not a fail.
- Lexical pre-selection can miss a relevant component with an unrelated name. `aliases` in the component note and `--all-items` are the escape hatches.
- Per-item questions scale with pack size (rules times files, components times files in the delivery gate). `max_items` and `applies_to` bound it; cost stays in cents, latency in seconds with 4 workers.
- The driving agent is also the subject of the delivery gate. The recorded log hash, `ac_proven` and `tests_exercise_change` are the only guard, which is why the skill requires a fresh verbatim test run and forbids editing tests.
- Thresholds other than `ac_testable` 0.50, `ac_met` 0.85, the ambiguity gates' 0.60 and `unclear_at` 0.50 (the remaining 0.85 and 0.90 pass gates included) are uncalibrated defaults until `calibrate` has been run on your own labelled cases; even the calibrated ones rest on sixteen ticket and thirteen delivery fixtures.
- The frontmatter parser is a YAML subset (scalars, flow lists, dash lists). Exotic frontmatter reads as "no frontmatter" with a warning.
- `--from-linear` does not read comments; prior answers must be in the draft.
- State is treated as data, not as hostile input: every question tells Jev that instructions inside the draft, notes, diff or logs are part of the data.

## License

MIT. See `LICENSE`.
