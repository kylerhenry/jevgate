# Setting up your context

This is the long form of the README's "Setting up your context" section, with an FAQ at the end. It describes the one thing jevgate needs from you beyond a draft or a diff: a folder of markdown notes that tells Jev how your system is put together.

## 1. Why the gates need it

Every architecture, reuse, decision, constraint and grounding question is answered only from these notes. Jev is never shown your repository. For each question it is shown your draft (or one file of your diff) plus the notes selected for that question, and nothing else, because irrelevant state is a distractor. So:

- a rule Jev is not told about is never checked;
- a component with no note is never offered as something to reuse, and a change that duplicates it is never flagged;
- a decision with no note cannot be contradicted;
- a `## Context` bullet in a draft that no note speaks to is reported as `not_covered`.

A thin pack does not produce a pass. A rule or component Jev cannot decide on reads as `unclear`, and enough `unclear` yields the `gather` route (exit 5), whose findings name the note to enrich. That is by design: revising a draft without the knowledge is guessing.

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

Within an area, some notes yield **items**: one rule, one layer, one component, one decision, one constraint, one convention. Items are what the per-item questions iterate over, and findings cite the item's note and line. A missing area skips the questions that need it and adds a `rule:missing_context_area:<area>` warning (no API call).

## 2. Supported pattern: tag your Obsidian vault

**This is the officially supported and tested way to feed the gates: an Obsidian vault (or any subfolder of one) whose notes carry tags in the `jevgate` namespace.** Point `context.dir` at it and nothing else is needed. Tags go in frontmatter `tags:` or inline as `#tag` anywhere in the body (outside code spans); Obsidian nested tags are used throughout, so they show up in Obsidian's tag pane as a `jevgate` tree.

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

### Resolution order

Each note's area and each item's kind are resolved in this order. The first hit wins, and `jevgate context show --why` prints which rule assigned it:

