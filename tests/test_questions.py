"""Question shapes and the policy that turns answers into readings."""

from __future__ import annotations

import pytest

from jevgate.questions import (
    Choice, Gate, Noul, Score, group_by_needs, read, to_questions, with_overrides,
)

# -- to_api -------------------------------------------------------------------


def test_noul_to_api_with_and_without_criteria():
    assert Noul("Is it?").to_api() == {"type": "noul", "instructions": "Is it?"}
    assert Noul("Is it?", true="yes", false="no").to_api() == {
        "type": "noul", "instructions": "Is it?", "criteria": {"true": "yes", "false": "no"}}
    assert Noul("Is it?", true="yes").to_api()["criteria"] == {"true": "yes"}


def test_choice_to_api():
    q = Choice("Which?", {"a": "A", "b": {"description": "B"}, "unclear": None})
    assert q.to_api() == {"type": "choice", "instructions": "Which?",
                          "criteria": {"a": "A", "b": {"description": "B"}, "unclear": None}}


def test_score_to_api_accepts_list():
    q = Score("How?", ["none", "some", "all"])
    assert q.to_api() == {"type": "score", "instructions": "How?", "criteria": ["none", "some", "all"]}
    assert q.levels == ("none", "some", "all")
    assert hash(q)  # frozen and hashable


# -- gates --------------------------------------------------------------------


def pass_gate(threshold=0.85):
    return Gate("why_is_a_problem", Noul("q"), "pass", needs={"glossary"}, threshold=threshold)


def fire_gate(threshold=0.60):
    return Gate("rule:R01", Noul("q"), "fire", threshold=threshold)


def level_gate(threshold=0.70):
    return Gate("effort", Score("q", ["a", "b", "c", "d"]), "level", acceptable={0, 1, 2}, threshold=threshold)


def choice_pass_gate(threshold=0.90):
    return Gate("ac_met:2", Choice("q", {"met": "M", "partial": "P", "not_met": "N", "unclear": "U"}),
                "choice", pass_options=("met",), threshold=threshold, item={"index": 2})


def choice_fire_gate(threshold=0.60):
    return Gate("arch_rule:R03", Choice("q", {"complies": "C", "violates": "V", "not_applicable": "NA", "unclear": "U"}),
                "choice", fail_options=("violates",), threshold=threshold, hint="cite the rule")


def choice_answer(**probabilities):
    top = max(probabilities, key=probabilities.get)
    return {"type": "choice", "choice": top, "confidence": probabilities[top], "probabilities": probabilities}


def test_gate_validation():
    with pytest.raises(ValueError, match="kind"):
        Gate("x", Noul("q"), "maybe")
    with pytest.raises(ValueError, match="exactly one"):
        Gate("x", Choice("q", {"a": None}), "choice")
    with pytest.raises(ValueError, match="exactly one"):
        Gate("x", Choice("q", {"a": None}), "choice", pass_options=("a",), fail_options=("a",))
    with pytest.raises(ValueError, match="acceptable"):
        Gate("x", Score("q", ["a"]), "level")


def test_gate_family_and_item_id():
    assert choice_fire_gate().family == "arch_rule" and choice_fire_gate().item_id == "R03"
    assert pass_gate().family == "why_is_a_problem" and pass_gate().item_id is None
    assert Gate("dup:cache-helper", Noul("q"), "info").item_id == "cache-helper"


def test_gates_are_hashable_with_needs():
    gate = pass_gate()
    assert gate.needs == frozenset({"glossary"})
    assert {gate: 1}[gate] == 1


# -- read: pass / fire / info -------------------------------------------------


