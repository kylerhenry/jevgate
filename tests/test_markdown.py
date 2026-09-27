"""Tests for jevgate.markdown: frontmatter, sections, bullets, tickets, links, tags."""

from __future__ import annotations

from jevgate import markdown as md

DRAFT = """\
---
labels: [Agent, panel]
estimate: 3
---
# Deploy script for panel-1

## Why

Every deploy means driving the overlay dance by hand. It is six SSH commands with two reboots.

## What

Add `deploy/release.sh` in `carbon-panel` that performs the whole persistent deploy end to end.

### Behaviour

1. Preflight: `ssh panel-1` reachable.
2. Read the current overlay state.

### Flags

* `--quick`: deploy with the overlay on (no reboots).
* `--restart-only`: pass-through to `deploy.sh --restart-only`.

## Acceptance

* One command takes a clean checkout to a persistent deploy on panel-1
  with the overlay on afterwards.
* Ctrl-C during the deploy still ends with the overlay on.
    * killing the script during step 4 counts as the test
* `--quick` works and warns.

## Context

* `deploy/deploy.sh` and `deploy/screenshot.sh` already exist and should be reused.
* Runbook: `docs/panel-ops.md`.

### Prior answers

- (B03) **Which host runs the script?** carbon, over ssh.
- **Is a screenshot required?** No, optional.
"""


# ----------------------------------------------------------------- frontmatter


def test_frontmatter_scalars_lists_and_body():
    text = '---\ntitle: "Quoted: title"\narea: components\nnum: 3\nflag: true\nflow: [a, "b, c", d]\ndash:\n  - one\n  - two\nempty:\n---\n# Body\n\ntext\n'
    data, body, warnings = md.parse_frontmatter_ex(text)
    assert warnings == []
    assert data["title"] == "Quoted: title"
    assert data["area"] == "components"
    assert data["num"] == "3" and data["flag"] == "true"
    assert data["flow"] == ["a", "b, c", "d"]
    assert data["dash"] == ["one", "two"]
    assert data["empty"] == ""
    assert body.startswith("# Body")
    assert md.parse_frontmatter(text) == (data, body)


def test_frontmatter_absent_or_broken_returns_text_and_warning():
    assert md.parse_frontmatter_ex("# No frontmatter\n") == ({}, "# No frontmatter\n", [])
    data, body, warnings = md.parse_frontmatter_ex("---\ntitle: x\n# never closed\n")
    assert data == {} and body.startswith("---") and "closing" in warnings[0]
    nested = "---\nmeta:\n  key: value\n---\nbody\n"
    data, body, warnings = md.parse_frontmatter_ex(nested)
    assert data == {} and body == nested and "nested" in warnings[0]
    block = "---\ndesc: |\n  multi\n---\nbody\n"
    data, _, warnings = md.parse_frontmatter_ex(block)
    assert data == {} and warnings


# -------------------------------------------------------------------- sections


def test_parse_sections_keys_and_preamble():
    text = "# Title\n\nintro\n\n## Why\n\nbecause\n\n## What To Do\n\nstep\n\n### Sub A\n\ndetail\n\n```\n## not a heading\n```\n\n## Acceptance ##\n\n- ok\n"
    sections = md.parse_sections(text)
    assert sections[""] == "# Title\n\nintro\n"
    assert sections["why"].strip() == "because"
    assert "### Sub A" in sections["what to do"] and "## not a heading" in sections["what to do"]
    assert sections["what to do/sub a"].strip().startswith("detail")
    assert "acceptance" in sections and "not a heading" not in sections
    ex = md.parse_sections_ex(text)
    why = next(s for s in ex if s.key == "why")
    assert (why.level, why.line, why.start) == (2, 5, 6)


def test_heading_key_strips_tags():
    assert md.heading_key("Storage layer #jevgate/layer") == "storage layer"


# --------------------------------------------------------------------- bullets


def test_bullets_markers_continuations_and_nesting():
    text = "- first item\n  continues here\n* second\n    - nested one\n    - nested two\n+ third\n\n1. numbered is prose\n\n- after blank\n"
    assert md.bullets(text) == [
        "first item continues here",
        "second; nested one; nested two",
        "third",
        "after blank",
    ]
    assert [line for line, _ in md.bullets_ex(text, first_line=10)] == [10, 12, 15, 19]


def test_bullets_skip_fences_and_headings():
    text = "- one\n### Heading\n- two\n```\n- inside fence\n```\n"
    assert md.bullets(text) == ["one", "two"]


# ---------------------------------------------------------------------- tickets


