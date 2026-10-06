"""Synthetic capture-limit reporting, independent of later agent observations."""
from __future__ import annotations

import copy
import json

import pytest

from review_ledger.reports import export, markdown


REPO = "synthetic/example"
CAPTURE_FIELDS = ("files_complete", "patches_complete", "total_files", "omitted_files", "truncation_reasons")


def _json_report(ledger, run, actor):
    return json.loads(export(ledger, REPO, run["id"], actor, format="json")["content"])


def _capture_section(report):
    return report.split("## Captured-material completeness (initial snapshot)\n", 1)[1].split("\n## ", 1)[0]


def test_markdown_reports_complete_initial_capture(ledger, synthetic_snapshot, actor):
    supplied = {**synthetic_snapshot, "files_complete": True, "patches_complete": True,
                "total_files": 1, "omitted_files": 0, "truncation_reasons": []}
    run = ledger.open(supplied, actor, "complete-capture")["run"]

    report = export(ledger, REPO, run["id"], actor, format="markdown")["content"]
    section = _capture_section(report)

    assert "- Files complete: true" in section
    assert "- Patches complete: true" in section
    assert "- Total files: 1" in section
    assert "- Omitted files: 0" in section
    assert "- Truncation reasons: none recorded" in section
    assert "do not establish review coverage or correctness" in section
    assert report.index("## Captured-material completeness") < report.index("## Supported current assessments")
    assert _json_report(ledger, run, actor)["snapshot"] == run["snapshot"]


@pytest.mark.parametrize("files_complete,total_files,omitted_files,reasons", [
    (False, 3, 2, ["Synthetic file limit", "Synthetic patch limit"]),
    (True, 1, 0, ["Synthetic patch unavailable"]),
])
def test_markdown_reports_incomplete_capture_even_without_findings(
    ledger, synthetic_snapshot, actor, files_complete, total_files, omitted_files, reasons,
):
    supplied = {**synthetic_snapshot, "files_complete": files_complete, "patches_complete": False,
                "total_files": total_files, "omitted_files": omitted_files, "truncation_reasons": reasons}
    run = ledger.open(supplied, actor, "incomplete-capture")["run"]

    report = export(ledger, REPO, run["id"], actor, format="markdown")["content"]
    section = _capture_section(report)

    assert f"- Files complete: {str(files_complete).lower()}" in section
    assert "- Patches complete: false" in section
    assert f"- Total files: {total_files}" in section
    assert f"- Omitted files: {omitted_files}" in section
    for reason in reasons:
        assert f"  - {reason}" in section
    assert "Zero supported findings is a valid result" in report
    assert not any(_json_report(ledger, run, actor)["omitted"].values())


@pytest.mark.parametrize("missing", [True, False], ids=["older-missing-fields", "explicit-null-fields"])
def test_markdown_marks_unrecorded_capture_facts_unknown(ledger, opened, actor, missing):
    data = _json_report(ledger, opened, actor)
    for key in CAPTURE_FIELDS:
        if missing:
            data["snapshot"].pop(key)
        else:
            data["snapshot"][key] = None
    original = copy.deepcopy(data)

    section = _capture_section(markdown(data))

    for label in ("Files complete", "Patches complete", "Total files", "Omitted files", "Truncation reasons"):
        assert f"- {label}: unknown" in section
    assert "Missing capture facts remain unknown" in section
    assert "- Truncation reasons: none recorded" not in section
    assert data == original


def test_markdown_keeps_known_capture_facts_when_other_fields_are_missing(ledger, opened, actor):
    data = _json_report(ledger, opened, actor)
    data["snapshot"] = {"files_complete": False, "omitted_files": 0, "truncation_reasons": []}

    section = _capture_section(markdown(data))

    assert "- Files complete: false" in section
    assert "- Patches complete: unknown" in section
    assert "- Total files: unknown" in section
    assert "- Omitted files: 0" in section
    assert "- Truncation reasons: none recorded" in section
    assert "no truncation reasons recorded does not imply completeness" in section


def test_markdown_escapes_capture_reasons_and_preserves_json(ledger, synthetic_snapshot, actor):
    reason = "Synthetic limit\r\n# Capture note\r\n<note> & [sample] *text*"
    run = ledger.open({**synthetic_snapshot, "truncation_reasons": [reason]}, actor, "literal-capture")["run"]
    before = _json_report(ledger, run, actor)["snapshot"]

    report = export(ledger, REPO, run["id"], actor, format="markdown")["content"]
    section = _capture_section(report)

    assert "  - Synthetic limit\n    \\# Capture note" in section
    assert "&lt;note&gt; &amp; \\[sample\\] \\*text\\*" in section
    assert "\n# Capture note" not in report
    assert "<note>" not in report
    assert "\r" not in section
    after = _json_report(ledger, run, actor)["snapshot"]
    assert before == after == run["snapshot"]
    assert after["truncation_reasons"] == [reason]


def test_later_agent_observations_do_not_replace_initial_capture_facts(ledger, opened, actor):
    before = _json_report(ledger, opened, actor)["snapshot"]
    summary = "Additional material was inspected after the initial capture"
    ledger.record(REPO, opened["id"], actor, opened["generation"], "observation", {
        "kind": "inspection", "outcome": "inspection", "summary": summary,
        "limitations": "Synthetic agent report without independent verification",
    }, "later-inspection")

    report = export(ledger, REPO, opened["id"], actor, format="markdown")["content"]
    section = _capture_section(report)

    assert "- Patches complete: false" in section
    assert "  - Synthetic missing patch" in section
    assert "Later agent-reported observations do not replace them" in section
    assert summary not in section
    assert summary in report.split("## Observations (agent-reported)", 1)[1]
    assert _json_report(ledger, opened, actor)["snapshot"] == before