def test_read_pass_gate():
    ok = read(pass_gate(), {"type": "noul", "noul": 0.95})
    assert (ok.status, ok.p, ok.p_pass, ok.p_fail, ok.borderline) == ("pass", 0.95, 0.95, 0.05, False)
    assert ok.kind == "pass" and ok.threshold == 0.85 and ok.gate_id == "why_is_a_problem"
    bad = read(pass_gate(), {"type": "noul", "noul": 0.5})
    assert bad.status == "fail" and bad.p_pass == 0.5
    edge = read(pass_gate(), {"type": "noul", "noul": 0.85})
    assert edge.status == "pass" and edge.borderline is True
    near = read(pass_gate(), {"type": "noul", "noul": 0.81})
    assert near.status == "fail" and near.borderline is True
    far = read(pass_gate(), {"type": "noul", "noul": 0.79})
    assert far.status == "fail" and far.borderline is False


def test_read_fire_gate():
    fired = read(fire_gate(), {"type": "noul", "noul": 0.75})
    assert (fired.status, fired.p, fired.p_pass, fired.p_fail) == ("fail", 0.75, 0.25, 0.75)
    quiet = read(fire_gate(), {"type": "noul", "noul": 0.1})
    assert quiet.status == "pass" and quiet.p_pass == 0.9
    edge = read(fire_gate(), {"type": "noul", "noul": 0.58})
    assert edge.status == "pass" and edge.borderline


def test_read_info_gate_never_fails():
    gate = Gate("touches_ac:1", Noul("q"), "info", threshold=0.5)
    low = read(gate, {"type": "noul", "noul": 0.05})
    assert low.status == "pass" and low.p == 0.05 and low.borderline is False
    assert read(gate, None).status == "unknown"


# -- read: level --------------------------------------------------------------


def test_read_level_gate():
    answer = {"type": "score", "score": 1, "confidence": 0.5, "probabilities": {"0": 0.2, "1": 0.5, "2": 0.1, "3": 0.2}}
    r = read(level_gate(), answer)
    assert r.status == "pass" and r.p == pytest.approx(0.8) and r.level == 1 and r.legend == "b"
    assert r.probabilities == {"0": 0.2, "1": 0.5, "2": 0.1, "3": 0.2}
    assert r.confidence == 0.5
    big = {"type": "score", "score": 3, "confidence": 0.7, "probabilities": {"0": 0.0, "1": 0.1, "2": 0.2, "3": 0.7}}
    assert read(level_gate(), big).status == "fail"
    assert read(level_gate(), big).p == pytest.approx(0.3)


# -- read: choice -------------------------------------------------------------


def test_read_choice_pass_style():
    r = read(choice_pass_gate(), choice_answer(met=0.93, partial=0.04, not_met=0.02, unclear=0.01))
    assert r.status == "pass" and r.p == 0.93 and r.p_pass == 0.93 and r.choice == "met" and r.legend == "M"
    assert r.item == {"index": 2}
    r = read(choice_pass_gate(), choice_answer(met=0.6, partial=0.3, not_met=0.05, unclear=0.05))
    assert r.status == "fail" and r.p_fail == pytest.approx(0.4)
    edge = read(choice_pass_gate(), choice_answer(met=0.88, partial=0.1, not_met=0.01, unclear=0.01))
    assert edge.status == "fail" and edge.borderline


def test_read_choice_fire_style():
    r = read(choice_fire_gate(), choice_answer(complies=0.2, violates=0.7, not_applicable=0.05, unclear=0.05))
    assert r.status == "fail" and r.p == 0.7 and r.p_fail == 0.7 and r.p_pass == pytest.approx(0.3)
    r = read(choice_fire_gate(), choice_answer(complies=0.8, violates=0.1, not_applicable=0.05, unclear=0.05))
    assert r.status == "pass" and r.borderline is False


