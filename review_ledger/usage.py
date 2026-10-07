"""Bounded local telemetry, never a proxy for full inference cost or quality.

The Hermes adapter supplies only locally observed rendered request/response text.
No provider hook exists here. Optional provider normalization is a pure function
for a future trusted adapter contract, and is not exposed as a model tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import math
import time
from typing import Callable, TypeVar

from .models import Scope, canonical
from .storage import Store, now

T = TypeVar("T")
TOOLS = frozenset({"ledger_open", "ledger_recall", "ledger_record", "ledger_assess",
                   "ledger_lesson", "ledger_export", "ledger_control", "ledger_context",
                   "ledger_status", "ledger_resume", "ledger_run", "ledger_observe"})


def metric(value, unit, *, coverage="plugin_boundary", source="local_counter", quality="measured"):
    return {"value": value, "unit": unit, "coverage": coverage, "source": source,
            "quality": "unavailable" if value is None else quality}


@dataclass(frozen=True)
class ProviderUsage:
    """Trusted adapter data only. All subsets are included in their parent totals.

    A provider reporting additive cache tokens must convert them to inclusive
    input totals before constructing this record. No credentials or request text
    belong here. Correlation is to one actual inference attempt, including retries.
    """
    attempt_id: str
    contract: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None


def normalize_provider_usage(usage: ProviderUsage | None, *, trusted_contract: str | None = None):
    """Return unavailable unless a trusted host adapter supplies a known contract.

    This type does not authenticate its caller; the boundary must not construct it
    from model arguments. No host integration currently passes this parameter.
    """
    names = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")
    if usage is None or not trusted_contract or usage.contract != trusted_contract:
        return {name: metric(None, "tokens", coverage="inference_attempt", source="host_unavailable")
                for name in (*names, "total_tokens")}
    if not usage.attempt_id:
        raise ValueError("An actual inference attempt identity is required")
    for name in names:
        value = getattr(usage, name)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("Token counters must be nonnegative integers or unavailable")
    for part, whole in ((usage.cached_input_tokens, usage.input_tokens),
                        (usage.reasoning_output_tokens, usage.output_tokens)):
        if part is not None and (whole is None or part > whole):
            raise ValueError("A measured subset requires an inclusive parent total")
    result = {name: metric(getattr(usage, name), "tokens", coverage="inference_attempt",
                           source=trusted_contract) for name in names}
    total = (usage.input_tokens + usage.output_tokens
             if usage.input_tokens is not None and usage.output_tokens is not None else None)
    result["total_tokens"] = metric(total, "tokens", coverage="inference_attempt", source=trusted_contract)
    result["subset_semantics"] = "Cache is included in input; reasoning is included in output. Never add subsets again."
    return result


class Usage:
    def __init__(self, store: Store, *, max_events: int = 10000, retention_days: int = 30):
        if type(max_events) is not int or not 1 <= max_events <= 100000:
            raise ValueError("max_events must be 1..100000")
        if type(retention_days) is not int or not 1 <= retention_days <= 365:
            raise ValueError("retention_days must be 1..365")
        self.store, self.max_events, self.retention_days = store, max_events, retention_days

    def record(self, scope: Scope, *, event_id: str, tool: str, request_text: str, response_text: str,
               latency_ms: float, run_id: str | None = None, candidate_count: int | None = None,
               selected_count: int | None = None, omitted_count: int | None = None,
               confirmed_delivery: bool = False, source: str = "adapter") -> bool:
        """Best effort, after the business operation. Never change its result.

        Deduplicate actual events, not repeated output content. The text is used
        transiently for exact counters and a content identity, and never persisted.
        Similar output is only an indicator, not a judgment that a call was wasted.
        """
        try:
            if not isinstance(event_id, str) or not event_id or tool not in TOOLS:
                return False
            if source not in {"adapter", "fixture", "log_inference"}:
                return False
            if not isinstance(request_text, str) or not isinstance(response_text, str):
                return False
            if isinstance(latency_ms, bool) or not math.isfinite(latency_ms) or latency_ms < 0:
                return False
            if type(confirmed_delivery) is not bool:
                return False
            counts = (candidate_count, selected_count, omitted_count)
            if any(v is not None and (type(v) is not int or v < 0) for v in counts):
                return False
            request_bytes, response_bytes = request_text.encode("utf-8"), response_text.encode("utf-8")
            identity = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
            response_digest = hashlib.sha256(response_bytes).hexdigest()
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat(timespec="microseconds")
            with self.store.connect() as conn, self.store.transaction(conn):
                if run_id is not None:
                    self.store.run(conn, scope, run_id)
                if conn.execute("SELECT 1 FROM usage_events WHERE event_id=?", (identity,)).fetchone():
                    return True
                duplicate = conn.execute("SELECT 1 FROM usage_events WHERE repository_id=? AND run_id IS ? AND tool=? AND source=? AND response_digest=? LIMIT 1",
                                         (scope.repository_id, run_id, tool, source, response_digest)).fetchone() is not None
                conn.execute("INSERT INTO usage_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (identity, scope.repository_id, run_id, tool, source, now(), len(request_text), len(request_bytes),
                              len(response_text), len(response_bytes), response_digest, latency_ms,
                              *counts, int(duplicate), int(confirmed_delivery)))
                conn.execute("DELETE FROM usage_events WHERE created_at<?", (cutoff,))
                conn.execute("DELETE FROM usage_events WHERE event_id IN (SELECT event_id FROM usage_events ORDER BY created_at DESC,event_id DESC LIMIT -1 OFFSET ?)", (self.max_events,))
            return True
        except Exception:
            # Telemetry is deliberately isolated from committed evidence writes.
            return False

    def observe(self, scope: Scope, invoke: Callable[[], T], *, event_id: str, tool: str,
                request_text: str, run_id: str | None = None) -> T:
        start = time.perf_counter()
        result = invoke()
        try:
            self.record(scope, event_id=event_id, tool=tool, request_text=request_text,
                        response_text=result if isinstance(result, str) else canonical(result),
                        run_id=run_id, latency_ms=(time.perf_counter() - start) * 1000)
        except Exception:
            pass
        return result

    def report(self, scope: Scope, run_id: str | None = None) -> dict:
        """Retained local totals grouped by provenance; missing is never zero usage."""
        with self.store.connect() as conn:
            if run_id is not None:
                self.store.run(conn, scope, run_id)
            clause = " WHERE repository_id=?" + (" AND run_id=?" if run_id is not None else "")
            args = (scope.repository_id, run_id) if run_id is not None else (scope.repository_id,)
            rows = conn.execute("SELECT source,COUNT(*) AS events,SUM(request_chars) AS request_chars,SUM(request_bytes) AS request_bytes,"
                                "SUM(response_chars) AS response_chars,SUM(response_bytes) AS response_bytes,SUM(latency_ms) AS latency_ms,"
                                "SUM(candidate_count) AS candidate_count,COUNT(candidate_count) AS candidate_samples,"
                                "SUM(selected_count) AS selected_count,COUNT(selected_count) AS selected_samples,"
                                "SUM(omitted_count) AS omitted_count,COUNT(omitted_count) AS omitted_samples,"
                                "SUM(duplicate_response) AS duplicates,SUM(confirmed_delivery) AS confirmed,MIN(created_at) AS first_event,MAX(created_at) AS last_event "
                                "FROM usage_events" + clause + " GROUP BY source ORDER BY source", args).fetchall()
        groups = []
        for row in rows:
            quality = "estimated" if row["source"] == "log_inference" else "measured"
            counters = {}
            for key, unit in (("request_chars", "characters"), ("request_bytes", "utf8_bytes"),
                              ("response_chars", "characters"), ("response_bytes", "utf8_bytes"),
                              ("latency_ms", "milliseconds"), ("events", "rendered_responses"),
                              ("candidate_count", "records"), ("selected_count", "records"),
                              ("omitted_count", "records"), ("duplicates", "repeated_response_indicators")):
                counters[key] = metric(row[key], unit, source=row["source"], quality=quality)
            counters["confirmed_delivery"] = metric(row["confirmed"] if row["confirmed"] else None,
                                                      "confirmed_responses", source=row["source"])
            groups.append({"source": row["source"], "metrics": counters,
                           "samples": {key: row[key + "_samples"] for key in ("candidate", "selected", "omitted")},
                           "first_event": row["first_event"], "last_event": row["last_event"]})
        return {"groups": groups, "retention": {"max_events": self.max_events, "days": self.retention_days},
                "provider_usage": normalize_provider_usage(None),
                "cost": metric(None, "currency", source="host_unavailable", coverage="full_review"),
                "notice": "Retained plugin-boundary measurements only. Rendered is not confirmed delivery. "
                          "Fixtures and inferred logs are separate. Repetition may be necessary. "
                          "No total-review savings, billing, quota, tokenizer count, or context residency is established."}