def test_parse_ticket_template():
    ticket = md.parse_ticket(DRAFT)
    assert ticket["title"] == "Deploy script for panel-1"
    assert ticket["why"].startswith("Every deploy") and ticket["why"].endswith("reboots.")
    assert "### Behaviour" in ticket["what"] and "### Flags" in ticket["what"]
    assert ticket["what"].startswith("Add `deploy/release.sh`")
    assert ticket["acceptance"] == [
        "One command takes a clean checkout to a persistent deploy on panel-1 with the overlay on afterwards.",
        "Ctrl-C during the deploy still ends with the overlay on.; killing the script during step 4 counts as the test",
        "`--quick` works and warns.",
    ]
    assert ticket["context_bullets"] == [
        "`deploy/deploy.sh` and `deploy/screenshot.sh` already exist and should be reused.",
        "Runbook: `docs/panel-ops.md`.",
    ]
    assert ticket["prior_answers"] == [
        {"id": "B03", "question": "Which host runs the script?", "answer": "carbon, over ssh."},
        {"question": "Is a screenshot required?", "answer": "No, optional."},
    ]
    assert ticket["labels"] == ["Agent", "panel"] and ticket["estimate"] == 3


def test_parse_ticket_linear_body_without_title_and_variants():
    body = "## Why\n\nreason\n\n## What to do\n\n### 1. Step\n\ndo it\n\n## Acceptance\n\n* a\n* b\n\n## Context\n\n* c\n\n## Prior answers\n\n- **Q one?** A one\n- plain question? plain answer\n"
    ticket = md.parse_ticket(body)
    assert ticket["title"] == ""
    assert ticket["what"] == "### 1. Step\n\ndo it"
    assert ticket["acceptance"] == ["a", "b"] and ticket["context_bullets"] == ["c"]
    assert ticket["prior_answers"][0] == {"question": "Q one?", "answer": "A one"}
    assert ticket["prior_answers"][1] == {"question": "plain question?", "answer": "plain answer"}
    assert ticket["labels"] == [] and ticket["estimate"] is None
    empty = md.parse_ticket("")
    assert empty["acceptance"] == [] and empty["why"] == "" and empty["prior_answers"] == []


def test_render_ticket_round_trip_and_order():
    ticket = md.parse_ticket(DRAFT)
    rendered = md.render_ticket(ticket)
    order = [rendered.index(h) for h in ("# Deploy", "## Why", "## What", "## Acceptance", "## Context", "### Prior answers")]
    assert order == sorted(order)
    assert "- (B03) **Which host runs the script?** carbon, over ssh." in rendered
    again = md.parse_ticket(rendered)
    for key in ("title", "why", "what", "acceptance", "context_bullets", "prior_answers"):
        assert again[key] == ticket[key], key
    assert md.render_ticket(again) == rendered
    minimal = md.render_ticket({"title": "T", "why": "w", "what": "x", "acceptance": ["a"]})
    assert "## Context" not in minimal and minimal.endswith("- a\n")


# ------------------------------------------------------------------- wikilinks


def test_wikilinks_targets_aliases_headings_and_code():
    text = "See [[Cache helper]] and [[Postgres store|the store]] and [[Auth#tokens]] again [[Cache helper]].\nNot `[[in code]]` and\n```\n[[fenced]]\n```\n![[image.png]] [[#same-note]]"
    assert md.wikilinks(text) == ["Cache helper", "Postgres store", "Auth"]


# ------------------------------------------------------------------------ tags


def test_inline_tags_positions_and_exclusions():
    text = "# Title #jevgate/architecture\n\n- rule one #jevgate/rule\n- `#jevgate/rule` in code\n```\n#jevgate/rule fenced\n```\nhttps://x/#jevgate/nope (#jevgate/layer) #other/tag #jevgate/applies/py"
    tags = md.inline_tags(text)
    assert tags == [
        (1, "jevgate/architecture"),
        (3, "jevgate/rule"),
        (8, "jevgate/layer"),
        (8, "jevgate/applies/py"),
    ]
    assert md.inline_tags("- x #proj/rule\n", prefix="proj/") == [(1, "proj/rule")]


def test_strip_tags():
    assert md.strip_tags("Interface code never calls the store. #jevgate/rule #jevgate/applies/py") == "Interface code never calls the store."
    assert md.strip_tags("Layer name (#jevgate/layer)") == "Layer name"
    assert md.strip_tags("keep #other and `#jevgate/x` #jevgate/rule") == "keep #other and `#jevgate/x`"
