"""Regression contracts for the five PR15 token-report blockers.

Synthetic-only: every record is created through the public Ledger/Learning
admission paths over a temporary Store, and the injected reader returns
allowlisted, body-free metadata.  No live profile, provider, or session data
is read or fabricated as live evidence.

Two parametrized functions cover all report scopes:

* ``test_token_report_fixes_regression`` exercises worker attribution
  (direct + inherited memberships), selected-only shared disclosures, stable
  auxiliary total keys, and the truthful auxiliary-cost exclusion notice.
* ``test_snapshot_writer_progress_and_captured_view`` proves, with a real
  independent SQLite connection and a deterministic writer interleaving, that
  all bounded Ledger reads happen inside one read snapshot which is released
  before any host read, and that the emitted report retains that one view.
"""
from __future__ import annotations

import threading

import pytest

from review_ledger.models import Actor
from review_ledger.token_report import TokenReport


REPO = "synthetic/example"

# Every auxiliary totals mapping must expose the same derived keys, including
# prompt_tokens and total_tokens, whether the session is complete, missing, or
# already counted with a representative lineage member.
_AUX_TOTAL_KEYS = frozenset({
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "reasoning_tokens", "api_call_count", "prompt_tokens", "total_tokens",
})


def _snap(snapshot, number):
    return {**snapshot, "number": number,
            "url": f"https://github.com/synthetic/example/pull/{number}"}


def _meta(session_id, **overrides):
    base = {
        "id": session_id, "source": "cli", "model": "synthetic-model",
        "billing_provider": "synthetic-provider",
        "input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 20,
        "cache_write_tokens": 10, "reasoning_tokens": 15, "api_call_count": 3,
        "estimated_cost_usd": 0.01, "actual_cost_usd": None,
        "cost_status": "estimated",
    }
    base.update(overrides)
    return base


def _session(report, session_id):
    return next(s for s in report["sessions"] if s["session_id"] == session_id)


def _totals_keys(report, session_id):
    return frozenset(_session(report, session_id)["auxiliary"]["totals"])


def _shared_ids(report):
    return {s["session_id"] for s in report["shared_sessions"]}


def _excludes_auxiliary_cost(report):
    return any("cost" in lim.lower() and "auxiliary" in lim.lower()
               for lim in report["limitations"])


class FixtureReader:
    """Reader seam with one bounded host enumeration and auxiliary lineage.

    ``sessions`` holds allowlisted metadata for known sessions (absent ids are
    reported missing).  ``host_sessions``/``parents`` describe the host's
    descendant enumeration; ``lineage`` mirrors the host's auxiliary scope
    getter, which returns ancestor prefixes rather than equal lineages.
    """

    def __init__(self, sessions, host_sessions, parents=None, auxiliary=None, lineage=None):
        self.sessions = sessions
        self.host_sessions = host_sessions
        self.parents = parents or {}
        self.auxiliary = auxiliary or {}
        self.lineage = lineage or {}

    def get_session(self, session_id):
        return self.sessions.get(session_id)

    def auxiliary_usage_by_task(self, session_id):
        return self.auxiliary.get(session_id, {})

    def auxiliary_lineage_ids(self, session_id):
        return self.lineage.get(session_id, [session_id])

    def list_sessions_metadata(self):
        return [{"id": sid, "parent_session_id": self.parents.get(sid)}
                for sid in self.host_sessions]


def _regression_fixture(ledger, synthetic_snapshot):
    """Build runs/sessions that expose every blocker in one repository."""
    a = Actor("synthetic-session-a")
    b = Actor("synthetic-session-b")
    c = Actor("synthetic-child-c")   # worker of A that also directly owns a run
    w = Actor("synthetic-worker-w")  # worker of A that owns no run
    runs = {
        "a": ledger.open(_snap(synthetic_snapshot, 7), a, "fix-open-a7")["run"],
        "a2": ledger.open(_snap(synthetic_snapshot, 8), a, "fix-open-a8")["run"],
        "b": ledger.open(_snap(synthetic_snapshot, 9), b, "fix-open-b9")["run"],
        "c": ledger.open(_snap(synthetic_snapshot, 10), c, "fix-open-c10")["run"],
    }
    auxiliary = {"compression": {
        "input_tokens": 10, "output_tokens": 7, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "reasoning_tokens": 0, "api_call_count": 1,
        "estimated_cost_usd": 9.0,
    }}
    reader = FixtureReader(
        sessions={
            a.session_id: _meta(a.session_id, estimated_cost_usd=0.01),
            b.session_id: _meta(b.session_id, estimated_cost_usd=0.02),
            c.session_id: _meta(c.session_id, estimated_cost_usd=None, actual_cost_usd=None),
        },
        host_sessions=[a.session_id, b.session_id, c.session_id, w.session_id],
        parents={c.session_id: a.session_id, w.session_id: a.session_id},
        auxiliary={a.session_id: auxiliary, b.session_id: {}},
        lineage={
            a.session_id: [a.session_id, c.session_id],
            c.session_id: [c.session_id],
            b.session_id: [b.session_id],
        },
    )
    return a, b, c, w, runs, reader


