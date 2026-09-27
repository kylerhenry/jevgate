---
name: context-pack
description: >
  Build, verify and maintain the folder of tagged markdown notes (an Obsidian vault with #jevgate/... tags)
  that jevgate's gates read architecture, components, decisions and constraints from. Use before the first
  ticket-gate run on a repository, and whenever a gate returns the gather route or missing_context_area.
---

# Context pack

The gates answer architecture, reuse, decision, constraint and grounding questions only from these notes. A thin pack yields `gather`, not a pass. The supported pattern is an Obsidian vault (or any folder of markdown) whose notes carry tags in the `jevgate` namespace, in frontmatter `tags:` or inline as `#jevgate/...`.

## Tags

| tag | on | meaning |
|---|---|---|
| `#jevgate/<area>` (`architecture`, `components`, `decisions`, `data`, `interfaces`, `constraints`, `conventions`, `glossary`) | a note | the whole note belongs to that area |
| `#jevgate/component` | a note | one component: `aliases`, `path`, `provides`, `interface` from frontmatter, else `## Provides` / `## Interface` sections; implies area `components` |
| `#jevgate/decision` | a note | one ADR: `status`, `## Decision`, `## Alternatives` (bullets = rejected alternatives); implies area `decisions` |
| `#jevgate/rule` | a bullet or paragraph | one architecture rule; implies area `architecture` for that item |
| `#jevgate/constraint`, `#jevgate/convention` | a bullet or paragraph | one constraint / convention item; conventions may add `#jevgate/applies/py` (extension after `applies/`) |
| `#jevgate/layer` | a bullet or heading in an architecture note | a placement option (name = the line's text) |
| `#jevgate/project/<name>` | a note | loaded only when `context.project` (or `--project`) matches; untagged notes are global |
| `#jevgate/ignore` | a note or a line | never sent |

Resolution order for a note's area and an item's kind, first hit wins: tags; frontmatter `area:`/`kind:`; `jevgate.json` `context.areas` globs plus `## Rules`/`## Constraints`/`## Conventions`/`## Alternatives` headings; parent folder name; `general`.

## Build

1. **Start from skeletons if there is no pack.** `jevgate context init --repo . --out <vault>/projects/<name> --project <name>` (or `--out docs/context` to keep it in the repository). It writes `architecture.md` with example `#jevgate/layer` headings and `#jevgate/rule` bullets, `components/<module>.md` per package or module with `path` filled and `provides` seeded from the first docstring, `decisions/0001-template.md`, `constraints.md`, `conventions.md`, `data.md`, `interfaces.md`, `glossary.md`. `--force` overwrites. Replace every `(replace me)`.

2. **Architecture note** (`#jevgate/architecture`): one paragraph on how the system is put together; a `## Layers` section with one heading per layer tagged `#jevgate/layer` and one sentence on what lives there; a `## Rules` list where every bullet is one invariant tagged `#jevgate/rule` ("Interface code never calls the store directly; it goes through a service."). One rule per bullet; the gate asks one question per bullet. Wikilink the component notes.

3. **One `#jevgate/component` note per module, service or helper** that a ticket could reuse or duplicate. Frontmatter: `path` (the file or package), `provides` (one sentence saying what it does, the reuse question is built from it), `interface` (the public functions or commands), `aliases` (other names people use for it). A short body with how it is used and by whom. Skip `provides` and the reuse question comes back `unclear`.

4. **One `#jevgate/decision` note per recorded decision**: `status` in frontmatter, `## Decision` in one paragraph, `## Alternatives` with one bullet per rejected option and why. The gate checks drafts against both.

5. **Constraints and conventions**: bullets tagged `#jevgate/constraint` (performance, security, environment, forbidden dependencies) and `#jevgate/convention` (style, naming, errors, logging, testing; add `#jevgate/applies/py` to scope a convention to one file type). Seed from `CLAUDE.md`, `CONTRIBUTING.md` or `AGENTS.md` where they exist. `data`, `interfaces` and `glossary` are plain notes tagged with their area.

6. **Point jevgate at it.** In `jevgate.json`: `{"context": {"dir": "~/vault", "project": "<name>", "follow_links": 1}}`, or `--context-dir DIR` per command. Tag project-specific notes `#jevgate/project/<name>` when the vault holds several projects; leave shared notes untagged.

## Verify

`jevgate context show --context-dir DIR --why` (add `--project NAME`, `--area X`, `--draft ticket.md` to see what a real run would select). Check that every area the project has is listed with its notes and items (`R01..` rules, `L01..` layers, component ids, `adr-...` decisions), that each note says `via tag:#jevgate/...`, and that nothing important shows `area=general via default:general` (untagged) or `skipped: project:...` (wrong project). Fix the tag, run again. Warnings list unresolved wikilinks and unparsable frontmatter.

## Respond to `gather`

A `gather` route or a `rule:missing_context_area:<area>` warning names the area, the note if any, and what is missing:

- "no notes for area X": add `#jevgate/<X>` to the right note, or check `context.dir` and `context.project`.
- "architecture has no Rules section": tag the rule bullets `#jevgate/rule`, or write them.
- "component X lacks `provides`" or a reuse question `unclear` for X: fill `provides` and `interface` in X's note; add `aliases` if the draft calls it something else.
- a decision question `unclear`: write the `## Alternatives` bullets.
- a grounding claim `not_covered`: the draft relies on a fact no note states; put the fact in the right area's note (verified against the code), or take the claim out of the draft.
- a delivery-gate `gather`: that one is about code, not notes; it asks for `--files <path>`.

Then run the gate again with the same `--run-dir`. Keep the pack in the vault with the rest of the project's notes; it is documentation, and the gates are its readers.

## Other sources

Frontmatter `area:`/`kind:`, `context.areas` glob mapping with heading-based items, folder names, `--context-json pack.json` (`{"<area>": [{"title", "text", "meta", "items"}]}`) and `ContextPack.from_dict()` all work and are tested. Tags are the supported and tested way to get information in; anything that yields notes with an area works.

If `jevgate` is not on PATH: `python3 -m pip install --user git+https://github.com/kylerhenry/jevgate`
