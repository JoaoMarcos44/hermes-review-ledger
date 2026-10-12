"""Operator grammar for read-only token reports; no live state is opened."""
import argparse

import pytest

from review_ledger.operator import configure_parser


@pytest.mark.parametrize("extra,expected_format,workers", [
    ([], "human", False),
    (["--format", "json", "--include-workers"], "json", True),
])
def test_tokens_operator_accepts_explicit_scope(extra, expected_format, workers):
    parser = argparse.ArgumentParser()
    configure_parser(parser)
    try:
        args = parser.parse_args(["tokens", "synthetic/example", "--run-id", "run_" + "a" * 32, *extra])
    except SystemExit:
        args = None
    assert args is not None, "The native tokens operator command is not registered"
    assert args.ledger_operator_action == "tokens"
    assert args.repository == "synthetic/example"
    assert args.run_id == "run_" + "a" * 32
    assert args.format == expected_format
    assert args.include_workers is workers


@pytest.mark.parametrize("case", ["json", "human", "unavailable"])
def test_tokens_dispatch_closes_reader_and_labels_scope(ledger, opened, actor, monkeypatch, capsys, case):
    import json
    from review_ledger import hermes_usage, tools
    from review_ledger.operator import dispatch
    from test_token_report import FakeMetadataReader, _main_metadata

    closed = []
    class Reader(FakeMetadataReader):
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            closed.append(True)
        def get_session(self, sid):
            if case == "unavailable":
                raise RuntimeError("synthetic private endpoint detail")
            return super().get_session(sid)
    reader = Reader({actor.session_id: _main_metadata()})
    monkeypatch.setattr(tools, "ledger_for_context", lambda ctx: ledger)
    monkeypatch.setattr(hermes_usage, "SessionDBMetadataReader", lambda: reader)
    parser = argparse.ArgumentParser()
    configure_parser(parser)
    args = parser.parse_args(["tokens", "synthetic/example", "--run-id", opened["id"],
                              "--format", "json" if case != "human" else "human"])
    rc = dispatch(None, args)
    output = capsys.readouterr().out
    assert closed == [True]
    assert "synthetic private endpoint detail" not in output
    if case == "unavailable":
        assert rc == 1
        assert json.loads(output)["error"]["code"] == "host_usage_unavailable"
    elif case == "json":
        data = json.loads(output)
        assert rc == 0 and data["scope"] == "run"
        m = _main_metadata()
        assert data["totals"]["main"]["total_tokens"] == m["input_tokens"] + m["cache_read_tokens"] + m["cache_write_tokens"] + m["output_tokens"]
    else:
        assert rc == 0
        assert "whole_session_non_marginal" in output
        assert "uncached=100" in output and "cache_read=20" in output
        assert "Scope: run" in output
