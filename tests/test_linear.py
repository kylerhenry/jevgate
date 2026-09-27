"""Linear adapter: transport (raw Authorization, retry, GraphQL errors), issue
mapping, ticket round-trips and the ``linear`` CLI. Nothing touches the network:
``jevgate.linear._open`` is replaced by a scripted transport."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from jevgate import cli
from jevgate import linear as ln

TEST_KEY = "lin_api_TESTKEY0123456789abcdef"

TEMPLATE_DESCRIPTION = """## Why

The panel shows stale disk numbers after a remount.

## What

### Collector

Publish disk usage every 30s from the collector, not from the panel.

### Panel

Subscribe to `carbon/disk` and render the retained value.

## Acceptance

- Disk tile updates within 60s of a remount
- No polling from the panel process

## Context

- Collector lives in mqtt/host/collector.py
- Panel renders tiles from retained topics

### Prior answers

- (B03) **Should the panel keep polling as a fallback?** No, retained topics only.
"""

ISSUE_NODE = {
    "id": "uuid-issue-17",
    "identifier": "DIY-17",
    "title": "Disk tile via MQTT",
    "description": TEMPLATE_DESCRIPTION,
    "url": "https://linear.app/x/issue/DIY-17",
    "branchName": "khenry/diy-17-disk-tile-via-mqtt",
    "state": {"name": "Todo"},
    "team": {"id": "uuid-team-diy", "key": "DIY"},
    "labels": {"nodes": [{"name": "panel"}, {"name": "mqtt"}]},
    "project": {"name": "Status panel"},
}


def http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(ln.ENDPOINT, code, f"HTTP {code}", {}, io.BytesIO(body.encode()))


class Transport:
    """Scripted ``_open``: records requests, pops one outcome per call."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return json.dumps(outcome).encode()

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].data.decode("utf-8"))


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(ln, "_sleep", slept.append)
    return slept


@pytest.fixture
def no_key(monkeypatch, tmp_path):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.setattr(ln, "KEY_FILE", tmp_path / "absent")


@pytest.fixture
def no_network(monkeypatch):
    def refuse(request, timeout):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(ln, "_open", refuse)


# -- key loading ---------------------------------------------------------------


