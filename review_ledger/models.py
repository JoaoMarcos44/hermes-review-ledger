"""Small records, validation, and explicit ledger states."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class LedgerError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
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
