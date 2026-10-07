"""Synthetic local measurement and provider-subset contracts."""
import sqlite3
from unittest.mock import patch

import pytest

from review_ledger.models import Scope
from review_ledger.usage import ProviderUsage, Usage, normalize_provider_usage


def test_exact_unicode_duplicate_event_and_delivery(ledger, opened):
    usage = Usage(ledger.store)
    scope = ledger.scope("synthetic/example")
    args = dict(tool="ledger_context", request_text='é🔬', response_text='答え🔬',
                run_id=opened["id"], latency_ms=1.25)
    assert usage.record(scope, event_id="event-a", **args)
    assert usage.record(scope, event_id="event-a", **args)
    assert usage.record(scope, event_id="event-b", **args)
    report = usage.report(scope, opened["id"])
    counters = report["groups"][0]["metrics"]
    assert counters["events"]["value"] == 2
    assert counters["request_chars"]["value"] == 4
    assert counters["request_bytes"]["value"] == 12
    assert counters["response_chars"]["value"] == 6
    assert counters["response_bytes"]["value"] == 20
    assert counters["duplicates"]["value"] == 1
    assert counters["confirmed_delivery"]["value"] is None
    assert counters["candidate_count"]["value"] is None
    assert report["provider_usage"]["total_tokens"]["value"] is None
    with ledger.store.connect() as conn:
        row = dict(conn.execute("SELECT * FROM usage_events").fetchone())
    assert 'é🔬' not in str(row) and '答え🔬' not in str(row)
    assert "event-a" not in str(row)


def test_no_cross_scope_record_or_report(ledger, opened):
    usage = Usage(ledger.store)
    wrong = Scope(99999, "synthetic/other")
    assert not usage.record(wrong, event_id="wrong", tool="ledger_context", request_text="", response_text="",
                            run_id=opened["id"], latency_ms=1)
    with pytest.raises(Exception):
        usage.report(wrong, opened["id"])


def test_telemetry_failure_preserves_successful_write(ledger, opened, actor, observation_data):
    scope = ledger.scope("synthetic/example")
    usage = Usage(ledger.store)
    invoke = lambda: ledger.record("synthetic/example", opened["id"], actor, opened["generation"],
                                   "observation", observation_data, "usage-write")
    with patch.object(usage, "record", side_effect=sqlite3.OperationalError("telemetry unavailable")):
        result = usage.observe(scope, invoke, event_id="attempt", tool="ledger_record", request_text="{}")
    assert result["observation_id"]
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1


def test_retention_and_source_coverage_do_not_delete_evidence(ledger, opened, observed):
    usage = Usage(ledger.store, max_events=2)
    scope = ledger.scope("synthetic/example")
    for n, source in enumerate(("adapter", "fixture", "log_inference")):
        assert usage.record(scope, event_id=str(n), tool="ledger_context", request_text="{}", response_text="{}",
                            latency_ms=1, run_id=opened["id"], source=source)
    report = usage.report(scope)
    assert {g["source"] for g in report["groups"]} == {"fixture", "log_inference"}
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        conn.execute("UPDATE usage_events SET created_at='2000-01-01T00:00:00.000000+00:00'")
    assert usage.record(scope, event_id="fresh", tool="ledger_context", request_text="", response_text="",
                        latency_ms=0, run_id=opened["id"])
    assert sum(g["metrics"]["events"]["value"] for g in usage.report(scope)["groups"]) == 1


def test_provider_subsets_are_not_double_counted():
    usage = ProviderUsage("attempt", "synthetic-inclusive-v1", input_tokens=100, output_tokens=40,
                          cached_input_tokens=70, reasoning_output_tokens=20)
    assert normalize_provider_usage(usage)["total_tokens"]["value"] is None
    report = normalize_provider_usage(usage, trusted_contract="synthetic-inclusive-v1")
    assert report["total_tokens"]["value"] == 140
    assert report["cached_input_tokens"]["value"] == 70
    assert report["reasoning_output_tokens"]["value"] == 20


def test_missing_and_invalid_provider_usage():
    assert normalize_provider_usage(None)["input_tokens"]["quality"] == "unavailable"
    assert normalize_provider_usage(ProviderUsage("a", "v", 100), trusted_contract="v")["total_tokens"]["value"] is None
    for usage in (ProviderUsage("a", "v", 10, 1, 11), ProviderUsage("a", "v", 10, 1, 2, 2),
                  ProviderUsage("a", "v", True, 1), ProviderUsage("a", "v", None, 1, 1)):
        with pytest.raises(ValueError):
            normalize_provider_usage(usage, trusted_contract="v")


def test_invalid_counts_do_not_break_business_flow(ledger, opened):
    usage = Usage(ledger.store)
    scope = ledger.scope("synthetic/example")
    assert not usage.record(scope, event_id="x", tool="ledger_context", request_text="", response_text="",
                            latency_ms=float("nan"), run_id=opened["id"])
    assert not usage.record(scope, event_id="x", tool="/private/credential", request_text="", response_text="",
                            latency_ms=0, run_id=opened["id"])
