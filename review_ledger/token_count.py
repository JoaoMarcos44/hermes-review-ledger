"""Offline counts for one exact local string, never a model-input estimate.

The default is standard-library-only: Python Unicode code points and strict
UTF-8 bytes are exact, while tokens are explicitly unavailable. Nothing here
loads a tokenizer, downloads assets, resolves a model, or reads credentials.
Injected counters must be trusted, local, deterministic objects with ``count``
and a JSON-object ``identity``. Each CountSession belongs to one caller/profile;
its bounded, in-memory cache is never shared or persisted.

The native host has no prepared-counter bridge by default. Only a trusted core
caller can inject one; a model-requested token cap remains explicitly unverified
without it, and strict token enforcement fails with token_count_unavailable.
The real tiktoken implementation is untested when that optional local dependency
is absent; synthetic adapters still verify this module's offline contract.
"""
from __future__ import annotations

from collections import OrderedDict
from importlib import metadata
import json

from .models import LedgerError, canonical, integer, text as validated_text


MAX_TOKEN_INPUT_BYTES = 4 * 1024 * 1024
MAX_CACHE_ITEMS = 4096
MAX_CACHE_BYTES = 16 * 1024 * 1024


def _utf8(value):
    if not isinstance(value, str):
        raise LedgerError("invalid_input", "Counted text must be a string")
    try:
        return value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        raise LedgerError("invalid_input", "Counted text must contain valid Unicode scalar values; surrogates are invalid") from None


def _identity(counter):
    try:
        value = counter.identity
        if not isinstance(value, dict) or not value:
            raise ValueError
        serialized = canonical(value)
        if len(_utf8(serialized)) > 4096:
            raise ValueError
        return json.loads(serialized)
    except Exception:
        raise LedgerError("invalid_input", "Token counter identity must be a nonempty JSON object of at most 4096 UTF-8 bytes") from None


class TiktokenCounter:
    """Wrap an explicitly supplied, already prepared local tiktoken encoding.

    This adapter never imports tiktoken or calls a tokenizer/model factory.
    When omitted, library_version is read from installed distribution metadata
    only. Supplying it explicitly is useful for isolated local integrations;
    the caller is responsible for providing the actual library version.
    encode_ordinary counts special-token-looking strings literally. Counts
    exclude chat framing, tool definitions, hidden prompts, and provider costs.
    """

    def __init__(self, encoding, *, library_version=None):
        if not callable(getattr(encoding, "encode_ordinary", None)):
            raise LedgerError("invalid_input", "A prepared local encoding with encode_ordinary is required")
        name = validated_text(getattr(encoding, "name", None), "encoding name", 200)
        if library_version is None:
            try:
                library_version = metadata.version("tiktoken")
            except metadata.PackageNotFoundError:
                raise LedgerError("token_count_unavailable", "Local tiktoken library version is unavailable") from None
        library_version = validated_text(library_version, "tokenizer library version", 200)
        self._encoding = encoding
        self._identity = {"library": "tiktoken", "version": library_version,
                          "encoding": name, "scope": "local_string",
                          "special_tokens": "literal"}
        # Validate Unicode and JSON even for an adapter used without a session.
        _identity(self)

    @property
    def identity(self):
        return dict(self._identity)

    def count(self, value):
        encoded = _utf8(value)
        if len(encoded) > MAX_TOKEN_INPUT_BYTES:
            raise LedgerError("token_count_unavailable", "Local string exceeds the token-count size limit")
        try:
            tokens = self._encoding.encode_ordinary(value)
            if not isinstance(tokens, (list, tuple)) or any(type(token) is not int or token < 0 for token in tokens):
                raise ValueError
            return len(tokens)
        except Exception:
            raise LedgerError("token_count_unavailable", "Prepared local tokenizer could not count this string") from None


