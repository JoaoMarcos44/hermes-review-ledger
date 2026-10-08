"""Bounded local telemetry, never a proxy for full inference cost or quality.

The Hermes adapter supplies only locally observed rendered request/response text.
No provider hook exists here. Optional provider normalization is a pure function
for a future trusted adapter contract, and is not exposed as a model tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import time
from typing import Callable, TypeVar

from .models import Scope, canonical
from .storage import Store, now

T = TypeVar("T")
TOOLS = frozenset({"ledger_open", "ledger_recall", "ledger_record", "ledger_assess",
                   "ledger_lesson", "ledger_export", "ledger_control", "ledger_context",
                   "ledger_status", "ledger_resume", "ledger_run", "ledger_observe", "ledger_critic"})


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
    @staticmethod
    def compression_measurement(bundle, result, *, counter, selection_ms,
                                transformation_ms, adjustment, requested_mode):
        """Best-effort, body-free diagnostics for one actual rendered response.

        Candidate size is NOT an equivalent baseline for selected output. The
        separate same-record comparison covers only the selected record payload
        and its common-field envelope. It does not claim whole-response or
        full-review savings. Counting above 256 KiB skips optional tokenization.
        Source text and arbitrary adjustment/counter fields are never retained.
        """
        try:
            from .compression import POLICY_VERSION, expand_records, project_records
            from .token_count import CountSession

            started = time.perf_counter()
            counts = counter if counter is not None else CountSession()
            sizes = {}
            counting_ms = 0.0
            tokenization_skips = 0

            def measure(value):
                nonlocal counting_ms, tokenization_skips
                count_started = time.perf_counter()
                rendered = canonical(value)
                byte_count = len(rendered.encode("utf-8"))
                tokenization_skips += int(byte_count > 256 * 1024)
                counted = (counts.measure(rendered) if byte_count <= 256 * 1024 else
                           {"chars": len(rendered), "bytes": byte_count, "tokens": None,
                            "counter": None})
                # Exact byte/character values come from the actual string, even
                # when an optional caller-provided counter fails its contract.
                tokens = counted.get("tokens")
                tokens = tokens if type(tokens) is int and tokens >= 0 else None
                identity = Usage._compression_counter(counted.get("counter"))
                # A numeric result without explicit counter identity is not an
                # attributable local-token measurement.
                if identity is None:
                    tokens = None
                counting_ms += (time.perf_counter() - count_started) * 1000
                return {"chars": len(rendered), "bytes": byte_count, "tokens": tokens,
                        "counter": identity,
                        "quality": {"chars": "exact", "bytes": "exact",
                                    "tokens": "exact_local_string" if tokens is not None else "unavailable"}}

            full_candidates = {key: value for key, value in bundle.items()
                               if key != "_snapshot_key"}
            representation = result.get("representation", {})
            records = result.get("records", [])
            expanded = expand_records(records, representation.get("common_fields", {}))
            projected, common = project_records(expanded)
            sizes["bounded_candidates_full"] = measure(full_candidates)
            sizes["selected_output"] = measure(result)
            sizes["same_records_full"] = measure({"records": expanded})
            sizes["same_records_compact"] = measure({"records": projected, "common_fields": common})
            numeric = lambda value: (value if type(value) in (int, float)
                                      and math.isfinite(value) and value >= 0 else None)
            integer_count = lambda value: value if type(value) is int and 0 <= value <= 1_000_000 else None
            record_kinds = ("observation", "assessment", "finding", "lesson", "skill", "snapshot_completeness")
            mode = lambda value: value if value in ("full", "compact", "reference") else "unknown"
            measured = {
                "schema_version": 1, "policy_version": POLICY_VERSION,
                "requested_mode": mode(requested_mode),
                "effective_mode": mode(representation.get("mode")),
                "source": "local_counter", "coverage": "rendered_context_manifest",
                "units": {"chars": "characters", "bytes": "utf8_bytes",
                          "tokens": "local_encoding_tokens", "timings_ms": "milliseconds"},
                "sizes": sizes,
                "records": {"bounded_candidates": len(bundle.get("records", [])),
                            "selected": len(records),
                            "selected_sources": sum(len(r.get("sources", [])) for r in records),
                            "references": len(result.get("references", [])),
                            "omission_entries": len(result.get("omitted", [])),
                            "diagnostic_tokenization_skips": tokenization_skips,
                            "by_kind": {kind: sum(r.get("kind") == kind for r in records)
                                        for kind in record_kinds}},
                "timings_ms": {"selection": numeric(selection_ms),
                               "transformation": numeric(transformation_ms),
                               "diagnostic_counting": counting_ms,
                               "diagnostics_total": (time.perf_counter() - started) * 1000},
                "adjustment": {key: integer_count(adjustment.get(key)) for key in
                               ("fallback_count", "reference_metadata_omitted", "full_view_comparisons")},
                "same_records_reduction": {
                    unit: (1 - sizes["same_records_compact"][unit] / sizes["same_records_full"][unit]
                           if sizes["same_records_full"][unit]
                           and sizes["same_records_compact"][unit] is not None
                           and (unit != "tokens" or sizes["same_records_full"]["counter"] == sizes["same_records_compact"]["counter"]) else None)
                    for unit in ("chars", "bytes", "tokens")},
                "same_records_coverage": "selected_record_payload_and_common_fields_only",
                "candidate_comparison": "different_selection_not_equivalent_compression",
                "delivery": "rendered_not_confirmed", "real_inference_savings": None,
            }
            return measured if len(canonical(measured).encode("utf-8")) <= 16384 else {}
        except Exception:
            return {}

    @staticmethod
    def _compression_counter(value):
        """Allow only bounded identity metadata, never arbitrary counter payloads."""
        if not isinstance(value, dict):
            return None
        keys = ("library", "version", "encoding", "scope", "special_tokens")
        result = {key: value[key] for key in keys if isinstance(value.get(key), str)
                  and len(value[key]) <= 200}
        return result or None

    @staticmethod
    def _compression_report_sample(value):
        """Read only our numeric allowlist; persisted JSON cannot echo bodies."""
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
            return None
        modes = ("full", "compact", "reference")
        if value.get("requested_mode") not in modes or value.get("effective_mode") not in modes:
            return None
        sample = {"schema_version": 1, "requested_mode": value["requested_mode"],
                  "effective_mode": value["effective_mode"], "policy_version": "1" if value.get("policy_version") == "1" else "unknown"}
        for group, keys in (("records", ("bounded_candidates", "selected", "selected_sources", "references", "omission_entries", "diagnostic_tokenization_skips")),
                            ("timings_ms", ("selection", "transformation", "diagnostic_counting", "diagnostics_total")),
                            ("adjustment", ("fallback_count", "reference_metadata_omitted", "full_view_comparisons")),
                            ("same_records_reduction", ("chars", "bytes", "tokens"))):
            items = value.get(group, {})
            if not isinstance(items, dict):
                return None
            sample[group] = {}
            for key in keys:
                item = items.get(key)
                valid = type(item) in (int, float) and math.isfinite(item)
                if valid and group in ("records", "adjustment"):
                    valid = type(item) is int and 0 <= item <= 1_000_000
                elif valid and group == "timings_ms":
                    valid = item >= 0
                elif valid:
                    valid = item <= 1
                sample[group][key] = item if valid else None
        sample["sizes"] = {}
        for name in ("bounded_candidates_full", "selected_output", "same_records_full", "same_records_compact"):
            measurement = value.get("sizes", {}).get(name, {})
            if not isinstance(measurement, dict):
                return None
            sample["sizes"][name] = {key: (measurement.get(key) if type(measurement.get(key)) is int
                                          and measurement[key] >= 0 else None)
                                     for key in ("chars", "bytes", "tokens")}
            sample["sizes"][name]["counter"] = Usage._compression_counter(measurement.get("counter"))
            if sample["sizes"][name]["counter"] is None:
                sample["sizes"][name]["tokens"] = None
            sample["sizes"][name]["quality"] = {
                key: ("unavailable" if sample["sizes"][name][key] is None else
                      "exact_local_string" if key == "tokens" else "exact")
                for key in ("chars", "bytes", "tokens")}
        return sample

    def _compression_report(self, conn, clause, args):
        """A bounded read of existing manifests, with no telemetry writes."""
        try:
            rows = conn.execute("SELECT substr(request_json,1,65537) AS request_json FROM context_manifests"
                                + clause + " ORDER BY created_at DESC,id DESC LIMIT 21", args).fetchall()
            samples, invalid = [], 0
            for row in rows[:20]:
                try:
                    if len(row["request_json"]) > 65536:
                        invalid += 1
                        continue
                    request = json.loads(row["request_json"])
                    if not isinstance(request, dict) or not request.get("metrics"):
                        continue
                    sample = self._compression_report_sample(request["metrics"])
                    if sample is None:
                        invalid += 1
                    else:
                        samples.append(sample)
                except Exception:
                    invalid += 1
            if not samples and not invalid:
                return None
            return {"source": "local_counter", "coverage": "latest_context_manifests",
                    "window_limit": 20, "window_limited": len(rows) > 20,
                    "samples": samples, "unavailable_samples": invalid,
                    "units": {"chars": "characters", "bytes": "utf8_bytes", "tokens": "local_encoding_tokens", "timings_ms": "milliseconds"},
                    "same_records_coverage": "selected_record_payload_and_common_fields_only",
                    "candidate_comparison": "different_selection_not_equivalent_compression",
                    "real_inference_savings": None,
                    "notice": "Rendered, not confirmed delivery. Token counts exclude host framing. "
                              "Candidate and selected sizes have different coverage; do not infer equivalent compression."}
        except Exception:
            return None

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
            compression = self._compression_report(conn, clause, args)
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
        result = {"groups": groups, "retention": {"max_events": self.max_events, "days": self.retention_days},
                "provider_usage": normalize_provider_usage(None),
                "cost": metric(None, "currency", source="host_unavailable", coverage="full_review"),
                "notice": "Retained plugin-boundary measurements only. Rendered is not confirmed delivery. "
                          "Fixtures and inferred logs are separate. Repetition may be necessary. "
                          "No total-review savings, billing, quota, tokenizer count, or context residency is established."}
        if compression is not None:
            result["compression"] = compression
        return result