# ── Blocker regressions across every report scope ───────────────────────────

@pytest.mark.parametrize("scope", ["run", "session", "repository"])
def test_token_report_fixes_regression(scope, ledger, synthetic_snapshot):
    a, b, c, w, runs, reader = _regression_fixture(ledger, synthetic_snapshot)
    analyzer = TokenReport(ledger, reader)

    if scope == "run":
        report = analyzer.report(REPO, run_id=runs["a"]["id"], include_workers=True)
        # Worker attribution: a child keeps its direct run and inherits every
        # membership of its ancestors; a child owning nothing inherits them too.
        assert {s["session_id"] for s in report["sessions"]} == {a.session_id, c.session_id, w.session_id}
        assert set(_session(report, c.session_id)["correlated_runs"]) == {
            runs["a"]["id"], runs["a2"]["id"], runs["c"]["id"]}
        assert set(_session(report, w.session_id)["correlated_runs"]) == {runs["a"]["id"], runs["a2"]["id"]}
        assert set(_session(report, a.session_id)["correlated_runs"]) == {runs["a"]["id"], runs["a2"]["id"]}
        # Shared disclosures are restricted to selected sessions.
        assert _shared_ids(report) == {a.session_id, c.session_id, w.session_id}
        # Stable auxiliary total keys across complete/already_counted/missing.
        assert (_totals_keys(report, a.session_id) == _totals_keys(report, c.session_id)
                == _totals_keys(report, w.session_id) == _AUX_TOTAL_KEYS)
        assert _session(report, a.session_id)["cost"]["estimated_usd"] == 0.01
        assert _session(report, c.session_id)["cost"]["status"] == "unknown"
        assert _excludes_auxiliary_cost(report)
        # A report for an unrelated run must not disclose another session's
        # sharing that has nothing to do with the selected run.
        unrelated = analyzer.report(REPO, run_id=runs["b"]["id"])
        assert {s["session_id"] for s in unrelated["sessions"]} == {b.session_id}
        assert unrelated["shared_sessions"] == []

    elif scope == "session":
        report = analyzer.report(REPO, session_id=b.session_id)
        assert _session(report, b.session_id)["correlated_runs"] == [runs["b"]["id"]]
        assert report["shared_sessions"] == []
        assert _session(report, b.session_id)["cost"]["estimated_usd"] == 0.02
        assert _excludes_auxiliary_cost(report)
        # A selected session that is genuinely shared is still disclosed.
        shared = analyzer.report(REPO, session_id=a.session_id)
        assert set(_session(shared, a.session_id)["correlated_runs"]) == {runs["a"]["id"], runs["a2"]["id"]}
        assert _shared_ids(shared) == {a.session_id}

    else:
        report = analyzer.report(REPO, include_workers=True)
        assert {s["session_id"] for s in report["sessions"]} == {
            a.session_id, b.session_id, c.session_id, w.session_id}
        assert report["totals"]["session_count"] == 4
        assert set(_session(report, c.session_id)["correlated_runs"]) == {
            runs["a"]["id"], runs["a2"]["id"], runs["c"]["id"]}
        assert set(_session(report, w.session_id)["correlated_runs"]) == {runs["a"]["id"], runs["a2"]["id"]}
        assert _shared_ids(report) == {a.session_id, c.session_id, w.session_id}
        assert (_totals_keys(report, a.session_id) == _totals_keys(report, c.session_id)
                == _totals_keys(report, w.session_id) == _AUX_TOTAL_KEYS)
        assert _session(report, a.session_id)["cost"]["estimated_usd"] == 0.01
        assert _session(report, b.session_id)["cost"]["estimated_usd"] == 0.02
        assert _session(report, c.session_id)["cost"]["status"] == "unknown"
        assert _excludes_auxiliary_cost(report)


# ── Snapshot isolation with a real independent writer ───────────────────────

