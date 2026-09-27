"""TypeSafe (Jev) transport: key loading, retrying HTTP, strict answer validation,
a content-addressed request cache and a JSONL audit log.

The key comes from ``TYPESAFE_API_KEY`` or the first line of
``~/.config/typesafe/api-key`` and is never printed, logged, cached or placed in
an error message. ``call_api`` is module-level so tests patch the transport and
exercise everything above it. ``TypeSafeClient.ask`` never raises on an API or
key failure: every question in the request reads as ``None`` and the failure is
recorded in ``usage.errors`` and the audit line, so callers fail to "uncertain"
instead of crashing.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import CATALOG_VERSION, __version__

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
KEY_FILE = Path.home() / ".config" / "typesafe" / "api-key"
KEY_PATTERN = re.compile(r"[A-Za-z0-9._\-]{8,200}")
RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 529})
# Statuses that mean "fix the request or the key", never "try again".
FATAL_STATUS = frozenset({401, 422})
INPUT_USD_PER_TOKEN = 0.042 / 1e6
ERROR_BODY_CHARS = 300
PROBABILITY_TOLERANCE = 0.02


class ClientError(RuntimeError):
    """A request that could not be made or did not succeed. Never carries the key."""


class RedirectRefused(urllib.error.URLError):
    """The endpoint answered with a redirect; following it could leak the key."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so the bearer token only ever goes to ENDPOINT."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        raise RedirectRefused(f"Refusing HTTP {code} redirect from the TypeSafe endpoint")