def test_key_from_env(monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", f" {TEST_KEY} ")
    assert ln.load_linear_key() == TEST_KEY


def test_key_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    key_file = tmp_path / "api-key"
    key_file.write_text("abcdefghijklmnopqrstuvwxyz_-01\nignored second line\n")
    monkeypatch.setattr(ln, "KEY_FILE", key_file)
    assert ln.load_linear_key() == "abcdefghijklmnopqrstuvwxyz_-01"


def test_missing_key(no_key):
    with pytest.raises(ln.LinearError, match="LINEAR_API_KEY"):
        ln.load_linear_key()


def test_malformed_key_rejected_without_echo(monkeypatch):
    bad = "short key with spaces!"
    monkeypatch.setenv("LINEAR_API_KEY", bad)
    with pytest.raises(ln.LinearError) as info:
        ln.load_linear_key()
    assert bad not in str(info.value)
    assert "spaces" not in str(info.value)


# -- transport -----------------------------------------------------------------


def test_graphql_body_and_headers(monkeypatch):
    transport = Transport([{"data": {"ok": True}}])
    monkeypatch.setattr(ln, "_open", transport)
    data = ln.Linear(TEST_KEY).graphql("query { viewer { id } }", {"a": 1})
    assert data == {"ok": True}
    request = transport.requests[0]
    assert request.full_url == ln.ENDPOINT
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == TEST_KEY  # raw, no Bearer
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("User-agent").startswith("jevgate/")
    assert transport.body() == {"query": "query { viewer { id } }", "variables": {"a": 1}}


def test_retry_429_then_success(monkeypatch, no_sleep):
    transport = Transport([http_error(429, "slow down"), http_error(503), {"data": {"x": 1}}])
    monkeypatch.setattr(ln, "_open", transport)
    assert ln.Linear(TEST_KEY).graphql("q") == {"x": 1}
    assert len(transport.requests) == 3
    assert no_sleep == [1, 2]


def test_retries_exhausted(monkeypatch, no_sleep):
    monkeypatch.setattr(ln, "_open", Transport([http_error(502)] * 4))
    with pytest.raises(ln.LinearError, match="HTTP 502.*after 4 attempts"):
        ln.Linear(TEST_KEY).graphql("q")
    assert no_sleep == [1, 2, 4]


def test_4xx_not_retried_and_key_not_echoed(monkeypatch, no_sleep):
    transport = Transport([http_error(401, '{"error":"unauthorized"}')])
    monkeypatch.setattr(ln, "_open", transport)
    with pytest.raises(ln.LinearError) as info:
        ln.Linear(TEST_KEY).graphql("q")
    assert "HTTP 401" in str(info.value) and "unauthorized" in str(info.value)
    assert TEST_KEY not in str(info.value)
    assert len(transport.requests) == 1
    assert no_sleep == []


def test_redirect_refused(monkeypatch, no_sleep):
    monkeypatch.setattr(ln, "_open", Transport([ln.RedirectRefused("Refusing HTTP 302 redirect from the Linear endpoint")]))
    with pytest.raises(ln.LinearError, match="redirect"):
        ln.Linear(TEST_KEY).graphql("q")
    assert no_sleep == []


def test_graphql_errors_raise(monkeypatch):
    payload = {"data": None, "errors": [{"message": "Entity not found"}, {"message": "Access denied"}]}
    monkeypatch.setattr(ln, "_open", Transport([payload]))
    with pytest.raises(ln.LinearError, match="Entity not found; Access denied"):
        ln.Linear(TEST_KEY).graphql("q")


# -- reads and writes ----------------------------------------------------------


def test_get_issue_mapping(monkeypatch):
    transport = Transport([{"data": {"issue": ISSUE_NODE}}])
    monkeypatch.setattr(ln, "_open", transport)
    issue = ln.Linear(TEST_KEY).get_issue("DIY-17")
    assert transport.body()["variables"] == {"id": "DIY-17"}
    assert issue == {
        "id": "uuid-issue-17",
        "identifier": "DIY-17",
        "title": "Disk tile via MQTT",
        "description": TEMPLATE_DESCRIPTION,
        "url": "https://linear.app/x/issue/DIY-17",
        "team_key": "DIY",
        "team_id": "uuid-team-diy",
        "state": "Todo",
        "labels": ["panel", "mqtt"],
        "project": "Status panel",
        "branch": "khenry/diy-17-disk-tile-via-mqtt",
    }


def test_get_issue_missing(monkeypatch):
    monkeypatch.setattr(ln, "_open", Transport([{"data": {"issue": None}}]))
    with pytest.raises(ln.LinearError, match="DIY-99 not found"):
        ln.Linear(TEST_KEY).get_issue("DIY-99")


def test_team_and_project_ids(monkeypatch):
    transport = Transport(
        [
            {"data": {"teams": {"nodes": [{"id": "uuid-team-diy", "key": "DIY"}]}}},
            {
                "data": {
                    "projects": {
                        "nodes": [
                            {"id": "p-other", "name": "Panel", "teams": {"nodes": [{"id": "uuid-team-x"}]}},
                            {"id": "p-diy", "name": "Panel", "teams": {"nodes": [{"id": "uuid-team-diy"}]}},
                        ]
                    }
                }
            },
            {"data": {"projects": {"nodes": []}}},
            {"data": {"teams": {"nodes": []}}},
        ]
    )
    monkeypatch.setattr(ln, "_open", transport)
    api = ln.Linear(TEST_KEY)
    assert api.team_id("DIY") == "uuid-team-diy"
    assert transport.body(0)["variables"] == {"key": "DIY"}
    assert api.project_id("Panel", "uuid-team-diy") == "p-diy"
    assert api.project_id("Nope") is None
    with pytest.raises(ln.LinearError, match="team ZZZ not found"):
        api.team_id("ZZZ")


def test_create_issue_variables(monkeypatch):
    created = {"success": True, "issue": {"id": "uuid-new", "identifier": "DIY-42", "url": "https://linear.app/x/issue/DIY-42"}}
    transport = Transport([{"data": {"issueCreate": created}}])
    monkeypatch.setattr(ln, "_open", transport)
    result = ln.Linear(TEST_KEY).create_issue("uuid-team-diy", "Title", "## Why\n\nbody\n", project_id="p-diy", label_ids=["l1"])
    assert result == {"identifier": "DIY-42", "url": "https://linear.app/x/issue/DIY-42", "id": "uuid-new"}
    body = transport.body()
    assert "issueCreate" in body["query"]
    assert body["variables"] == {
        "input": {"teamId": "uuid-team-diy", "title": "Title", "description": "## Why\n\nbody\n", "projectId": "p-diy", "labelIds": ["l1"]}
    }


def test_create_issue_failure(monkeypatch):
    monkeypatch.setattr(ln, "_open", Transport([{"data": {"issueCreate": {"success": False, "issue": None}}}]))
    with pytest.raises(ln.LinearError, match="issueCreate did not succeed"):
        ln.Linear(TEST_KEY).create_issue("t", "Title", "d")


def test_update_issue_and_comments(monkeypatch):
    updated = {"success": True, "issue": {"id": "uuid-issue-17", "identifier": "DIY-17", "url": "u"}}
    comments = {"nodes": [{"body": "Use retained topics.", "createdAt": "2026-09-20T10:00:00.000Z", "user": {"name": "Kyle"}}, {"body": "bot", "createdAt": "t2", "user": None}]}
    transport = Transport([{"data": {"issueUpdate": updated}}, {"data": {"issue": {"comments": comments}}}])
    monkeypatch.setattr(ln, "_open", transport)
    api = ln.Linear(TEST_KEY)
    assert api.update_issue("uuid-issue-17", description="new")["identifier"] == "DIY-17"
    assert transport.body(0)["variables"] == {"id": "uuid-issue-17", "input": {"description": "new"}}
    assert api.list_comments("DIY-17") == [
        {"body": "Use retained topics.", "user": "Kyle", "created": "2026-09-20T10:00:00.000Z"},
        {"body": "bot", "user": None, "created": "t2"},
    ]
    with pytest.raises(ln.LinearError, match="nothing to update"):
        api.update_issue("x")


# -- issue <-> ticket ----------------------------------------------------------


def test_issue_to_ticket_template():
    issue = ln._flatten_issue(ISSUE_NODE)
    ticket = ln.issue_to_ticket(issue)
    assert ticket["title"] == "Disk tile via MQTT"
    assert ticket["why"] == "The panel shows stale disk numbers after a remount."
    assert ticket["what"].startswith("### Collector")
    assert "### Panel" in ticket["what"]
    assert "Subscribe to `carbon/disk`" in ticket["what"]
    assert ticket["acceptance"] == ["Disk tile updates within 60s of a remount", "No polling from the panel process"]
    assert ticket["context_bullets"] == ["Collector lives in mqtt/host/collector.py", "Panel renders tiles from retained topics"]
    assert ticket["prior_answers"] == [
        {"id": "B03", "question": "Should the panel keep polling as a fallback?", "answer": "No, retained topics only."}
    ]
    assert ticket["linear"] == {
        "identifier": "DIY-17",
        "url": "https://linear.app/x/issue/DIY-17",
        "team": "DIY",
        "project": "Status panel",
    }
    ln.acceptance_required(ticket)  # does not raise


def test_acceptance_required_fails_without_section():
    issue = {**ln._flatten_issue(ISSUE_NODE), "description": "## Why\n\nBecause.\n\n## What\n\nDo it.\n"}
    ticket = ln.issue_to_ticket(issue)
    assert ticket["acceptance"] == []
    with pytest.raises(ln.LinearError, match="issue DIY-17 has no '## Acceptance' section with bullets"):
        ln.acceptance_required(ticket)


def test_issue_to_ticket_empty_description():
    ticket = ln.issue_to_ticket({"title": "Bare", "description": None, "identifier": "DIY-1"})
    assert ticket["title"] == "Bare"
    assert ticket["acceptance"] == [] and ticket["why"] == ""


def test_ticket_to_description_round_trip():
    original = ln.issue_to_ticket(ln._flatten_issue(ISSUE_NODE))
    description = ln.ticket_to_description(original)
    assert description.startswith("## Why\n")
    assert "Disk tile via MQTT" not in description
    again = ln.issue_to_ticket({"title": original["title"], "description": description})
    for key in ("title", "why", "what", "acceptance", "context_bullets", "prior_answers"):
        assert again[key] == original[key], key


# -- CLI -------------------------------------------------------------------------


def draft_markdown() -> str:
    return "# Disk tile via MQTT\n\n" + TEMPLATE_DESCRIPTION


def test_cli_create_dry_run_without_key(tmp_path, capsys, no_key, no_network):
    draft = tmp_path / "draft.md"
    draft.write_text(draft_markdown())
    rc = cli.main(["linear", "create", str(draft), "--team", "DIY", "--project", "Status panel", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "team: DIY" in out and "project: Status panel" in out
    assert "title: Disk tile via MQTT" in out
    assert "## Acceptance" in out and "- Disk tile updates within 60s of a remount" in out
    assert "# Disk tile via MQTT\n" not in out  # title is not part of the description


def test_cli_create_dry_run_json_draft(tmp_path, capsys, no_key, no_network):
    ticket = {"title": "From JSON", "why": "w", "what": "x", "acceptance": ["a1"], "linear": {"team": "DIY", "project": "P"}}
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps(ticket))
    assert cli.main(["linear", "create", str(draft), "--dry-run", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["dry_run"] is True
    assert (record["team"], record["project"], record["title"]) == ("DIY", "P", "From JSON")
    assert record["description"] == "## Why\n\nw\n\n## What\n\nx\n\n## Acceptance\n\n- a1\n"


def test_cli_create_needs_key_and_maps_to_exit_4(tmp_path, capsys, no_key, no_network):
    draft = tmp_path / "draft.md"
    draft.write_text(draft_markdown())
    assert cli.main(["linear", "create", str(draft), "--team", "DIY"]) == 4
    assert "jevgate: error: No Linear key" in capsys.readouterr().err


def test_cli_create_creates_issue(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", TEST_KEY)
    created = {"success": True, "issue": {"id": "uuid-new", "identifier": "DIY-42", "url": "https://linear.app/x/issue/DIY-42"}}
    transport = Transport(
        [
            {"data": {"teams": {"nodes": [{"id": "uuid-team-diy", "key": "DIY"}]}}},
            {"data": {"projects": {"nodes": [{"id": "p-diy", "name": "Status panel", "teams": {"nodes": [{"id": "uuid-team-diy"}]}}]}}},
            {"data": {"issueCreate": created}},
        ]
    )
    monkeypatch.setattr(ln, "_open", transport)
    draft = tmp_path / "draft.md"
    draft.write_text(draft_markdown())
    assert cli.main(["linear", "create", str(draft), "--team", "DIY", "--project", "Status panel"]) == 0
    assert capsys.readouterr().out.strip() == "DIY-42 https://linear.app/x/issue/DIY-42"
    sent = transport.body(2)["variables"]["input"]
    assert sent["teamId"] == "uuid-team-diy" and sent["projectId"] == "p-diy"
    assert sent["title"] == "Disk tile via MQTT"
    assert sent["description"].startswith("## Why\n")
    for request in transport.requests:
        assert request.get_header("Authorization") == TEST_KEY


def test_cli_create_untitled_draft_is_error(tmp_path, capsys, no_key, no_network):
    draft = tmp_path / "draft.md"
    draft.write_text("## Why\n\nno title here\n")
    assert cli.main(["linear", "create", str(draft), "--team", "DIY", "--dry-run"]) == 4
    assert "draft has no title" in capsys.readouterr().err


def test_cli_get_markdown_and_json(capsys, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", TEST_KEY)
    monkeypatch.setattr(ln, "_open", Transport([{"data": {"issue": ISSUE_NODE}}, {"data": {"issue": ISSUE_NODE}}]))
    assert cli.main(["linear", "get", "DIY-17"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Disk tile via MQTT\n\n## Why\n")
    assert "- (B03) **Should the panel keep polling as a fallback?** No, retained topics only." in out
    assert cli.main(["linear", "get", "DIY-17", "--json"]) == 0
    ticket = json.loads(capsys.readouterr().out)
    assert ticket["linear"]["identifier"] == "DIY-17"
    assert ticket["acceptance"][0] == "Disk tile updates within 60s of a remount"


def test_cli_get_graphql_error_exit_4(capsys, monkeypatch):
    monkeypatch.setenv("LINEAR_API_KEY", TEST_KEY)
    monkeypatch.setattr(ln, "_open", Transport([{"errors": [{"message": "Entity not found: Issue"}]}]))
    assert cli.main(["linear", "get", "DIY-404"]) == 4
    err = capsys.readouterr().err
    assert "Entity not found" in err and TEST_KEY not in err


def test_cli_linear_without_action_prints_help(capsys):
    assert cli.main(["linear"]) == 4
    assert "get" in capsys.readouterr().out
