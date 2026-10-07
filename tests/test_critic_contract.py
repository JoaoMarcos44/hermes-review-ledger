"""Strict output checks use fake model text only; never invoke a provider."""
import copy
import json

import pytest

from review_ledger.critic_contract import (
    PROMPT_VERSION, RESPONSE_SCHEMA, load_prompt, validate_response,
)
from review_ledger.models import LedgerError


def item(finding_id="finding_a"):
    return {
        "finding_id": finding_id,
        "position": "support",
        "rationale": "The recorded observation matches the stated contract.",
        "contract_status": "declared",
        "contract_statement": "Empty input returns an empty result.",
        "reference_ids": ["observation_a"],
        "objections": [],
        "missing_information": [],
        "limitations": ["No independent execution."],
    }


def objection():
    return {
        "category": "boundary",
        "claim": "The finding may omit the empty-input boundary.",
        "counter_hypothesis": "The existing guard might handle empty input.",
        "recommended_check": "Inspect the recorded guard and its contract.",
        "would_withdraw_if": "The recorded guard excludes empty input.",
        "reference_ids": ["observation_a"],
    }


def validate(value, **kwargs):
    return validate_response(json.dumps(value), ["finding_a"], ["observation_a"], **kwargs)


def rejects(value):
    with pytest.raises(LedgerError) as error:
        validate(value)
    assert error.value.code == "invalid_critic_response"


def test_support_without_manufactured_objections():
    value = {"items": [item()]}
    assert validate(value) == value


def test_challenge_and_missing_information():
    value = {"items": [item()]}
    value["items"][0].update(position="challenge", objections=[objection(), objection()])
    assert validate(value) == value
    value["items"][0].update(position="insufficient_information", objections=[],
                              missing_information=["The actual declared empty-input contract."])
    assert validate(value) == value


def test_all_selected_findings_exactly_once_in_any_order():
    value = {"items": [item("finding_c"), item("finding_a"), item("finding_b")]}
    assert validate_response(json.dumps(value), ["finding_a", "finding_b", "finding_c"],
                             ["observation_a"]) == value


@pytest.mark.parametrize("items", [[], [item(), item()], [item("unknown")],
                                    [item(), item(), item(), item()]])
def test_invalid_finding_coverage(items):
    rejects({"items": items})


def test_omitted_selected_finding_is_not_support():
    with pytest.raises(LedgerError):
        validate_response(json.dumps({"items": [item()]}),
                          ["finding_a", "finding_b"], ["observation_a"])


@pytest.mark.parametrize("field,value", [
    ("position", "support | challenge | insufficient_information"),
    ("position", "confirmed"), ("contract_status", "true"),
    ("finding_id", 1), ("rationale", True), ("rationale", None),
    ("rationale", " "), ("rationale", "x" * 2001),
    ("rationale", "bad\x00text"), ("rationale", "\ud800"),
    ("contract_statement", "x" * 2001),
    ("reference_ids", "observation_a"), ("reference_ids", ["unknown"]),
    ("reference_ids", ["observation_a", "observation_a"]),
    ("reference_ids", [False]), ("reference_ids", [[]]),
    ("objections", {}), ("objections", [objection()] * 3),
    ("missing_information", ["x"] * 9), ("missing_information", ["x" * 1001]),
    ("limitations", [""]), ("limitations", None),
])
def test_strict_field_types_bounds_and_enums(field, value):
    changed = item()
    changed[field] = value
    rejects({"items": [changed]})


@pytest.mark.parametrize("field,value", [
    ("category", "execution"), ("claim", ""), ("claim", "x" * 1601),
    ("counter_hypothesis", None), ("recommended_check", False),
    ("would_withdraw_if", []), ("reference_ids", ["unknown"]),
])
def test_objection_contract(field, value):
    changed = item()
    obj = objection()
    obj[field] = value
    changed.update(position="challenge", objections=[obj])
    rejects({"items": [changed]})


