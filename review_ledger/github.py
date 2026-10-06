"""Bounded, read-only GitHub pull-request snapshots using the Python stdlib.

Only ``GET /repos/{owner}/{repo}/pulls/{number}`` and its ``/files`` endpoint
are supported. Repository names must be explicitly authorized. Credentials
come only from the configured environment variable; no git, CLI, netrc,
credential helper, proxy environment, or local checkout is consulted.

API contract: https://docs.github.com/en/rest/pulls/pulls
GitHub caps the files endpoint at 3,000 files. Missing or incomplete patch
text and omitted files are represented explicitly, never as complete evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import http.client
import json
import math
import os
import re
import ssl
import time
from typing import Any, Callable, Iterable, Mapping
import urllib.error
import urllib.parse
import urllib.request


_API_ORIGIN = "https://api.github.com"
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_PAGE = re.compile(r"[1-9][0-9]{0,9}\Z")
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)\Z")
_STATUSES = frozenset({"added", "removed", "modified", "renamed", "copied", "changed", "unchanged"})
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_MAX_NUMBER = 2_147_483_647


class GitHubError(RuntimeError):
    """An explicit failure. Messages never include credentials or response bodies."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class GitHubSnapshot:
    """A GitHub PR comparison bracketed by matching HEAD and base metadata.

    ``files_complete`` concerns file enumeration; ``patches_complete`` also
    requires every enumerated patch to be available and not detectably cut off.
    GitHub does not provide an atomic snapshot endpoint: the final metadata
    check detects observed ref changes, but cannot rule out an ABA ref change.
    """

    repository_id: int
    repository_node_id: str | None
    repository_full_name: str
    number: int
    title: str
    url: str
    head_sha: str
    base_sha: str
    files: tuple[dict[str, Any], ...]
    total_files: int
    files_complete: bool
    patches_complete: bool
    omitted_files: int
    truncation_reasons: tuple[str, ...]
    comparison: str = "github_pr"

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["files"] = list(result["files"])
        result["truncation_reasons"] = list(result["truncation_reasons"])
        return result


@dataclass(frozen=True)
class GitHubResponse:
    """Small transport boundary, also usable by deterministic offline fixtures."""

    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str


Transport = Callable[..., GitHubResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GitHubError("redirect_refused", "GitHub redirects are not permitted.", status=code)


def _bounded_int(value: Any, name: str, maximum: int, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise GitHubError("invalid_config", f"{name} must be an integer from {minimum} to {maximum}.")
    return value


def _repository_name(owner: Any, repo: Any) -> str:
    if not isinstance(owner, str) or not _OWNER.fullmatch(owner):
        raise GitHubError("invalid_repository", "Repository owner must be a GitHub account name.")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo) or repo in {".", ".."}:
        raise GitHubError("invalid_repository", "Repository name must be a single GitHub path component.")
    return f"{owner}/{repo}"


def _strict_api_url(url: Any, expected_path: str, *, files: bool) -> int | None:
    """Validate a URL without ever requesting a server-supplied URL."""
    if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) >= 127 for char in url):
        raise GitHubError("untrusted_url", "GitHub returned an invalid API URL.")
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        raise GitHubError("untrusted_url", "GitHub returned an invalid API URL.") from None
    if (parsed.scheme != "https" or parsed.netloc != "api.github.com"
            or parsed.path != expected_path or parsed.fragment):
        raise GitHubError("untrusted_url", "Only the exact authorized GitHub API endpoint is permitted.")
    if not files:
        if parsed.query:
            raise GitHubError("untrusted_url", "Unexpected query on the GitHub metadata endpoint.")
        return None
    try:
        pairs = urllib.parse.parse_qsl(parsed.query, strict_parsing=True, max_num_fields=2)
    except ValueError:
        raise GitHubError("untrusted_url", "Invalid GitHub files pagination query.") from None
    if len(pairs) != 2 or {key for key, _ in pairs} != {"page", "per_page"} or "%" in parsed.query:
        raise GitHubError("untrusted_url", "Unexpected GitHub files pagination parameters.")
    params = dict(pairs)
    page = params["page"]
    if params["per_page"] != "100" or not _PAGE.fullmatch(page) or int(page) > _MAX_NUMBER:
        raise GitHubError("untrusted_url", "Invalid GitHub files pagination bounds.")
    return int(page)