1. tags (the table above), the supported pattern;
2. frontmatter `area:` / `kind:`;
3. `jevgate.json` `context.areas` glob mapping (`{"architecture": ["arch/*.md"], "components": ["components/**/*.md"]}`) and heading-based items (`## Rules`, `## Constraints`, `## Conventions`, `## Alternatives` bullets) for vaults that do not use tags;
4. parent folder name matching an area (`adr/` also counts as `decisions`);
5. `general` (sent only with the clarifying bank's note index).

The normal shape is an area tag on the note plus item tags inside it: one `architecture.md` tagged `#jevgate/architecture` whose rule bullets carry `#jevgate/rule` and whose layer headings carry `#jevgate/layer`; a `components/` folder of notes each tagged `#jevgate/component`; ADRs tagged `#jevgate/decision`. `[[Wikilinks]]` between them are followed up to `context.follow_links` hops (default 1); aliases are honoured and unresolved links are listed as warnings, never errors.

### Worked example

These three notes are `fixtures/vault/` in the repository, shown verbatim. They describe a small bookkeeping service called Ledger. The rest of the fixture vault (`data.md`, `interfaces.md`, `constraints.md`, `conventions.md`, `glossary.md`, six more components, two more ADRs) follows the same shapes.

`architecture.md`. The note is tagged with its area, headings carry `#jevgate/layer`, rule bullets carry `#jevgate/rule`, and one draft bullet is kept out with `#jevgate/ignore`. Note the wikilink to a component that has no note yet; `context show` reports it as unresolved and carries on.

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

What the gates get from it: four placement options (`L01` to `L04`) for the "where should this logic live" question, six rules (`R01` to `R06`) each asked as its own compliance question against a draft and against every changed file, and the overview paragraph as prose for the mechanism and boundary questions.

`components/cache-helper.md`. One component. `path`, `provides`, `interface` and `aliases` come from frontmatter; the tag is inline in the body, which works the same as a frontmatter tag. `provides` is the sentence the reuse and duplication questions are built from, so write it as what the component does, not what it is.

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

What the gates get from it: a ticket that proposes "add a memoising layer in front of the store" is asked whether this component already provides that (`overlap_cache-helper`) and whether the draft mentions it (`uses_cache-helper`); a delivery whose diff adds a dict-with-expiry is asked whether it reimplements this component (`dup_cache-helper`). The `aliases` make the lexical pre-selection find the note when the draft says "cache".

`decisions/0003-event-bus-for-audit.md`. One ADR. `## Decision` is the decision text, and each `## Alternatives` bullet is a rejected alternative the gate checks the draft against.

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

What the gates get from it: a draft that says "call the mailer from the close-period service" is asked whether it contradicts this decision, and a draft that proposes RabbitMQ is asked whether it re-proposes a rejected alternative without saying the decision is being revisited. A draft that says "this revisits ADR 0003 because ..." reads as `revisits_explicitly`, which is fine.

### Project scoping

A vault usually holds several projects. Tag a note `#jevgate/project/<name>` and it is loaded only when `context.project` (or `--project`) is `<name>`. Untagged notes are global and always load. With no project set, project-tagged notes are skipped and `context show --why` says so:

```
interfaces.md  area=interfaces via tag:#jevgate/interfaces  ...  skipped: project:ledger (loading project none)
```

The fixture vault has `other-project.md`, tagged `#jevgate/architecture` and `#jevgate/project/other`; with `--project other` it contributes rule `R07` and with any other project it is skipped.

### Keeping things out

`#jevgate/ignore` on a note skips the whole note; on a bullet or paragraph it skips that line. Use it for scratch notes, half-written rules, and anything private that happens to share the folder.

### Pointing jevgate at the vault

Put a `jevgate.json` in the repository root (copy `jevgate.json.example`) and set `context.dir` to the vault or to a subfolder of it:

```json
{
  "context": {
    "dir": "~/vault",
    "project": "ledger",
    "follow_links": 1,
    "context_budget": 8000,
    "max_items": 16
  }
}
```

`--context-dir DIR` on any command overrides `context.dir`; `--project NAME` on `context show` overrides `context.project`. That is the whole setup: a tagged vault and one path.

## 3. Check what Jev will see

Before spending a request, print exactly what a run would send:

```
jevgate context show --why
jevgate context show --area components --draft draft.md
jevgate context show --context-dir ~/vault --project ledger --json
```

Flags: `--context-dir DIR` (default: `context.dir`, else `docs/context`), `--context-json F` (a prebuilt pack instead of a folder), `--config F`, `--project NAME`, `--area X`, `--draft F` (use a ticket draft as the selection query, so you see the items lexical selection would keep and the token count of each slice), `--why`, `--json`.

The first block lists, per area, the notes and items with token counts. `[sent]` means the note's prose goes into the state; `[items only]` means only its items do (component, decision and rule notes are usually items only, which is what keeps requests small). The `--why` block that follows says how each note and item was resolved:

```
architecture.md  area=architecture via tag:#jevgate/architecture  kind=note via area:architecture  tags=jevgate/architecture,jevgate/layer,jevgate/rule,jevgate/ignore
  L01    layer line 11 via tag:#jevgate/layer
  R01    rule line 29 via tag:#jevgate/rule
components/cache-helper.md  area=components via tag:#jevgate/component  kind=component via tag:#jevgate/component
adr/0001-postgres-over-sqlite.md  area=decisions via folder:adr  kind=decision via area:decisions
arch/architecture.md  area=general via default:general  kind=note via area:general
```

Read it as: `via tag:` is the supported path; `via folder:`, `via glob:` or `via frontmatter:` means a fallback fired; `via default:general` means nothing matched and the note is only in the clarifying bank's index. A note that should be an area note and shows `default:general` is missing its tag. Warnings at the end list unresolved wikilinks and unparsable frontmatter.

## 4. Starting from nothing

```
jevgate context init --repo . --out ~/vault/projects/<name> --project <name>
```

This scans the repository and writes tagged skeletons for an agent or a person to fill: an `architecture.md` with example `#jevgate/layer` headings and `#jevgate/rule` bullets; one `components/<name>.md` per package or module tagged `#jevgate/component` with `path` filled in and `provides` seeded from the first docstring; `decisions/0001-template.md`; `constraints.md` and `conventions.md`, seeded from an existing `CLAUDE.md`, `CONTRIBUTING.md` or `AGENTS.md` if found; `data.md`, `interfaces.md` and `glossary.md`. Every note carries `#jevgate/project/<name>` when `--project` is given. `--out` may be relative (under `--repo`) or absolute, so it can sit inside a vault; `--force` overwrites existing notes.

Then replace every `(replace me)` marker, delete the component notes for modules nobody would reuse, write the real rules, and run `jevgate context show --context-dir <out> --why`. The `context-pack` skill (`skills/context-pack/SKILL.md`) is the agent-facing version of this procedure.

## 5. Other sources

The loader does not care where notes came from. All of these are fully supported by the code and covered by the tests; `fixtures/vault-untagged/` is a whole vault built this way:

- frontmatter `area: architecture` or `kind: component` / `kind: decision` on a note, for vaults that prefer keys to tags;
- a `jevgate.json` `context.areas` glob mapping plus heading-based items: `## Rules`, `## Constraints`, `## Conventions` and `## Alternatives` bullets become items without any tag;
- folder names: a note in a folder named after an area (`components/`, `decisions/`, or `adr/` for decisions) takes that area;
- `--context-json pack.json`, a prebuilt pack `{"<area>": [{"title", "text", "meta": {...}, "items": [...]}]}` for programmatic producers (an agent assembling context from code, a wiki export, a script); `fixtures/context-pack.json` is an example;
- `ContextPack.from_dict()` in Python, the library entry point the CLI itself uses.

Tags are the supported and tested way to get information in; anything that yields notes with an area works.

## FAQ

**My vault has many projects. Do I need one folder per project?**
No. Tag each project-specific note `#jevgate/project/<name>` and set `context.project` in that project's `jevgate.json`. Notes without a project tag (a shared glossary, company-wide constraints, conventions) load for every project. If you prefer folders, point `context.dir` at the project's subfolder; then only the notes in it load, and shared notes would need to be inside it or linked from it (wikilinks are followed `follow_links` hops, but only to notes under `context.dir`).

**What happens to notes without any tag?**
They go through the resolution order: frontmatter `area:`/`kind:`, then `context.areas` globs, then the parent folder name. If none matches they land in `general`, which is not sent to any judgment question; only the note's title and first line appear in the index the clarifying bank sees. `context show --why` shows them as `via default:general`. A vault full of unrelated notes is therefore harmless, and only what you tag is judged.

**My vault is huge. Will it all be sent?**
No. Per area there is a token cap (`context.context_budget`, default 8000), and item lists (components, decisions, rules, conventions, constraints) are pre-filtered in code by lexical overlap between the item's title, aliases, path and `provides` and the draft or patch, keeping `max_items` (default 16) per area. Prose notes are truncated to the cap. `--all-items` sends every item; `--draft F` on `context show` previews the selection for a specific draft. If a relevant component is missed because its name shares no words with the draft, add `aliases` to its note. The report's `## Evidence` section lists exactly what was sent, so nothing is skipped silently.

**Do wikilinks matter?**
Yes, twice. A `[[link]]` from a selected note pulls the linked note in (up to `follow_links` hops, default 1), so an architecture note that links its component notes brings them along even when lexical selection would not. And `[[Cache helper]]` resolves by title or alias, so findings can cite the right note. An unresolved link is a warning in `context show` and in the report, never an error.

**A `gather` finding says something is missing. Which tag do I add?**

| finding says | do this |
|---|---|
| `rule:missing_context_area:architecture` (or any area) | no note resolved to that area: add `#jevgate/<area>` to a note, or check `context.dir` and `context.project` |
| "architecture has no Rules section" | tag the invariant bullets `#jevgate/rule` (or write them) |
| "component X lacks `provides`" / reuse `unclear` for X | fill `provides` and `interface` in X's `#jevgate/component` note; add `aliases` if the draft calls it something else |
| decision `unclear` | write the `## Alternatives` bullets in the ADR |
| placement `unclear` or `new_component` | add `#jevgate/layer` headings with one sentence each on what lives there |
| constraint or convention `unclear` | the bullet is too vague; say what is forbidden or required and where |
| claim `not_covered` | the draft relies on a fact no note states: put the verified fact in the right area's note, or take the claim out of the draft |
| delivery `gather` naming file paths | not a notes problem: re-run `delivery check` with `--files <path>` |

**Can I keep the pack in the repository instead of a vault?**
Yes. `jevgate context init --out docs/context` writes it there and `context show` defaults to `docs/context` when `context.dir` is unset. It is the same format; a vault is recommended because the notes are documentation people already maintain, and keeping them where people read them is what keeps them true.

**Does the pack ever leave the machine?**
The selected notes and items are sent to `api.typesafe.ai` as request state, nothing else, and only the slices each question needs. `#jevgate/ignore` keeps a note or a line out entirely. Requests are cached locally by hash, so unchanged notes are not re-sent on the next round.
