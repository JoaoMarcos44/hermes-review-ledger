"""Offline mechanism comparison against frozen pre-V1.5 retrieval.

Baseline methods below are preserved from the repository HEAD at implementation
start, rather than a fabricated database dump. No model or network is required.
"""
from __future__ import annotations

import json
import unicodedata

from .models import (Actor, ELIGIBLE_OUTCOMES, LedgerError, Scope, canonical, choice,
                     digest, fields, integer, strings, text)
from .storage import Store, new_id, now


def normalize_search(value: str) -> str:
    """Same Unicode normalization for SQL filtering and Python ranking."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", value).casefold())


def _search_text(question, conditions, exclusions, tags, symbols, verification) -> str:
    """Search reported values, without treating JSON escapes as reported text."""
    return normalize_search("\n".join([question, *conditions, *exclusions, *tags, *symbols, verification]))


def _sql_search_text(question, conditions, exclusions, tags, symbols, verification) -> str:
    # Python decoding keeps retrieval independent of SQLite's optional JSON1 extension.
    return _search_text(question, json.loads(conditions), json.loads(exclusions),
                        json.loads(tags), json.loads(symbols), verification)


def _decode_version(row) -> dict:
    item = dict(row)
    for key in ("conditions", "exclusions", "tags", "symbols"):
        item[key] = json.loads(item.pop(key + "_json"))
    return item


class SavedBaseline:
    def __init__(self, store: Store, *, improvements_enabled: bool = False):
        self.store = store
        self.improvements_enabled = improvements_enabled

    @staticmethod
    def eligible(conn, scope: Scope, version_id: str) -> bool:
        lesson = conn.execute("SELECT state FROM lesson_versions WHERE repository_id=? AND id=?", (scope.repository_id, version_id)).fetchone()
        if lesson is None or lesson["state"] != "active":
            return False
        sources = conn.execute("""SELECT o.valid,o.outcome FROM lesson_sources s JOIN observations o ON o.id=s.observation_id
            WHERE s.repository_id=? AND s.version_id=? LIMIT 21""", (scope.repository_id, version_id)).fetchall()
        return bool(sources) and len(sources) <= 20 and all(s["valid"] and s["outcome"] in ELIGIBLE_OUTCOMES for s in sources)

    @staticmethod
    def version(conn, scope: Scope, version_id: str) -> dict:
        row = conn.execute("SELECT * FROM lesson_versions WHERE repository_id=? AND id=?", (scope.repository_id, version_id)).fetchone()
        if row is None:
            raise LedgerError("scope_not_found", "Lesson version is not in this repository and profile")
        item = _decode_version(row)
        sources = [dict(r) for r in conn.execute("""SELECT s.observation_id,s.relation,o.run_id,o.provenance,o.valid,o.outcome
            FROM lesson_sources s JOIN observations o ON o.id=s.observation_id WHERE s.repository_id=? AND s.version_id=? ORDER BY s.observation_id LIMIT 21""", (scope.repository_id, version_id))]
        item["sources"] = sources[:20]
        item["eligible_now"] = (item["state"] == "active" and bool(sources) and len(sources) <= 20
                                and all(s["valid"] and s["outcome"] in ELIGIBLE_OUTCOMES for s in sources))
        return item

    def recall(self, scope: Scope, run_id: str, *, terms: list[str] | None = None,
               tags: list[str] | None = None, symbols: list[str] | None = None,
               limit: int = 5, context_budget: int = 6000, offset: int = 0,
               result_offset: int = 0) -> dict:
        terms = strings(terms or [], "terms", 20, 80)
        tags = strings(tags or [], "tags", 20, 80)
        symbols = strings(symbols or [], "symbols", 20, 200)
        integer(limit, "limit", 1, 5)
        integer(context_budget, "context_budget", 500, 20000)
        integer(offset, "offset", 0, 1_000_000)
        integer(result_offset, "result_offset", 0, 200)
        with self.store.connect() as conn:
            conn.create_function("ledger_search_text", 6, _sql_search_text, deterministic=True)
            conn.execute("BEGIN")
            self.store.run(conn, scope, run_id)
            # SQL scope/state/text filtering and a bounded candidate window; no FTS dependency.
            clauses, args = [], [scope.repository_id]
            for term in terms + tags + symbols:
                # Parameter binding plus LIKE escaping keeps user text literal.
                term = normalize_search(term).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                clauses.append("ledger_search_text(question,conditions_json,exclusions_json,tags_json,symbols_json,verification) LIKE ? ESCAPE '\\'")
                args.append("%" + term + "%")
            filter_sql = " AND (" + " OR ".join(clauses) + ")" if clauses else ""
            args.extend([201, offset])
            rows = conn.execute("SELECT * FROM lesson_versions WHERE repository_id=? AND state='active'" + filter_sql + " ORDER BY lesson_id,version,id LIMIT ? OFFSET ?", args).fetchall()
            window = rows[:200]
            eligible_ids = set()
            if window:
                outcomes = sorted(ELIGIBLE_OUTCOMES)
                source_args = [*outcomes, scope.repository_id, *(row["id"] for row in window)]
                outcome_slots = ",".join("?" for _ in outcomes)
                version_slots = ",".join("?" for _ in window)
                source_rows = conn.execute(f"""SELECT s.version_id,COUNT(*) AS source_count,
                    MIN(CASE WHEN o.valid=1 AND o.outcome IN ({outcome_slots}) THEN 1 ELSE 0 END) AS sources_eligible
                    FROM lesson_sources s JOIN observations o ON o.id=s.observation_id
                    WHERE s.repository_id=? AND s.version_id IN ({version_slots}) GROUP BY s.version_id""", source_args)
                eligible_ids = {row["version_id"] for row in source_rows
                                if 1 <= row["source_count"] <= 20 and row["sources_eligible"]}
            normalized_terms = [normalize_search(term) for term in terms]
            normalized_tags = {normalize_search(tag) for tag in tags}
            normalized_symbols = {normalize_search(symbol) for symbol in symbols}
            candidates = []
            for row in window:
                if row["id"] not in eligible_ids:
                    continue
                item = _decode_version(row)
                haystack = _search_text(item["question"], item["conditions"], item["exclusions"],
                                        item["tags"], item["symbols"], item["verification"])
                score = (sum(1 for term in normalized_terms if term in haystack)
                         + 2 * len(normalized_tags & {normalize_search(tag) for tag in item["tags"]})
                         + 3 * len(normalized_symbols & {normalize_search(symbol) for symbol in item["symbols"]}))
                candidates.append((score, item))
            candidates.sort(key=lambda pair: (-pair[0], pair[1]["lesson_id"], pair[1]["version"], pair[1]["id"]))
            selected, references, used, cursor = [], [], 0, result_offset
            for _, item in candidates[result_offset:]:
                if len(selected) + len(references) >= limit:
                    break
                # Load full source details only for this result page, in the same read snapshot.
                item = self.version(conn, scope, item["id"])
                size = len(canonical(item))
                if used + size <= context_budget:
                    selected.append(item)
                    used += size
                else:
                    reference = {"version_id": item["id"], "required_context_chars": size}
                    reference_size = len(canonical(reference))
                    if used + reference_size > context_budget:
                        break
                    references.append(reference)
                    used += reference_size
                cursor += 1
            more_results = cursor < len(candidates)
            result = {"state": "ok", "lessons": selected, "lesson_references": references,
                    "context_chars": used, "context_budget": context_budget,
                    "omitted": {"candidate_window": len(rows) > 200, "budget": len(references),
                                "result_limit": more_results},
                    "next_result_offset": cursor if more_results else None,
                    "next_offset": offset + 200 if len(rows) > 200 else None,
                    "notice": "Conditions guide investigation; similarity does not prove a defect. "
                              "References omit no fields from storage: retrieve all detail pages by version_id before use. "
                              "Page next_result_offset within this candidate window before advancing next_offset. "
                              "Recheck eligibility before use."}
            conn.commit()
            return result

    def detail(self, scope: Scope, run_id: str, version_id: str, *,
               context_budget: int = 6000, offset: int = 0) -> dict:
        """Page exact, complete lesson JSON without dropping conditions or exclusions."""
        text(version_id, "version_id", 64)
        integer(context_budget, "context_budget", 500, 20000)
        integer(offset, "offset", 0, 1_000_000)
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            self.store.run(conn, scope, run_id)
            item = self.version(conn, scope, version_id)
            if not item["eligible_now"]:
                raise LedgerError("lesson_not_eligible", "This version is no longer eligible; do not use previous detail pages")
            content = canonical(item)
            if offset >= len(content):
                raise LedgerError("invalid_input", "Detail offset must refer to a character within the complete lesson JSON")
            # The JSON string's escaped size, not only its raw text, is budgeted.
            low, high = 0, min(len(content) - offset, context_budget)
            while low < high:
                length = (low + high + 1) // 2
                if len(canonical(content[offset:offset + length])) <= context_budget:
                    low = length
                else:
                    high = length - 1
            chunk = content[offset:offset + low]
            end = offset + low
            return {"state": "detail", "version_id": version_id, "eligible_now": True,
                    "content_format": "canonical_json", "content": chunk,
                    "content_sha256": digest(item), "total_chars": len(content), "offset": offset,
                    "next_offset": end if end < len(content) else None,
                    "complete": offset == 0 and end == len(content),
                    "context_chars": len(canonical(chunk)), "context_budget": context_budget,
                    "notice": "Partial pages are not an applicable strategy. Reassemble all pages with the same "
                              "content_sha256 and verify it before interpreting conditions, exclusions or sources. "
                              "Restart if the digest changes; eligibility is checked again for every page and use."}


# The executable harness below operates only on synthetic temporary repositories.
BASELINE_SOURCE = "Learning.recall/detail frozen from public main 99f8d98b59720847df410ac59d8e7782d9f51629"


def _synthetic_case(root, name):
    from .service import Ledger
    from .learning import Learning
    actor = Actor("offline-evaluation")
    repository = "synthetic/offline-evaluation"
    ledger = Ledger(Store(root / name, "offline-evaluation"), [repository],
                    skill_version="offline-fixture", skill_hash="a" * 64)
    snapshot = {"repository_id": 987654, "repository_full_name": repository,
                "repository_node_id": "synthetic-node", "number": 1,
                "title": "Offline mechanism fixture", "url": "https://github.com/synthetic/offline-evaluation/pull/1",
                "head_sha": "a" * 40, "base_sha": "b" * 40, "comparison": "github_pr",
                "files": [], "files_complete": True, "patches_complete": True, "total_files": 0,
                "omitted_files": 0, "truncation_reasons": []}
    run = ledger.open(snapshot, actor, "open")["run"]
    scope = ledger.scope(repository)
    learning = Learning(ledger.store)
    versions, observations = [], []
    query = "retry"

    def add(question="Could a retry repeat the effect?", *, conditions=None, exclusions=None,
            verification="Inspect acknowledgement and effect ordering.", tags=None, outcome="behavior_failure"):
        n = len(versions)
        observation = ledger.record(repository, run["id"], actor, run["generation"], "observation",
                                    {"kind": "inspection", "outcome": outcome, "summary": f"Synthetic source {n}",
                                     "details": "Fixture labels establish mechanism expectations only.",
                                     "limitations": "No model review or target execution."}, f"observation-{n}")["observation_id"]
        data = {"question": question, "conditions": conditions or ["A persistent effect can be retried"],
                "exclusions": exclusions or ["Do not apply when the effect is already deduplicated"],
                "verification": verification, "tags": tags or ["retry"], "symbols": [],
                "sources": [{"observation_id": observation, "relation": "supports"}]}
        version = learning.propose(scope, run["id"], actor, run["generation"], data, f"propose-{n}")["version_id"]
        learning.operator(scope, version, "approve", "Synthetic offline fixture approval", f"approve-{n}")
        versions.append(version)
        observations.append(observation)
        return version

    expected = []
    if name == "cold_start":
        pass
    elif name == "irrelevant":
        expected.append(add())
        add("Check a CSS color", conditions=["A stylesheet changes"], exclusions=["No style change"],
            verification="Compare color contrast.", tags=["css"])
    elif name == "long_with_exclusions":
        expected.append(add(conditions=["condition " + str(i) + " " + "x" * 470 for i in range(9)],
                            exclusions=["CRUCIAL: do not apply to atomic retries", "exception " + "y" * 470],
                            verification="z" * 1900))
    elif name == "duplicate_distinct_sources":
        expected.extend([add(), add()])
    elif name == "unicode":
        query = "cafe\u0301"
        expected.append(add("Does CAFÉ retry preserve the effect?", tags=["café"]))
    elif name == "contradiction":
        expected.append(add("Retry can repeat the persistent effect"))
        expected.append(add("Retry cannot repeat the persistent effect", outcome="hypothesis_refuted",
                            verification="Inspect the negative control and keep this refutation distinct."))
    elif name == "changed_snapshot":
        expected.append(add())
        snapshot = {**snapshot, "head_sha": "c" * 40}
        run = ledger.open(snapshot, actor, "changed-snapshot")["run"]
    elif name == "expired_applicability":
        version = add(conditions=["Only applicable before 2000-01-01; expiry must remain explicit"],
                      exclusions=["Excluded on or after 2000-01-01"])
        learning.operator(scope, version, "restrict", "Operator evaluated expiry in synthetic fixture", "restrict-expired")
    elif name == "revoked_source":
        add()
        # Source invalidation is simulated at canonical storage to avoid testing
        # an unrelated host command. No context or evidence is fabricated.
        with ledger.store.connect() as conn, ledger.store.transaction(conn):
            conn.execute("UPDATE observations SET valid=0,invalid_reason='Synthetic revoked source' WHERE id=?", (observations[0],))
    else:
        raise ValueError("Unknown synthetic fixture")
    with ledger.store.connect() as conn:
        expected_records = {v: learning.version(conn, scope, v) for v in expected}
    return ledger, repository, run, actor, scope, query, expected_records, versions


def _all_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _all_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _all_strings(child)


def evaluate(*, budget=12000):
    """Compare identical records/questions without model calls or persistent data.

    Actual elapsed timings vary by machine; deterministic describes retrieval,
    never a promise that execution latency is deterministic. Token counters and
    reviewer intelligence remain unavailable.
    """
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from time import perf_counter
    from .context import Context
    names = ("cold_start", "irrelevant", "long_with_exclusions", "duplicate_distinct_sources",
             "unicode", "contradiction", "changed_snapshot", "expired_applicability", "revoked_source")
    results = []
    with TemporaryDirectory(prefix="ledger-offline-evaluation-") as directory:
        for name in names:
            ledger, repository, run, actor, scope, query, expected, all_versions = _synthetic_case(Path(directory), name)
            baseline = SavedBaseline(ledger.store)
            start = perf_counter()
            old = baseline.recall(scope, run["id"], terms=[query], context_budget=budget)
            old_latency = (perf_counter() - start) * 1000
            context = Context(ledger, budget=budget)
            start = perf_counter()
            new = context.prepare(repository, run["id"], actor, query=query, max_chars=budget)
            new_latency = (perf_counter() - start) * 1000
            old_responses, new_responses = [old], [new]
            # Baseline's own detail contract reconstructs referenced full records.
            old_records = {item["id"]: item for item in old["lessons"]}
            for reference in old["lesson_references"]:
                chunks, offset = [], 0
                while True:
                    page = baseline.detail(scope, run["id"], reference["version_id"], context_budget=budget, offset=offset)
                    old_responses.append(page)
                    chunks.append(page["content"])
                    if page["next_offset"] is None:
                        break
                    offset = page["next_offset"]
                record = json.loads("".join(chunks))
                if digest(record) != page["content_sha256"]:
                    raise AssertionError("Frozen baseline detail digest mismatch")
                old_records[record["id"]] = record
            # Record IDs disclosed anywhere in a selected record or reference
            # establish the available detail access path without guessing IDs.
            disclosed = set(_all_strings(new)) & set(all_versions)
            complete_new = {}
            loaded_text = "\n".join(_all_strings(new))
            for ident in sorted(disclosed & set(expected)):
                item = expected[ident]
                critical_fields = [item["question"], *item["conditions"], *item["exclusions"], item["verification"]]
                if all(field in loaded_text for field in critical_fields):
                    continue
                page = context.detail(repository, run["id"], actor, kind="lesson", record_id=ident, max_chars=budget)
                new_responses.append(page)
                if page["state"] == "requires_more_context":
                    parts = []
                    for section in ("question", "conditions", "exclusions", "verification", "sources"):
                        part = context.detail(repository, run["id"], actor, kind="lesson", record_id=ident,
                                              section=section, max_chars=budget)
                        new_responses.append(part)
                        parts.append(part)
                    complete_new[ident] = parts
                else:
                    complete_new[ident] = page
            new_text = "\n".join(_all_strings([new, *complete_new.values()]))
            old_text = "\n".join(_all_strings(list(old_records.values())))
            critical = [field for item in expected.values()
                        for field in [item["question"], *item["conditions"], *item["exclusions"], item["verification"]]]
            preserved_old = all(field in old_text for field in critical)
            preserved_new = all(field in new_text for field in critical)
            old_serialized, new_serialized = list(map(canonical, old_responses)), list(map(canonical, new_responses))
            old_chars, new_chars = sum(map(len, old_serialized)), sum(map(len, new_serialized))
            new_ids = disclosed
            result = {"case": name, "expected_relevant_versions": len(expected),
                      "baseline": {"response_chars": old_chars, "response_utf8_bytes": sum(len(x.encode()) for x in old_serialized),
                                   "initial_response_chars": len(old_serialized[0]), "round_trips": len(old_responses),
                                   "initial_latency_ms": old_latency, "critical_fields_reconstructed": preserved_old,
                                   "relevant_versions_disclosed": len(set(old_records) & set(expected))},
                      "context": {"response_chars": new_chars, "response_utf8_bytes": sum(len(x.encode()) for x in new_serialized),
                                  "initial_response_chars": len(new_serialized[0]), "round_trips": len(new_responses),
                                  "initial_latency_ms": new_latency, "critical_fields_reconstructed": preserved_new,
                                  "relevant_versions_disclosed": len(new_ids & set(expected)),
                                  "unexpected_versions_disclosed": len(new_ids - set(expected)),
                                  "all_responses_within_cap": all(len(x) <= budget for x in new_serialized),
                                  "snapshot_matches_current_run": new.get("snapshot", {}).get("head_sha") == run["head_sha"],
                                  "residency_claim": new.get("residency")},
                      "output_size_reduction_fraction": 1 - new_chars / old_chars if old_chars > 0 else None,
                      "context_grew": new_chars > old_chars,
                      "extra_round_trips": len(new_responses) - len(old_responses),
                      "token_count": None, "missed_defects": None, "false_positives": None}
            if name == "expired_applicability":
                result["applicability_note"] = "Operator restriction after evaluated expiry makes the version ineligible; no natural-language date parser is claimed."
            results.append(result)
    return {"kind": "offline_synthetic_mechanism_evaluation", "baseline_source": BASELINE_SOURCE,
            "budget_unit": "serialized_characters", "budget": budget, "cases": results,
            "coverage": "Rendered plugin responses including explicit detail calls. Context includes protocol/run metadata absent from legacy recall. "
                        "Detail access is requested only for disclosed expected versions whose correctness-critical fields were not fully loaded; oversized detail uses complete semantic sections.",
            "latency": "Observed local initial retrieval/assembly milliseconds; hardware-dependent, not deterministic timing.",
            "total_review_token_savings": None, "total_review_cost_savings": None,
            "reviewer_quality": "Unavailable: no model reviews or independent adjudication. Fixture labels test mechanism only."}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Run an offline synthetic retrieval mechanism comparison; no model or network.")
    parser.add_argument("--budget", type=int, default=12000)
    args = parser.parse_args(argv)
    print(json.dumps(evaluate(budget=args.budget), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
