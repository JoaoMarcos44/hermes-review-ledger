"""Exact local counts, offline tokenizer injection, and bounded private caches."""
import importlib.util
import socket
import subprocess
import sys

import pytest

from review_ledger.models import LedgerError
from review_ledger import token_count
from review_ledger.token_count import CountSession, TiktokenCounter


class LocalCounter:
    def __init__(self, profile="one"):
        self.identity = {"library": "synthetic", "version": "1", "encoding": profile,
                         "scope": "local_string", "nested": {"profile": profile}}
        self.calls = []

    def count(self, value):
        self.calls.append(value)
        return len(value.encode("utf-8"))


class PreparedEncoding:
    name = "synthetic-bytes"

    def __init__(self):
        self.calls = []

    def encode(self, *_args, **_kwargs):
        raise AssertionError("Special-token-aware encoding must not be used")

    def encode_ordinary(self, value):
        self.calls.append(value)
        return list(value.encode("utf-8"))


@pytest.mark.parametrize("value,chars,byte_count", [
    ("", 0, 0), ("ASCII", 5, 5), ("\x00\n\r\t", 4, 4), ("café", 4, 5),
    ("e\u0301", 2, 3), ("中文", 2, 6), ("🔬", 1, 4), ("👩\u200d🔬", 3, 11),
    ("\ufeff\ufffd\U0010ffff", 3, 10),
])
def test_stdlib_counts_unicode_without_normalizing(value, chars, byte_count):
    result = CountSession().measure(value)
    assert result == {"chars": chars, "bytes": byte_count, "tokens": None, "counter": None,
                      "quality": {"chars": "exact", "bytes": "exact", "tokens": "unavailable"}}


@pytest.mark.parametrize("value", [None, b"text", 4, True, [], {}, "\ud800", "\udfff", "a\ud800z", "\ud83d\udd2c"])
def test_malformed_text_fails_with_ledger_error(value):
    with pytest.raises(LedgerError) as error:
        CountSession().measure(value)
    assert error.value.code == "invalid_input"
    if isinstance(value, str):
        assert "Unicode" in str(error.value)


def test_default_import_and_measure_do_not_load_tokenizers_or_network():
    script = '''
import builtins, socket, sys
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {'tiktoken', 'transformers', 'torch', 'requests', 'httpx'}:
        raise AssertionError('Optional dependency imported: ' + name)
    return real_import(name, *args, **kwargs)
def no_network(*args, **kwargs):
    raise AssertionError('Network must not be used')
builtins.__import__ = guarded_import
socket.socket = no_network
socket.create_connection = no_network
from review_ledger.token_count import CountSession
result = CountSession().measure('é <|endoftext|>')
assert result['bytes'] == 16
assert result['tokens'] is None
assert not any(name == 'tiktoken' or name.startswith('tiktoken.') for name in sys.modules)
'''
    completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_default_never_looks_up_optional_package_metadata(monkeypatch):
    def forbidden(*_args):
        raise AssertionError("Optional metadata is not needed by the default path")
    monkeypatch.setattr(token_count.metadata, "version", forbidden)
    assert CountSession().measure("text")["tokens"] is None


def test_exact_inclusive_budgets_for_unicode():
    session = CountSession(LocalCounter())
    assert session.fits("é🔬", max_chars=2, max_bytes=6, max_tokens=6, strict_tokens=True)
    assert not session.fits("é🔬", max_chars=1)
    assert not session.fits("é🔬", max_bytes=5)
    assert not session.fits("é🔬", max_tokens=5)
    assert session.fits("", max_chars=0, max_bytes=0, max_tokens=0)
    assert not session.fits("x", max_chars=0)
    assert session.fits("anything")


@pytest.mark.parametrize("dimension", ["max_chars", "max_bytes", "max_tokens"])
@pytest.mark.parametrize("invalid", [True, False, -1, 1.0, "5", float("nan"), [], {}])
def test_every_budget_rejects_boolean_negative_and_non_integer(dimension, invalid):
    with pytest.raises(LedgerError) as error:
        CountSession().fits("", **{dimension: invalid})
    assert error.value.code == "invalid_input"


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", []])
def test_strict_flag_must_be_boolean(invalid):
    with pytest.raises(LedgerError) as error:
        CountSession().fits("", strict_tokens=invalid)
    assert error.value.code == "invalid_input"


def test_unavailable_tokens_fail_closed_and_strict_is_explicit():
    session = CountSession()
    assert session.fits("é", max_chars=1, max_bytes=2)
    assert not session.fits("é", max_tokens=100)
    assert not session.fits("", max_tokens=0)
    with pytest.raises(LedgerError) as error:
        session.fits("é", max_chars=0, max_tokens=100, strict_tokens=True)
    assert error.value.code == "token_count_unavailable"
    assert session.fits("é", max_chars=1, strict_tokens=True)
    assert session.measure("é")["quality"]["tokens"] == "unavailable"


