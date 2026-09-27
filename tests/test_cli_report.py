"""The shared CLI helpers and the ``report`` subcommand. Gate subcommands are tested with their gates."""

from __future__ import annotations

import argparse
import json

import pytest

from jevgate import cli
from jevgate.client import ClientError
from jevgate.config import ConfigError
from jevgate.report import Finding, Report
from jevgate.runs import Run


def stored_run(tmp_path):
    run = Run(tmp_path, "r1")
    run.write(Report(gate="ticket", outcome="gather", exit_code=5, findings=[Finding(id="jev:a", severity="unclear")]))
    run.write(Report(gate="ticket", outcome="ready", exit_code=0))
    return run


def test_report_prints_latest_markdown(tmp_path, capsys):
    run = stored_run(tmp_path)
    assert cli.main(["report", str(run.dir)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# jevgate ticket — READY (round 2, exit 0)")


def test_report_round_and_json(tmp_path, capsys):
    run = stored_run(tmp_path)
    assert cli.main(["report", str(run.dir), "--round", "1"]) == 0
    assert "GATHER (round 1, exit 5)" in capsys.readouterr().out
    assert cli.main(["report", str(run.dir), "--round", "1", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outcome"] == "gather" and data["findings"][0]["id"] == "jev:a"


def test_report_regenerates_markdown_when_md_missing(tmp_path, capsys):
    run = stored_run(tmp_path)
    (run.dir / "round-2.md").unlink()
    assert cli.main(["report", str(run.dir)]) == 0
    assert "READY (round 2" in capsys.readouterr().out


def test_report_errors_exit_4(tmp_path, capsys):
    assert cli.main(["report", str(tmp_path / "nowhere")]) == 4
    assert "no rounds" in capsys.readouterr().err
    run = stored_run(tmp_path)
    assert cli.main(["report", str(run.dir), "--round", "7"]) == 4
    assert "round-7.json" in capsys.readouterr().err


def test_no_command_and_version(capsys):
    assert cli.main([]) == 4
    assert "usage:" in capsys.readouterr().out
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0
    assert capsys.readouterr().out.startswith("jevgate ")


@pytest.mark.parametrize("error", [ClientError("no key"), ConfigError("bad key 'x'"), FileNotFoundError("gone")])
def test_main_reports_known_errors(monkeypatch, capsys, error):
    def build():
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        boom = sub.add_parser("boom")
        boom.set_defaults(func=lambda args: (_ for _ in ()).throw(error))
        return parser
    monkeypatch.setattr(cli, "build_parser", build)
    assert cli.main(["boom"]) == 4
    assert str(error) in capsys.readouterr().err


def test_add_common_and_make_client(tmp_path):
    parser = cli.add_common(argparse.ArgumentParser())
    args = parser.parse_args(["--no-ai", "--no-cache", "--threshold", "a=0.5", "--threshold", "b=0.6",
                              "--context-dir", "notes", "--all-items", "--run-dir", str(tmp_path / "run")])
    assert args.threshold == ["a=0.5", "b=0.6"] and args.json is False
    cfg = cli.load_config(args, repo=tmp_path)
    assert cfg.thresholds == {"a": 0.5, "b": 0.6} and cfg.context["dir"] == "notes" and cfg.max_items is None
    run = cli.make_run(args, "ticket")
    assert run.dir == tmp_path / "run"
    client = cli.make_client(args, cfg, run)
    assert client.enabled is False and client.use_cache is False
    assert client.audit_path == run.dir / "requests.jsonl" and client.workers == cfg.workers


def test_emit(capsys):
    report = Report(gate="delivery", outcome="unproven", exit_code=2)
    assert cli.emit(report, argparse.Namespace(json=False)) == 2
    assert capsys.readouterr().out.startswith("# jevgate delivery — UNPROVEN")
    assert cli.emit(report, argparse.Namespace(json=True)) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2
    assert cli.TICKET_EXITS["gather"] == 5 and cli.DELIVERY_EXITS["accept"] == 0


def _fake_module(tmp_path, monkeypatch, name: str, body: str) -> None:
    (tmp_path / f"{name}.py").write_text(body)
    monkeypatch.syspath_prepend(str(tmp_path))


def test_build_parser_skips_only_absent_registry_modules(tmp_path, monkeypatch):
    # an absent registry module is skipped and the rest still register
    monkeypatch.setattr(cli, "REGISTRY", ("jevgate_missing_gate_xyz", "jevgate.ticket.cli"))
    assert cli.build_parser().parse_args(["ticket", "init", "--json"]).command == "ticket"

    # an ImportError raised inside a present module propagates
    _fake_module(tmp_path, monkeypatch, "jevgate_broken_gate", "raise ImportError('broken inside the module')\n")
    monkeypatch.setattr(cli, "REGISTRY", ("jevgate_broken_gate",))
    with pytest.raises(ImportError, match="broken inside"):
        cli.build_parser()

    # so does a missing dependency of a present module
    _fake_module(tmp_path, monkeypatch, "jevgate_needy_gate", "import jevgate_no_such_dependency_xyz\n")
    monkeypatch.setattr(cli, "REGISTRY", ("jevgate_needy_gate",))
    with pytest.raises(ModuleNotFoundError, match="jevgate_no_such_dependency_xyz"):
        cli.build_parser()
