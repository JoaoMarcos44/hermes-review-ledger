"""The offline harness validates retrieval mechanics, not model intelligence."""
import socket
from unittest.mock import patch

from review_ledger.evaluation import SavedBaseline, _synthetic_case, evaluate
from review_ledger.learning import Learning
from review_ledger.models import canonical


def test_saved_baseline_preserves_legacy_recall_fields_and_measures_new_metadata(tmp_path):
    ledger, _, run, _, scope, query, _, _ = _synthetic_case(tmp_path, "irrelevant")
    old = SavedBaseline(ledger.store).recall(scope, run["id"], terms=[query])
    current = Learning(ledger.store).recall(scope, run["id"], terms=[query])
    # Keep the historical comparator unchanged. Critic provenance is additive
    # metadata in current recall, and its serialized size is real overhead.
    assert len(old["lessons"]) == len(current["lessons"]) == 1
    assert old["lesson_references"] == current["lesson_references"] == []
    legacy_lessons = []
    for lesson in current["lessons"]:
        assert lesson["critic_assessment_ids"] == []
        assert lesson["critic_links_eligible"] is True
        legacy_lessons.append({key: value for key, value in lesson.items()
                               if key not in {"critic_assessment_ids", "critic_links_eligible"}})
    assert old["lessons"] == legacy_lessons
    assert {key: value for key, value in old.items() if key not in {"lessons", "context_chars"}} == {
        key: value for key, value in current.items() if key not in {"lessons", "context_chars"}}
    assert old["context_chars"] == sum(len(canonical(lesson)) for lesson in legacy_lessons)
    assert current["context_chars"] == sum(len(canonical(lesson)) for lesson in current["lessons"])
    assert current["context_chars"] > old["context_chars"]
    assert "CSS" not in canonical(old)


def test_offline_evaluation_preserves_fields_bounds_and_unavailable_quality():
    with patch.object(socket, "socket", side_effect=AssertionError("Offline evaluation attempted network")):
        result = evaluate(budget=12000)
    assert len(result["cases"]) == 9
    for case in result["cases"]:
        assert case["baseline"]["critical_fields_reconstructed"]
        assert case["context"]["critical_fields_reconstructed"]
        assert case["context"]["all_responses_within_cap"]
        assert case["context"]["snapshot_matches_current_run"]
        assert "unknown" in case["context"]["residency_claim"]
        assert case["context"]["relevant_versions_disclosed"] == case["expected_relevant_versions"]
        assert case["context"]["unexpected_versions_disclosed"] == 0
        assert case["token_count"] is None
        assert case["missed_defects"] is None and case["false_positives"] is None
    assert result["total_review_token_savings"] is None
    assert result["total_review_cost_savings"] is None
    assert any(case["context_grew"] for case in result["cases"])
    cases = {case["case"]: case for case in result["cases"]}
    assert cases["duplicate_distinct_sources"]["context"]["relevant_versions_disclosed"] == 2
    assert cases["contradiction"]["context"]["relevant_versions_disclosed"] == 2
    assert cases["unicode"]["context"]["response_utf8_bytes"] > cases["unicode"]["context"]["response_chars"]
    assert cases["revoked_source"]["context"]["relevant_versions_disclosed"] == 0
    assert cases["expired_applicability"]["context"]["relevant_versions_disclosed"] == 0


def test_oversized_guidance_uses_detail_and_reports_extra_roundtrips():
    result = evaluate(budget=6000)
    long_case = next(case for case in result["cases"] if case["case"] == "long_with_exclusions")
    assert long_case["context"]["critical_fields_reconstructed"]
    assert long_case["context"]["all_responses_within_cap"]
    assert long_case["context"]["round_trips"] > 1
    assert long_case["extra_round_trips"] == long_case["context"]["round_trips"] - long_case["baseline"]["round_trips"]
