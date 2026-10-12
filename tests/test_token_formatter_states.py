"""Formatter state coverage: every auxiliary coverage state is shown truthfully.

The human formatter must distinguish:
- MISSING (host metadata not found)
- already_counted (lineage counted with a representative session)
- none (known complete empty data — the only correct use of "none")
- partial (some task counters incomplete — stays explicit)

Worker enumeration limitations must be conditional on include_workers.
"""
from __future__ import annotations

import pytest

from review_ledger.models import Actor
from review_ledger.token_report import TokenReport, format_report_human
from test_token_report import FakeMetadataReader, _main_metadata


@pytest.mark.parametrize("case", [
    "missing_host_session",
    "missing_auxiliary_getter",
    "already_counted_lineage",
    "known_empty_auxiliary",
    "partial_auxiliary",
    "supported_worker_enumeration",
])
def test_auxiliary_formatter_distinguishes_all_coverage_states(
    ledger, synthetic_snapshot, opened, actor, case,
):
    """Human formatter distinguishes all auxiliary coverage states truthfully."""
    repo = "synthetic/example"

    if case == "missing_host_session":
        # Reader has no metadata for the session → main and aux both missing
        reader = FakeMetadataReader(sessions={})
        report = TokenReport(ledger, reader).report(repo, run_id=opened["id"])
        text = format_report_human(report)
        assert "Main usage: MISSING" in text
        assert "Auxiliary: MISSING" in text
        assert "Auxiliary: none" not in text

    elif case == "missing_auxiliary_getter":
        # Reader returns None for auxiliary_usage_by_task → aux coverage missing
        class NoneAuxReader(FakeMetadataReader):
            def auxiliary_usage_by_task(self, session_id):
                return None

        reader = NoneAuxReader(sessions={actor.session_id: _main_metadata()})
        report = TokenReport(ledger, reader).report(repo, run_id=opened["id"])
        text = format_report_human(report)
        assert "Auxiliary: MISSING" in text
        assert "Auxiliary: none" not in text

    elif case == "already_counted_lineage":
        # Two sessions in same lineage: non-representative shows already_counted
        root_id, child_id = actor.session_id, "synthetic-z-child-session"
        main = {
            root_id: _main_metadata(id=root_id),
            child_id: _main_metadata(id=child_id, parent_session_id=root_id, source="subagent"),
        }
        aux = {
            "compression": {
                "input_tokens": 200, "output_tokens": 80,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
                "reasoning_tokens": 0, "api_call_count": 1,
            }
        }

        class LineageReader(FakeMetadataReader):
            def auxiliary_lineage_ids(self, sid):
                return [root_id, child_id]

        reader = LineageReader(main, {root_id: aux, child_id: aux})
        snap8 = {**synthetic_snapshot, "number": 8,
                 "url": "https://github.com/synthetic/example/pull/8"}
        ledger.open(snap8, Actor(child_id), "child-open-8")
        report = TokenReport(ledger, reader).report(repo)
        text = format_report_human(report)
        assert "already counted with session" in text
        assert "Auxiliary: none" not in text

    elif case == "known_empty_auxiliary":
        # Reader returns {} → known complete empty data → "none" is correct
        reader = FakeMetadataReader(
            sessions={actor.session_id: _main_metadata()},
            auxiliary={actor.session_id: {}},
        )
        report = TokenReport(ledger, reader).report(repo, run_id=opened["id"])
        text = format_report_human(report)
        assert "Auxiliary: none" in text
        assert "Auxiliary: MISSING" not in text

    elif case == "partial_auxiliary":
        # Task with incomplete counters → coverage partial, stays explicit
        aux = {
            "compression": {
                "input_tokens": 5, "output_tokens": None,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
                "reasoning_tokens": 0, "api_call_count": 1,
            }
        }
        reader = FakeMetadataReader(
            sessions={actor.session_id: _main_metadata()},
            auxiliary={actor.session_id: aux},
        )
        report = TokenReport(ledger, reader).report(repo, run_id=opened["id"])
        text = format_report_human(report)
        # Per-session partial must be explicit, not just top-level coverage
        assert "Auxiliary tasks: 1 [partial]" in text

    elif case == "supported_worker_enumeration":
        # Reader supports list_sessions_metadata → no false limitation
        root_id, child_id = actor.session_id, "synthetic-z-child-session"
        main = {
            root_id: _main_metadata(id=root_id),
            child_id: _main_metadata(id=child_id, parent_session_id=root_id, source="subagent"),
        }

        class WorkerReader(FakeMetadataReader):
            def list_sessions_metadata(self):
                return list(self.sessions.values())

        reader = WorkerReader(main, {})
        report = TokenReport(ledger, reader).report(
            repo, run_id=opened["id"], include_workers=True,
        )
        assert "Worker descendant enumeration is not supported" not in str(report["limitations"])
        assert report["totals"]["session_count"] == 2
