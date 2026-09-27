"""Shared fixtures. Nothing here touches the network: ``canned`` replaces the
transport (``jevgate.client.call_api``) and answers by question id."""

from __future__ import annotations

import io
import urllib.error
from pathlib import Path

import pytest

from jevgate import client as client_module

TEST_KEY = "test-key-0123456789"


def http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    """An HTTPError with a readable body, as urllib would raise it."""
    return urllib.error.HTTPError(client_module.ENDPOINT, code, f"HTTP {code}", {}, io.BytesIO(body.encode()))


class Canned:
    """Fake transport: records every request body, answers from ``answers`` by
    question id (filling in ``type`` from the question), and raises the queued
    ``errors`` first, one per call."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.answers: dict[str, dict] = {}
        self.errors: list[BaseException] = []
        self.usage = {"input_tokens": 100, "output_tokens": 10}

    def __call__(self, body: dict, key: str, *, timeout: float = 60, attempts: int = 4) -> dict:
        assert key == TEST_KEY
        self.bodies.append(body)
        if self.errors:
            raise self.errors.pop(0)
        answers = {}
        for qid, question in body["questions"].items():
            if qid in self.answers:
                answer = dict(self.answers[qid])
                answer.setdefault("type", question["type"])
                answers[qid] = answer
        return {"answers": answers, "usage": dict(self.usage)}

    @property
    def calls(self) -> int:
        return len(self.bodies)


@pytest.fixture
def canned(monkeypatch: pytest.MonkeyPatch) -> Canned:
    fake = Canned()
    monkeypatch.setenv("TYPESAFE_API_KEY", TEST_KEY)
    monkeypatch.setattr(client_module, "call_api", fake)
    return fake


@pytest.fixture
def tmp_cache(tmp_path: Path) -> Path:
    path = tmp_path / "cache"
    path.mkdir()
    return path
