"""``cache_only`` clients never touch the transport, and Score answers may carry
a float expected value. No network."""

from __future__ import annotations

from pathlib import Path

import pytest

from jevgate import client as c

NOUL_Q = {"type": "noul", "instructions": "Is it?"}
SCORE_Q = {"type": "score", "instructions": "How much?", "criteria": ["none", "some", "all"]}
FOUR_LEVEL_Q = {"type": "score", "instructions": "How much?", "criteria": ["none", "little", "some", "all"]}


def body_sha(state: str = "s", questions: dict | None = None) -> str:
    return c.request_hash({"model": c.DEFAULT_MODEL, "state": state, "questions": questions or {"q": NOUL_Q}})


# -- cache_only ---------------------------------------------------------------


def test_cache_only_miss_answers_none_without_transport(canned, tmp_cache: Path):
    client = c.TypeSafeClient(cache_dir=tmp_cache, cache_only=True)
    assert client.ask("s", {"q": NOUL_Q, "r": NOUL_Q}, tag="t") == {"q": None, "r": None}
    assert canned.calls == 0
    sha = body_sha("s", {"q": NOUL_Q, "r": NOUL_Q})
    assert client.usage.errors == [f"cache miss: {sha}"]
    assert client.usage.requests == 0 and client.usage.cached == 0
    assert client.records[-1]["error"] == f"cache miss: {sha}" and client.records[-1]["sha"] == sha
    assert list(tmp_cache.iterdir()) == []


def test_cache_only_hit_is_served_from_disk(canned, tmp_cache: Path):
    canned.answers = {"q": {"noul": 0.7}}
    warm = c.TypeSafeClient(cache_dir=tmp_cache)
    first = warm.ask("s", {"q": NOUL_Q}, tag="t")
    assert canned.calls == 1 and first["q"]["noul"] == 0.7

    cold = c.TypeSafeClient(cache_dir=tmp_cache, cache_only=True)
    again = cold.ask("s", {"q": NOUL_Q}, tag="t")
    assert again == first
    assert canned.calls == 1
    assert cold.usage.cached == 1 and cold.usage.errors == []
    assert cold.records[-1]["cached"] is True


def test_cache_only_defaults_off_and_transport_is_used(canned, tmp_cache: Path):
    canned.answers = {"q": {"noul": 0.2}}
    client = c.TypeSafeClient(cache_dir=tmp_cache)
    assert client.cache_only is False
    assert client.ask("s", {"q": NOUL_Q}, tag="t")["q"]["noul"] == 0.2
    assert canned.calls == 1 and client.usage.requests == 1 and client.usage.errors == []


def test_cache_only_with_ai_disabled_reports_disabled_not_miss(canned, tmp_cache: Path):
    client = c.TypeSafeClient(enabled=False, cache_dir=tmp_cache, cache_only=True)
    assert client.ask("s", {"q": NOUL_Q}, tag="t") == {"q": None}
    assert client.usage.errors == [] and client.records[-1]["error"].startswith("skipped: AI disabled")


def test_cache_only_with_use_cache_false_is_always_a_miss(canned, tmp_cache: Path):
    canned.answers = {"q": {"noul": 0.7}}
    c.TypeSafeClient(cache_dir=tmp_cache).ask("s", {"q": NOUL_Q}, tag="t")
    client = c.TypeSafeClient(cache_dir=tmp_cache, use_cache=False, cache_only=True)
    assert client.ask("s", {"q": NOUL_Q}, tag="t") == {"q": None}
    assert client.usage.errors == [f"cache miss: {body_sha()}"] and canned.calls == 1


def test_cache_only_misses_are_recorded_per_request(canned, tmp_cache: Path):
    client = c.TypeSafeClient(cache_dir=tmp_cache, cache_only=True)
    client.ask("a", {"q": NOUL_Q}, tag="one")
    client.ask("b", {"q": NOUL_Q}, tag="two")
    assert client.usage.errors == [f"cache miss: {body_sha('a')}", f"cache miss: {body_sha('b')}"]
    assert [r["tag"] for r in client.records] == ["one", "two"]


# -- float scores -------------------------------------------------------------


def float_score(score: float, probabilities: dict) -> dict:
    return {"type": "score", "score": score, "confidence": 0.92, "legend": {"2": "all"}, "probabilities": probabilities}


def test_float_score_validates():
    answer = float_score(1.95, {"0": 0.0, "1": 0.05, "2": 0.95})
    assert c.validate_answer(answer, SCORE_Q) == answer
    top = float_score(3.0, {"0": 0.0, "1": 0.0, "2": 0.0, "3": 1.0})
    assert c.validate_answer(top, FOUR_LEVEL_Q) == top
    assert c.validate_answer({**answer, "score": 2}, SCORE_Q) is not None


@pytest.mark.parametrize("score", [4.5, 3.01, -0.5, True, "2"])
def test_score_outside_the_levels_is_rejected(score):
    answer = float_score(score, {"0": 0.0, "1": 0.0, "2": 0.5, "3": 0.5})
    assert c.validate_answer(answer, FOUR_LEVEL_Q) is None


def test_float_score_reaches_the_caller(canned, tmp_cache: Path):
    canned.answers = {"q": {"score": 1.95, "confidence": 0.92, "probabilities": {"0": 0.0, "1": 0.05, "2": 0.95}}}
    client = c.TypeSafeClient(cache_dir=tmp_cache)
    got = client.ask("s", {"q": SCORE_Q}, tag="t")["q"]
    assert got is not None and got["score"] == 1.95 and got["probabilities"]["2"] == 0.95
