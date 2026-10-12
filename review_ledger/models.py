"""Small records, validation, and explicit ledger states."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class LedgerError(Exception):
    def __init__(self, code: str, message: str, *, hints: dict | None = None):
        self.code = code
        self.hints = safe_error_hints(hints)
        super().__init__(message)


class Assessment(StrEnum):
    UNVERIFIED = "unverified"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    NOT_APPLICABLE = "not_applicable"


class Freshness(StrEnum):
    CURRENT = "current"
    NEEDS_REVALIDATION = "needs_revalidation"
    HISTORICAL = "historical"


OUTCOMES = {"inspection", "behavior_failure", "behavior_passed", "hypothesis_refuted",
            "infrastructure_failure", "timeout", "skipped", "incomplete"}
ELIGIBLE_OUTCOMES = {"inspection", "behavior_failure", "behavior_passed", "hypothesis_refuted"}
SHA = re.compile(r"^[0-9a-f]{40}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*_[0-9a-f]{32}$")


def safe_error_hints(value: Any) -> dict | None:
    """Only bounded identifiers, counters and fixed action/field names reach errors."""
    if not isinstance(value, dict):
        return None
    result = {}
    count = value.get("pending_use_count")
    if type(count) is int and 0 <= count <= 2**53 - 1:
        result["pending_use_count"] = count
    ids = value.get("pending_use_ids")
    if isinstance(ids, list) and len(ids) <= 20 and all(
            isinstance(i, str) and i.startswith("use_") and IDENTIFIER.fullmatch(i) for i in ids):
        result["pending_use_ids"] = ids
    omitted = value.get("pending_use_ids_omitted")
    if type(omitted) is bool:
        result["pending_use_ids_omitted"] = omitted
    invalid = value.get("invalid_sources")
    if isinstance(invalid, list) and len(invalid) <= 20:
        rows = []
        for row in invalid:
            if not isinstance(row, dict):
                continue
            ident, required = row.get("observation_id"), row.get("required_fields")
            if (isinstance(ident, str) and ident.startswith("observation_")
                    and IDENTIFIER.fullmatch(ident) and isinstance(required, list)
                    and len(required) <= 4 and all(isinstance(f, str) and f in {"kind", "outcome", "details", "environment"} for f in required)):
                rows.append({"observation_id": ident, "required_fields": required})
        result["invalid_sources"] = rows
    action = value.get("next_action")
    if isinstance(action, str) and action in {"record_lesson_result_or_pause", "separate_inspection_from_behavior_support"}:
        result["next_action"] = action
    return result or None


def behavior_report_hints(observations) -> dict:
    """Diagnose report shape, never infer execution or expose reported prose."""
    invalid = []
    for observation in observations:
        required = []
        if observation["kind"] != "test":
            required.append("kind")
        if observation["outcome"] == "inspection":
            required.append("outcome")
        if not (observation["details"] or "").strip():
            required.append("details")
        if not (observation["environment"] or "").strip():
            required.append("environment")
        if required:
            invalid.append({"observation_id": observation["id"], "required_fields": required})
    return {"invalid_sources": invalid, "next_action": "separate_inspection_from_behavior_support"}


@dataclass(frozen=True)
class Scope:
    repository_id: int
    repository_name: str


@dataclass(frozen=True)
class Actor:
    """Constructed at the trusted integration boundary, never from model arguments."""
    session_id: str

    def __post_init__(self):
        text(self.session_id, "trusted session identity", 256)


def canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise LedgerError("invalid_input", "Value must be finite JSON data") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text(value: Any, field: str, maximum: int = 4000, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value) > maximum or "\x00" in value:
        raise LedgerError("invalid_input", f"{field} must be text of 1..{maximum} characters")
    return value


def choice(value: Any, field: str, options) -> str:
    if not isinstance(value, str) or value not in options:
        raise LedgerError("invalid_input", f"Invalid {field}; expected one of {', '.join(sorted(options))}")
    return value


def integer(value: Any, field: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise LedgerError("invalid_input", f"{field} must be an integer from {low} to {high}")
    return value


def sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise LedgerError("invalid_input", f"{field} requires a complete lowercase 40-character Git revision")
    return value


def strings(value: Any, field: str, maximum: int = 20, item_size: int = 200) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise LedgerError("invalid_input", f"{field} must be a list with at most {maximum} items")
    return sorted(set(text(v, field, item_size) for v in value))


def fields(data: Any, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(data, dict):
        raise LedgerError("invalid_input", "Expected an object")
    unknown, absent = set(data) - allowed, required - set(data)
    if unknown or absent:
        raise LedgerError("invalid_input", f"Unknown fields: {sorted(unknown)}; missing fields: {sorted(absent)}")
    return data