def default_cache_dir() -> Path:
    """``$XDG_CACHE_HOME/jevgate/requests`` or ``~/.cache/jevgate/requests``."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "jevgate" / "requests"


def load_key() -> str:
    """Return the API key from the environment or the key file, validated by shape.

    Raises ``ClientError`` when no key is found or the key is malformed; the
    message never contains the key.
    """
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        lines = KEY_FILE.read_text(encoding="utf-8").splitlines()
        key = lines[0].strip() if lines else ""
    if not key:
        raise ClientError("No TypeSafe key in TYPESAFE_API_KEY or ~/.config/typesafe/api-key")
    if not KEY_PATTERN.fullmatch(key):
        raise ClientError("TypeSafe key is malformed; refusing to send it")
    return key


def _sleep(seconds: float) -> None:
    """Backoff hook; tests patch it to avoid waiting."""
    time.sleep(seconds)


def _open(request: urllib.request.Request, timeout: float) -> bytes:
    """Transport hook: POST the request without following redirects, return the body."""
    opener = urllib.request.build_opener(NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        return response.read()


def _error_detail(error: urllib.error.HTTPError) -> str:
    try:
        return error.read().decode("utf-8", errors="replace")[:ERROR_BODY_CHARS]
    except Exception:  # pragma: no cover - a body that cannot be read
        return ""


def call_api(body: dict, key: str, *, timeout: float = 60, attempts: int = 4) -> dict:
    """POST ``body`` to the TypeSafe endpoint and return the decoded JSON response.

    Retries 429/500/502/503/504/529, connection errors and timeouts with
    ``2**n`` second backoff; 401 and 422 (and any other 4xx) raise ``ClientError``
    at once with the first 300 characters of the response body. A redirect is
    refused immediately.
    """
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": f"jevgate/{__version__}",
        },
    )
    last = "TypeSafe request failed"
    for attempt in range(max(1, attempts)):
        try:
            return json.loads(_open(request, timeout))
        except urllib.error.HTTPError as error:
            detail = _error_detail(error)
            last = f"TypeSafe HTTP {error.code}: {detail}".rstrip(": ")
            if error.code not in RETRY_STATUS:  # FATAL_STATUS and every other 4xx
                raise ClientError(last) from None
        except RedirectRefused as error:
            raise ClientError(str(error.reason)) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            reason = getattr(error, "reason", None) or error
            last = f"TypeSafe request failed: {type(error).__name__}: {str(reason)[:ERROR_BODY_CHARS]}"
        if attempt + 1 < attempts:
            _sleep(2 ** attempt)
    raise ClientError(f"{last} (after {attempts} attempts)")


# ----------------------------------------------------------------------------
# Answer validation


def _is_probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1


def _valid_distribution(probabilities: Any, keys: set[str]) -> bool:
    if not isinstance(probabilities, dict) or set(probabilities) != keys:
        return False
    if not all(_is_probability(p) for p in probabilities.values()):
        return False
    return abs(sum(probabilities.values()) - 1) <= PROBABILITY_TOLERANCE


def validate_answer(answer: Any, question: dict) -> dict | None:
    """Return ``answer`` when it is a well-formed answer to ``question``, else ``None``.

    Strict on purpose: a wrong type, a choice outside the criteria, a probability
    table with the wrong keys or a sum off by more than 0.02, or a confidence
    outside [0, 1] all make the answer unusable.
    """
    if not isinstance(answer, dict) or not isinstance(question, dict):
        return None
    qtype = question.get("type")
    if answer.get("type") != qtype:
        return None
    if "confidence" in answer and not _is_probability(answer["confidence"]):
        return None
    if qtype == "noul":
        return dict(answer) if _is_probability(answer.get("noul")) else None
    if "confidence" not in answer:
        return None
    criteria = question.get("criteria")
    if qtype == "choice":
        if not isinstance(criteria, dict) or answer.get("choice") not in criteria:
            return None
        if not _valid_distribution(answer.get("probabilities"), set(criteria)):
            return None
        return dict(answer)
    if qtype == "score":
        if not isinstance(criteria, list) or not criteria:
            return None
        score = answer.get("score")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score < len(criteria):
            return None
        if not _valid_distribution(answer.get("probabilities"), {str(i) for i in range(len(criteria))}):
            return None
        return dict(answer)
    return None


def validate_answers(raw: Any, questions: dict[str, dict]) -> dict[str, dict | None]:
    """Validate every answer in a raw response; ``None`` for each id that is missing or malformed."""
    answers = raw.get("answers") if isinstance(raw, dict) else None
    if not isinstance(answers, dict):
        answers = {}
    return {qid: validate_answer(answers.get(qid), question) for qid, question in questions.items()}


def request_hash(body: dict) -> str:
    """Content hash of a request: model, state, questions and the catalog version."""
    payload = {
        "model": body.get("model"),
        "state": body.get("state"),
        "questions": body.get("questions"),
        "catalog": CATALOG_VERSION,
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------
# Client


@dataclass
class Job:
    """One request: a tag for the audit log, a state and the questions over it."""

    tag: str
    state: Any
    questions: dict[str, dict]


@dataclass
class Usage:
    """Running totals for one client. ``cost_usd`` follows input tokens only."""

    requests: int = 0
    cached: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)

    def add(self, usage: Any) -> None:
        """Fold one response's ``usage`` block into the totals."""
        if not isinstance(usage, dict):
            return
        self.input_tokens += _as_int(usage.get("input_tokens"))
        self.output_tokens += _as_int(usage.get("output_tokens"))
        self.cost_usd = self.input_tokens * INPUT_USD_PER_TOKEN

    def to_dict(self) -> dict:
        return {
            "requests": self.requests,
            "cached": self.cached,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "errors": list(self.errors),
        }


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TypeSafeClient:
    """Asks typed questions over state, with a request cache and an audit log.

    ``enabled=False`` answers ``None`` for everything without touching the key,
    the cache or the network. ``cache_dir=None`` uses the default cache
    directory; ``use_cache=False`` bypasses it entirely.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        cache_dir: Path | None = None,
        audit_path: Path | None = None,
        model: str = DEFAULT_MODEL,
        timeout: float = 60,
        workers: int = 4,
        use_cache: bool = True,
    ) -> None:
        self.enabled = enabled
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self.use_cache = use_cache
        self.audit_path = Path(audit_path) if audit_path is not None else None
        self.model = model
        self.timeout = timeout
        self.workers = max(1, int(workers))
        self.usage = Usage()
        self.records: list[dict] = []
        self._lock = threading.Lock()
        self._key: str | None = None

    # -- public -------------------------------------------------------------

    def ask(self, state: Any, questions: dict[str, dict], *, tag: str) -> dict[str, dict | None]:
        """Answer ``questions`` over ``state``; ``None`` per id on any failure."""
        if not questions:
            return {}
        unknown: dict[str, dict | None] = {qid: None for qid in questions}
        line = {
            "ts": _now(),
            "tag": tag,
            "sha": None,
            "cached": False,
            "question_ids": list(questions),
            "usage": None,
            "error": None,
        }
        if not self.enabled:
            line["error"] = "skipped: AI disabled (--no-ai)"
            self._record(line)
            return unknown
        body = {"model": self.model, "state": state, "questions": questions}
        sha = request_hash(body)
        line["sha"] = sha
        cached = self._cache_get(sha) if self.use_cache else None
        if cached is not None:
            line["cached"] = True
            line["usage"] = cached.get("usage")
            with self._lock:
                self.usage.cached += 1
            self._record(line)
            return validate_answers(cached.get("response"), questions)
        try:
            key = self._get_key()
        except ClientError as error:
            return self._fail(line, unknown, tag, str(error))
        started = time.monotonic()
        try:
            raw = call_api(body, key, timeout=self.timeout)
        except (ClientError, urllib.error.URLError, OSError, ValueError) as error:
            return self._fail(line, unknown, tag, _describe(error))
        seconds = round(time.monotonic() - started, 3)
        if not isinstance(raw, dict):
            return self._fail(line, unknown, tag, "TypeSafe response is not a JSON object")
        line["usage"] = raw.get("usage")
        with self._lock:
            self.usage.requests += 1
            self.usage.add(raw.get("usage"))
        if self.use_cache:
            self._cache_put(sha, body, raw, seconds)
        self._record(line)
        return validate_answers(raw, questions)

    def ask_many(self, jobs: list[Job]) -> dict[str, dict[str, dict | None]]:
        """Run every job in parallel (``workers`` threads); results keyed by tag."""
        jobs = list(jobs)
        if not jobs:
            return {}
        with ThreadPoolExecutor(max_workers=min(self.workers, len(jobs))) as pool:
            futures = [pool.submit(self.ask, job.state, job.questions, tag=job.tag) for job in jobs]
            return {job.tag: future.result() for job, future in zip(jobs, futures)}

    # -- internals ----------------------------------------------------------

    def _get_key(self) -> str:
        if self._key is None:
            self._key = load_key()
        return self._key

    def _fail(self, line: dict, unknown: dict, tag: str, message: str) -> dict:
        line["error"] = message
        with self._lock:
            self.usage.errors.append(f"{tag}: {message}")
        self._record(line)
        return unknown

    def _record(self, line: dict) -> None:
        with self._lock:
            self.records.append(line)
            if self.audit_path is None:
                return
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")

    def _cache_file(self, sha: str) -> Path:
        return self.cache_dir / f"{sha}.json"

    def _cache_get(self, sha: str) -> dict | None:
        path = self._cache_file(sha)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict) or entry.get("sha") != sha or "response" not in entry:
            return None
        return entry

    def _cache_put(self, sha: str, body: dict, response: dict, seconds: float) -> None:
        entry = {
            "sha": sha,
            "created": _now(),
            "body": body,
            "response": response,
            "usage": response.get("usage"),
            "seconds": seconds,
        }
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_file(sha)
            tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # a cache that cannot be written only costs money, never correctness


def _describe(error: BaseException) -> str:
    if isinstance(error, ClientError):
        return str(error)
    if isinstance(error, urllib.error.HTTPError):
        return f"TypeSafe HTTP {error.code}"
    return f"TypeSafe request failed: {type(error).__name__}"

