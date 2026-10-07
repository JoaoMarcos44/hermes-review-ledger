"""Versioned, provider-independent contract for untrusted critic responses."""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from .models import LedgerError

PROMPT_VERSION = "critic_v1"
_MAX_REFERENCES = 128


def _string(maximum: int) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": maximum}


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


_REFERENCES = {"type": "array", "items": _string(256),
               "maxItems": _MAX_REFERENCES, "uniqueItems": True}
_NOTES = {"type": "array", "items": _string(1000), "maxItems": 8}
_OBJECTION = _object({
    "category": {"type": "string", "enum": ["contract", "evidence", "alternative", "boundary", "proposed_fix"]},
    "claim": _string(1600),
    "counter_hypothesis": _string(1600),
    "recommended_check": _string(1600),
    "would_withdraw_if": _string(1600),
    "reference_ids": _REFERENCES,
})
_ITEM = _object({
    "finding_id": _string(256),
    "position": {"type": "string", "enum": ["support", "challenge", "insufficient_information"]},
    "rationale": _string(2000),
    "contract_status": {"type": "string", "enum": ["declared", "inferred", "unknown"]},
    "contract_statement": _string(2000),
    "reference_ids": _REFERENCES,
    "objections": {"type": "array", "items": _OBJECTION, "maxItems": 2},
    "missing_information": _NOTES,
    "limitations": _NOTES,
})
RESPONSE_SCHEMA = _object({
    "items": {"type": "array", "items": _ITEM, "minItems": 1, "maxItems": 3},
})


def load_prompt() -> str:
    """Read the packaged original English prompt, without a host dependency."""
    return files("review_ledger").joinpath("prompts", "critic_v1.md").read_text(encoding="utf-8")


def _invalid(message: str) -> None:
    raise LedgerError("invalid_critic_response", message)


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid("Duplicate JSON object key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    _invalid("Non-finite JSON constants are forbidden")


def _validate(value: Any, schema: dict, path: str) -> None:
    kind = schema["type"]
    if kind == "object":
        if type(value) is not dict or set(value) != set(schema["properties"]):
            _invalid(f"{path} must contain exactly the specified object fields")
        for key, child in schema["properties"].items():
            _validate(value[key], child, f"{path}.{key}")
    elif kind == "array":
        if type(value) is not list or not schema.get("minItems", 0) <= len(value) <= schema["maxItems"]:
            _invalid(f"{path} has an invalid array type or length")
        for index, child in enumerate(value):
            _validate(child, schema["items"], f"{path}[{index}]")
        if schema.get("uniqueItems") and len(value) != len(set(value)):
            _invalid(f"{path} contains duplicate references")
    elif kind == "string":
        if type(value) is not str or not value.strip() or "\x00" in value:
            _invalid(f"{path} must be nonempty text without NUL")
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            _invalid(f"{path} contains an invalid Unicode surrogate")
        if "enum" in schema and value not in schema["enum"]:
            _invalid(f"{path} has an invalid enum value")
        if len(value) > schema.get("maxLength", 256):
            _invalid(f"{path} exceeds its text limit")


def _input_ids(value: Any, name: str, minimum: int, maximum: int) -> set[str]:
    if (type(value) is not list or not minimum <= len(value) <= maximum
            or any(type(item) is not str or not item.strip() or len(item) > 256
                   or "\x00" in item or any(0xD800 <= ord(c) <= 0xDFFF for c in item)
                   for item in value)
            or len(value) != len(set(value))):
        raise LedgerError("invalid_input", f"{name} must contain unique bounded string IDs")
    return set(value)


def validate_response(raw: str, finding_ids: list[str], reference_ids: list[str],
                      max_chars: int = 16000) -> dict:
    """Validate the entire raw JSON response and its package-scoped references.

    JSON whitespace is allowed; surrounding prose, code fences, unknown fields,
    coercions and incomplete finding coverage are never accepted. The schema is
    host-facing guidance; validation here remains authoritative.
    """
    findings = _input_ids(finding_ids, "finding_ids", 1, 3)
    references = _input_ids(reference_ids, "reference_ids", 0, _MAX_REFERENCES)
    if type(max_chars) is not int or max_chars < 1:
        raise LedgerError("invalid_input", "max_chars must be a positive integer")
    if type(raw) is not str or len(raw) > max_chars:
        _invalid("Response must be raw text within the configured character budget")
    try:
        result = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (ValueError, RecursionError) as exc:
        raise LedgerError("invalid_critic_response", "Response must be one complete strict JSON object") from exc
    _validate(result, RESPONSE_SCHEMA, "response")
    selected = [item["finding_id"] for item in result["items"]]
    if len(selected) != len(findings) or set(selected) != findings:
        _invalid("Response must contain exactly one item per selected finding")
    for item in result["items"]:
        if item["position"] == "challenge" and not item["objections"]:
            _invalid("A challenge requires at least one concrete objection")
        if item["position"] == "insufficient_information" and not item["missing_information"]:
            _invalid("Insufficient information must identify the missing information")
        for source in [item, *item["objections"]]:
            if not set(source["reference_ids"]).issubset(references):
                _invalid("Response contains a reference outside the supplied package")
    return result
