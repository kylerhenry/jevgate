"""Config layering: defaults ← repo file ← explicit file ← CLI flags."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from jevgate import config
from jevgate.config import Config, ConfigError, apply_cli, load, parse_threshold

ROOT = Path(__file__).resolve().parents[1]


def test_defaults():
    cfg = Config()
    assert cfg.thresholds == {} and cfg.unclear_at == 0.40
    assert cfg.context == {"dir": None, "project": None, "areas": {}, "follow_links": 1,
                           "context_budget": 8000, "max_items": 16}
    assert "package-lock.json" in cfg.ignore and "leverage" in cfg.hedges and "as an AI" in cfg.hedges
    assert (cfg.state_budget, cfg.file_budget, cfg.tests_budget, cfg.context_budget) == (24000, 12000, 6000, 8000)
    assert (cfg.max_file_tokens, cfg.max_items, cfg.max_asks, cfg.workers) == (40000, 16, 4, 4)
    assert cfg.test_log_globs
    assert load(None, None) == Config()
    assert Config().to_dict()["context"]["max_items"] == 16


def test_repo_file(tmp_path):
    (tmp_path / "jevgate.json").write_text(json.dumps({
        "thresholds": {"ac_testable": 0.9}, "workers": 2, "context": {"dir": "notes", "max_items": 4},
        "_doc": {"anything": "ignored"}, "_comment_workers": "ignored too",
    }))
    cfg = load(tmp_path, None)
    assert cfg.thresholds == {"ac_testable": 0.9} and cfg.workers == 2
    assert cfg.context["dir"] == "notes" and cfg.context["max_items"] == 4
    assert cfg.context["follow_links"] == 1  # context merges key-wise
    assert cfg.max_asks == 4


def test_explicit_file_wins(tmp_path):
    (tmp_path / "jevgate.json").write_text(json.dumps({"thresholds": {"a": 0.5, "b": 0.6}, "workers": 2}))
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps({"thresholds": {"b": 0.9}, "unclear_at": 0.3}))
    cfg = load(tmp_path, extra)
    assert cfg.thresholds == {"a": 0.5, "b": 0.9}
    assert cfg.workers == 2 and cfg.unclear_at == 0.3


def test_explicit_file_must_exist(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path, tmp_path / "missing.json")


@pytest.mark.parametrize("data, key", [
    ({"tresholds": {}}, "tresholds"),
    ({"context": {"folder": "x"}}, "context.folder"),
])
def test_unknown_key_named(tmp_path, data, key):
    (tmp_path / "jevgate.json").write_text(json.dumps(data))
    with pytest.raises(ConfigError) as info:
        load(tmp_path, None)
    assert key in str(info.value) and "jevgate.json" in str(info.value)


def test_bad_values(tmp_path):
    for data in ({"thresholds": {"a": 1.5}}, {"workers": "four"}, {"hedges": "leverage"}, {"unclear_at": -1}, {"thresholds": []}):
        (tmp_path / "jevgate.json").write_text(json.dumps(data))
        with pytest.raises(ConfigError):
            load(tmp_path, None)
    (tmp_path / "jevgate.json").write_text("{not json")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load(tmp_path, None)


def test_threshold_overrides_from_cli():
    cfg = Config(thresholds={"a": 0.5})
    args = argparse.Namespace(threshold=["a=0.7", "arch_rule=0.65", " b = 1 "], context_dir="vault", all_items=True)
    apply_cli(cfg, args)
    assert cfg.thresholds == {"a": 0.7, "arch_rule": 0.65, "b": 1.0}
    assert cfg.context["dir"] == "vault"
    assert cfg.max_items is None and cfg.context["max_items"] is None
    assert apply_cli(Config(), argparse.Namespace()) == Config()


@pytest.mark.parametrize("text", ["a", "=0.5", "a=", "a=high", "a=1.5"])
def test_bad_threshold_flag(text):
    with pytest.raises(ConfigError):
        parse_threshold(text)


def test_example_file_documents_every_key():
    example = json.loads((ROOT / "jevgate.json.example").read_text())
    cfg = Config()
    config.merge(cfg, example, "example")  # loads cleanly, no unknown keys
    assert cfg == Config()  # the example carries the defaults
    documented = set(example["_doc"])
    for key in cfg.to_dict():
        assert key in documented, key
    for sub in config.CONTEXT_KEYS:
        assert f"context.{sub}" in documented, sub
