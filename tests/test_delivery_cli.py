"""``jevgate delivery check``: exit codes on a temporary git repository and on
``--diff-file``; argparse errors for missing sources."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from jevgate import cli
from jevgate import client as client_module

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
VAULT = FIXTURES / "vault"
ACCEPT = FIXTURES / "deliveries" / "accept-01"

TICKET = """# Greet by name

## Why

The CLI greets everyone as "world".

## What

`greet(name)` in `src/app.py` returns `Hello, <name>!`.

## Acceptance

- `greet("Ada")` returns `Hello, Ada!`.
- `greet("")` raises `ValueError`.

## Context

- Nothing else changes.
"""

PYTEST_OK = """============================= test session starts ==============================
collected 2 items

tests/test_app.py::test_greet_by_name PASSED                             [ 50%]
tests/test_app.py::test_greet_empty_raises PASSED                        [100%]

============================== 2 passed in 0.02s ===============================
"""

PYTEST_FAIL = PYTEST_OK.replace("test_greet_empty_raises PASSED ", "test_greet_empty_raises FAILED ").replace(
    "============================== 2 passed in 0.02s ===============================",
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_app.py::test_greet_empty_raises - Failed: DID NOT RAISE\n"
    "========================= 1 failed, 1 passed in 0.03s ==========================",
)


def answer(question: dict, value, p: float = 0.95) -> dict:
    qtype = question["type"]
    if qtype == "noul":
        return {"type": "noul", "noul": p}
    criteria = question["criteria"]
    keys = list(criteria) if isinstance(criteria, dict) else [str(i) for i in range(len(criteria))]
    rest = (1 - p) / max(len(keys) - 1, 1)
    probabilities = {k: (p if k == str(value) else rest) for k in keys}
    if qtype == "choice":
        return {"type": "choice", "choice": str(value), "probabilities": probabilities, "confidence": p}
    return {"type": "score", "score": int(value), "probabilities": probabilities, "confidence": p}


GOOD = {
    "dup": "unrelated", "over_engineered": "no", "correctness_defect": "no_defect_visible", "arch_rule": "complies",
    "convention": "follows", "edge_cases": 2, "touches_ac": None, "ac_met": "met", "ac_proven": "passing_test_covers",
    "scope_creep": "none", "tests_exercise_change": "covers",
}


class Scripted:
    """The canned transport answering every question well unless an override
    (by gate id or family) says otherwise; ``bodies`` are the recorded requests."""

    def __init__(self, canned) -> None:
        self.canned = canned
        self.overrides: dict[str, object] = {}
        self._lock = threading.Lock()

    def __setitem__(self, key: str, value) -> None:
        self.overrides[key] = value

    def __delitem__(self, key: str) -> None:
        del self.overrides[key]

    @property
    def bodies(self) -> list[dict]:
        return self.canned.bodies

    def __call__(self, body: dict, key: str, **kwargs) -> dict:
        with self._lock:
            for qid, question in body["questions"].items():
                family = qid.split(":", 1)[0]
                value = self.overrides.get(qid, self.overrides.get(family, GOOD[family]))
                self.canned.answers[qid] = answer(question, value)
            return self.canned(body, key, **kwargs)


@pytest.fixture
def scripted(canned, monkeypatch) -> Scripted:
    fake = Scripted(canned)
    monkeypatch.setattr(client_module, "call_api", fake)
    return fake


@pytest.fixture(autouse=True)
def _cache_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ, "HOME": str(repo), "GIT_CONFIG_GLOBAL": str(repo / "no-global-config"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=env).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if not shutil.which("git"):
        pytest.skip("git not installed")
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "symbolic-ref", "HEAD", "refs/heads/main")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text('def greet() -> str:\n    """Greet."""\n    return "Hello, world!"\n')
    (root / "ticket.md").write_text(TICKET)
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "init")
    (root / "src" / "app.py").write_text(
        'def greet(name: str) -> str:\n    """Greet ``name``."""\n    if not name:\n        raise ValueError("name required")\n'
        '    return f"Hello, {name}!"\n'
    )
    return root


def base_args(repo: Path, tmp_path: Path, *extra: str) -> list[str]:
    return ["delivery", "check", "--ticket", str(repo / "ticket.md"), "--repo", str(repo), "--context-dir", str(VAULT),
            "--run-dir", str(tmp_path / "run"), *extra]


def test_working_tree_accept_and_report_written(scripted, repo, tmp_path, capsys):
    log = tmp_path / "pytest.log"
    log.write_text(PYTEST_OK)
    code = cli.main(base_args(repo, tmp_path, "--base", "HEAD", "--test-log", str(log)))
    out = capsys.readouterr().out
    assert code == 0 and out.startswith("# jevgate delivery — ACCEPT (round 1, exit 0)")
    assert (tmp_path / "run" / "round-1.json").is_file()
    data = json.loads((tmp_path / "run" / "round-1.json").read_text())
    assert data["gate"] == "delivery" and data["evidence"]["files"][0]["path"] == "src/app.py"


def test_untracked_files_warn_and_head_ref(scripted, repo, tmp_path, capsys):
    (repo / "src" / "extra.py").write_text("x = 1\n")
    log = tmp_path / "pytest.log"
    log.write_text(PYTEST_OK)
    assert cli.main(base_args(repo, tmp_path, "--base", "HEAD", "--test-log", str(log), "--json")) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(f["id"] == "rule:untracked_files" for f in data["findings"])
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "greet by name")
    assert cli.main(base_args(repo, tmp_path, "--base", "HEAD~1", "--head", "HEAD", "--test-log", str(log), "--json")) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["round"] == 2 and not any(f["id"] == "rule:untracked_files" for f in data["findings"])
    assert {f["path"] for f in data["evidence"]["files"]} == {"src/app.py", "src/extra.py"}


def test_exit_codes_revise_unproven_gather_uncertain(scripted, repo, tmp_path, capsys):
    log_ok = tmp_path / "ok.log"
    log_ok.write_text(PYTEST_OK)
    log_bad = tmp_path / "bad.log"
    log_bad.write_text(PYTEST_FAIL)
    # 1: failing tests (rule) — no API call
    assert cli.main(base_args(repo, tmp_path, "--base", "HEAD", "--test-log", str(log_bad))) == 1
    assert "rule:tests_failing" in capsys.readouterr().out
    # 2: no logs
    assert cli.main(base_args(repo, tmp_path, "--base", "HEAD")) == 2
    assert "UNPROVEN" in capsys.readouterr().out
    # 0 with --no-tests-ok
    assert cli.main(base_args(repo, tmp_path, "--base", "HEAD", "--no-tests-ok")) == 0
    capsys.readouterr()
    # 1: an acceptance criterion not met
    scripted["ac_met:2"] = "not_met"
    assert cli.main(base_args(repo, tmp_path, "--base", "HEAD", "--test-log", str(log_ok), "--no-cache")) == 1
    assert "jev:ac_met:2" in capsys.readouterr().out
    # 5: unclear criterion → gather with a --files hint
    scripted["ac_met:2"] = "unclear"
    assert cli.main(base_args(repo, tmp_path, "--base", "HEAD", "--test-log", str(log_ok), "--no-cache")) == 5
    assert "pass --files" in capsys.readouterr().out
    del scripted["ac_met:2"]
    # 3: --no-ai
    assert cli.main(base_args(repo, tmp_path, "--base", "HEAD", "--test-log", str(log_ok), "--no-ai")) == 3
    assert "UNCERTAIN" in capsys.readouterr().out


def test_files_excerpt_and_diff_file(scripted, repo, tmp_path, capsys):
    log = tmp_path / "pytest.log"
    log.write_text(PYTEST_OK)
    code = cli.main(base_args(repo, tmp_path, "--base", "HEAD", "--test-log", str(log), "--files", "src/app.py:1-3", "--json"))
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["evidence"]["files_after"] == [{"path": "src/app.py", "range": [1, 3]}]
    body = [b for b in scripted.bodies if "diff" in b["state"]][-1]
    assert body["state"]["files_after"][0]["text"].startswith("def greet(name: str)")

    diff_file = tmp_path / "change.patch"
    diff_file.write_text((ACCEPT / "change.patch").read_text())
    args = ["delivery", "check", "--ticket", str(ACCEPT / "ticket.md"), "--diff-file", str(diff_file),
            "--test-log", str(ACCEPT / "tests" / "pytest.log"), "--context-dir", str(VAULT),
            "--run-dir", str(tmp_path / "run2"), "--json"]
    assert cli.main(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["outcome"] == "accept" and len(data["evidence"]["files"]) == 4


def test_json_ticket_and_project_flag(scripted, repo, tmp_path, capsys):
    ticket = {"title": "Greet by name", "why": "w", "what": "x", "acceptance": ["`greet(\"Ada\")` returns `Hello, Ada!`."]}
    (repo / "ticket.json").write_text(json.dumps(ticket))
    log = tmp_path / "pytest.log"
    log.write_text(PYTEST_OK)
    args = ["delivery", "check", "--ticket", str(repo / "ticket.json"), "--repo", str(repo), "--base", "HEAD",
            "--test-log", str(log), "--context-dir", str(VAULT), "--project", "ledger", "--run-dir", str(tmp_path / "run"), "--json"]
    assert cli.main(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data["readings"]) >= {"ac_met:1", "ac_proven:1"} and "ac_met:2" not in data["readings"]


def test_errors_exit_4(scripted, repo, tmp_path, capsys):
    assert cli.main(base_args(repo, tmp_path, "--base", "HEAD", "--test-log", str(tmp_path / "missing.log"))) == 4
    assert "test log not found" in capsys.readouterr().err
    args = ["delivery", "check", "--ticket", str(tmp_path / "nope.md"), "--repo", str(repo), "--base", "HEAD",
            "--run-dir", str(tmp_path / "run")]
    assert cli.main(args) == 4
    assert "ticket not found" in capsys.readouterr().err
    assert cli.main(base_args(repo, tmp_path, "--base", "nonexistent-ref")) == 4
    assert "git diff" in capsys.readouterr().err
    (repo / "empty.md").write_text("# No acceptance\n\n## Why\n\nw\n\n## What\n\nx\n")
    args = ["delivery", "check", "--ticket", str(repo / "empty.md"), "--repo", str(repo), "--base", "HEAD", "--run-dir", str(tmp_path / "run")]
    assert cli.main(args) == 4
    assert "no acceptance bullets" in capsys.readouterr().err
    args = base_args(repo, tmp_path, "--base", "HEAD")
    args[args.index(str(VAULT))] = str(tmp_path / "no-such-vault")
    assert cli.main(args) == 4
    assert "context dir not found" in capsys.readouterr().err


def test_change_over_chunk_cap_exits_4(scripted, repo, tmp_path, capsys):
    # 70 blocks of 12 wide lines; one edit per block gives 70 separate hunks, far more than 8 chunks at a 2000-token diff budget
    blocks = [f"# section {b:03d} " + "-" * 100 + "\n" + "".join(f"line {b:03d}-{i:02d} " + "x" * 100 + "\n" for i in range(11)) for b in range(70)]
    (repo / "src" / "wide.py").write_text("".join(blocks))
    _git(repo, "add", "src/wide.py")
    _git(repo, "commit", "-q", "-m", "wide")
    (repo / "src" / "wide.py").write_text("".join(b.replace("line ", "edit ", 1) for b in blocks))
    (repo / "jevgate.json").write_text(json.dumps({"state_budget": 2000}))
    log = tmp_path / "pytest.log"
    log.write_text(PYTEST_OK)
    code = cli.main(base_args(repo, tmp_path, "--base", "HEAD", "--test-log", str(log)))
    err = capsys.readouterr().err
    assert code == 4
    assert "change too large" in err and " chunks of 2000 tokens" in err and "split the change" in err
    assert int(err.split("change too large: ")[1].split()[0]) > 8
    audit = tmp_path / "run" / "requests.jsonl"
    assert not audit.exists() or audit.read_text() == ""
    assert not scripted.bodies


def test_no_context_pack_warns_and_runs(scripted, repo, tmp_path, capsys):
    log = tmp_path / "pytest.log"
    log.write_text(PYTEST_OK)
    args = ["delivery", "check", "--ticket", str(repo / "ticket.md"), "--repo", str(repo), "--base", "HEAD",
            "--test-log", str(log), "--run-dir", str(tmp_path / "run"), "--json"]
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    assert "no context pack" in captured.err
    data = json.loads(captured.out)
    assert {f["id"] for f in data["findings"]} >= {"rule:missing_context_area:architecture"}


def test_argparse_errors(repo, tmp_path, capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(["delivery", "check", "--ticket", str(repo / "ticket.md"), "--repo", str(repo)])
    assert info.value.code == 2 and "--base" in capsys.readouterr().err
    with pytest.raises(SystemExit) as info:
        cli.main(["delivery", "check", "--repo", str(repo), "--base", "HEAD"])
    assert info.value.code == 2 and "--ticket" in capsys.readouterr().err
    with pytest.raises(SystemExit) as info:
        cli.main(["delivery", "check", "--ticket", str(repo / "ticket.md"), "--diff-file", "x.patch", "--head", "HEAD"])
    assert info.value.code == 2 and "--head requires --base" in capsys.readouterr().err
    assert cli.main(["delivery"]) == 4
    assert "usage:" in capsys.readouterr().out
