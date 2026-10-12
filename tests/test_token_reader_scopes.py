"""Identity follows the exact ancestor scope of the host's auxiliary getter."""
from types import SimpleNamespace
import pytest

from review_ledger.hermes_usage import SessionDBMetadataReader


@pytest.mark.parametrize("case", ["ancestor_prefix", "branch_cut"])
def test_reader_matches_auxiliary_ancestor_scope(case):
    ids = ["synthetic-root", "synthetic-tip"] if case == "ancestor_prefix" else ["synthetic-root", "synthetic-branch", "synthetic-tip"]
    db = SimpleNamespace(
        get_compression_lineage=lambda sid: ["synthetic-tip", "synthetic-future-tip"],
        _session_lineage_root_to_tip=lambda sid: ids,
        _is_explicit_branch_session=lambda sid: sid == "synthetic-branch",
    )
    reader = SessionDBMetadataReader()
    reader._db = db
    expected = ids if case == "ancestor_prefix" else ["synthetic-branch", "synthetic-tip"]
    assert reader.auxiliary_lineage_ids("synthetic-tip") == expected