@pytest.mark.parametrize("invalid", [None, True, False, -1, 1.5, "1", [], {}])
def test_invalid_counter_results_are_unavailable_and_retried(invalid):
    counter = LocalCounter()
    counter.count = lambda _value: invalid
    session = CountSession(counter)
    measured = session.measure("private content")
    assert measured["tokens"] is None
    assert measured["counter"] == counter.identity
    assert measured["quality"]["tokens"] == "unavailable"
    with pytest.raises(LedgerError) as error:
        session.fits("private content", max_tokens=100, strict_tokens=True)
    assert error.value.code == "token_count_unavailable"
    counter.count = lambda _value: 3
    assert session.measure("private content")["tokens"] == 3


def test_counter_failure_preserves_counts_without_exception_content():
    counter = LocalCounter()
    def failed(_value):
        raise RuntimeError("Do not disclose private-input-value")
    counter.count = failed
    session = CountSession(counter)
    assert session.measure("é")["bytes"] == 2
    with pytest.raises(LedgerError) as error:
        session.fits("é", max_tokens=1, strict_tokens=True)
    assert error.value.code == "token_count_unavailable"
    assert "private-input-value" not in str(error.value)


def test_cache_is_lru_bounded_by_items():
    counter = LocalCounter()
    session = CountSession(counter, max_cache_items=2, max_cache_bytes=100)
    for value in ["a", "b", "a", "c", "a", "b"]:
        session.measure(value)
    assert counter.calls == ["a", "b", "c", "b"]
    assert len(session._cache) == 2


def test_cache_bytes_use_utf8_and_oversized_values_are_not_retained():
    counter = LocalCounter()
    session = CountSession(counter, max_cache_bytes=4)
    for value in ["é", "éé", "éé", "é", "12345", "12345"]:
        session.measure(value)
        assert session._cache_bytes <= 4
    assert counter.calls == ["é", "éé", "é", "12345", "12345"]
    assert list(session._cache) == ["é"]
    assert session._cache_bytes == 2


@pytest.mark.parametrize("bounds", [{"max_cache_items": 0}, {"max_cache_bytes": 0}])
def test_zero_cache_limit_disables_retention(bounds):
    counter = LocalCounter()
    session = CountSession(counter, **bounds)
    session.measure("a")
    session.measure("a")
    assert counter.calls == ["a", "a"]
    assert not session._cache
    assert session._cache_bytes == 0


@pytest.mark.parametrize("name,invalid", [
    ("max_cache_items", True), ("max_cache_items", -1), ("max_cache_items", 4097),
    ("max_cache_items", 1.5), ("max_cache_bytes", False), ("max_cache_bytes", -1),
    ("max_cache_bytes", 16 * 1024 * 1024 + 1), ("max_cache_bytes", None),
])
def test_cache_bounds_are_validated(name, invalid):
    with pytest.raises(LedgerError) as error:
        CountSession(**{name: invalid})
    assert error.value.code == "invalid_input"


def test_cache_is_not_shared_between_instances_or_profiles():
    counter = LocalCounter()
    first, second = CountSession(counter), CountSession(counter)
    first.measure("private")
    second.measure("private")
    assert counter.calls == ["private", "private"]
    assert first._cache is not second._cache
    other_counter = LocalCounter("two")
    other = CountSession(other_counter)
    assert other.measure("private")["counter"]["encoding"] == "two"
    assert other_counter.calls == ["private"]


def test_cache_and_counter_identity_are_not_changed_by_returned_metadata():
    counter = LocalCounter()
    session = CountSession(counter)
    measured = session.measure("é")
    measured["tokens"] = 1000
    measured["quality"]["tokens"] = "made_up"
    measured["counter"]["nested"]["profile"] = "different"
    repeated = session.measure("é")
    assert repeated["tokens"] == 2
    assert repeated["quality"]["tokens"] == "exact_local_string"
    assert repeated["counter"] == counter.identity
    assert counter.calls == ["é"]


def test_changed_counter_identity_drops_cache_and_requires_new_session():
    counter = LocalCounter()
    session = CountSession(counter)
    session.measure("private")
    counter.identity["nested"]["profile"] = "another"
    with pytest.raises(LedgerError, match="identity changed"):
        session.measure("private")
    assert not session._cache
    assert session._cache_bytes == 0


@pytest.mark.parametrize("identity", [None, {}, "name", {"x": float("nan")}, {"x": "\ud800"}, {"x": "a" * 4096}])
def test_invalid_counter_identity_is_rejected(identity):
    counter = LocalCounter()
    counter.identity = identity
    with pytest.raises(LedgerError) as error:
        CountSession(counter)
    assert error.value.code == "invalid_input"


