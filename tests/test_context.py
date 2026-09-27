"""Tests for the context pack loader, its CLI and the fixture vaults."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from jevgate import context_cli
from jevgate.context import ContextPack, Item, Note

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
UNTAGGED_AREAS = {"architecture": ["arch/*.md"]}


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def item_key(pack: ContextPack) -> list[tuple]:
    kinds = ("rule", "layer", "constraint", "convention", "component", "decision")
    return sorted((i.id, i.kind, i.text, json.dumps(i.meta, sort_keys=True)) for k in kinds for i in pack.items(k))


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="jevgate")
    context_cli.register(top.add_subparsers(dest="command"))
    return top


def run(argv: list[str]) -> int:
    args = parser().parse_args(argv)
    return args.func(args)


@pytest.fixture
def vault() -> ContextPack:
    return ContextPack.load(FIXTURES / "vault", project="ledger")


# ------------------------------------------------------------------ tagging


def test_tags_from_frontmatter_and_inline_ignore_code(tmp_path):
    write(tmp_path, "a.md", "---\ntags: [jevgate/data, extra]\n---\n# A\n\ntext #jevgate/project/ledger\n\n- r `#jevgate/rule`\n```\n- fenced #jevgate/rule\n```\n- real #jevgate/rule\n")
    pack = ContextPack.load(tmp_path, project="ledger")
    note = pack.notes("data")[0]
    assert note.tags == ["jevgate/data", "extra", "jevgate/project/ledger", "jevgate/rule"]
    assert note.resolved_by == "tag:#jevgate/data" and note.project == "ledger"
    rules = pack.items("rule")
    assert [r.text for r in rules] == ["real"] and rules[0].line == 12 and rules[0].note == "a.md"
    assert "#jevgate/project" not in note.body and "real #jevgate/rule" not in note.body  # tags stripped
    assert "`#jevgate/rule`" in note.body  # code span kept verbatim


def test_resolution_order_with_attribution(tmp_path):
    write(tmp_path, "tagged.md", "---\narea: data\n---\n# T\n\n#jevgate/glossary\n")
    write(tmp_path, "fm.md", "---\narea: data\nkind: component\n---\n# F\n")
    write(tmp_path, "arch/over.md", "# O\n\n## Rules\n\n- glob rule\n")
    write(tmp_path, "components/c.md", "# C\n\nprovides text\n")
    write(tmp_path, "adr/d.md", "# D\n\nStatus: accepted\n\n## Decision\n\nwe decide\n")
    write(tmp_path, "loose.md", "# L\n\nnothing\n")
    write(tmp_path, ".obsidian/hidden.md", "# H\n\n#jevgate/architecture\n")
    pack = ContextPack.load(tmp_path, areas={"architecture": ["arch/**/*.md"]})
    by_path = {n.path: n for n in pack.all_notes()}
    assert ".obsidian/hidden.md" not in by_path
    assert (by_path["tagged.md"].area, by_path["tagged.md"].resolved_by) == ("glossary", "tag:#jevgate/glossary")
    assert (by_path["fm.md"].area, by_path["fm.md"].kind, by_path["fm.md"].resolved_by, by_path["fm.md"].kind_by) == ("data", "component", "frontmatter:area", "frontmatter:kind")
    assert (by_path["arch/over.md"].area, by_path["arch/over.md"].resolved_by) == ("architecture", "glob:arch/**/*.md")
    assert (by_path["components/c.md"].area, by_path["components/c.md"].kind, by_path["components/c.md"].resolved_by) == ("components", "component", "folder:components")
    assert (by_path["adr/d.md"].area, by_path["adr/d.md"].kind, by_path["adr/d.md"].resolved_by) == ("decisions", "decision", "folder:adr")
    assert (by_path["loose.md"].area, by_path["loose.md"].resolved_by) == ("general", "default:general")
    assert [r.text for r in pack.items("rule")] == ["glob rule"]
    decision = pack.items("decision")[0]
    assert decision.meta["status"] == "accepted" and decision.meta["decision"] == "we decide"
    assert pack.items("component")[0].text == "provides text"
    explain = {e["path"]: e for e in pack.explain()}
    assert explain["arch/over.md"]["items"][0]["resolved_by"] == "heading:## Rules"
    assert explain["loose.md"]["area"] == "general"


def test_project_scoping(tmp_path):
    write(tmp_path, "global.md", "# G\n\n#jevgate/data\n")
    write(tmp_path, "mine.md", "---\ntags: [jevgate/data, jevgate/project/ledger]\n---\n# M\n")
    write(tmp_path, "theirs.md", "# T\n\n#jevgate/data #jevgate/project/other\n")
    write(tmp_path, "fm.md", "---\narea: data\nproject: ledger\n---\n# F\n")
    ledger = ContextPack.load(tmp_path, project="ledger")
    assert sorted(n.path for n in ledger.notes("data")) == ["fm.md", "global.md", "mine.md"]
    skipped = next(n for n in ledger.all_notes() if n.path == "theirs.md")
    assert skipped.ignored and skipped.reason.startswith("project:other")
    nobody = ContextPack.load(tmp_path)
    assert [n.path for n in nobody.notes("data")] == ["global.md"]


def test_ignore_on_notes_and_lines(tmp_path):
    write(tmp_path, "arch.md", "# A #jevgate/architecture\n\n## Rules\n\n- keep me #jevgate/rule\n- drop me #jevgate/rule #jevgate/ignore\n- also dropped #jevgate/ignore\n\nsecret paragraph #jevgate/ignore\n")
    write(tmp_path, "gone.md", "---\ntags: [jevgate/ignore]\n---\n# Gone\n\n- never #jevgate/rule\n")
    write(tmp_path, "gone2.md", "# Gone too\n\n#jevgate/ignore\n\n- never #jevgate/rule\n")
    write(tmp_path, "gone3.md", "---\nignore: true\n---\n# Gone three\n")
    pack = ContextPack.load(tmp_path)
    assert [r.text for r in pack.items("rule")] == ["keep me"]
    body = pack.notes("architecture")[0].body
    assert "drop me" not in body and "secret" not in body and "keep me" in body
    ignored = {n.path: n.reason for n in pack.all_notes() if n.ignored}
    assert ignored == {"gone.md": "tag:#jevgate/ignore", "gone2.md": "tag:#jevgate/ignore", "gone3.md": "frontmatter:ignore"}
    assert [e["title"] for e in pack.index()] == ["A"]


# --------------------------------------------------------------------- items


def test_items_from_tags_and_heading_fallbacks(tmp_path):
    write(tmp_path, "general.md", "# Mixed\n\nA rule in a general note. #jevgate/rule\n\n## Rules\n\n- not a rule: general notes get no heading items\n")
    write(tmp_path, "layers.md", "---\narea: architecture\n---\n# Layers note\n\n## Layers\n\n- Web: handlers\n- Core — services\n")
    write(tmp_path, "cc.md", "---\narea: constraints\n---\n# CC\n\n- stray bullet outside the heading\n\n## Constraints\n\n- no network\n")
    write(tmp_path, "conv.md", "---\narea: conventions\n---\n# Conv\n\n## Conventions\n\n- docstrings #jevgate/convention #jevgate/applies/py\n- tests next to code\n")
    write(tmp_path, "only-bullets.md", "---\narea: constraints\n---\n# Bare\n\n- first bare\n- second bare\n")
    write(tmp_path, "arch.md", "---\ntags: [jevgate/architecture]\n---\n# Arch\n\n## Layers\n\n### UI #jevgate/layer\n\nRenders pages.\n\n### API\n\nServes JSON.\n")
    pack = ContextPack.load(tmp_path)
    assert pack.has("architecture") and pack.has("constraints") and pack.has("conventions")
    rules = pack.items("rule")
    assert [(r.id, r.text, r.resolved_by) for r in rules] == [("R01", "A rule in a general note.", "tag:#jevgate/rule")]
    layers = pack.items("layer")
    assert [(l.id, l.meta["name"], l.meta["responsibility"]) for l in layers] == [
        ("L01", "UI", "Renders pages."),
        ("L02", "API", "Serves JSON."),
        ("L03", "Web", "handlers"),
        ("L04", "Core", "services"),
    ]
    assert [l.resolved_by for l in layers] == ["tag:#jevgate/layer", "heading:## Layers", "heading:## Layers", "heading:## Layers"]
    constraints = pack.items("constraint")
    assert [(c.id, c.text, c.resolved_by) for c in constraints] == [
        ("C01", "no network", "heading:## Constraints"),
        ("C02", "first bare", "area:constraints bullets"),
        ("C03", "second bare", "area:constraints bullets"),
    ]
    conventions = pack.items("convention")
    assert [(c.id, c.text, c.meta["applies_to"], c.resolved_by) for c in conventions] == [
        ("V01", "docstrings", ["*.py"], "tag:#jevgate/convention"),
        ("V02", "tests next to code", [], "heading:## Conventions"),
    ]
    assert conventions[0].line == 8 and conventions[0].note == "conv.md"


def test_applies_filtering(tmp_path):
    write(tmp_path, "conv.md", "---\ntags: [jevgate/conventions]\n---\n# C\n\n- py only #jevgate/convention #jevgate/applies/py\n- js only #jevgate/convention #jevgate/applies/js\n- everywhere #jevgate/convention\n")
    write(tmp_path, "pyconv.md", "---\narea: conventions\napplies_to: [\"*.py\", \"tests/*\"]\n---\n# P\n\n- note-level python\n")
    pack = ContextPack.load(tmp_path)
    by_text = {i.text: i for i in pack.items("convention")}
    assert by_text["py only"].meta["applies_to"] == ["*.py"]
    assert by_text["js only"].meta["applies_to"] == ["*.js"]
    assert by_text["note-level python"].meta["applies_to"] == ["*.py", "tests/*"]
    assert [i.text for i in pack.applicable("src/x.py")] == ["py only", "everywhere", "note-level python"]
    assert [i.text for i in pack.applicable("web/app.js")] == ["js only", "everywhere"]
    assert [i.text for i in pack.applicable("tests/data.json")] == ["everywhere", "note-level python"]
    assert [i.text for i in pack.applicable("README.md", "conventions")] == ["everywhere"]


# ----------------------------------------------------------- links and dict


def test_wikilinks_resolution_and_follow_links(tmp_path):
    write(tmp_path, "data.md", "# Data model\n\n#jevgate/data\n\nSee [[Store|the store]] and [[Missing note]].\n")
    write(tmp_path, "components/store.md", "---\naliases: [Store]\npath: src/store.py\nprovides: keeps rows\n---\n# Postgres store\n\nKeeps rows in Postgres. Links to [[Cache]].\n")
    write(tmp_path, "components/cache.md", "---\npath: src/cache.py\nprovides: caches rows\n---\n# Cache\n\nTwo hops away.\n")
    pack = ContextPack.load(tmp_path, follow_links=1)
    assert pack.resolve("store").title == "Postgres store" and pack.resolve("Store#x") is not None
    assert pack.warnings == ["data.md: unresolved wikilink [[Missing note]]"]
    assert pack.notes("data")[0].links == ["Store", "Missing note"]
    assert "the store" in pack.notes("data")[0].body and "[[" not in pack.notes("data")[0].body
    one_hop = pack.slice({"data"}, "")["data"]["overview"]
    assert "Keeps rows in Postgres" in one_hop and "Two hops away" not in one_hop
    assert pack.last_evidence["data"]["notes"] == ["data.md", "components/store.md"]
    two = ContextPack.load(tmp_path, follow_links=2).slice({"data"}, "")["data"]["overview"]
    assert "Two hops away" in two
    zero = ContextPack.load(tmp_path, follow_links=0).slice({"data"}, "")["data"]["overview"]
    assert "Keeps rows in Postgres" not in zero
    linked = ContextPack.load(tmp_path, follow_links=1)
    store = [i for i in linked.items("component") if i.id == "postgres-store"]
    assert [c["id"] for c in linked.slice({"components"}, "keeps rows")["components"]][:2] == ["cache", "postgres-store"]
    assert store and store[0].meta["aliases"] == ["Store"]


def test_from_dict_shape_and_defaults():
    pack = ContextPack.from_dict(
        {
            "architecture": [
                {"title": "Arch", "text": "overview text", "items": [{"kind": "rule", "text": "no sql in ui"}, {"kind": "layer", "text": "UI", "meta": {"name": "UI"}}]}
            ],
            "components": [
                {"title": "Cache helper", "text": "body", "meta": {"path": "src/cache.py", "provides": "ttl cache", "aliases": ["cache"]}},
                {"title": "Explicit", "text": "body", "items": [{"id": "explicit", "text": "given", "meta": {"path": "x.py"}}]},
            ],
            "constraints": [{"title": "Limits", "text": "- a\n", "items": [{"text": "stdlib only", "line": 3}]}],
            "bogus": [{"title": "X", "text": "y"}],
        }
    )
    assert pack.areas() == ["architecture", "components", "constraints", "general"]
    assert [(i.id, i.kind, i.text) for i in pack.items("architecture")] == [("R01", "rule", "no sql in ui"), ("L01", "layer", "UI")]
    cache = pack.items("component")[0]
    assert (cache.id, cache.text, cache.meta["path"], cache.meta["aliases"], cache.note) == ("cache-helper", "ttl cache", "src/cache.py", ["cache"], "Cache helper")
    assert pack.items("component")[1].id == "explicit"
    assert pack.items("constraint")[0].line == 3 and pack.items("constraint")[0].id == "C01"
    assert pack.resolve("cache").title == "Cache helper"
    assert any("bogus" in w for w in pack.warnings)
    assert pack.notes("architecture")[0].resolved_by == "dict:architecture"
    assert not pack.has("decisions")


# ------------------------------------------------------- selection, budgets


def test_selection_ranking_and_caps(vault):
    ranked = vault.select_items("component", "invoice rendering to PDF and HTML templates", 3)
    assert ranked[0].id == "invoice-renderer" and len(ranked) == 3
    assert [i.id for i in vault.select_items("component", "", None)] == [i.id for i in vault.items("component")]
    assert len(vault.select_items("component", "", 2)) == 2
    notes = vault.select_notes("decisions", "server rendered invoices pdf", 1)
    assert notes[0].title == "ADR 0002: Server-rendered invoices"
    small = ContextPack.load(FIXTURES / "vault", project="ledger", max_items=2)
    fragment = small.slice({"components", "decisions", "architecture"}, "cache in front of postgres store")
    assert [c["id"] for c in fragment["components"]] == ["cache-helper", "postgres-store"]
    assert len(fragment["decisions"]) == 2 and len(fragment["architecture"]["rules"]) == 2
    assert len(small.slice({"components"}, "", all_items=True)["components"]) == 8
    assert small.last_evidence["components"]["items"] == [i.id for i in small.items("component")]


def test_slice_shapes_and_missing_area(vault):
    fragment = vault.slice({"architecture", "components", "decisions", "data", "interfaces", "glossary", "constraints", "conventions", "nope"}, "")
    assert set(fragment) == {"architecture", "components", "decisions", "data", "interfaces", "glossary", "constraints", "conventions"}
    arch = fragment["architecture"]
    assert arch["overview"].startswith("# Ledger architecture") and arch["overview"].count("# Ledger architecture") == 1
    assert {"id", "text", "note", "line"} == set(arch["rules"][0]) and arch["rules"][0]["note"] == "architecture.md"
    assert [l["name"] for l in arch["layers"]] == ["Interface layer", "Service layer", "Storage layer", "Integration layer"]
    assert set(fragment["components"][0]) == {"id", "name", "path", "provides", "interface", "aliases"}
    assert set(fragment["decisions"][0]) == {"id", "title", "status", "decision", "alternatives"}
    assert set(fragment["data"]) == {"overview"} and "Entries are immutable" in fragment["data"]["overview"]
    assert fragment["constraints"][0] == {"id": "C01", "text": vault.items("constraint")[0].text, "note": "constraints.md"}
    assert fragment["conventions"][0]["applies_to"] == ["*.py"] and "applies_to" not in fragment["conventions"][4]
    assert not vault.has("nope") and "nope" not in fragment
    empty = ContextPack.from_dict({})
    assert empty.slice({"architecture"}, "x") == {} and not empty.has("architecture")


def test_budget_truncation(tmp_path):
    write(tmp_path, "big.md", "# Big\n\n#jevgate/data\n\n" + ("lorem ipsum dolor sit amet. " * 200))
    write(tmp_path, "conv.md", "---\ntags: [jevgate/conventions]\n---\n# C\n\n" + "".join(f"- convention number {n} with some padding text #jevgate/convention\n" for n in range(40)))
    pack = ContextPack.load(tmp_path, context_budget=300)
    assert any("exceeds context_budget" in w for w in pack.warnings)
    fragment = pack.slice({"data", "conventions"}, "")
    overview = fragment["data"]["overview"]
    assert overview.endswith("[... truncated ...]") and fragment["data"]["truncated"] is True
    assert pack.last_evidence["data"]["truncated"] and pack.last_evidence["data"]["tokens"] <= 300 + 8
    assert 0 < len(fragment["conventions"]) < 40 and pack.last_evidence["conventions"]["truncated"]
    assert pack.last_evidence["conventions"]["tokens"] <= 300
    roomy = ContextPack.load(tmp_path, context_budget=8000).slice({"data"}, "")["data"]
    assert "truncated" not in roomy


# ---------------------------------------------------------- fixture vaults


def test_fixture_vault_shape(vault):
    assert vault.areas() == ["architecture", "components", "decisions", "data", "interfaces", "constraints", "conventions", "glossary"]
    assert [i.id for i in vault.items("rule")] == [f"R0{n}" for n in range(1, 7)]
    assert [i.id for i in vault.items("layer")] == ["L01", "L02", "L03", "L04"]
    assert len(vault.items("component")) == 8 and len(vault.items("decision")) == 3
    assert [i.id for i in vault.items("constraint")] == ["C01", "C02", "C03", "C04"]
    conventions = vault.items("convention")
    assert [i.id for i in conventions] == ["V01", "V02", "V03", "V04", "V05"]
    assert [i.meta["applies_to"] for i in conventions] == [["*.py"], ["*.py"], [], [], []]
    assert vault.warnings == ["architecture.md: unresolved wikilink [[Billing engine]]"]
    skipped = {n.path: n.reason for n in vault.all_notes() if n.ignored}
    assert skipped == {"other-project.md": "project:other (loading project ledger)", "scratch.md": "tag:#jevgate/ignore"}
    assert all("Warehouse" not in r.text and "Old rule" not in r.text for r in vault.items("rule"))
    assert "(draft)" not in vault.notes("architecture")[0].body
    event_bus = next(i for i in vault.items("component") if i.id == "event-bus")
    assert event_bus.meta["provides"].startswith("An in-process publish/subscribe bus") and "publish(topic, payload)" in event_bus.meta["interface"]
    cache = next(n for n in vault.notes("components") if n.title == "Cache helper")
    assert cache.kind_by == "tag:#jevgate/component" and cache.resolved_by == "tag:#jevgate/component"
    adr = next(i for i in vault.items("decision") if i.id == "adr-0001-postgres-over-sqlite")
    assert adr.meta["status"] == "accepted" and len(adr.meta["alternatives"]) == 2 and "[[" not in adr.meta["alternatives"][1]
    interfaces = vault.notes("interfaces")[0]
    assert interfaces.project == "ledger"
    assert "interfaces" not in ContextPack.load(FIXTURES / "vault").areas()
    assert len(vault.index()) == 17 and vault.index()[0]["area"] == "architecture"


def test_tagged_untagged_and_json_packs_are_equivalent(vault):
    untagged = ContextPack.load(FIXTURES / "vault-untagged", project="ledger", areas=UNTAGGED_AREAS)
    from_json = ContextPack.from_dict(json.loads((FIXTURES / "context-pack.json").read_text()))
    assert item_key(untagged) == item_key(vault)
    assert item_key(from_json) == item_key(vault)
    assert untagged.areas() == vault.areas() == from_json.areas()
    for area in vault.areas():
        if area != "conventions":  # the untagged vault splits conventions over two notes to express applies_to
            assert sorted(n.title for n in untagged.notes(area)) == sorted(n.title for n in vault.notes(area))
    assert len(untagged.notes("conventions")) == 2 and len(vault.notes("conventions")) == 1
    assert [n.resolved_by for n in untagged.notes("architecture")] == ["glob:arch/*.md"]
    assert {n.resolved_by for n in untagged.notes("components")} == {"folder:components"}
    assert {n.resolved_by for n in untagged.notes("decisions")} == {"folder:adr"}
    assert {n.resolved_by for n in untagged.notes("data")} == {"frontmatter:area"}
    assert untagged.warnings == ["arch/architecture.md: unresolved wikilink [[Billing engine]]"]
    without_glob = ContextPack.load(FIXTURES / "vault-untagged", project="ledger")
    assert not without_glob.has("architecture") and without_glob.items("rule") == []  # arch/ is general without the glob
    query = "Add a TTL cache in front of postgres store reads for invoice rendering"
    assert [i.id for i in untagged.select_items("component", query, 3)] == [i.id for i in vault.select_items("component", query, 3)]
    a, b = vault.slice({"architecture", "constraints"}, query), untagged.slice({"architecture", "constraints"}, query)
    assert [r["id"] for r in a["architecture"]["rules"]] == [r["id"] for r in b["architecture"]["rules"]]
    assert [c["text"] for c in a["constraints"]] == [c["text"] for c in b["constraints"]]


def test_dataclasses_are_plain():
    note = Note("t", "p.md", "data", "note", {}, "body", [], [], None, "tag:x", False)
    item = Item("R01", "rule", "text", "p.md", 3)
    assert note.aliases == [] and item.meta == {} and item.area == "architecture" and item.applies_to("anything")


# ------------------------------------------------------------------ the CLI


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    write(repo, "src/acme/__init__.py", '"""Acme: a widget service.\n\nMore lines."""\n')
    write(repo, "src/acme/cli.py", '"""Command-line entry point for acme."""\n\ndef main():\n    pass\n')
    write(repo, "src/acme/store.py", "x = 1\n")
    write(repo, "src/acme/tests/test_x.py", "")
    write(repo, "src/acme/ticket/__init__.py", '"""Ticket gate."""\n')
    write(repo, "CLAUDE.md", "# Rules\n\n- Never commit secrets to the repo.\n- Use four-space indentation everywhere.\n- short\n")
    return repo


def test_context_init_writes_tagged_skeleton_that_loads(tmp_path, capsys):
    repo = make_repo(tmp_path)
    assert run(["context", "init", "--repo", str(repo), "--out", "docs/context", "--project", "acme"]) == 0
    out = repo / "docs" / "context"
    names = sorted(p.relative_to(out).as_posix() for p in out.rglob("*.md"))
    assert names == [
        "architecture.md",
        "components/acme-cli.md",
        "components/acme-store.md",
        "components/acme-ticket.md",
        "components/acme.md",
        "constraints.md",
        "conventions.md",
        "data.md",
        "decisions/0001-template.md",
        "glossary.md",
        "interfaces.md",
    ]
    cli_note = (out / "components/acme-cli.md").read_text()
    assert "tags: [jevgate/component, jevgate/project/acme]" in cli_note
    assert 'path: "src/acme/cli.py"' in cli_note and 'provides: "Command-line entry point for acme."' in cli_note
    assert 'provides: "Acme: a widget service."' in (out / "components/acme.md").read_text()
    assert "- Never commit secrets to the repo. #jevgate/constraint" in (out / "constraints.md").read_text()
    assert "- Use four-space indentation everywhere. #jevgate/convention" in (out / "conventions.md").read_text()
    assert "#jevgate/layer" in (out / "architecture.md").read_text() and "#jevgate/rule" in (out / "architecture.md").read_text()

    pack = ContextPack.load(out, project="acme")
    assert pack.areas() == ["architecture", "components", "decisions", "data", "interfaces", "constraints", "conventions", "glossary"]
    assert pack.warnings == []
    assert {n.resolved_by for n in pack.notes("components")} == {"tag:#jevgate/component"}
    assert len(pack.items("component")) == 4 and len(pack.items("rule")) == 2 and len(pack.items("layer")) == 3
    assert next(i for i in pack.items("component") if i.id == "acme-cli").meta["path"] == "src/acme/cli.py"
    assert pack.items("decision")[0].meta["status"] == "proposed"
    assert all(n.project == "acme" for n in pack.notes("components"))
    assert ContextPack.load(out).notes("components") == []  # scoped to acme

    (out / "glossary.md").write_text("# Mine\n")
    assert run(["context", "init", "--repo", str(repo), "--out", "docs/context"]) == 0
    assert (out / "glossary.md").read_text() == "# Mine\n"
    assert "kept" in capsys.readouterr().out
    assert run(["context", "init", "--repo", str(repo), "--out", "docs/context", "--force"]) == 0
    assert (out / "glossary.md").read_text().startswith("---")
    assert run(["context", "init", "--repo", str(tmp_path / "missing")]) == 4


def test_context_init_without_packages(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    assert run(["context", "init", "--repo", str(repo), "--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "components" / "example.md").exists()
    assert "Nothing to seed" in (tmp_path / "out" / "constraints.md").read_text()


def test_context_show_why_mentions_every_resolution_rule(tmp_path, capsys, monkeypatch):
    vault_dir = tmp_path / "v"
    write(vault_dir, "arch.md", "---\ntags: [jevgate/architecture]\n---\n# Arch\n\n## Rules\n\n- tagged rule #jevgate/rule\n- heading rule\n")
    write(vault_dir, "data.md", "---\narea: data\n---\n# Data\n\nrows\n")
    write(vault_dir, "glob/iface.md", "# Iface\n\napi\n")
    write(vault_dir, "components/c.md", "---\npath: src/c.py\nprovides: does things\n---\n# C\n")
    write(vault_dir, "misc.md", "# Misc\n\nloose\n")
    write(vault_dir, "skip.md", "# Skip\n\n#jevgate/ignore\n")
    write(tmp_path, "jevgate.json", json.dumps({"context": {"dir": str(vault_dir), "areas": {"interfaces": ["glob/*.md"]}}}))
    monkeypatch.chdir(tmp_path)
    assert run(["context", "show", "--why"]) == 0
    out = capsys.readouterr().out
    for needle in ("tag:#jevgate/architecture", "frontmatter:area", "glob:glob/*.md", "folder:components", "default:general", "skipped: tag:#jevgate/ignore", "tag:#jevgate/rule", "heading:## Rules"):
        assert needle in out, needle
    assert "R01" in out and "R02" in out and "interfaces: 1 note(s)" in out

    assert run(["context", "show", "--context-dir", str(vault_dir), "--area", "architecture", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert list(report["areas"]) == ["architecture"] and report["areas"]["architecture"]["items"][0]["id"] == "R01"
    assert report["areas"]["architecture"]["slice_tokens"] > 0 and "explain" not in report

    assert run(["context", "show", "--context-dir", str(vault_dir), "--area", "decisions"]) == 0
    assert "decisions: missing" in capsys.readouterr().out
    assert run(["context", "show", "--context-dir", str(tmp_path / "nowhere")]) == 4


def test_context_show_with_draft_and_json_pack(tmp_path, capsys):
    draft = write(tmp_path, "draft.md", "# Cache invoices\n\n## Why\n\nslow\n\n## What\n\nAdd a TTL cache for invoice rendering.\n\n## Acceptance\n\n- fast\n\n## Context\n\n- uses Cache helper\n")
    config = write(tmp_path, "cfg.json", json.dumps({"context": {"max_items": 2, "follow_links": 0}}))
    assert run(["context", "show", "--context-dir", str(FIXTURES / "vault"), "--config", str(config), "--project", "ledger", "--area", "components", "--draft", str(draft), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    items = report["areas"]["components"]["items"]
    assert len(items) == 8 and sum(i["sent"] for i in items) == 2
    assert set(report["areas"]["components"]["sent"]["items"]) == {"cache-helper", "invoice-renderer"}
    assert report["warnings"] == ["architecture.md: unresolved wikilink [[Billing engine]]"]
    assert run(["context", "show", "--context-json", str(FIXTURES / "context-pack.json"), "--area", "decisions"]) == 0
    text = capsys.readouterr().out
    assert "adr-0001-postgres-over-sqlite" in text and "decisions: 3 note(s), 3 item(s)" in text