def _headers(headers: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        raise GitHubError("invalid_response", "GitHub returned invalid response headers.")
    result = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise GitHubError("invalid_response", "GitHub returned invalid response headers.")
        lowered = key.lower()
        if lowered in result:
            raise GitHubError("invalid_response", "GitHub returned duplicate response headers.")
        result[lowered] = value
    return result


def _default_transport(*, url: str, headers: Mapping[str, str], timeout: float,
                       max_response_bytes: int) -> GitHubResponse:
    # No configurable origin, SSL context, redirect handling, or proxy lookup.
    # The caller has already validated the exact constructed endpoint.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        status = response.getcode()
        response_headers = dict(response.headers.items())
        final_url = response.geturl()
        if final_url != url:
            raise GitHubError("redirect_refused", "GitHub response URL changed unexpectedly.")
        if status != 200:
            # Error bodies are neither necessary nor copied into errors/logs.
            return GitHubResponse(status, response_headers, b"", final_url)
        normalized = _headers(response_headers)
        length = normalized.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdecimal():
                raise GitHubError("invalid_response", "GitHub returned an invalid response length.")
            if len(length) > 12 or int(length) > max_response_bytes:
                raise GitHubError("response_too_large", "GitHub response exceeded the configured byte limit.")
        body = response.read(max_response_bytes + 1)
        if len(body) > max_response_bytes:
            raise GitHubError("response_too_large", "GitHub response exceeded the configured byte limit.")
        return GitHubResponse(status, response_headers, body, final_url)


def _integer_field(data: Mapping[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_NUMBER:
        raise GitHubError("invalid_response", f"GitHub returned an invalid {key} value.")
    return value


def _file_name(value: Any) -> str:
    if (not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value
            or value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise GitHubError("invalid_response", "GitHub returned an invalid repository-relative filename.")
    return value


def _patch_is_complete(patch: str, additions: int, deletions: int) -> bool:
    """Detect cut-off hunks/count mismatches without treating patch text as code."""
    old_left = new_left = 0
    seen_hunk = False
    added = removed = 0
    # Only LF is a patch record separator. str.splitlines() also splits valid
    # source characters such as vertical tabs and Unicode line separators.
    lines = patch.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for line in lines:
        match = _HUNK.fullmatch(line)
        if match:
            if old_left or new_left:
                return False
            # A bounded response may still contain arbitrarily long decimal text.
            counts = (match.group(2) or "1", match.group(4) or "1")
            if any(len(count) > 10 for count in counts):
                return False
            old_left, new_left = map(int, counts)
            seen_hunk = True
        elif line == "\\ No newline at end of file" and seen_hunk:
            continue
        elif seen_hunk and line.startswith("+"):
            new_left -= 1
            added += 1
        elif seen_hunk and line.startswith("-"):
            old_left -= 1
            removed += 1
        elif seen_hunk and line.startswith(" "):
            old_left -= 1
            new_left -= 1
        else:
            return False
        if old_left < 0 or new_left < 0:
            return False
    return seen_hunk and old_left == new_left == 0 and added == additions and removed == deletions


class GitHubClient:
    """Read-only, explicitly authorized PR snapshot client.

    ``transport`` is solely a dependency-injection seam for offline tests. The
    normal transport always verifies TLS and refuses redirects. Injected
    responses still receive the same host, identity, status, and size checks.
    ``max_retries`` applies only to idempotent GET transport/server failures;
    authorization, rate limits, malformed data and TLS errors are not retried.
    """

    def __init__(self, *, allowed_repositories: Iterable[str],
                 token_env: str = "REVIEW_LEDGER_GITHUB_TOKEN", timeout: float = 10.0,
                 max_pages: int = 30, max_files: int = 3000,
                 max_response_bytes: int = _MAX_RESPONSE_BYTES, max_patch_chars: int = 100_000,
                 max_retries: int = 2, transport: Transport | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        if isinstance(allowed_repositories, (str, bytes)):
            raise GitHubError("invalid_config", "allowed_repositories must be a collection of owner/name entries.")
        allowed = set()
        try:
            for name in allowed_repositories:
                if not isinstance(name, str) or name.count("/") != 1:
                    raise GitHubError("invalid_config", "Each allowed repository must be owner/name.")
                allowed.add(_repository_name(*name.split("/")).casefold())
        except TypeError:
            raise GitHubError("invalid_config", "allowed_repositories must be an explicit collection.") from None
        if not isinstance(token_env, str) or not _ENV.fullmatch(token_env):
            raise GitHubError("invalid_config", "token_env must name one environment variable.")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 30):
            raise GitHubError("invalid_config", "timeout must be greater than zero and at most 30 seconds.")
        self.allowed_repositories = frozenset(allowed)
        self.token_env = token_env
        self.timeout = float(timeout)
        self.max_pages = _bounded_int(max_pages, "max_pages", 30)
        self.max_files = _bounded_int(max_files, "max_files", 3000)
        self.max_response_bytes = _bounded_int(max_response_bytes, "max_response_bytes", _MAX_RESPONSE_BYTES)
        self.max_patch_chars = _bounded_int(max_patch_chars, "max_patch_chars", 100_000)
        self.max_retries = _bounded_int(max_retries, "max_retries", 2, minimum=0)
        self._transport = transport if transport is not None else _default_transport
        self._sleep = sleep

    def _request(self, path: str, *, token: str, page: int | None = None) -> tuple[Any, dict[str, str]]:
        url = _API_ORIGIN + path
        if page is not None:
            url += f"?per_page=100&page={page}"
        _strict_api_url(url, path, files=page is not None)
        headers = {
            "Accept": "application/vnd.github+json",
            "Accept-Encoding": "identity",
            "Authorization": f"Bearer {token}",
            "User-Agent": "hermes-review-ledger/1",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        for attempt in range(self.max_retries + 1):
            try:
                response = self._transport(url=url, headers=headers, timeout=self.timeout,
                                           max_response_bytes=self.max_response_bytes)
            except GitHubError:
                raise
            except ssl.SSLError:
                raise GitHubError("tls_error", "GitHub TLS validation or negotiation failed.") from None
            except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                if isinstance(getattr(error, "reason", None), ssl.SSLError):
                    raise GitHubError("tls_error", "GitHub TLS validation or negotiation failed.") from None
                if attempt < self.max_retries:
                    self._sleep(0.1 * (2 ** attempt))
                    continue
                code = "timeout" if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError) else "network_error"
                raise GitHubError(code, "GitHub request failed after bounded transport retries.") from None
            if not isinstance(response, GitHubResponse):
                raise GitHubError("invalid_response", "GitHub transport returned an invalid response.")
            _strict_api_url(response.url, path, files=page is not None)
            if response.url != url:
                raise GitHubError("redirect_refused", "GitHub response URL changed unexpectedly.")
            if isinstance(response.status, bool) or not isinstance(response.status, int):
                raise GitHubError("invalid_response", "GitHub returned an invalid HTTP status.")
            response_headers = _headers(response.headers)
            status = response.status
            if 300 <= status < 400:
                raise GitHubError("redirect_refused", "GitHub redirects are not permitted.", status=status)
            if status == 401:
                raise GitHubError("authentication_failed", "GitHub rejected the configured token.", status=status)
            if status == 429 or (status == 403 and (
                    response_headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response_headers)):
                raise GitHubError("rate_limited", "GitHub rate limit prevents fetching this snapshot.", status=status)
            if status == 403:
                raise GitHubError("access_denied", "GitHub denied access or applied a secondary rate limit.", status=status)
            if status == 404:
                raise GitHubError("not_found", "GitHub pull request was not found or is not accessible.", status=status)
            if status in {500, 502, 503, 504} and attempt < self.max_retries:
                self._sleep(0.1 * (2 ** attempt))
                continue
            if status != 200:
                raise GitHubError("http_error", f"GitHub request failed with HTTP {status}.", status=status)
            if not isinstance(response.body, bytes):
                raise GitHubError("invalid_response", "GitHub returned an invalid response body.")
            if len(response.body) > self.max_response_bytes:
                raise GitHubError("response_too_large", "GitHub response exceeded the configured byte limit.")
            if response_headers.get("content-encoding", "identity").lower() != "identity":
                raise GitHubError("invalid_response", "Compressed GitHub responses are not accepted.")
            try:
                payload = json.loads(response.body.decode("utf-8"))
            except (ValueError, RecursionError):
                raise GitHubError("invalid_response", "GitHub returned malformed JSON.") from None
            return payload, response_headers
        raise GitHubError("network_error", "GitHub request failed.")  # Defensive, unreachable.

    def _metadata(self, payload: Any, owner: str, repo: str, number: int) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise GitHubError("invalid_response", "GitHub pull-request metadata must be an object.")
        if type(payload.get("number")) is not int or payload["number"] != number:
            raise GitHubError("identity_mismatch", "GitHub returned metadata for a different pull request.")
        base, head = payload.get("base"), payload.get("head")
        if not isinstance(base, dict) or not isinstance(head, dict) or not isinstance(base.get("repo"), dict):
            raise GitHubError("invalid_response", "GitHub metadata is missing its base repository or refs.")
        repository = base["repo"]
        full_name = repository.get("full_name")
        expected_name = f"{owner}/{repo}"
        if not isinstance(full_name, str) or full_name.casefold() != expected_name.casefold():
            raise GitHubError("identity_mismatch", "GitHub returned metadata for a different repository.")
        _repository_name(*full_name.split("/"))
        repository_id = repository.get("id")
        if type(repository_id) is not int or not 0 < repository_id < 2 ** 63:
            raise GitHubError("invalid_response", "GitHub metadata lacks a stable numeric repository ID.")
        node_id = repository.get("node_id")
        if node_id is not None and (not isinstance(node_id, str) or not node_id or len(node_id) > 256
                                    or any(ord(char) <= 32 or ord(char) >= 127 for char in node_id)):
            raise GitHubError("invalid_response", "GitHub returned an invalid repository node ID.")
        canonical_owner, canonical_repo = full_name.split("/")
        if "name" in repository and repository["name"] != canonical_repo:
            raise GitHubError("identity_mismatch", "GitHub repository name and full name disagree.")
        if "owner" in repository:
            actual_owner = repository["owner"]
            if not isinstance(actual_owner, dict) or actual_owner.get("login") != canonical_owner:
                raise GitHubError("identity_mismatch", "GitHub repository owner and full name disagree.")
        for obj, key, expected in (
            (repository, "url", f"{_API_ORIGIN}/repos/{full_name}"),
            (payload, "url", f"{_API_ORIGIN}/repos/{full_name}/pulls/{number}"),
            (repository, "html_url", f"https://github.com/{full_name}"),
        ):
            if key in obj and obj[key] != expected:
                raise GitHubError("identity_mismatch", "GitHub returned an inconsistent repository or pull-request URL.")
        expected_url = f"https://github.com/{full_name}/pull/{number}"
        if payload.get("html_url") != expected_url:
            raise GitHubError("identity_mismatch", "GitHub returned an inconsistent pull-request web URL.")
        for label, ref in (("head", head), ("base", base)):
            if not isinstance(ref.get("sha"), str) or not _SHA.fullmatch(ref["sha"]):
                raise GitHubError("invalid_sha", f"GitHub returned an invalid full {label} SHA.")
        title = payload.get("title")
        if not isinstance(title, str) or len(title) > 10_000:
            raise GitHubError("invalid_response", "GitHub returned an invalid pull-request title.")
        return {
            "repository_id": repository_id, "repository_node_id": node_id,
            "repository_full_name": full_name, "number": number, "title": title,
            "url": expected_url, "head_sha": head["sha"].lower(), "base_sha": base["sha"].lower(),
            "total_files": _integer_field(payload, "changed_files"),
        }

    def _next_page(self, headers: Mapping[str, str], path: str, current: int) -> int | None:
        link = headers.get("link")
        if link is None:
            return None
        if not link or len(link) > 16_384:
            raise GitHubError("invalid_pagination", "GitHub returned an invalid pagination header.")
        relations = {}
        for entry in link.split(","):
            match = re.fullmatch(r'\s*<([^<>]+)>\s*;\s*rel="(next|prev|first|last)"\s*', entry)
            if not match or match.group(2) in relations:
                raise GitHubError("invalid_pagination", "GitHub returned malformed pagination links.")
            relations[match.group(2)] = _strict_api_url(match.group(1), path, files=True)
        next_page = relations.get("next")
        if next_page is not None and next_page != current + 1:
            raise GitHubError("invalid_pagination", "GitHub pagination did not advance by exactly one page.")
        return next_page

    def _file(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise GitHubError("invalid_response", "GitHub file entries must be objects.")
        filename = _file_name(payload.get("filename"))
        status = payload.get("status")
        if not isinstance(status, str) or status not in _STATUSES:
            raise GitHubError("invalid_response", "GitHub returned an unsupported file status.")
        previous = payload.get("previous_filename")
        if previous is not None:
            previous = _file_name(previous)
        if status == "renamed" and previous is None:
            raise GitHubError("invalid_response", "GitHub renamed file lacks its previous filename.")
        sha = payload.get("sha")
        if sha is not None and (not isinstance(sha, str) or not _SHA.fullmatch(sha)):
            raise GitHubError("invalid_sha", "GitHub returned an invalid full file SHA.")
        additions = _integer_field(payload, "additions")
        deletions = _integer_field(payload, "deletions")
        changes = _integer_field(payload, "changes")
        patch = payload.get("patch")
        if patch is not None and not isinstance(patch, str):
            raise GitHubError("invalid_response", "GitHub returned a non-text patch.")
        original_chars = None if patch is None else len(patch)
        unavailable_reason = truncation_reason = None
        if not patch:
            patch_status = "unavailable"
            unavailable_reason = "github_missing_patch" if patch is None else "github_empty_patch"
            patch = None
        elif len(patch) > self.max_patch_chars:
            patch_status = "truncated"
            truncation_reason = "max_patch_chars"
            patch = patch[:self.max_patch_chars]
        elif not _patch_is_complete(patch, additions, deletions):
            patch_status = "truncated"
            truncation_reason = "github_incomplete_patch"
        else:
            patch_status = "available"
        return {
            "filename": filename, "previous_filename": previous, "status": status,
            "sha": sha.lower() if sha is not None else None,
            "additions": additions, "deletions": deletions, "changes": changes,
            "patch": patch, "patch_status": patch_status,
            "patch_unavailable_reason": unavailable_reason,
            "patch_truncation_reason": truncation_reason,
            "patch_original_chars": original_chars,
        }

    def fetch_snapshot(self, owner: str, repo: str, number: int) -> GitHubSnapshot:
        """Fetch only one authorized PR; reject a changing comparison or identity."""
        full_name = _repository_name(owner, repo)
        if full_name.casefold() not in self.allowed_repositories:
            raise GitHubError("unauthorized_repository", "Repository is outside the configured allowlist.")
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= _MAX_NUMBER:
            raise GitHubError("invalid_pull_number", "Pull-request number must be a positive integer.")
        token = os.environ.get(self.token_env)
        if not token:
            raise GitHubError("missing_token", "The configured GitHub token environment variable is empty or unset.")
        if len(token) > 16_384 or any(ord(char) <= 32 or ord(char) >= 127 for char in token):
            raise GitHubError("invalid_token", "The configured GitHub token is not a valid header credential.")
        path = f"/repos/{full_name}/pulls/{number}"
        first_payload, _ = self._request(path, token=token)
        metadata = self._metadata(first_payload, owner, repo, number)
        files_path = path + "/files"
        files = []
        seen = set()
        reasons = []
        page = 1
        while True:
            payload, headers = self._request(files_path, token=token, page=page)
            if not isinstance(payload, list) or len(payload) > 100:
                raise GitHubError("invalid_response", "GitHub files response must contain at most 100 entries.")
            next_page = self._next_page(headers, files_path, page)
            if len(files) + len(payload) > metadata["total_files"]:
                raise GitHubError("inconsistent_snapshot", "GitHub file count exceeds its pull-request metadata.")
            if not payload and len(files) < metadata["total_files"]:
                raise GitHubError("inconsistent_snapshot", "GitHub returned an empty page before the reported file count.")
            for item in payload:
                if len(files) >= self.max_files:
                    break
                descriptor = self._file(item)
                if descriptor["filename"] in seen:
                    raise GitHubError("inconsistent_snapshot", "GitHub returned duplicate filenames across pages.")
                seen.add(descriptor["filename"])
                files.append(descriptor)
            if len(files) > metadata["total_files"]:
                raise GitHubError("inconsistent_snapshot", "GitHub file count exceeds its pull-request metadata.")
            remaining = metadata["total_files"] - len(files)
            if not remaining:
                if next_page is not None:
                    raise GitHubError("inconsistent_snapshot", "GitHub pagination conflicts with its file count.")
                break
            if len(files) >= self.max_files:
                reasons.append("max_files")
                if len(files) == 3000 and metadata["total_files"] > 3000:
                    reasons.append("github_file_limit")
                break
            if next_page is None:
                raise GitHubError("inconsistent_snapshot", "GitHub file enumeration ended before the reported file count.")
            if page >= self.max_pages:
                reasons.append("max_pages")
                break
            page = next_page
        final_payload, _ = self._request(path, token=token)
        final_metadata = self._metadata(final_payload, owner, repo, number)
        for key in ("repository_id", "repository_node_id", "repository_full_name", "number", "head_sha", "base_sha", "total_files"):
            if metadata[key] != final_metadata[key]:
                raise GitHubError("snapshot_changed", "GitHub comparison or repository identity changed during pagination; retry the snapshot.")
        omitted = metadata["total_files"] - len(files)
        for descriptor in files:
            if descriptor["patch_status"] == "truncated" and descriptor["patch_truncation_reason"] not in reasons:
                reasons.append(descriptor["patch_truncation_reason"])
        return GitHubSnapshot(
            **metadata, files=tuple(files), files_complete=omitted == 0,
            patches_complete=omitted == 0 and all(item["patch_status"] == "available" for item in files),
            omitted_files=omitted, truncation_reasons=tuple(reasons),
        )

    fetch_pr_snapshot = fetch_snapshot


__all__ = ["GitHubClient", "GitHubError", "GitHubResponse", "GitHubSnapshot"]
