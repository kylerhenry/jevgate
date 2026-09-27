"""Configuration: defaults ← ``<repo>/jevgate.json`` ← ``--config FILE`` ← CLI flags.

JSON has no comments, so a config file may carry a top-level ``"_doc"`` object
(and any other key starting with ``_``), which the loader ignores. See
``jevgate.json.example`` at the repository root for every key.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

CONFIG_NAME = "jevgate.json"

DEFAULT_HEDGES = [
    "leverage", "robust", "seamless", "seamlessly", "utilize", "utilise", "comprehensive",
    "holistic", "streamline", "ensure that", "in order to", "it is important to note",
    "it's worth noting", "various", "cutting-edge", "state-of-the-art", "delve", "as an AI",
]
DEFAULT_IGNORE = [
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    "Cargo.lock", "Gemfile.lock", "composer.lock", "go.sum", "*.lock",
]
DEFAULT_TEST_LOG_GLOBS = ["test*.log", "tests*.log", "pytest*.log"]
CONTEXT_KEYS = ("dir", "project", "areas", "follow_links", "context_budget", "max_items")


class ConfigError(ValueError):
    """A config file or flag that cannot be applied; the message names the key."""


def default_context() -> dict:
    return {
        "dir": None,
        "project": None,
        "areas": {},
        "follow_links": 1,
        "context_budget": 8000,
        "max_items": 16,
    }


@dataclass
class Config:
    """Every tunable the gates read. Thresholds are keyed by gate id or family."""

    thresholds: dict[str, float] = field(default_factory=dict)
    unclear_at: float = 0.40
    context: dict = field(default_factory=default_context)
    ignore: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))
    test_log_globs: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_LOG_GLOBS))
    hedges: list[str] = field(default_factory=lambda: list(DEFAULT_HEDGES))
    state_budget: int = 24000
    file_budget: int = 12000
    tests_budget: int = 6000
    context_budget: int = 8000
    max_file_tokens: int = 40000
    max_items: int | None = 16
    max_asks: int = 4
    workers: int = 4

    def to_dict(self) -> dict:
        return asdict(self)


_FIELDS = {f.name for f in fields(Config)}
_INT_FIELDS = ("state_budget", "file_budget", "tests_budget", "context_budget", "max_file_tokens", "max_asks", "workers")
_LIST_FIELDS = ("ignore", "test_log_globs", "hedges")


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path}: invalid JSON at line {error.lineno}: {error.msg}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")
    return data


def _check_probability(key: str, value: Any, source: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ConfigError(f"{source}: {key} must be a number between 0 and 1, got {value!r}")
    return float(value)


def merge(cfg: Config, data: dict, source: str = "config") -> Config:
    """Apply one layer of settings onto ``cfg`` in place; unknown keys raise ``ConfigError``."""
    for key, value in data.items():
        if key.startswith("_"):
            continue
        if key not in _FIELDS:
            raise ConfigError(f"{source}: unknown key {key!r}")
        if key == "thresholds":
            if not isinstance(value, dict):
                raise ConfigError(f"{source}: thresholds must be an object of gate id -> probability")
            for gate, p in value.items():
                cfg.thresholds[gate] = _check_probability(f"thresholds.{gate}", p, source)
        elif key == "unclear_at":
            cfg.unclear_at = _check_probability(key, value, source)
        elif key == "context":
            if not isinstance(value, dict):
                raise ConfigError(f"{source}: context must be an object")
            for sub, subvalue in value.items():
                if sub.startswith("_"):
                    continue
                if sub not in CONTEXT_KEYS:
                    raise ConfigError(f"{source}: unknown key 'context.{sub}'")
                cfg.context[sub] = subvalue
        elif key in _LIST_FIELDS:
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ConfigError(f"{source}: {key} must be a list of strings")
            setattr(cfg, key, list(value))
        elif key in _INT_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ConfigError(f"{source}: {key} must be a non-negative integer, got {value!r}")
            setattr(cfg, key, value)
        elif key == "max_items":
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ConfigError(f"{source}: max_items must be a non-negative integer or null")
            cfg.max_items = value
        else:  # pragma: no cover - every field is handled above
            setattr(cfg, key, value)
    return cfg


def load(repo: Path | None = None, explicit: Path | None = None) -> Config:
    """Defaults, then ``<repo>/jevgate.json`` if present, then ``explicit`` (which must exist)."""
    cfg = Config()
    if repo is not None:
        repo_file = Path(repo) / CONFIG_NAME
        if repo_file.is_file():
            merge(cfg, _read_json(repo_file), str(repo_file))
    if explicit is not None:
        explicit = Path(explicit)
        if not explicit.is_file():
            raise FileNotFoundError(f"config file not found: {explicit}")
        merge(cfg, _read_json(explicit), str(explicit))
    return cfg


def parse_threshold(text: str) -> tuple[str, float]:
    """Parse one ``--threshold GATE=P`` value."""
    gate, sep, value = text.partition("=")
    gate = gate.strip()
    if not sep or not gate:
        raise ConfigError(f"--threshold expects GATE=P, got {text!r}")
    try:
        p = float(value)
    except ValueError:
        raise ConfigError(f"--threshold {gate}: {value!r} is not a number") from None
    return gate, _check_probability(gate, p, "--threshold")


def apply_cli(cfg: Config, args: Any) -> Config:
    """Fold CLI flags into ``cfg``: ``--threshold`` overrides, ``--context-dir``, ``--all-items``."""
    for text in getattr(args, "threshold", None) or []:
        gate, p = parse_threshold(text)
        cfg.thresholds[gate] = p
    context_dir = getattr(args, "context_dir", None)
    if context_dir:
        cfg.context["dir"] = str(context_dir)
    if getattr(args, "all_items", False):
        cfg.max_items = None
        cfg.context["max_items"] = None
    return cfg