@pytest.mark.parametrize("scope", ["run", "session", "repository"])
def test_snapshot_writer_progress_and_captured_view(scope, ledger, synthetic_snapshot):
    """One captured Ledger view, released before any host read.

    A real writer thread admits a second run through the public Ledger API on
    an independent SQLite connection while correlation is running.  With one
    held read snapshot the writer cannot commit until that snapshot is
    released, so it progresses only after the Ledger phase and before the first
    host ``get_session`` returns.  The report must retain the captured view:
    the later admission never appears.
    """
    actor = Actor("synthetic-session-a")
    other = Actor("synthetic-session-b")
    run_a = ledger.open(_snap(synthetic_snapshot, 7), actor, "snap-a7")["run"]
    ledger.open(_snap(synthetic_snapshot, 8), other, "snap-b8")

    state = {"in_transaction": [], "started": False, "writer": None,
             "writer_error": None, "thread": None}

    class SnapshotReader:
        """Host seam: triggers/verifies the writer on the first host read."""

        def __init__(self):
            self._joined = False

        def get_session(self, session_id):
            if not self._joined:
                self._joined = True
                # The snapshot must already be released: the writer progresses
                # while this host callback runs.
                state["thread"].join(timeout=10)
                assert not state["thread"].is_alive(), \
                    "writer did not progress during host reads (read snapshot still held)"
                assert state["writer_error"] is None, f"writer failed: {state['writer_error']}"
            return _meta(session_id)

        def auxiliary_usage_by_task(self, session_id):
            return {}

        def close(self):
            pass

    analyzer = TokenReport(ledger, SnapshotReader())
    original = analyzer._correlate_run_sessions

    def correlated(conn, scope_obj, run_id):
        result = original(conn, scope_obj, run_id)
        # The first correlation read has now run inside the snapshot, so this
        # connection holds its read lock (if any) before the writer starts.
        state["in_transaction"].append(conn.in_transaction)
        if not state["started"]:
            state["started"] = True

            def writer():
                try:
                    state["writer"] = ledger.open(
                        _snap(synthetic_snapshot, 9), actor, "snap-writer9")["run"]
                except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                    state["writer_error"] = repr(exc)

            thread = threading.Thread(target=writer, daemon=True)
            state["thread"] = thread
            thread.start()
            # Deterministic interleaving: on an autocommit code path the
            # independent writer commits here (fast); with one held read
            # snapshot it blocks until that snapshot is released, so the join
            # times out, correlation continues, and the writer commits before
            # the first host read.
            thread.join(timeout=1.0)
        return result

    analyzer._correlate_run_sessions = correlated

    if scope == "run":
        report = analyzer.report(REPO, run_id=run_a["id"])
    elif scope == "session":
        report = analyzer.report(REPO, session_id=actor.session_id)
    else:
        report = analyzer.report(REPO)

    # Every bounded Ledger read ran inside one explicit read snapshot.
    assert state["in_transaction"] and all(state["in_transaction"]), \
        f"correlation reads were not inside one read snapshot: {state['in_transaction']}"
    assert state["writer_error"] is None
    run_w = state["writer"]["id"]
    session_a = _session(report, actor.session_id)
    assert session_a["correlated_runs"] == [run_a["id"]], \
        "report mixed a later admission into one captured view"
    assert run_w not in session_a["correlated_runs"]
    assert report["shared_sessions"] == []

    # Reader-failure cleanup: a host read failure must not leave the Ledger
    # read snapshot held, so a durable writer still progresses.
    class FailingReader:
        def get_session(self, session_id):
            raise RuntimeError("host metadata unavailable")

        def auxiliary_usage_by_task(self, session_id):
            return {}

    failing = TokenReport(ledger, FailingReader())
    fail_original = failing._correlate_run_sessions
    fail_state = {"thread": None, "started": False, "error": None}

    def fail_correlate(conn, scope_obj, run_id):
        result = fail_original(conn, scope_obj, run_id)
        if not fail_state["started"]:
            fail_state["started"] = True

            def fail_writer():
                try:
                    ledger.open(_snap(synthetic_snapshot, 11), actor, "snap-fail-writer11")
                except BaseException as exc:  # noqa: BLE001 - recorded for the assertion
                    fail_state["error"] = repr(exc)

            thread = threading.Thread(target=fail_writer, daemon=True)
            fail_state["thread"] = thread
            thread.start()
            thread.join(timeout=1.0)
        return result

    failing._correlate_run_sessions = fail_correlate
    with pytest.raises(RuntimeError, match="host metadata unavailable"):
        if scope == "run":
            failing.report(REPO, run_id=run_a["id"])
        elif scope == "session":
            failing.report(REPO, session_id=actor.session_id)
        else:
            failing.report(REPO)
    fail_state["thread"].join(timeout=10)
    assert not fail_state["thread"].is_alive(), "reader failure left the read snapshot held"
    assert fail_state["error"] is None
