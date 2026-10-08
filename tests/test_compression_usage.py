"""Body-free local compression diagnostics, separate from provider accounting."""
from copy import deepcopy
import json
import sqlite3
from unittest.mock import patch

import pytest

from review_ledger.compression import project_bundle
from review_ledger.context import Context
from review_ledger.models import canonical
from review_ledger.token_count import CountSession
from review_ledger.usage import Usage


def _bundle():
    return {"records": [
        {"kind": "observation", "sources": [{"id": f"source-{n}", "kind": "observation"}],
         "content": {"run_id": "private-run", "summary": f"private source body {n}",
                     "limitations": "Private qualification remains literal", "valid": False}}
        for n in range(4)], "references": [], "omitted": [], "_snapshot_key": "private-snapshot"}


def _measure(bundle=None, result=None, counter=None, **kwargs):
    bundle = _bundle() if bundle is None else bundle
    result = project_bundle(bundle) if result is None else result
    return Usage.compression_measurement(bundle, result, counter=counter or CountSession(),
        selection_ms=1.25, transformation_ms=2.5, requested_mode="compact", adjustment={}, **kwargs)


def test_compression_numbers_match_actual_unicode_output_and_do_not_save_bodies():
    bundle = _bundle()
    bundle["records"][0]["content"]["summary"] = 'Secret é🔬 "literal" \\ path\n'
    before = deepcopy(bundle)
    result = project_bundle(bundle)
    measured = _measure(bundle, result)
    assert measured["sizes"]["selected_output"]["chars"] == len(canonical(result))
    assert measured["sizes"]["selected_output"]["bytes"] == len(canonical(result).encode("utf-8"))
    assert measured["sizes"]["selected_output"]["tokens"] is None
    assert measured["timings_ms"]["selection"] == 1.25
    assert measured["records"]["selected"] == 4
    assert measured["records"]["by_kind"]["observation"] == 4
    assert measured["real_inference_savings"] is None
    assert bundle == before
    saved = canonical(measured)
    for private in ("Secret", "Private qualification", "private-run", "private-snapshot", "source-0"):
        assert private not in saved
    assert len(saved.encode("utf-8")) <= 16384


def test_different_candidate_and_selected_sizes_are_not_equivalent_compression():
    bundle = _bundle()
    selected = {**bundle, "records": bundle["records"][:1]}
    measured = _measure(bundle, project_bundle(selected))
    assert measured["records"]["bounded_candidates"] == 4
    assert measured["records"]["selected"] == 1
    assert measured["candidate_comparison"] == "different_selection_not_equivalent_compression"
    assert measured["same_records_coverage"] == "selected_record_payload_and_common_fields_only"
    assert measured["sizes"]["bounded_candidates_full"]["chars"] > measured["sizes"]["same_records_full"]["chars"]
    # One row cannot factor fields; an honest representation metric shows growth.
    assert measured["same_records_reduction"]["chars"] < 0


def test_count_failure_is_best_effort_and_large_sources_skip_tokenizer():
    class Fail:
        def measure(self, value):
            raise OSError("private source body from failing counter")
    assert _measure(counter=Fail()) == {}
    calls = []
    class Counter:
        identity = {"library": "fixture", "version": "1", "encoding": "characters", "private_dump": "not persisted"}
        def count(self, value):
            calls.append(len(value.encode("utf-8")))
            return len(value)
    bundle = _bundle()
    bundle["records"][0]["content"]["details"] = "x" * (300 * 1024)
    result = project_bundle({**bundle, "records": []})
    measured = _measure(bundle, result, CountSession(Counter()))
    assert measured["sizes"]["bounded_candidates_full"]["tokens"] is None
    assert measured["sizes"]["selected_output"]["tokens"] == measured["sizes"]["selected_output"]["chars"]
    assert max(calls) <= 256 * 1024
    assert "not persisted" not in canonical(measured)
    assert measured["sizes"]["selected_output"]["counter"] == {"library": "fixture", "version": "1", "encoding": "characters"}


def test_usage_legacy_report_unchanged_without_compression(ledger, opened, actor):
    Context(ledger).prepare("synthetic/example", opened["id"], actor, query="")
    assert "compression" not in Usage(ledger.store).report(ledger.scope("synthetic/example"), opened["id"])


def test_report_reads_bounded_scoped_numeric_diagnostics_without_writes(ledger, opened, actor):
    context = Context(ledger, compression_enabled=True)
    for _ in range(23):
        context.prepare("synthetic/example", opened["id"], actor, query="private query", mode="compact")
    with ledger.store.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM context_manifests").fetchone()[0]
        row = conn.execute("SELECT id,request_json FROM context_manifests ORDER BY created_at DESC LIMIT 1").fetchone()
        request = json.loads(row["request_json"])
        request["metrics"]["source_body"] = "never echo sensitive body"
        request["metrics"]["sizes"]["selected_output"]["source_body"] = "never echo sensitive body"
        conn.execute("UPDATE context_manifests SET request_json=? WHERE id=?", (canonical(request), row["id"]))
    report = Usage(ledger.store).report(ledger.scope("synthetic/example"), opened["id"])
    assert report["compression"]["window_limited"]
    assert len(report["compression"]["samples"]) == 20
    assert report["provider_usage"]["total_tokens"]["value"] is None
    assert "private query" not in canonical(report)
    assert "never echo" not in canonical(report)
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM context_manifests").fetchone()[0] == before
        conn.execute("UPDATE context_manifests SET request_json='not json'")
    broken = Usage(ledger.store).report(ledger.scope("synthetic/example"), opened["id"])
    assert broken["compression"]["unavailable_samples"] == 20
    assert broken["compression"]["samples"] == []


def test_compression_measurement_failure_does_not_break_response_or_evidence(ledger, opened, actor, observed):
    with patch.object(Usage, "compression_measurement", side_effect=sqlite3.OperationalError("telemetry unavailable")):
        result = Context(ledger, compression_enabled=True).prepare("synthetic/example", opened["id"], actor,
                                                                  query="", mode="compact")
    assert result["state"] == "ok"
    with ledger.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 1
        request = json.loads(conn.execute("SELECT request_json FROM context_manifests WHERE id=?", (result["manifest_id"],)).fetchone()[0])
    assert not request.get("metrics")


def test_invalid_counter_or_diagnostics_are_not_promoted_to_provider_usage():
    class Counter:
        def measure(self, value):
            return {"chars": -10, "bytes": -1, "tokens": True, "counter": None}
    sample = _measure(counter=Counter())
    assert sample["sizes"]["selected_output"]["chars"] > 0
    assert sample["sizes"]["selected_output"]["tokens"] is None
    damaged = deepcopy(sample)
    damaged["timings_ms"]["selection"] = float("nan")
    damaged["records"]["selected"] = "private body"
    sanitized = Usage._compression_report_sample(damaged)
    assert sanitized["timings_ms"]["selection"] is None
    assert sanitized["records"]["selected"] is None
    assert "private body" not in canonical(sanitized)