def test_read_choice_unclear_and_na():
    unclear = read(choice_fire_gate(), choice_answer(complies=0.25, violates=0.15, not_applicable=0.05, unclear=0.55))
    assert unclear.status == "unclear" and unclear.p_unclear == 0.55 and unclear.borderline is False
    below = read(choice_fire_gate(), choice_answer(complies=0.3, violates=0.2, not_applicable=0.05, unclear=0.45))
    assert below.status == "pass"  # 0.45 is under the 0.50 default
    na = read(choice_fire_gate(), choice_answer(complies=0.1, violates=0.3, not_applicable=0.55, unclear=0.05))
    assert na.status == "na"
    # na wins over unclear, unclear wins over pass/fail
    both = read(choice_fire_gate(), choice_answer(complies=0.0, violates=0.0, not_applicable=0.5, unclear=0.5))
    assert both.status == "na"
    custom = Gate("g", Choice("q", {"yes": None, "no": None, "dunno": None}), "choice",
                  fail_options=("yes",), unclear_options=("dunno",), unclear_at=0.3, threshold=0.6)
    assert read(custom, choice_answer(yes=0.5, no=0.15, dunno=0.35)).status == "unclear"
    assert read(custom, choice_answer(yes=0.7, no=0.1, dunno=0.2)).status == "fail"


def test_read_none_is_unknown_for_every_kind():
    for gate in (pass_gate(), fire_gate(), level_gate(), choice_pass_gate(), choice_fire_gate()):
        r = read(gate, None)
        assert r.status == "unknown" and r.p is None and r.p_pass is None and r.borderline is False
        assert r.gate_id == gate.id and r.threshold == gate.threshold
    assert read(pass_gate(), {"type": "noul"}).status == "unknown"
    assert read(level_gate(), {"type": "score", "score": 1}).status == "unknown"


def test_reading_to_dict():
    d = read(pass_gate(), {"type": "noul", "noul": 0.9}).to_dict()
    assert d["gate_id"] == "why_is_a_problem" and d["status"] == "pass" and d["p_unclear"] == 0.0
    assert set(d) >= {"p", "p_pass", "p_fail", "p_unclear", "status", "borderline", "level", "legend",
                      "probabilities", "confidence", "choice", "item", "kind", "threshold"}


# -- grouping, overrides, questions -------------------------------------------


def test_group_by_needs_preserves_order():
    a = Gate("a", Noul("q"), "pass", needs=())
    b = Gate("b", Noul("q"), "pass", needs={"architecture", "components"})
    c = Gate("c", Noul("q"), "pass", needs={"components", "architecture"})
    d = Gate("d", Noul("q"), "pass")
    groups = group_by_needs([a, b, c, d])
    assert list(groups) == [frozenset(), frozenset({"architecture", "components"})]
    assert [g.id for g in groups[frozenset()]] == ["a", "d"]
    assert [g.id for g in groups[frozenset({"architecture", "components"})]] == ["b", "c"]


def test_with_overrides_by_id_and_family():
    gates = [choice_fire_gate(), Gate("arch_rule:R07", Noul("q"), "fire", threshold=0.6), pass_gate(), fire_gate()]
    out = with_overrides(gates, {"arch_rule": 0.7, "arch_rule:R07": 0.95, "why_is_a_problem": 0.5})
    assert [g.threshold for g in out] == [0.7, 0.95, 0.5, 0.6]
    assert [g.id for g in out] == [g.id for g in gates]
    assert out[0].hint == "cite the rule" and out[0].fail_options == ("violates",)
    assert gates[0].threshold == 0.6  # originals untouched
    assert with_overrides(gates, {}) == gates


def test_to_questions():
    qs = to_questions([pass_gate(), choice_fire_gate()])
    assert list(qs) == ["why_is_a_problem", "arch_rule:R03"]
    assert qs["arch_rule:R03"]["type"] == "choice" and "violates" in qs["arch_rule:R03"]["criteria"]


def test_float_score_gets_nearest_level_and_legend():
    from jevgate.questions import Gate, Score, read

    gate = Gate("readability", Score("How readable?", ["dense", "wordy", "plain"]), kind="level",
                threshold=0.6, acceptable=frozenset({2}))
    answer = {"type": "score", "score": 1.95, "confidence": 0.92,
              "probabilities": {"0": 0.0, "1": 0.05, "2": 0.95}, "legend": {"0": "dense", "1": "wordy", "2": "plain"}}
    reading = read(gate, answer)
    assert reading.level == 2 and reading.legend == "plain" and reading.status == "pass"