def test_counter_protocol_is_required():
    with pytest.raises(LedgerError, match="count"):
        CountSession(object())


def test_tokenization_work_is_bounded_by_utf8_bytes(monkeypatch):
    monkeypatch.setattr(token_count, "MAX_TOKEN_INPUT_BYTES", 4)
    counter = LocalCounter()
    session = CountSession(counter)
    assert session.measure("éé")["tokens"] == 4
    measured = session.measure("ééx")
    assert measured["chars"] == 3
    assert measured["bytes"] == 5
    assert measured["tokens"] is None
    assert counter.calls == ["éé"]
    assert "ééx" not in session._cache
    with pytest.raises(LedgerError) as error:
        session.fits("ééx", max_tokens=20, strict_tokens=True)
    assert error.value.code == "token_count_unavailable"


def test_prepared_tiktoken_adapter_counts_marker_text_literally(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("No network or factory calls are allowed")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(token_count.metadata, "version", forbidden)
    encoding = PreparedEncoding()
    counter = TiktokenCounter(encoding, library_version="local-fixture-1")
    value = "é <|endoftext|> <|fim_prefix|>"
    assert counter.count(value) == len(value.encode("utf-8"))
    measured = CountSession(counter).measure(value)
    assert measured["tokens"] == len(value.encode("utf-8"))
    assert encoding.calls == [value, value]
    assert measured["counter"] == {"library": "tiktoken", "version": "local-fixture-1",
                                   "encoding": "synthetic-bytes", "scope": "local_string",
                                   "special_tokens": "literal"}
    copy = counter.identity
    copy["encoding"] = "different"
    assert counter.identity["encoding"] == "synthetic-bytes"


def test_adapter_reads_only_local_version_metadata_when_needed(monkeypatch):
    looked_up = []
    def local_version(name):
        looked_up.append(name)
        return "local-fixture-2"
    monkeypatch.setattr(token_count.metadata, "version", local_version)
    assert TiktokenCounter(PreparedEncoding()).identity["version"] == "local-fixture-2"
    assert looked_up == ["tiktoken"]


def test_missing_optional_library_version_is_explicit(monkeypatch):
    def missing(name):
        raise token_count.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(token_count.metadata, "version", missing)
    with pytest.raises(LedgerError) as error:
        TiktokenCounter(PreparedEncoding())
    assert error.value.code == "token_count_unavailable"


@pytest.mark.parametrize("encoding,version", [(None, "1"), ("encoding-name", "1"), (PreparedEncoding(), ""),
                                               (PreparedEncoding(), True), (PreparedEncoding(), "\ud800")])
def test_adapter_requires_prepared_encoding_and_valid_version(encoding, version):
    with pytest.raises(LedgerError) as error:
        TiktokenCounter(encoding, library_version=version)
    assert error.value.code == "invalid_input"


def test_adapter_rejects_malformed_unicode_before_encoding_and_honors_size_guard(monkeypatch):
    encoding = PreparedEncoding()
    counter = TiktokenCounter(encoding, library_version="1")
    with pytest.raises(LedgerError) as error:
        counter.count("\ud800")
    assert error.value.code == "invalid_input"
    monkeypatch.setattr(token_count, "MAX_TOKEN_INPUT_BYTES", 1)
    with pytest.raises(LedgerError) as error:
        counter.count("é")
    assert error.value.code == "token_count_unavailable"
    assert encoding.calls == []


def test_optional_tiktoken_supports_absent_dependency_or_explicit_offline_encoding(monkeypatch):
    if importlib.util.find_spec("tiktoken") is None:
        session = CountSession()
        assert session.measure("é🔬 <|endoftext|>")["tokens"] is None
        assert session.measure("é🔬 <|endoftext|>")["quality"]["tokens"] == "unavailable"
        with pytest.raises(LedgerError) as error:
            session.fits("é🔬 <|endoftext|>", max_tokens=100, strict_tokens=True)
        assert error.value.code == "token_count_unavailable"
        return
    import tiktoken
    def forbidden(*_args, **_kwargs):
        raise AssertionError("No network or tokenizer factories are allowed")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(tiktoken, "get_encoding", forbidden)
    monkeypatch.setattr(tiktoken, "encoding_for_model", forbidden)
    encoding = tiktoken.Encoding(name="ledger-local-fixture", pat_str=r"(?s).",
                                 mergeable_ranks={bytes([byte]): byte for byte in range(256)},
                                 special_tokens={"<|endoftext|>": 256})
    session = CountSession(TiktokenCounter(encoding))
    value = "é🔬 <|endoftext|>"
    assert session.measure(value)["tokens"] == len(value.encode("utf-8"))
    assert session.measure(value)["counter"]["encoding"] == "ledger-local-fixture"
