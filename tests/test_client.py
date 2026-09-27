"""Transport, validation, cache and audit behaviour of the TypeSafe client. No network."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from jevgate import CATALOG_VERSION
from jevgate import client as c

TEST_KEY = "test-key-0123456789"  # what the canned fixture exports; any well-formed key works here


def http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(c.ENDPOINT, code, f"HTTP {code}", {}, io.BytesIO(body.encode()))

NOUL_Q = {"type": "noul", "instructions": "Is it?"}
CHOICE_Q = {"type": "choice", "instructions": "Which?", "criteria": {"a": "A", "b": "B", "unclear": None}}
SCORE_Q = {"type": "score", "instructions": "How much?", "criteria": ["none", "some", "all"]}


# -- key loading --------------------------------------------------------------


def test_key_from_env(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", " env-key-abcdefgh ")
    assert c.load_key() == "env-key-abcdefgh"


def test_key_from_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    key_file = tmp_path / "api-key"
    key_file.write_text("file-key-abcdefgh\nsecond line ignored\n")
    monkeypatch.setattr(c, "KEY_FILE", key_file)
    assert c.load_key() == "file-key-abcdefgh"


def test_missing_key(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr(c, "KEY_FILE", tmp_path / "absent")
    with pytest.raises(c.ClientError, match="No TypeSafe key"):
        c.load_key()


def test_malformed_key_rejected_without_echo(monkeypatch):
    bad = "bad key with spaces $$$"
    monkeypatch.setenv("TYPESAFE_API_KEY", bad)
    with pytest.raises(c.ClientError) as info:
        c.load_key()
    assert "malformed" in str(info.value)
    assert bad not in str(info.value)
    assert "spaces" not in str(info.value)


# -- transport ----------------------------------------------------------------


class Transport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return json.dumps(outcome).encode()


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(c, "_sleep", slept.append)
    return slept


def test_retry_429_then_success(monkeypatch, no_sleep):
    transport = Transport([http_error(429, "slow down"), {"answers": {}, "usage": {}}])
    monkeypatch.setattr(c, "_open", transport)
    result = c.call_api({"model": "m", "state": {}, "questions": {}}, TEST_KEY)
    assert result == {"answers": {}, "usage": {}}
    assert len(transport.requests) == 2
    assert no_sleep == [1]
    request = transport.requests[0]
    assert request.get_header("Authorization") == f"Bearer {TEST_KEY}"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("User-agent").startswith("jevgate/")
    assert request.full_url == c.ENDPOINT


def test_401_not_retried_and_body_in_message(monkeypatch, no_sleep):
    transport = Transport([http_error(401, '{"error": "bad token"}' + "x" * 500)])
    monkeypatch.setattr(c, "_open", transport)
    with pytest.raises(c.ClientError) as info:
        c.call_api({"model": "m", "state": {}, "questions": {}}, TEST_KEY)
    message = str(info.value)
    assert "401" in message and "bad token" in message
    assert len(message) < 400
    assert TEST_KEY not in message
    assert len(transport.requests) == 1
    assert no_sleep == []


def test_422_not_retried(monkeypatch, no_sleep):
    monkeypatch.setattr(c, "_open", Transport([http_error(422, "schema")]))
    with pytest.raises(c.ClientError, match="422"):
        c.call_api({}, TEST_KEY)
    assert no_sleep == []


def test_retries_exhausted(monkeypatch, no_sleep):
    monkeypatch.setattr(c, "_open", Transport([http_error(503)] * 3))
    with pytest.raises(c.ClientError, match="503"):
        c.call_api({}, TEST_KEY, attempts=3)
    assert no_sleep == [1, 2]


def test_url_error_retried_then_raises(monkeypatch, no_sleep):
    monkeypatch.setattr(c, "_open", Transport([urllib.error.URLError("refused"), TimeoutError()]))
    with pytest.raises(c.ClientError, match="TimeoutError"):
        c.call_api({}, TEST_KEY, attempts=2)
    assert no_sleep == [1]


def test_redirect_refused(monkeypatch, no_sleep):
    handler = c.NoRedirect()
    with pytest.raises(urllib.error.URLError):
        handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/")
    monkeypatch.setattr(c, "_open", Transport([c.RedirectRefused("Refusing HTTP 302 redirect")]))
    with pytest.raises(c.ClientError, match="redirect"):
        c.call_api({}, TEST_KEY)
    assert no_sleep == []  # a redirect is never retried


# -- validation ---------------------------------------------------------------


def test_validate_noul():
    ok = c.validate_answers({"answers": {"q": {"type": "noul", "noul": 0.7}}}, {"q": NOUL_Q})
    assert ok == {"q": {"type": "noul", "noul": 0.7}}
    for bad in ({"type": "noul", "noul": 1.2}, {"type": "noul", "noul": True}, {"type": "choice", "noul": 0.5},
                {"type": "noul"}, "0.7", None, {"type": "noul", "noul": 0.5, "confidence": 2}):
        assert c.validate_answers({"answers": {"q": bad}}, {"q": NOUL_Q}) == {"q": None}


def test_validate_choice_good_and_bad_distribution():
    good = {"type": "choice", "choice": "a", "confidence": 0.8, "probabilities": {"a": 0.8, "b": 0.15, "unclear": 0.05}}
    assert c.validate_answers({"answers": {"q": good}}, {"q": CHOICE_Q})["q"] == good
    off_sum = dict(good, probabilities={"a": 0.8, "b": 0.3, "unclear": 0.05})
    wrong_keys = dict(good, probabilities={"a": 0.9, "b": 0.1})
    bad_choice = dict(good, choice="zzz")
    no_conf = {k: v for k, v in good.items() if k != "confidence"}
    for bad in (off_sum, wrong_keys, bad_choice, no_conf):
        assert c.validate_answers({"answers": {"q": bad}}, {"q": CHOICE_Q}) == {"q": None}


def test_validate_score():
    good = {"type": "score", "score": 2, "confidence": 0.6, "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7}}
    assert c.validate_answers({"answers": {"q": good}}, {"q": SCORE_Q})["q"] == good
    for bad in (dict(good, score=3), dict(good, score="2"), dict(good, probabilities={"0": 0.5, "1": 0.5}),
                dict(good, probabilities={"0": 0.1, "1": 0.2, "2": 0.6})):
        assert c.validate_answers({"answers": {"q": bad}}, {"q": SCORE_Q}) == {"q": None}


def test_validate_missing_and_garbage_response():
    assert c.validate_answers({"answers": {}}, {"q": NOUL_Q}) == {"q": None}
    assert c.validate_answers("nonsense", {"q": NOUL_Q}) == {"q": None}
    assert c.validate_answers({"answers": []}, {"q": NOUL_Q}) == {"q": None}


def test_request_hash_is_content_addressed():
    body = {"model": "m", "state": {"a": 1, "b": [1, 2]}, "questions": {"q": NOUL_Q}}
    same = {"questions": {"q": dict(NOUL_Q)}, "state": {"b": [1, 2], "a": 1}, "model": "m", "extra": "ignored"}
    assert c.request_hash(body) == c.request_hash(same)
    assert c.request_hash(dict(body, model="other")) != c.request_hash(body)
    assert len(c.request_hash(body)) == 64
    assert CATALOG_VERSION  # part of the hash: a catalog bump invalidates the cache


# -- client -------------------------------------------------------------------


def client_for(tmp_path, tmp_cache, **kw):
    return c.TypeSafeClient(cache_dir=tmp_cache, audit_path=tmp_path / "run" / "requests.jsonl", **kw)


def audit_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_ask_answers_and_writes_audit(canned, tmp_path, tmp_cache):
    canned.answers = {"q1": {"noul": 0.9}, "q2": {"choice": "a", "confidence": 0.7,
                                                   "probabilities": {"a": 0.7, "b": 0.2, "unclear": 0.1}}}
    client = client_for(tmp_path, tmp_cache)
    result = client.ask({"draft": "x"}, {"q1": NOUL_Q, "q2": CHOICE_Q, "q3": SCORE_Q}, tag="slice-a")
    assert result["q1"]["noul"] == 0.9
    assert result["q2"]["choice"] == "a"
    assert result["q3"] is None
    assert canned.calls == 1
    assert canned.bodies[0]["model"] == "jev-latest"
    assert canned.bodies[0]["state"] == {"draft": "x"}
    assert client.usage.requests == 1 and client.usage.cached == 0
    assert client.usage.input_tokens == 100 and client.usage.output_tokens == 10
    assert client.usage.cost_usd == pytest.approx(100 * 0.042 / 1e6)
    assert client.usage.errors == []
    lines = audit_lines(client.audit_path)
    assert len(lines) == 1
    line = lines[0]
    assert line["tag"] == "slice-a" and line["cached"] is False and line["error"] is None
    assert line["question_ids"] == ["q1", "q2", "q3"]
    assert line["usage"] == {"input_tokens": 100, "output_tokens": 10}
    assert len(line["sha"]) == 64
    assert "state" not in line and "body" not in line and TEST_KEY not in json.dumps(line)


def test_cache_hit_skips_transport(canned, tmp_path, tmp_cache):
    canned.answers = {"q1": {"noul": 0.4}}
    first = client_for(tmp_path, tmp_cache)
    first.ask({"s": 1}, {"q1": NOUL_Q}, tag="t")
    assert canned.calls == 1
    cached_files = list(tmp_cache.glob("*.json"))
    assert len(cached_files) == 1
    entry = json.loads(cached_files[0].read_text())
    assert set(entry) == {"sha", "created", "body", "response", "usage", "seconds"}
    assert entry["body"]["state"] == {"s": 1}

    second = client_for(tmp_path, tmp_cache)
    result = second.ask({"s": 1}, {"q1": NOUL_Q}, tag="t")
    assert result["q1"]["noul"] == 0.4
    assert canned.calls == 1  # no transport call
    assert second.usage.cached == 1 and second.usage.requests == 0
    assert audit_lines(second.audit_path)[-1]["cached"] is True

    second.ask({"s": 2}, {"q1": NOUL_Q}, tag="t")  # a different state misses
    assert canned.calls == 2


def test_no_cache_bypasses_reads_and_writes(canned, tmp_path, tmp_cache):
    canned.answers = {"q1": {"noul": 0.4}}
    client = client_for(tmp_path, tmp_cache, use_cache=False)
    client.ask({"s": 1}, {"q1": NOUL_Q}, tag="t")
    client.ask({"s": 1}, {"q1": NOUL_Q}, tag="t")
    assert canned.calls == 2
    assert list(tmp_cache.glob("*.json")) == []


def test_api_failure_reads_as_unknown(canned, tmp_path, tmp_cache):
    canned.errors = [c.ClientError("TypeSafe HTTP 503: down")]
    client = client_for(tmp_path, tmp_cache)
    assert client.ask({}, {"q1": NOUL_Q, "q2": CHOICE_Q}, tag="t") == {"q1": None, "q2": None}
    assert client.usage.errors == ["t: TypeSafe HTTP 503: down"]
    assert client.usage.requests == 0
    assert audit_lines(client.audit_path)[0]["error"] == "TypeSafe HTTP 503: down"
    assert list(tmp_cache.glob("*.json")) == []  # failures are never cached


def test_raw_http_error_from_transport_is_contained(canned, tmp_path, tmp_cache):
    canned.errors = [http_error(500, "boom")]
    client = client_for(tmp_path, tmp_cache)
    assert client.ask({}, {"q1": NOUL_Q}, tag="t") == {"q1": None}
    assert client.usage.errors == ["t: TypeSafe HTTP 500"]


def test_missing_key_reads_as_unknown(canned, monkeypatch, tmp_path, tmp_cache):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setattr(c, "KEY_FILE", tmp_path / "absent")
    client = client_for(tmp_path, tmp_cache)
    assert client.ask({}, {"q1": NOUL_Q}, tag="t") == {"q1": None}
    assert canned.calls == 0
    assert "No TypeSafe key" in client.usage.errors[0]


def test_disabled_client_never_calls(canned, tmp_path, tmp_cache):
    canned.answers = {"q1": {"noul": 0.9}}
    client = client_for(tmp_path, tmp_cache, enabled=False)
    assert client.ask({"s": 1}, {"q1": NOUL_Q}, tag="t") == {"q1": None}
    assert canned.calls == 0
    assert client.usage.requests == 0 and client.usage.errors == []
    line = audit_lines(client.audit_path)[0]
    assert "skipped" in line["error"] and line["sha"] is None


def test_empty_questions_no_call(canned, tmp_path, tmp_cache):
    client = client_for(tmp_path, tmp_cache)
    assert client.ask({}, {}, tag="t") == {}
    assert canned.calls == 0
    assert not client.audit_path.exists()


def test_ask_many_returns_per_tag(canned, tmp_path, tmp_cache):
    canned.answers = {"q1": {"noul": 0.1}, "q2": {"noul": 0.2}, "q3": {"noul": 0.3}}
    client = client_for(tmp_path, tmp_cache, workers=2)
    jobs = [
        c.Job("a", {"slice": "a"}, {"q1": NOUL_Q}),
        c.Job("b", {"slice": "b"}, {"q2": NOUL_Q, "q3": NOUL_Q}),
        c.Job("c", {"slice": "c"}, {"missing": NOUL_Q}),
    ]
    results = client.ask_many(jobs)
    assert set(results) == {"a", "b", "c"}
    assert results["a"]["q1"]["noul"] == 0.1
    assert results["b"]["q2"]["noul"] == 0.2 and results["b"]["q3"]["noul"] == 0.3
    assert results["c"] == {"missing": None}
    assert canned.calls == 3
    assert client.usage.requests == 3
    assert {line["tag"] for line in audit_lines(client.audit_path)} == {"a", "b", "c"}
    assert client.ask_many([]) == {}


def test_default_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert c.default_cache_dir() == tmp_path / "xdg" / "jevgate" / "requests"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert c.default_cache_dir() == Path.home() / ".cache" / "jevgate" / "requests"


def test_usage_to_dict():
    usage = c.Usage()
    usage.add({"input_tokens": 1000, "output_tokens": 5})
    usage.add("garbage")
    assert usage.to_dict() == {"requests": 0, "cached": 0, "input_tokens": 1000, "output_tokens": 5,
                               "cost_usd": pytest.approx(0.000042), "errors": []}
