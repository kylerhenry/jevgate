"""``jevgate ticket init|render|check`` through ``jevgate.cli.main``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jevgate import cli
from jevgate.config import Config
from jevgate.context import ContextPack
from jevgate.markdown import parse_ticket
from jevgate.ticket import cli as ticket_cli
from jevgate.ticket.schema import TEMPLATE_MD, load_ticket

from test_ticket_gate import FIXTURES, TICKETS, prime

VAULT = str(FIXTURES / "vault")


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def common(tmp_path: Path, *extra: str) -> list[str]:
    return ["--context-dir", VAULT, "--project", "ledger", "--run-dir", str(tmp_path / "run"), "--no-cache", *extra]


def test_init_writes_template_and_refuses_overwrite(cwd, capsys):
    assert cli.main(["ticket", "init"]) == 0
    text = (cwd / "ticket.md").read_text()
    assert text == TEMPLATE_MD and "## Prior answers" in text
    parsed = parse_ticket(text)
    assert parsed["title"].startswith("<Title") and len(parsed["acceptance"]) == 2 and parsed["prior_answers"] == []
    assert cli.main(["ticket", "init"]) == 4
    assert "exists" in capsys.readouterr().err
    assert cli.main(["ticket", "init", "--force"]) == 0
    assert cli.main(["ticket", "init", "--json", "--out", "t.json"]) == 0
    data = json.loads((cwd / "t.json").read_text())
    assert set(data) == {"title", "why", "what", "acceptance", "context_bullets", "prior_answers", "labels", "estimate"}


def test_render_prints_or_writes(cwd, capsys):
    draft = str(TICKETS / "ready-01" / "draft.md")
    assert cli.main(["ticket", "render", draft]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Add a `--dry-run` flag") and "## Acceptance" in out and "## Context" in out
    assert cli.main(["ticket", "render", draft, "--out", "out.md"]) == 0
    assert parse_ticket((cwd / "out.md").read_text())["acceptance"] == load_ticket(draft)["acceptance"]
    assert cli.main(["ticket", "render", str(TICKETS / "missing-why-01" / "draft.md")]) == 0
    assert "why is missing" in capsys.readouterr().err


def test_render_accepts_json_drafts(cwd, capsys):
    (cwd / "d.json").write_text(json.dumps({"title": "T", "why": "W", "what": "X", "acceptance": ["a", "a"], "context": ["c"]}))
    assert cli.main(["ticket", "render", "d.json"]) == 0
    out = capsys.readouterr().out
    assert out.count("- a") == 1 and "- c" in out


def test_check_rule_fail_exits_1_without_calls(canned, tmp_path, capsys):
    code = cli.main(["ticket", "check", str(TICKETS / "missing-why-01" / "draft.md"), *common(tmp_path)])
    assert code == 1 and canned.calls == 0
    assert "REVISE" in capsys.readouterr().out
    assert (tmp_path / "run" / "round-1.json").is_file()


def test_check_ready_exits_0_and_json(canned, tmp_path, capsys):
    draft = TICKETS / "ready-01" / "draft.md"
    prime(canned, load_ticket(draft), ContextPack.load(FIXTURES / "vault", project="ledger"), Config())
    assert cli.main(["ticket", "check", str(draft), *common(tmp_path, "--json")]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outcome"] == "ready" and data["exit_code"] == 0 and data["gate"] == "ticket"
    assert canned.calls == 7


def test_check_ask_exits_2_and_threshold_flag(canned, tmp_path, capsys):
    draft = TICKETS / "ready-01" / "draft.md"
    prime(canned, load_ticket(draft), ContextPack.load(FIXTURES / "vault", project="ledger"), Config())
    canned.answers["design_unambiguous"] = {"noul": 0.8}
    canned.answers["bank:B03"] = {"noul": 0.9}
    assert cli.main(["ticket", "check", str(draft), *common(tmp_path, "--json")]) == 2
    assert json.loads(capsys.readouterr().out)["asks"][0]["id"] == "B03"
    assert cli.main(["ticket", "check", str(draft), *common(tmp_path, "--json", "--threshold", "design_unambiguous=0.7")]) == 0
    assert json.loads(capsys.readouterr().out)["round"] == 2


def test_check_no_ai_exits_3_and_missing_area_exits_5(canned, tmp_path, capsys):
    draft = str(TICKETS / "ready-01" / "draft.md")
    assert cli.main(["ticket", "check", draft, *common(tmp_path, "--no-ai")]) == 3
    assert canned.calls == 0
    args = ["--context-dir", str(FIXTURES / "vault-noarch"), "--project", "ledger", "--run-dir", str(tmp_path / "run2"), "--no-cache", "--no-ai"]
    assert cli.main(["ticket", "check", draft, *args]) == 5
    assert "architecture" in capsys.readouterr().out


def test_check_without_pack_warns_and_gathers(canned, cwd, capsys):
    draft = str(TICKETS / "ready-01" / "draft.md")
    assert cli.main(["ticket", "check", draft, "--run-dir", str(cwd / "run"), "--no-cache", "--no-ai"]) == 5
    captured = capsys.readouterr()
    assert "without a context pack" in captured.err and "**architecture**" in captured.out


def test_check_errors_exit_4(canned, tmp_path, capsys):
    assert cli.main(["ticket", "check", *common(tmp_path)]) == 4
    assert "give a draft" in capsys.readouterr().err
    assert cli.main(["ticket", "check", str(tmp_path / "nope.md"), *common(tmp_path)]) == 4
    assert "not found" in capsys.readouterr().err
    draft = str(TICKETS / "ready-01" / "draft.md")
    assert cli.main(["ticket", "check", draft, "--context-dir", str(tmp_path / "nowhere"), "--run-dir", str(tmp_path / "run"), "--no-ai"]) == 4
    assert cli.main(["ticket"]) == 4


def test_check_from_linear(canned, tmp_path, monkeypatch, capsys):
    description = (TICKETS / "ready-01" / "draft.md").read_text().split("\n", 1)[1]

    class FakeLinear:
        def __init__(self, key):
            assert key == "lin_key_0123456789"

        def get_issue(self, identifier):
            assert identifier == "DIY-17"
            return {"identifier": "DIY-17", "title": "Add a `--dry-run` flag to `ledger import`", "description": description, "team_key": "DIY", "url": "u"}

    monkeypatch.setattr(ticket_cli, "Linear", FakeLinear)
    monkeypatch.setattr(ticket_cli, "load_linear_key", lambda: "lin_key_0123456789")
    assert cli.main(["ticket", "check", "--from-linear", "DIY-17", *common(tmp_path, "--no-ai", "--json")]) == 3
    data = json.loads(capsys.readouterr().out)
    assert data["outcome"] == "uncertain" and "ac_testable:4" in data["readings"]