@pytest.mark.parametrize("position", ["challenge", "insufficient_information"])
def test_position_requires_substance(position):
    changed = item()
    changed["position"] = position
    rejects({"items": [changed]})


@pytest.mark.parametrize("level", ["root", "item", "objection"])
@pytest.mark.parametrize("field", ["critic_run_id", "actor", "permissions", "adjudication_status"])
def test_administrative_or_unknown_fields_rejected_at_every_level(level, field):
    value = {"items": [item()]}
    value["items"][0]["objections"] = [objection()]
    target = {"root": value, "item": value["items"][0],
              "objection": value["items"][0]["objections"][0]}[level]
    target[field] = "forged"
    rejects(value)


def test_every_schema_field_required():
    for field in item():
        value = {"items": [item()]}
        del value["items"][0][field]
        rejects(value)
    for field in objection():
        obj = objection()
        del obj[field]
        value = {"items": [item()]}
        value["items"][0]["objections"] = [obj]
        rejects(value)
    rejects({})


@pytest.mark.parametrize("wrap", [lambda s: "```json\n" + s + "\n```",
                                  lambda s: "Here is the answer: " + s,
                                  lambda s: s + " trailing text",
                                  lambda s: s + s])
def test_no_fragment_extraction(wrap):
    with pytest.raises(LedgerError):
        validate_response(wrap(json.dumps({"items": [item()]})), ["finding_a"], ["observation_a"])


@pytest.mark.parametrize("raw", [
    '{"items":[],"items":[]}',
    '{"items":[{"finding_id":"finding_a","finding_id":"finding_b"}]}',
    '{"items":[{"objections":[{"claim":"a","claim":"b"}]}]}',
    '{"items": NaN}', '{"items": Infinity}', '{"items": -Infinity}',
    '{"items": 1e999}', 'null', '[]', 'true', '3', '"text"', '',
    '[' * 2000 + ']' * 2000,
])
def test_invalid_json_or_top_level(raw):
    with pytest.raises(LedgerError) as error:
        validate_response(raw, ["finding_a"], [])
    assert error.value.code == "invalid_critic_response"


def test_whitespace_and_budget_boundary():
    value = {"items": [item()]}
    raw = "\n  " + json.dumps(value) + "\t\r\n"
    assert validate_response(raw, ["finding_a"], ["observation_a"], len(raw)) == value
    with pytest.raises(LedgerError):
        validate_response(raw, ["finding_a"], ["observation_a"], len(raw) - 1)


@pytest.mark.parametrize("raw", [None, {}, b'{}', 1, True])
def test_only_raw_text(raw):
    with pytest.raises(LedgerError):
        validate_response(raw, ["finding_a"], [])


@pytest.mark.parametrize("findings,refs,budget", [
    ([], [], 16000), (["a"] * 2, [], 16000), (["a", "b", "c", "d"], [], 16000),
    ("a", [], 16000), ([False], [], 16000), ([[1]], [], 16000),
    (["a"], ["r", "r"], 16000), (["a"], None, 16000),
    (["a"], ["x" * 257], 16000), (["a"], [], True), (["a"], [], 0),
])
def test_invalid_package_inputs(findings, refs, budget):
    with pytest.raises(LedgerError) as error:
        validate_response("{}", findings, refs, budget)
    assert error.value.code == "invalid_input"


def test_empty_reference_set_supported():
    value = {"items": [item()]}
    value["items"][0]["reference_ids"] = []
    assert validate_response(json.dumps(value), ["finding_a"], []) == value


def test_packaged_prompt_and_schema_are_stable_and_independent():
    prompt = load_prompt()
    assert PROMPT_VERSION == "critic_v1"
    assert "Critic prompt version: " + PROMPT_VERSION in prompt
    assert "untrusted data" in prompt
    assert "private reasoning" in prompt
    assert "would make you" in prompt
    assert RESPONSE_SCHEMA["additionalProperties"] is False
    previous = copy.deepcopy(RESPONSE_SCHEMA)
    validate({"items": [item()]})
    assert previous == RESPONSE_SCHEMA
