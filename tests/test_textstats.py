"""Tests for jevgate.textstats: token estimates, prose stats, rule findings."""

from __future__ import annotations

from jevgate import textstats as ts


def test_tokens_prose_and_code():
    assert ts.tokens("") == 0
    assert ts.tokens("abcd") == 1 and ts.tokens("abcde") == 2
    assert ts.tokens("abcdef", kind="code") == 2 and ts.tokens("abcdefg", "code") == 3


def test_stats_counts_sentences_words_and_bullets():
    text = "# Heading\n\nThe cache sits in front of the store. It expires entries after sixty seconds!\n\n- one bullet here\n- another bullet\n\n```\ncode is skipped. really.\n```\n"
    numbers = ts.stats(text)
    assert numbers["sentences"] == 5  # heading, two prose sentences, two bullets
    assert numbers["words"] == 20
    assert numbers["avg_sentence_words"] == 4.0
    assert numbers["bullet_ratio"] == round(2 / 4, 3)
    assert numbers["hedges"] == []


def test_stats_handles_abbreviations_and_inline_code():
    numbers = ts.stats("Run `pytest -q tests` first, e.g. before a deploy. Then stop.")
    assert numbers["sentences"] == 2


def test_long_word_share_and_hedges_case_insensitive():
    text = "We leverage a Robust, comprehensive and seamlessly integrated implementation in order to streamline everything. It's worth noting various state-of-the-art choices."
    numbers = ts.stats(text)
    assert numbers["hedges"] == [
        "leverage",
        "robust",
        "comprehensive",
        "seamlessly",
        "in order to",
        "streamline",
        "it's worth noting",
        "various",
        "state-of-the-art",
    ]
    assert 0 < numbers["long_word_share"] < 1
    assert ts.find_hedges("we utilize and utilized it", ["utilize"]) == ["utilize", "utilize"]
    assert ts.find_hedges("robustness is robust", ["robust"]) == ["robust", "robust"]
    assert ts.stats("nothing here", hedges=["nothing"])["hedges"] == ["nothing"]


def test_rule_findings_clean_ticket():
    ticket = {
        "why": "Deploys are manual. They take ten minutes.",
        "what": "Add a script. It runs the steps in order.",
        "acceptance": ["One command deploys.", "Failures restore the overlay."],
    }
    assert ts.rule_findings(ticket) == []


def test_rule_findings_fire_with_ids_and_stats():
    long_sentence = " ".join(["configuration synchronization"] * 30) + "."  # 60 long words, one sentence
    ticket = {
        "why": long_sentence,
        "what": "We leverage robust seamless comprehensive holistic tooling to streamline the implementation of infrastructural orchestration.",
        "acceptance": ["administrative configuration synchronization"],
    }
    findings = ts.rule_findings(ticket)
    ids = [f["id"] for f in findings]
    assert ids == ["rule:long_sentences", "rule:dense_words", "rule:hedges"]
    for finding in findings:
        assert finding["severity"] == "warn"
        assert finding["message"] and finding["hint"]
        assert finding["stats"]["sentences"] >= 3
    assert "leverage" in findings[2]["message"]
    relaxed = ts.rule_findings(ticket, max_avg_sentence_words=100, max_long_word_share=0.9, max_hedges=10)
    assert relaxed == []
    custom = ts.rule_findings(ticket, max_avg_sentence_words=100, max_long_word_share=0.9, max_hedges=0, hedges=["tooling"])
    assert [f["id"] for f in custom] == ["rule:hedges"] and custom[0]["stats"]["hedges"] == ["tooling"]