class CountSession:
    """Count exact local strings with an optional local token counter.

    Cache bounds limit entries and retained input UTF-8 bytes (not Python
    object overhead). Zero disables caching. Oversized strings are measured
    but not retained, and strings above MAX_TOKEN_INPUT_BYTES are not tokenized.
    Returned dictionaries are fresh and safe to mutate. Counter identity must
    remain stable; create a new session for another encoding/profile.

    Tokens are None when no counter exists, the local counter fails/returns
    None, or the size guard prevents tokenization. No heuristic is substituted.
    Failed/unavailable injected counts are not cached, so a later local retry
    can recover. A supplied invalid count is treated as unavailable as well.
    """

    def __init__(self, counter=None, *, max_cache_items=128, max_cache_bytes=1024 * 1024):
        self._max_cache_items = integer(max_cache_items, "max_cache_items", 0, MAX_CACHE_ITEMS)
        self._max_cache_bytes = integer(max_cache_bytes, "max_cache_bytes", 0, MAX_CACHE_BYTES)
        if counter is not None and not callable(getattr(counter, "count", None)):
            raise LedgerError("invalid_input", "Token counter must provide count(text)")
        self._counter = counter
        self._identity = _identity(counter) if counter is not None else None
        self._cache = OrderedDict()
        self._cache_bytes = 0

    def measure(self, value):
        encoded = _utf8(value)
        chars, byte_count = len(value), len(encoded)
        if self._counter is not None and _identity(self._counter) != self._identity:
            self._cache.clear()
            self._cache_bytes = 0
            raise LedgerError("invalid_input", "Token counter identity changed; create a new CountSession")
        cached = self._cache.get(value)
        if cached is not None:
            self._cache.move_to_end(value)
            tokens = cached[2]
        else:
            tokens = None
            if self._counter is not None and byte_count <= MAX_TOKEN_INPUT_BYTES:
                try:
                    counted = self._counter.count(value)
                    if type(counted) is int and counted >= 0:
                        tokens = counted
                except Exception:
                    # Preserve usable byte/character counts without exposing
                    # exception text that may contain private input content.
                    pass
            if (self._max_cache_items and self._max_cache_bytes
                    and byte_count <= self._max_cache_bytes
                    and (self._counter is None or tokens is not None)):
                while self._cache and (len(self._cache) >= self._max_cache_items
                                       or self._cache_bytes + byte_count > self._max_cache_bytes):
                    _, removed = self._cache.popitem(last=False)
                    self._cache_bytes -= removed[1]
                self._cache[value] = (chars, byte_count, tokens)
                self._cache_bytes += byte_count
        return {"chars": chars, "bytes": byte_count, "tokens": tokens,
                "counter": json.loads(canonical(self._identity)),
                "quality": {"chars": "exact", "bytes": "exact",
                            "tokens": "exact_local_string" if tokens is not None else "unavailable"}}

    def fits(self, value, max_chars=None, max_bytes=None, max_tokens=None, strict_tokens=False):
        """Check every requested inclusive cap; None means no cap.

        Caps must be nonnegative integers, never booleans. An unavailable
        requested token count returns False, or raises token_count_unavailable
        in strict mode. For an intentional character/byte-only fallback, pass
        max_tokens=None and retain measure()'s explicit unavailable metadata.
        strict_tokens does not require tokens unless max_tokens is supplied.
        """
        for name, cap in (("max_chars", max_chars), ("max_bytes", max_bytes), ("max_tokens", max_tokens)):
            if cap is not None and (type(cap) is not int or cap < 0):
                raise LedgerError("invalid_input", f"{name} must be a nonnegative integer or None")
        if type(strict_tokens) is not bool:
            raise LedgerError("invalid_input", "strict_tokens must be a boolean")
        measured = self.measure(value)
        if max_tokens is not None and measured["tokens"] is None:
            if strict_tokens:
                raise LedgerError("token_count_unavailable", "An exact local token count is required for max_tokens")
            return False
        return all(cap is None or measured[key] <= cap for key, cap in
                   (("chars", max_chars), ("bytes", max_bytes), ("tokens", max_tokens)))
