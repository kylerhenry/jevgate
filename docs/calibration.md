# Calibration (live pass, 2026-09-27)

This records the first live calibration of both gates against the labelled fixtures
(`fixtures/tickets`, `fixtures/deliveries`) and one real repository (`carbon-panel`),
what changed because of it, what was left alone, and how to repeat it. Catalog version
after this pass: `2026-09-27.5`. All Jev requests are cached in `fixtures/responses/`
(131 entries, one per request, no credentials), so `jevgate calibrate` reproduces every
number below offline.

## Method

1. `jevgate calibrate ticket|delivery <fixtures> --refresh` runs every case live, stores
   each request's answer under its content hash, and sweeps every family's threshold over
   0.50–0.95 and `unclear_at` over 0.30–0.50 against `expected.json`.
2. Each route/verdict disagreement was traced to the gate that caused it and classified as
   fixture defect, threshold, question wording, or wrong label. Wording was changed only
   after reading the raw probabilities in the cached response; every wording change bumped
   `CATALOG_VERSION` and re-ran both gates (four rounds: `.2` → `.5`).
3. Candidate phrasings for the two ambiguity questions were compared side by side with a
   one-off `tsjudge run` over six drafts before the wording was chosen.
4. Thresholds were changed only where the sweep agreed on at least three labelled cases;
   none did, so no default changed. The plan's 0.85 (ticket pass gates) and 0.90 (delivery
   `ac_met`, `ac_proven`, `tests_exercise_change`) stay; the evidence against them is below.
5. The delivery gate then ran on a real commit (`carbon-panel` `db5dfcf`, DIY-14/15/16) with a
   pack built from the repo, on a scratch worktree with one acceptance bullet broken, and the
   ticket gate ran on the DIY-16 ticket written in the template.

Cost: 523 calibration requests over four rounds, 2.14 M tokens, about $0.085 at $0.04 per
million tokens; the carbon-panel runs added 32 requests (~$0.017); the wording experiment
6 requests. One full round of both gates is ~131 requests and ~$0.021.

## Agreement before and after

| gate | round 1 (catalog .2) | final (catalog .5) |
|---|---|---|
| ticket route | 6/16 | 11/16 |
| ticket gather (expected ⊆ report) | 12/13 | 4/4 |
| ticket asks (expected ⊆ report) | 10/11 | 1/2 |
| delivery verdict | 7/13 | 12/13 |
| delivery gather | 6/6 | 2/2 |

