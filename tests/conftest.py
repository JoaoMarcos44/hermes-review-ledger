"""All ledger records and repositories in these fixtures are synthetic."""
from __future__ import annotations

import pytest

from review_ledger.models import Actor
from review_ledger.service import Ledger
from review_ledger.storage import Store


@pytest.fixture
def synthetic_snapshot():
    return {
        "repository_id": 1001, "repository_node_id": "synthetic-repository-node-1001",
        "repository_full_name": "synthetic/example", "number": 7,
        "title": "Synthetic ledger fixture", "url": "https://github.com/synthetic/example/pull/7",
        "head_sha": "a" * 40, "base_sha": "b" * 40, "comparison": "github_pr",
        "files": [{"filename": "example.py", "sha": "c" * 40, "status": "modified", "patch_status": "unavailable"}],
        "files_complete": True, "patches_complete": False, "total_files": 1,
        "omitted_files": 0, "truncation_reasons": ["Synthetic missing patch"],
    }


@pytest.fixture
def ledger(tmp_path):
    return Ledger(Store(tmp_path / "profile-a" / "ledger", "synthetic-profile-a"),
                  ["synthetic/example", "synthetic/other"], skill_version="0.1.0", skill_hash="d" * 64,
                  relevant_config={"context_budget": 6000})


@pytest.fixture
def actor():
    return Actor("synthetic-trusted-session-a")


@pytest.fixture
def opened(ledger, synthetic_snapshot, actor):
    return ledger.open(synthetic_snapshot, actor, "synthetic-open-1")["run"]


@pytest.fixture
def observation_data():
    return {"kind": "test", "outcome": "behavior_failure", "summary": "Synthetic repeated write was reported",
            "details": "A synthetic local data-integrity fixture reported two rows instead of one.",
            "environment": "Synthetic fixture; no target repository code executed",
            "limitations": "Agent-reported fixture; plugin did not observe execution."}


@pytest.fixture
def observed(ledger, opened, actor, observation_data):
    return ledger.record("synthetic/example", opened["id"], actor, opened["generation"], "observation", observation_data, "synthetic-observe-1")["observation_id"]


@pytest.fixture
def lesson_data(observed):
    return {"question": "Could retrying repeat a persistent effect?",
            "conditions": ["An operation can be repeated", "The operation has a persistent effect"],
            "exclusions": ["Deduplication is shown to cover the relevant effect"],
            "verification": "Inspect the boundary between effect and acknowledgement.",
            "tags": ["retry", "persistence"], "symbols": ["save_item"],
            "sources": [{"observation_id": observed, "relation": "supports"}]}
