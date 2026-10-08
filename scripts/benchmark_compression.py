"""Offline synthetic representation and full-trajectory measurements.

Run explicitly: python scripts/benchmark_compression.py --iterations 3
No network, inference, external repositories, credentials, or persistent cache.
Importing this module neither runs the benchmark nor writes an artifact. Optional
local counts can be supplied to benchmark(counter=prepared_local_counter).
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc

# Support the documented source-checkout command without installing the package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from review_ledger.compression import expand_records, project_bundle
from review_ledger.context import Context
from review_ledger.learning import Learning
from review_ledger.models import Actor, canonical, integer
from review_ledger.service import Ledger
from review_ledger.storage import Store
from review_ledger.token_count import CountSession

REPOSITORY = "synthetic/compression-benchmark"
CASES = ("short", "long_low_repetition", "high_repetition", "unicode_escaping",
         "extensive_conditions", "contradictory_history", "incomplete_capture", "no_lessons")
PROTECTED_FIELDS = {
    "observation": ("provenance", "kind", "outcome", "summary", "details", "environment", "limitations", "valid", "command_text", "reproduction_patch_sha", "created_at"),
    "assessment": ("claim", "state", "basis", "rationale", "limitations", "freshness", "sources", "resolution_json"),
    "finding": ("claim", "origin_run_id"),
    "lesson": ("question", "conditions", "exclusions", "verification", "tags", "symbols"),
    "snapshot_completeness": ("files_complete", "patches_complete", "total_files", "omitted_files", "truncation_reasons"),
}


def reduction(baseline, candidate):
    """Comparable units only; unavailable or zero denominator is not a gain."""
    if baseline is None or candidate is None or baseline == 0:
        return None
    return 1 - candidate / baseline


def distribution(values):
    """Retain all samples as well as order statistics; never report only best."""
    values = [v for v in values if v is not None]
    if not values:
        return {"samples": [], "count": 0, "min": None, "median": None, "max": None, "p10": None, "p90": None}
    ordered = sorted(values)
    def percentile(p):
        index = (len(ordered) - 1) * p
        low = int(index)
        high = min(low + 1, len(ordered) - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (index - low)
    return {"samples": values, "count": len(values), "min": min(values), "median": statistics.median(values),
            "max": max(values), "p10": percentile(.1), "p90": percentile(.9)}


def _ledger(path):
    return Ledger(Store(path, "offline-compression-fixture"), [REPOSITORY],
                  skill_version="synthetic-fixture", skill_hash="d" * 64)


def _fixture(path, name):
    ledger, actor = _ledger(path), Actor("offline-compression-fixture")
    incomplete = name == "incomplete_capture"
    snapshot = {"repository_id": 998877, "repository_node_id": "synthetic-node",
                "repository_full_name": REPOSITORY, "number": 1, "title": "Offline synthetic fixture",
                "url": "https://github.com/synthetic/compression-benchmark/pull/1",
                "head_sha": "a" * 40, "base_sha": "b" * 40, "comparison": "github_pr", "files": [],
                "files_complete": not incomplete, "patches_complete": not incomplete,
                "total_files": 2 if incomplete else 0, "omitted_files": 2 if incomplete else 0,
                "truncation_reasons": ["Fixture omitted two patches; result is inconclusive"] if incomplete else []}
    run = ledger.open(snapshot, actor, "open")["run"]
    observations = []
    count = 1 if name in ("short", "extensive_conditions", "no_lessons") else 8
    for n in range(count):
        common = "After persist and before acknowledgement, retry may repeat exactly 2 writes; other interruption points were not tested."
        if name == "long_low_repetition":
            details = " ".join(f"Case {n}, boundary {i}: expected {i + n} ms; not checked before acknowledgement." for i in range(32))
        elif name == "unicode_escaping":
            details = ('café 答え 🔬 e\u0301; "quoted"; C:\\test\\file.py\n    if value != 0:\n        raise ValueError("not ready")\n') * 10
        else:
            details = common
        limitations = ("Only the reported synthetic check; no independent execution. " * 12
                       if name == "high_repetition" else f"Synthetic scope {n}; does not establish behavior in other conditions.")
        outcome = "skipped" if incomplete and n == 0 else "behavior_failure"
        observation = ledger.record(REPOSITORY, run["id"], actor, run["generation"], "observation",
            {"kind": "test", "outcome": outcome, "summary": f"Synthetic retry observation {n}",
             "details": details, "environment": "Offline synthetic fixture, 0 network calls",
             "limitations": limitations, "command_text": 'python -c "print(0)"'}, f"observation-{n}")["observation_id"]
        observations.append(observation)
    if name != "no_lessons":
        conditions = ["A retry occurs after persistence and before acknowledgement"]
        exclusions = ["Do not apply when the effect and acknowledgement are atomic"]
        if name == "extensive_conditions":
            conditions = [f"Condition {n}: " + ("must preserve both boundaries, units of 10 ms, and not infer completion; " * 6) for n in range(9)]
            exclusions = [f"Exception {n}: " + ("do not apply to an atomic transaction or a skipped check; " * 6) for n in range(5)]
        learning = Learning(ledger.store)
        version = learning.propose(ledger.scope(REPOSITORY), run["id"], actor, run["generation"],
            {"question": "Can retry repeat a persistent effect?", "conditions": conditions, "exclusions": exclusions,
             "verification": "Inspect persist-before-acknowledgement ordering and an atomic negative control.",
             "tags": ["retry"], "symbols": ["save_item"],
             "sources": [{"observation_id": observations[-1], "relation": "supports"}]}, "lesson")["version_id"]
        learning.operator(ledger.scope(REPOSITORY), version, "approve", "Synthetic fixture approval", "approve")
    if name == "contradictory_history":
        finding = ledger.record(REPOSITORY, run["id"], actor, run["generation"], "finding",
                                {"claim": "Retry can repeat the persistent effect"}, "finding")["finding_id"]
        for n, state in enumerate(("supported", "refuted", "inconclusive")):
            ledger.record(REPOSITORY, run["id"], actor, run["generation"], "assessment",
                {"finding_id": finding, "state": state, "basis": "behavior" if state != "inconclusive" else "none",
                 "rationale": f"Synthetic {state} report remains separate; conditions {n} differ.",
                 "limitations": "No unified conclusion is inferred from contradictory historical reports.",
                 "observation_ids": [observations[n]] if state != "inconclusive" else []}, f"assessment-{n}")
    # An explicit checkpoint with unresolved work; no completed steps are invented.
    ledger.run_action(REPOSITORY, run["id"], actor, run["generation"], "pause", "pause",
                      note="Checkpoint: inspect retry ordering next; atomic control remains pending.")
    full = Context(ledger, 64000).prepare(REPOSITORY, run["id"], actor, query="retry")
    required = []
    for record in full["records"]:
        required.append({"kind": record["kind"], "id": record["sources"][0]["id"],
                         "fields": {key: record["content"][key] for key in PROTECTED_FIELDS.get(record["kind"], ())
                                    if key in record["content"]}})
    return ledger, actor, run, full, required


def compare_representations(full, counter=None):
    """Experiment A freezes one actual legacy full response before projection."""
    frozen = deepcopy(full)
    compact = project_bundle(frozen, mode="compact")
    expanded = expand_records(compact["records"], compact["representation"]["common_fields"])
    measured = CountSession(counter)
    baseline = measured.measure(canonical(full))
    projected = measured.measure(canonical(compact))
    return {"baseline": baseline, "compact": projected,
            "reduction": {unit: reduction(baseline[unit], projected[unit]) for unit in ("chars", "bytes", "tokens")},
            "same_records": expanded == full["records"], "source_unchanged": frozen == full,
            "selected_records": len(full["records"]),
            "protected_fields_equal": canonical(expanded) == canonical(full["records"]),
            "compact_grew": projected["bytes"] > baseline["bytes"],
            "coverage": "same_frozen_legacy_response_records_and_fields_with_actual_envelopes"}


def _matches(actual, expected):
    return all(key in actual and canonical(actual[key]) == canonical(value) for key, value in expected.items())


def trajectory(ledger, actor, run, required, *, mode, budget, counter=None):
    """Scripted prepare -> required detail (including failures) -> pinned resume.

    The same fixture oracle names the necessary record fields for every mode.
    It is a deterministic workload, not measured LLM behavior. Authorized legacy
    status pages are a fallback for stored fields absent from legacy detail.
    """
    context = Context(ledger, budget, compression_enabled=True, counter=counter)
    responses, requests = [], []
    measured = CountSession(counter)
    def invoke(action, **kwargs):
        requests.append(canonical({"action": action, "repository": REPOSITORY, "run_id": run["id"], **kwargs}))
        response = getattr(context, action)(REPOSITORY, run["id"], actor, **kwargs)
        responses.append(response)
        return response
    prepared = invoke("prepare", query="retry", mode=mode)
    expanded = expand_records(prepared.get("records", []), prepared.get("representation", {}).get("common_fields", {}))
    available = {(row["kind"], source["id"]): row["content"] for row in expanded for source in row["sources"]}
    unresolved = 0
    legacy_pages = 0
    for requirement in required:
        kind, ident, expected = requirement["kind"], requirement["id"], requirement["fields"]
        content = available.get((kind, ident), {})
        if _matches(content, expected):
            continue
        detail = invoke("detail", kind=kind, record_id=ident, mode="full" if mode == "full" else "compact")
        content = detail.get("content", {})
        if kind == "snapshot_completeness" and "snapshot" in content:
            content = content["snapshot"]
        if not _matches(content, expected) and detail.get("available_sections"):
            for section in expected:
                if section not in detail["available_sections"] or section in content:
                    continue
                focused = invoke("detail", kind=kind, record_id=ident, section=section,
                                 mode="full" if mode == "full" else "compact")
                content.update(focused.get("content", {}))
        if not _matches(content, expected) and kind in ("observation", "assessment", "finding") and budget >= 4000:
            offset, pieces, checksum = 0, [], None
            while True:
                kwargs = {"detail_collection": kind, "detail_id": ident, "offset": offset, "max_chars": budget}
                requests.append(canonical({"action": "status_detail", "repository": REPOSITORY, "run_id": run["id"], **kwargs}))
                page = ledger.status_detail(REPOSITORY, run["id"], actor, **kwargs)
                responses.append(page)
                legacy_pages += 1
                if checksum is not None and checksum != page["content_sha256"]:
                    raise AssertionError("Synthetic source changed between detail pages")
                checksum = page["content_sha256"]
                pieces.append(page["content"])
                if page["next_offset"] is None:
                    break
                offset = page["next_offset"]
            joined = "".join(pieces)
            if hashlib.sha256(joined.encode("utf-8")).hexdigest() != checksum:
                raise AssertionError("Synthetic detail integrity failed")
            content = json.loads(joined)
        if not _matches(content, expected):
            unresolved += 1
    if prepared.get("manifest_id"):
        resumed = invoke("resume", manifest_id=prepared["manifest_id"])
    else:
        resumed = invoke("resume", query="retry", mode=mode)
    response_counts = [measured.measure(canonical(response)) for response in responses]
    request_counts = [measured.measure(request) for request in requests]
    def totals(values):
        return {unit: sum(item[unit] for item in values) if all(item[unit] is not None for item in values) else None
                for unit in ("chars", "bytes", "tokens")}
    response_total, request_total = totals(response_counts), totals(request_counts)
    return {"prepare": response_counts[0], "resume": response_counts[-1],
            "response_total": response_total, "request_total": request_total,
            "observed_total": {unit: response_total[unit] + request_total[unit]
                               if response_total[unit] is not None and request_total[unit] is not None else None
                               for unit in ("chars", "bytes", "tokens")},
            "responses": len(responses), "detail_requests": len(responses) - 2,
            "legacy_detail_pages": legacy_pages, "required_records": len(required), "unresolved_records": unresolved,
            "protected_fields_recovered": unresolved == 0,
            "all_responses_within_char_cap": all(item["chars"] <= budget for item in response_counts),
            "all_responses_within_byte_cap": all(item["bytes"] <= 256000 for item in response_counts),
            "selected_records": len(prepared.get("records", [])), "references": len(prepared.get("references", [])),
            "effective_mode": prepared.get("representation", {}).get("mode", "full"),
            "fallback_used": mode == "compact" and prepared.get("representation", {}).get("mode") == "full",
            "requires_more_context_responses": sum(response.get("state") == "requires_more_context" for response in responses),
            "prepare_state": prepared["state"], "resume_state": resumed["state"],
            "counter": measured.measure("")["counter"], "provider_tokens": None,
            "confirmed_delivery": None}


def benchmark(*, budget=6000, iterations=3, cases=None, counter=None, measure_memory=True):
    """Return measurements; caller decides whether and where to save the JSON."""
    integer(budget, "budget", 2000, 64000)
    integer(iterations, "iterations", 1, 30)
    cases = CASES if cases is None else tuple(cases)
    if not cases or any(case not in CASES for case in cases):
        raise ValueError("Unknown or empty synthetic case selection")
    if type(measure_memory) is not bool:
        raise ValueError("measure_memory must be boolean")
    results = []
    with tempfile.TemporaryDirectory(prefix="ledger-compression-") as temporary:
        root = Path(temporary)
        for case in cases:
            seed_path = root / case / "seed"
            _, actor, run, full, required = _fixture(seed_path, case)
            representation = compare_representations(full, counter)
            modes = {}
            for mode in ("full", "compact", "reference"):
                profile = root / case / mode
                shutil.copytree(seed_path, profile)
                ledger = _ledger(profile)
                start = time.perf_counter()
                observed = trajectory(ledger, actor, run, required, mode=mode, budget=budget, counter=counter)
                cold = (time.perf_counter() - start) * 1000
                warm = []
                for _ in range(iterations):
                    start = time.perf_counter()
                    trajectory(ledger, actor, run, required, mode=mode, budget=budget, counter=counter)
                    warm.append((time.perf_counter() - start) * 1000)
                peak = None
                if measure_memory:
                    # Memory instrumentation deliberately does not wrap latency samples.
                    tracemalloc.start()
                    try:
                        trajectory(ledger, actor, run, required, mode=mode, budget=budget, counter=counter)
                        _, peak = tracemalloc.get_traced_memory()
                    finally:
                        tracemalloc.stop()
                modes[mode] = {**observed, "latency_ms": {"cold_first_trajectory": cold, "warm_repeated_trajectories": distribution(warm)},
                               "peak_tracemalloc_bytes": peak}
            baseline, compact = modes["full"], modes["compact"]
            results.append({"case": case, "representation": representation,
                            "trajectory": {"modes": modes,
                                "observed_context_reduction": {unit: reduction(baseline["observed_total"][unit], compact["observed_total"][unit])
                                                               for unit in ("chars", "bytes", "tokens")},
                                "extra_detail_requests": compact["detail_requests"] - baseline["detail_requests"]}})
    return {"schema_version": 1, "source": "offline_synthetic_fixture", "budget_chars": budget,
            "counter": CountSession(counter).measure("")["counter"],
            "methodology": {
                "experiment_a": "Representation reduction: freeze an actual legacy Context full response; project identical selected records and verify expansion of all fields. Count both actual canonical envelopes.",
                "experiment_b": "Observed context reduction: identical synthetic stored records, eligibility, required-field oracle and per-response character cap; prepare, load missing complete fields, then pinned resume. Include every attempted detail request and response.",
                "cold": "First trajectory on a separate cloned temporary profile in this process. Imports and fixture setup excluded; not OS-cache-cold or process-startup latency.",
                "warm": "Repeated trajectories on that profile in the same process. No persistent projection/token cache; CountSession caches are per operation only. OS and SQLite caches may be warm.",
                "memory": "Separate trajectory under tracemalloc after latency sampling; Python allocation peak, not process RSS.",
                "tokens": "Optional exact local-string counts for the explicitly supplied encoding. Default unavailable. No host framing, provider use, cache billing or inference measured.",
                "reduction": "1 - compact/baseline for comparable units and coverage; negative values are growth; zero or unavailable baseline yields unavailable.",
                "limitations": "Scripted synthetic workload, not an LLM comprehension evaluation or evidence of real inference savings. References are explicitly unloaded and still incur detail cost."},
            "cases": results,
            "summary": {"representation_reduction": {unit: distribution([row["representation"]["reduction"][unit] for row in results]) for unit in ("chars", "bytes", "tokens")},
                        "observed_context_reduction": {unit: distribution([row["trajectory"]["observed_context_reduction"][unit] for row in results]) for unit in ("chars", "bytes", "tokens")}},
            "real_inference_savings": None, "provider_total_tokens": None, "provider_cost": None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budget", type=int, default=6000)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--case", action="append", choices=CASES, dest="cases")
    parser.add_argument("--output", type=Path, help="Write JSON only to this explicit path instead of stdout")
    parser.add_argument("--no-memory", action="store_true", help="Skip the separate tracemalloc run")
    args = parser.parse_args(argv)
    result = benchmark(budget=args.budget, iterations=args.iterations, cases=args.cases, measure_memory=not args.no_memory)
    rendered = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