Round 1 routed eleven ticket cases to `ask` because bank question B04 ("what happens when
the operation fails") fired at 0.72–0.85 on almost every draft, including both `ready`
drafts, and `ask` outranks `revise`. Round 1 also produced `gather` on nearly every ticket
because constraint C02 (p95 latency) and C03 (tenant isolation) came back `unclear` (0.41–0.90)
for drafts that simply did not mention them.

## What changed and why

### Question wording (catalog `.2` → `.5`)

| gate | change | evidence |
|---|---|---|
| `bank:*` (`ticket/clarify.py`) | Ask one thing: "would a human's answer change the What or Acceptance?", with "existing behaviour the ticket leaves untouched" and "an implementer would settle it routinely" named as settled. | B04 fired on 11/15 drafts at 0.72–0.85 with a 0.09 gap between `ready-01` and `missing-context-01`; after: 0.49 vs 0.66, no ask on any `ready` draft. |
| `constraint:*`, `arch_rule:*`, shared `UNCLEAR` text | "Judge only what the What says: it respects/complies when nothing it describes conflicts, even if the draft never mentions it"; `unclear` reserved for a missing fact. | C02/C03 `unclear` on 13/15 drafts → 0/15; gather agreement 12/13 → 4/4 with no spurious gathers. |
| `design_unambiguous` | Statement form: "The What names where the logic lives, which interface or command changes and how data flows, so two competent engineers would build the same thing; only decisions that change the work count." | Side-by-side: `ready-01` 0.77 (orig) / 0.58 (checklist question) / 0.86 (statement); `ambiguous-design-01` 0.21 / 0.12 / 0.20. Best level with the same separation. |
| `language_unambiguous` | "Would a competent engineer read every sentence the way its author meant it? Only wording that changes the work counts", with examples of the pronouns and vague terms that do. | `ready-01` 0.47 → 0.63, `vague-language-01` 0.07 → 0.10; no pass-framed phrasing reached 0.85 (see below). |
| `ac_testable:*` | Says the check need not exist yet and the bullet need not spell out test code; only the pass/fail outcome must be unambiguous. | Fires on labelled-passing bullets at 0.85: 13/14 → 7/14; `untestable-ac-01` bullets stay at 0.05–0.07. |
| `split` | `keep` and `design_first` are structured options with `not_for` and examples: vague wording the author can clarify is `keep`, not `design_first`. | `vague-language-01` design_first 0.92 → 0.64 → below 0.60 in the final round; `design-first-01` stays 0.97. |
| `overlap:*` | `unclear` option now says "the component note does not say enough about what it provides". | `thin-component-note-01` `overlap:event-bus` unclear 0.39 → 0.91, so the thin note is gathered as labelled. |
| `claim:*` | "Read each note's text and its provides and interface fields; a note that states the same facts in other words supports the claim." Plus a state fix: `claim_notes` now sends the note's `provides`/`interface`/`aliases` frontmatter, which it had dropped. | The CSV-importer claim was `supported` 0.78 with the architecture note present but `not_covered` 0.76–0.83 without it, although the component note's `provides` line states the fact; the thin-pack claim about the Postgres store was `not_covered` 0.99 for the same reason. Both are `supported` now. |
| `ac_met:*` (`delivery/rubric.py`) | Test output counts only where it shows the behaviour; a missing or unrelated test is not evidence against the code. Unchanged paths and shown behaviour that delegates routine work count as met; `unclear` only when the outcome is decided in code that is neither in the diff nor in the excerpts. | `unproven-unrelated-tests-01` ACs were `met` at 0.76–0.86 with the unrelated log (fail at 0.90) and are 0.95–0.98 now; `unclear-ac-01` AC1 is `unclear` 0.95 (was a 0.41 borderline). An intermediate wording that only said "do not assume unshown code" pushed `unclear` mass onto "behaves as before" bullets (0.10–0.33) and flipped four accept/unproven cases; the final wording restored them. |
| `dup:*` | Test code, callers and other areas never count as re-implementing; `unrelated` now has a description. | `accept-01` `dup:postgres-store` on the test file was `unclear` 0.49; 0.0 now. |
| `convention:*` | `not_applicable` now has a description ("the added lines contain nothing the convention speaks about"). | Supporting change for the V01 fixture fix below. |

### Fixture changes

| fixture | change | class |
|---|---|---|
| `fixtures/vault/conventions.md`, `vault-noarch/conventions.md`, `vault-untagged/conventions-python.md`, `context-pack.json` | V01 reads "Public functions in production modules carry type hints and a one-line docstring; test functions in `test_*.py` are exempt." | fixture defect: the old wording applied to `test_*.py` and fired on the test file of every accept/unproven delivery case at 0.61–0.74. `applies_to` globs cannot express an exclusion, so the convention says it. |
| `fixtures/tickets/thin-component-note-01/draft.md` | The Context bullet now states a fact the thin pack supports (the Postgres store owns all SQL) instead of one only the full Event-bus note states. | fixture defect: the claim was unsupported by design of the thin pack, which is a legitimate `revise`, masking the `gather` the case is meant to exercise. |
| `fixtures/deliveries/unmet-ac-01/` | Added `after/src/ledger/api/router.py` (no 409 mapping) and listed it under `files` in `expected.json`. | fixture defect: AC2 (API returns 409) could not be judged from a diff that never touches the router, so `gather` was the honest answer; with the router excerpt it is decidably `not_met` (0.29 → the labelled `revise`). |
| `tests/test_delivery_cli.py` | The "ticket not found" invocation passes `--run-dir` under `tmp_path`. | housekeeping: it left `.jevgate/runs/<stamp>` in the repo on every suite run. |

No `expected.json` label was changed.

## Sweeps (round 1 vs final; rows 0.50–0.90)

Only families asked in at least three labelled cases are shown. "fires on failing" is the
fraction of labelled-failing cases the family fires on at that threshold; "fires on passing"
the fraction of the other cases; `*` marks the default.
#### ac_testable (pass, pass; default 0.85; asked in 15 cases; labelled failing: untestable-ac-01)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 1/1 | 2/14 | 0.86 | 1/1 | 2/14 | 0.86 |
| 0.60 | 1/1 | 3/14 | 0.8 | 1/1 | 4/14 | 0.73 |
| 0.70 | 1/1 | 5/14 | 0.66 | 1/1 | 5/14 | 0.66 |
| 0.80 | 1/1 | 9/14 | 0.4 | 1/1 | 5/14 | 0.66 |
| 0.85 * | 1/1 | 13/14 | 0.13 | 1/1 | 7/14 | 0.53 |
| 0.90 | 1/1 | 14/14 | 0.06 | 1/1 | 12/14 | 0.2 |

#### design_unambiguous (pass, pass; default 0.85; asked in 15 cases; labelled failing: ambiguous-design-01, missing-context-01)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 2/2 | 5/13 | 0.66 | 1/2 | 2/13 | 0.8 |
| 0.60 | 2/2 | 6/13 | 0.6 | 2/2 | 3/13 | 0.8 |
| 0.70 | 2/2 | 7/13 | 0.53 | 2/2 | 6/13 | 0.6 |
| 0.80 | 2/2 | 13/13 | 0.13 | 2/2 | 11/13 | 0.26 |
| 0.85 * | 2/2 | 13/13 | 0.13 | 2/2 | 13/13 | 0.13 |
| 0.90 | 2/2 | 13/13 | 0.13 | 2/2 | 13/13 | 0.13 |

#### language_unambiguous (pass, pass; default 0.85; asked in 15 cases; labelled failing: vague-language-01)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 1/1 | 14/14 | 0.06 | 1/1 | 7/14 | 0.53 |
| 0.60 | 1/1 | 14/14 | 0.06 | 1/1 | 9/14 | 0.4 |
| 0.70 | 1/1 | 14/14 | 0.06 | 1/1 | 14/14 | 0.06 |
| 0.80 | 1/1 | 14/14 | 0.06 | 1/1 | 14/14 | 0.06 |
| 0.85 * | 1/1 | 14/14 | 0.06 | 1/1 | 14/14 | 0.06 |
| 0.90 | 1/1 | 14/14 | 0.06 | 1/1 | 14/14 | 0.06 |

#### bank (fire, fire; default 0.7; asked in 15 cases; labelled failing: none)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 0/0 | 15/15 | 0.0 | 0/0 | 9/15 | 0.4 |
| 0.60 | 0/0 | 14/15 | 0.06 | 0/0 | 4/15 | 0.73 |
| 0.70 * | 0/0 | 11/15 | 0.26 | 0/0 | 1/15 | 0.93 |
| 0.80 | 0/0 | 5/15 | 0.66 | 0/0 | 0/15 | 1.0 |
| 0.85 | 0/0 | 3/15 | 0.8 | 0/0 | 0/15 | 1.0 |
| 0.90 | 0/0 | 0/15 | 1.0 | 0/0 | 0/15 | 1.0 |

#### split (choice, fire; default 0.6; asked in 15 cases; labelled failing: design-first-01)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 1/1 | 3/14 | 0.8 | 1/1 | 0/14 | 1.0 |
| 0.60 * | 1/1 | 2/14 | 0.86 | 1/1 | 0/14 | 1.0 |
| 0.70 | 1/1 | 2/14 | 0.86 | 1/1 | 0/14 | 1.0 |
| 0.80 | 1/1 | 2/14 | 0.86 | 1/1 | 0/14 | 1.0 |
| 0.85 | 1/1 | 1/14 | 0.93 | 1/1 | 0/14 | 1.0 |
| 0.90 | 1/1 | 1/14 | 0.93 | 1/1 | 0/14 | 1.0 |

#### unclear_at (ticket; default 0.4)

| unclear_at | r1 unclear on gather-labelled | r1 unclear on other | final unclear on gather-labelled | final unclear on other | final agree |
|---|---|---|---|---|---|
| 0.3 | 1/1 | 19/158 | 1/1 | 4/158 | 0.89 |
| 0.4 | 0/1 | 16/158 | 1/1 | 3/158 | 0.89 |
| 0.5 | 0/1 | 12/158 | 1/1 | 0/158 | 0.91 |

#### ac_met (choice, pass; default 0.9; asked in 12 cases; labelled failing: defect-01, unmet-ac-01)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 1/2 | 2/10 | 0.75 | 2/2 | 2/10 | 0.83 |
| 0.60 | 1/2 | 2/10 | 0.75 | 2/2 | 2/10 | 0.83 |
| 0.70 | 1/2 | 2/10 | 0.75 | 2/2 | 2/10 | 0.83 |
| 0.80 | 1/2 | 4/10 | 0.58 | 2/2 | 2/10 | 0.83 |
| 0.85 | 1/2 | 4/10 | 0.58 | 2/2 | 2/10 | 0.83 |
| 0.90 * | 1/2 | 5/10 | 0.5 | 2/2 | 4/10 | 0.66 |

#### ac_proven (choice, pass; default 0.9; asked in 11 cases; labelled failing: unproven-unrelated-tests-01)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 1/1 | 4/10 | 0.63 | 1/1 | 4/10 | 0.63 |
| 0.60 | 1/1 | 4/10 | 0.63 | 1/1 | 4/10 | 0.63 |
| 0.70 | 1/1 | 5/10 | 0.54 | 1/1 | 5/10 | 0.54 |
| 0.80 | 1/1 | 6/10 | 0.45 | 1/1 | 6/10 | 0.45 |
| 0.85 | 1/1 | 6/10 | 0.45 | 1/1 | 6/10 | 0.45 |
| 0.90 * | 1/1 | 6/10 | 0.45 | 1/1 | 6/10 | 0.45 |

#### tests_exercise_change (choice, pass; default 0.9; asked in 11 cases; labelled failing: unproven-unrelated-tests-01)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 1/1 | 0/10 | 1.0 | 1/1 | 0/10 | 1.0 |
| 0.60 | 1/1 | 0/10 | 1.0 | 1/1 | 0/10 | 1.0 |
| 0.70 | 1/1 | 0/10 | 1.0 | 1/1 | 0/10 | 1.0 |
| 0.80 | 1/1 | 0/10 | 1.0 | 1/1 | 0/10 | 1.0 |
| 0.85 | 1/1 | 0/10 | 1.0 | 1/1 | 0/10 | 1.0 |
| 0.90 * | 1/1 | 0/10 | 1.0 | 1/1 | 0/10 | 1.0 |

#### convention (choice, fire; default 0.6; asked in 12 cases; labelled failing: convention-01)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 1/1 | 7/11 | 0.41 | 1/1 | 3/11 | 0.75 |
| 0.60 * | 1/1 | 6/11 | 0.5 | 1/1 | 1/11 | 0.91 |
| 0.70 | 1/1 | 3/11 | 0.75 | 1/1 | 1/11 | 0.91 |
| 0.80 | 1/1 | 1/11 | 0.91 | 1/1 | 1/11 | 0.91 |
| 0.85 | 1/1 | 1/11 | 0.91 | 1/1 | 1/11 | 0.91 |
| 0.90 | 1/1 | 1/11 | 0.91 | 1/1 | 1/11 | 0.91 |

#### dup (choice, fire; default 0.6; asked in 12 cases; labelled failing: duplicate-01)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 1/1 | 1/11 | 0.91 | 1/1 | 1/11 | 0.91 |
| 0.60 * | 1/1 | 1/11 | 0.91 | 1/1 | 1/11 | 0.91 |
| 0.70 | 1/1 | 1/11 | 0.91 | 1/1 | 1/11 | 0.91 |
| 0.80 | 1/1 | 1/11 | 0.91 | 1/1 | 1/11 | 0.91 |
| 0.85 | 1/1 | 1/11 | 0.91 | 1/1 | 1/11 | 0.91 |
| 0.90 | 1/1 | 1/11 | 0.91 | 1/1 | 1/11 | 0.91 |

#### edge_cases (level, pass; default 0.7; asked in 12 cases; labelled failing: none)

| t | r1 fires on failing | r1 fires on passing | r1 agree | final fires on failing | final fires on passing | final agree |
|---|---|---|---|---|---|---|
| 0.50 | 0/0 | 2/12 | 0.83 | 0/0 | 2/12 | 0.83 |
| 0.60 | 0/0 | 3/12 | 0.75 | 0/0 | 3/12 | 0.75 |
| 0.70 * | 0/0 | 5/12 | 0.58 | 0/0 | 4/12 | 0.66 |
| 0.80 | 0/0 | 5/12 | 0.58 | 0/0 | 4/12 | 0.66 |
| 0.85 | 0/0 | 5/12 | 0.58 | 0/0 | 4/12 | 0.66 |
| 0.90 | 0/0 | 5/12 | 0.58 | 0/0 | 5/12 | 0.58 |

#### unclear_at (delivery; default 0.4)

| unclear_at | r1 unclear on gather-labelled | r1 unclear on other | final unclear on gather-labelled | final unclear on other | final agree |
|---|---|---|---|---|---|
| 0.3 | 1/1 | 8/105 | 1/1 | 2/105 | 0.83 |
| 0.4 | 1/1 | 6/105 | 1/1 | 1/105 | 0.83 |
| 0.5 | 0/1 | 1/105 | 1/1 | 1/105 | 0.83 |

## Remaining disagreements

Ticket gate, five cases, all honest:

| case | expected | got | cause |
|---|---|---|---|
| `ready-01` | ready | revise | `language_unambiguous` 0.63, `design_unambiguous` 0.80 vs 0.85 |
| `ready-02` | ready | revise | `language_unambiguous` 0.55, `design_unambiguous` 0.76 |
| `no-architecture-01` | gather | revise | same draft as `ready-01`: 0.63 / 0.80; the architecture gather is reported but fails outrank it |
| `thin-component-note-01` | gather | revise | `language_unambiguous` 0.36, `design_unambiguous` 0.60; the component gather is reported |
| `missing-context-01` | ask | revise | B04 0.66 and B03 0.34 against `BANK_AT` 0.70; the five revise findings (design 0.55, reuse of Ledger CLI 0.76, simpler alternative 0.78, ...) are right |

Delivery gate, one case: `gather-resolved-02` expected accept, got revise, because AC1
("after `close_period` the next `balance` reads from the store") is `met` 0.89 with 0.11
on `unclear`, a hair under 0.90: the excerpt shows `after_close` invalidating the cache but
not `balance` itself.

### The 0.85 pass gates

Two findings, left for Kyle rather than acted on:

1. No pass-framed wording of `language_unambiguous` puts a clean draft above 0.70 (final:
   `ready-01` 0.63, `ready-02` 0.55, `ungrounded-01` 0.64, `reuse-missed-01` 0.65, and the
   real DIY-16 ticket 0.32), while the vague draft sits at 0.10. Every ticket fixture and the
   real ticket fail this gate at 0.85, so `ready` is unreachable. The sweep's best threshold
   is 0.50 (7/14 clean drafts still fire). Jev is asymmetric here: asked the defect-finding
   way ("does any term, pronoun or phrase have two readings that lead to different work?")
   the same six drafts gave 0.18 / 0.16 (ready) vs 0.90 (vague) and 0.48 (ambiguous design),
   i.e. a fire gate at 0.60 would agree on all of them. The same holds for design: the
   defect-finding form gave 0.42 / 0.51 (ready) vs 0.85 / 0.88 (ambiguous, vague).
   Options: lower these two gates (0.50 language, 0.75 design), or flip them to fire gates on
   the defect question at 0.60. The TypeSafe docs recommend the second ("phrase the question
   so a high value means yes"; asking for the absence of a defect inverts it).
2. `design_unambiguous` in statement form reaches 0.76–0.80 on the two `ready` drafts and
   0.66 on DIY-16; ambiguous drafts stay at 0.20–0.38. 0.85 fails all of them.

`ac_met` at 0.90 fails 4/10 non-failing fixtures at the final wording (2/10 at 0.60–0.80),
all on "behaves as before"-style bullets where Jev keeps 0.05–0.11 on `unclear`; on the real
commit it failed the four-volume bullet at 0.68 because the 90 % colour rule lives in
`draw.bar`, outside the diff. `BANK_AT` 0.70 is the best of the swept values (0.60 would ask
on three drafts labelled `revise`); with one ask-labelled fixture there is no basis to move it.
`unclear_at` 0.40 vs 0.50: the sweep prefers 0.50 on both gates (1 labelled gather case each),
and on carbon-panel 20 of the 35 `dup` gathers sit at 0.40–0.44; not changed for lack of
labelled cases.

## carbon-panel trial

Pack: `jevgate context init` seeded 22 notes; filled by hand from `README.md`,
`docs/panel-ops.md` and the tree: six `#jevgate/rule` bullets (one `draw_<page>` per board
registered in `BOARDS`; boards read only `State`; palette/draw/fonts tokens; metrics only over
`home/status/<area>/<host>` from the separate collector; fixture + golden per board; touch
map outside boards), three layers, 16 component notes with `provides`/`interface`
(including the external collector), four constraints, four conventions (one
`#jevgate/applies/py`), data/interfaces/glossary notes and one real ADR. Ticket:
`diy-16-ticket.md` in the template (Why from the first paragraph, What from Layout + Data,
Acceptance = the three Verification bullets + the two hooks, Context naming the mock,
DIY-14/15 and the data topic). Tests: `pytest -q tests` fresh, 343 passed in 14.6 s.

### Delivery, real commit `db5dfcf` (base `db5dfcf~1`)

Verdict **revise**; 23 requests, 316 k input tokens, $0.0133; 12 fail, 35 unclear, 4 warn.

| finding | p | judged |
|---|---|---|
| `scope_creep` | 0.97 | true: the commit also implements DIY-14 (overview reorder) and DIY-15 (CPU page, Host page removed) |
| `ac_met:5` (live screenshot with SABnzbd leading) | met 0.52 / unclear 0.35 | true: not decidable from code; the bullet is a manual check |
| `ac_met:4` (goldens in the set, `pytest` passes) | met 0.60 | half true: the goldens are binary, so only the fixture JSON and the log are visible |
| `ac_met:2` (four bars, `status.err` above 90 %) | met 0.68 / partial 0.23 | false: `disks[:4]` is in the diff; the 90 % rule is in `draw.bar`, outside it (a `--files panel/draw.py` case) |
| `ac_proven:2..5` (warn) | 0.35–0.62 | true: `pytest -q` prints dots, so the log names no test; run with `-v`/`-rA` for proof |
| `correctness_defect:panel/boards/disk.py` | 0.64 (borderline) | unverified; the message names no hunk |
| `over_engineered:panel/draw.py` 0.74, `panel/tokens.py` 0.64 | | false: 10- and 2-line edits to shared primitives |
| `edge_cases` on `app.py`, `boards/__init__.py`, `tokens.py`, two fixture JSONs | 0.49–0.67 | false: the gate ran on a registry, a token table and JSON fixtures |
| 35 × `dup` unclear (0.40–0.52) | | false: component × file pairs such as `panel-fonts` vs `tests/fixtures/all.json`, `docs/panel-ops.md` and the deleted `host.py`; the canned message "note lacks provides/interface" is wrong, every note has both |

Architecture and conventions produced no findings (six rules complied, V01 applies-to-py held).
`tests_exercise_change` 1.0.

### Delivery, broken worktree (`disks[:4]` → `disks[:3]`, same green log)

Verdict **revise** (2 live requests, 21 cached, $0.0015). `correctness_defect:panel/boards/disk.py`
rose 0.64 → **0.95**, `ac_met:2` fell from met 0.68 to met 0.43 / partial 0.40 / not_met 0.15,
and `ac_met:4` from met 0.60 to partial 0.46; both name `panel/boards/disk.py`. So the break
is detected and attributed to the right file, but since the real commit was already `revise`
the verdict alone does not distinguish them; the per-file defect gate does.

### Ticket gate on DIY-16

Route **revise** (7 requests, $0.0018): `readability` level 1 (0.27), `language_unambiguous`
0.32, `design_unambiguous` 0.66, `ac_testable:5` 0.35 (the live-screenshot bullet, correctly
untestable), `ungrounded:2` 0.94 (the DIY-14/15 history is not in any note; true by the rule,
though the claim is about ticket history). All six rules complied, placement matched the Board
layer, reuse showed `uses` for panel-boards and panel-draw, no bank question fired, no gather.
A dense layout spec reads as "padded" to the readability rubric and as ambiguous wording at
0.32; on this evidence a person's ticket does not pass the 0.85 gates either.

## Recommendations

- Decide the two ambiguity gates (see "The 0.85 pass gates"); nothing routes `ready` until then.
- Delivery per-file gates should skip deleted files and non-source files (`.json`, `.png`, `.md`)
  for `dup`, `over_engineered`, `correctness_defect`, `edge_cases`; and `dup` should skip the
  component's own file. That removes all 35 `dup` gathers and five spurious fails on carbon-panel.
- Replace the `dup` unclear message with the actual `P(unclear)` and a `--files` hint; it
  asserts a missing `provides` line that is present.
- `ac_met` findings whose top choice is `met` should say "not clearly met (P(met) = 0.68 <
  0.90)" rather than "not satisfied (Implemented as described ...)".
- SKILL.md: require a test log that names tests (`pytest -v` or `-rA`); `-q` cannot prove anything.
- More labelled fixtures with `ask`, `gather` and `accept` outcomes; every threshold question
  above stalls on one labelled case.

## Re-running

```sh
# offline, from the cache (what CI can run)
.venv/bin/jevgate calibrate ticket fixtures/tickets --out /tmp/calib-ticket.md
.venv/bin/jevgate calibrate delivery fixtures/deliveries --out /tmp/calib-delivery.md
# live: after any wording change bump CATALOG_VERSION, then
.venv/bin/jevgate calibrate ticket fixtures/tickets --refresh
.venv/bin/jevgate calibrate delivery fixtures/deliveries --refresh
# prune entries older than the last refresh (stale hashes are harmless but clutter the dir)
python3 -c 'import json,glob,os,sys;[os.remove(f) for f in glob.glob("fixtures/responses/*.json") if json.load(open(f))["created"]<sys.argv[1]]' 2026-09-27T23:00:00+00:00
# carbon-panel trial (pack and ticket were written under the session scratchpad; recreate with)
.venv/bin/jevgate context init --repo /media/SSD/dev/carbon-panel --out /tmp/carbon-pack --project carbon-panel
cd /media/SSD/dev/carbon-panel && SDL_VIDEODRIVER=dummy .venv/bin/pytest -v tests > /tmp/carbon-tests.log 2>&1
.venv/bin/jevgate delivery check --ticket /tmp/diy-16-ticket.md --repo /media/SSD/dev/carbon-panel --base db5dfcf~1 --head db5dfcf \
  --test-log /tmp/carbon-tests.log --context-dir /tmp/carbon-pack --project carbon-panel --run-dir /tmp/carbon-run
.venv/bin/jevgate ticket check /tmp/diy-16-ticket.md --context-dir /tmp/carbon-pack --project carbon-panel --run-dir /tmp/carbon-ticket-run
```
