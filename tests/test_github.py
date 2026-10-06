"""Deterministic synthetic fixtures; these tests never contact GitHub."""

from copy import deepcopy
import io
import json
import os
import ssl
import unittest
from unittest.mock import patch
import urllib.error

from review_ledger.github import (
    GitHubClient, GitHubError, GitHubResponse, _NoRedirect, _default_transport,
)


PR_URL = "https://api.github.com/repos/Example/project/pulls/42"
FILES_URL = PR_URL + "/files?per_page=100&page="
TOKEN = "synthetic-test-token"


def metadata(count=1):
    return {
        "number": 42,
        "title": "Synthetic bookkeeping fixture",
        "url": PR_URL,
        "html_url": "https://github.com/Example/project/pull/42",
        "changed_files": count,
        "head": {"sha": "a" * 40},
        "base": {
            "sha": "b" * 40,
            "repo": {
                "id": 123456,
                "node_id": "R_fixture123456",
                "name": "project",
                "full_name": "Example/project",
                "owner": {"login": "Example"},
                "url": "https://api.github.com/repos/Example/project",
                "html_url": "https://github.com/Example/project",
            },
        },
    }


def file_entry(name="src/example.py", **changes):
    result = {
        "filename": name,
        "sha": "c" * 40,
        "status": "modified",
        "additions": 1,
        "deletions": 1,
        "changes": 2,
        "patch": "@@ -1 +1 @@\n-old\n+new",
    }
    result.update(changes)
    return result


def response(payload, *, url=PR_URL, status=200, headers=None):
    return GitHubResponse(status, headers or {}, json.dumps(payload).encode(), url)


def files_response(items, *, page=1, next_page=None, headers=None):
    all_headers = dict(headers or {})
    if next_page is not None:
        all_headers["Link"] = f'<{FILES_URL}{next_page}>; rel="next"'
    return response(items, url=FILES_URL + str(page), headers=all_headers)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("Unexpected extra HTTP request")
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class GitHubClientTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {"REVIEW_LEDGER_GITHUB_TOKEN": TOKEN}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def client(self, responses=(), **kwargs):
        self.transport = FakeTransport(responses)
        self.sleeps = []
        return GitHubClient(allowed_repositories=["Example/project"], transport=self.transport,
                            sleep=self.sleeps.append, **kwargs)

    def successful(self, items=None, **kwargs):
        items = [file_entry()] if items is None else items
        meta = metadata(len(items))
        return self.client([response(meta), files_response(items), response(meta)], **kwargs)

    def fetch(self, client):
        return client.fetch_snapshot("Example", "project", 42)

    def assert_error(self, code, client):
        with self.assertRaises(GitHubError) as context:
            self.fetch(client)
        self.assertEqual(context.exception.code, code)
        self.assertNotIn(TOKEN, str(context.exception))
        return context.exception

    def test_complete_snapshot_and_exact_request_contract(self):
        snapshot = self.fetch(self.successful())
        self.assertEqual(snapshot.repository_id, 123456)
        self.assertEqual(snapshot.repository_node_id, "R_fixture123456")
        self.assertEqual(snapshot.repository_full_name, "Example/project")
        self.assertEqual(snapshot.head_sha, "a" * 40)
        self.assertEqual(snapshot.base_sha, "b" * 40)
        self.assertEqual(snapshot.comparison, "github_pr")
        self.assertEqual(snapshot.number, 42)
        self.assertTrue(snapshot.files_complete)
        self.assertTrue(snapshot.patches_complete)
        self.assertEqual(snapshot.omitted_files, 0)
        self.assertEqual(snapshot.files[0]["patch_status"], "available")
        self.assertIsInstance(snapshot.as_dict()["files"], list)
        self.assertEqual([call["url"] for call in self.transport.calls], [PR_URL, FILES_URL + "1", PR_URL])
        for call in self.transport.calls:
            self.assertEqual(call["headers"]["Authorization"], "Bearer " + TOKEN)
            self.assertEqual(call["headers"]["Accept-Encoding"], "identity")
            self.assertEqual(call["timeout"], 10)
            self.assertEqual(call["max_response_bytes"], 5 * 1024 * 1024)

    def test_two_pages_are_fetched_in_order_before_metadata_recheck(self):
        first = [file_entry(f"src/{number}.py") for number in range(100)]
        last = [file_entry("src/100.py")]
        meta = metadata(101)
        client = self.client([
            response(meta), files_response(first, next_page=2),
            files_response(last, page=2), response(meta),
        ])
        snapshot = self.fetch(client)
        self.assertEqual(len(snapshot.files), 101)
        self.assertTrue(snapshot.files_complete)
        self.assertEqual(self.transport.calls[2]["url"], FILES_URL + "2")
        self.assertEqual(self.transport.calls[-1]["url"], PR_URL)

    def test_empty_pr_is_valid_when_metadata_and_files_agree(self):
        result = self.fetch(self.successful([]))
        self.assertEqual(result.files, ())
        self.assertTrue(result.files_complete)

    def test_missing_patch_is_explicitly_unavailable(self):
        entry = file_entry()
        del entry["patch"]
        result = self.fetch(self.successful([entry]))
        self.assertEqual(result.files[0]["patch_status"], "unavailable")
        self.assertEqual(result.files[0]["patch_unavailable_reason"], "github_missing_patch")
        self.assertIsNone(result.files[0]["patch"])
        self.assertTrue(result.files_complete)
        self.assertFalse(result.patches_complete)

    def test_empty_patch_is_explicitly_unavailable(self):
        result = self.fetch(self.successful([file_entry(patch="")]))
        self.assertEqual(result.files[0]["patch_unavailable_reason"], "github_empty_patch")
        self.assertFalse(result.patches_complete)

    def test_patch_truncation_preserves_original_length(self):
        result = self.fetch(self.successful(max_patch_chars=10))
        entry = result.files[0]
        self.assertEqual(len(entry["patch"]), 10)
        self.assertEqual(entry["patch_status"], "truncated")
        self.assertEqual(entry["patch_original_chars"], len(file_entry()["patch"]))
        self.assertIn("max_patch_chars", result.truncation_reasons)
        self.assertFalse(result.patches_complete)

    def test_upstream_cut_off_patch_is_marked_truncated(self):
        result = self.fetch(self.successful([file_entry(patch="@@ -1 +1 @@\n-old")]))
        self.assertEqual(result.files[0]["patch_status"], "truncated")
        self.assertIn("github_incomplete_patch", result.truncation_reasons)

    def test_patch_change_count_mismatch_is_not_complete(self):
        result = self.fetch(self.successful([file_entry(additions=2, changes=3)]))
        self.assertEqual(result.files[0]["patch_status"], "truncated")

    def test_multihunk_patch_with_no_newline_marker_is_complete(self):
        entry = file_entry(additions=2, deletions=2, changes=4,
                           patch="@@ -1 +1 @@\n-old\n+new\n@@ -4 +4 @@\n-old2\n+new2\n\\ No newline at end of file\n")
        result = self.fetch(self.successful([entry]))
        self.assertEqual(result.files[0]["patch_status"], "available")

    def test_unicode_source_separators_do_not_split_patch_records(self):
        entry = file_entry(patch="@@ -1 +1 @@\n-old\n+new\u2028still same source line")
        self.assertTrue(self.fetch(self.successful([entry])).patches_complete)

    def test_rename_preserves_both_paths(self):
        result = self.fetch(self.successful([file_entry("new.py", status="renamed", previous_filename="old.py")]))
        self.assertEqual(result.files[0]["previous_filename"], "old.py")
        self.assertEqual(result.files[0]["status"], "renamed")

    def test_file_limit_records_omissions_and_still_rechecks_refs(self):
        meta = metadata(3)
        client = self.client([
            response(meta), files_response([file_entry(f"{number}.py") for number in range(3)]),
            response(meta),
        ], max_files=2)
        result = self.fetch(client)
        self.assertEqual(len(result.files), 2)
        self.assertEqual(result.omitted_files, 1)
        self.assertFalse(result.files_complete)
        self.assertFalse(result.patches_complete)
        self.assertIn("max_files", result.truncation_reasons)
        self.assertEqual(self.transport.calls[-1]["url"], PR_URL)

    def test_page_limit_records_omissions(self):
        meta = metadata(101)
        client = self.client([
            response(meta), files_response([file_entry(f"{n}.py") for n in range(100)], next_page=2),
            response(meta),
        ], max_pages=1)
        result = self.fetch(client)
        self.assertEqual(result.omitted_files, 1)
        self.assertEqual(result.truncation_reasons, ("max_pages",))
        self.assertFalse(result.files_complete)

    def test_github_3000_file_limit_is_explicit(self):
        meta = metadata(3001)
        replies = [response(meta)]
        for page in range(1, 31):
            items = [file_entry(f"{(page - 1) * 100 + n}.py", patch=None) for n in range(100)]
            replies.append(files_response(items, page=page, next_page=page + 1 if page < 30 else None))
        replies.append(response(meta))
        result = self.fetch(self.client(replies))
        self.assertEqual(len(result.files), 3000)
        self.assertEqual(result.omitted_files, 1)
        self.assertIn("github_file_limit", result.truncation_reasons)
        self.assertEqual(len(self.transport.calls), 32)

    def test_missing_configured_token_does_not_discover_other_credentials(self):
        with patch.dict(os.environ, {"GH_TOKEN": "ignored", "GITHUB_TOKEN": "ignored"}, clear=True):
            self.assert_error("missing_token", self.client())
        self.assertEqual(self.transport.calls, [])

    def test_only_explicit_token_environment_variable_is_used(self):
        with patch.dict(os.environ, {"REVIEW_TOKEN": "configured-token"}, clear=True):
            self.fetch(self.successful(token_env="REVIEW_TOKEN"))
        self.assertEqual(self.transport.calls[0]["headers"]["Authorization"], "Bearer configured-token")

    def test_token_header_injection_is_refused_before_transport(self):
        with patch.dict(os.environ, {"REVIEW_LEDGER_GITHUB_TOKEN": "bad\r\nInjected: value"}):
            self.assert_error("invalid_token", self.client())
        self.assertEqual(self.transport.calls, [])

    def test_unauthorized_repository_is_refused_before_transport(self):
        client = self.client()
        with self.assertRaises(GitHubError) as context:
            client.fetch_snapshot("Other", "private", 42)
        self.assertEqual(context.exception.code, "unauthorized_repository")
        self.assertEqual(self.transport.calls, [])

    def test_repository_and_pr_arguments_cannot_be_urls_or_paths(self):
        for owner, repo, number in [
            ("https://example.com", "project", 42), ("Example", "../project", 42),
            ("Example", "project?redirect=evil", 42), ("Example", "..", 42),
            ("Example", "project", "42/files"), ("Example", "project", True),
            ("Example", "project", 0), ("Example", "project", -1),
        ]:
            with self.subTest(owner=owner, repo=repo, number=number):
                client = self.client()
                with self.assertRaises(GitHubError):
                    client.fetch_snapshot(owner, repo, number)
                self.assertEqual(self.transport.calls, [])

    def test_allowlist_matching_is_case_insensitive(self):
        client = self.successful()
        client.allowed_repositories = frozenset({"example/project"})
        self.assertEqual(self.fetch(client).repository_id, 123456)

    def test_bad_configuration_is_rejected(self):
        for settings in [
            {"timeout": 31}, {"timeout": 0}, {"timeout": float("inf")}, {"timeout": True},
            {"max_pages": 31}, {"max_pages": 0}, {"max_files": 3001},
            {"max_response_bytes": 5 * 1024 * 1024 + 1}, {"max_patch_chars": 100001},
            {"max_retries": 3}, {"token_env": "BAD=NAME"},
        ]:
            with self.subTest(settings=settings), self.assertRaises(GitHubError):
                self.client(**settings)
        for allowed in ["Example/project", ["https://github.com/Example/project"], None]:
            with self.subTest(allowed=allowed), self.assertRaises(GitHubError):
                GitHubClient(allowed_repositories=allowed)

    def test_timeout_retries_are_bounded_and_explicit(self):
        client = self.client([TimeoutError("secret-ish upstream message")] * 3, timeout=2)
        self.assert_error("timeout", client)
        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual(self.sleeps, [0.1, 0.2])
        self.assertTrue(all(call["timeout"] == 2 for call in self.transport.calls))

    def test_transient_network_failure_can_recover(self):
        meta = metadata()
        client = self.client([urllib.error.URLError("temporary"), response(meta), files_response([file_entry()]), response(meta)])
        self.assertTrue(self.fetch(client).files_complete)
        self.assertEqual(self.sleeps, [0.1])

    def test_network_failure_never_returns_empty_success(self):
        self.assert_error("network_error", self.client([OSError("offline")] * 3))

    def test_server_error_retries_only_bounded_gets(self):
        self.assert_error("http_error", self.client([response({}, status=503)] * 3))
        self.assertEqual(len(self.transport.calls), 3)
        self.assertEqual(self.sleeps, [0.1, 0.2])

    def test_auth_rate_and_not_found_errors_are_explicit_without_retry(self):
        for status, headers, code in [
            (401, {}, "authentication_failed"),
            (403, {}, "access_denied"),
            (403, {"X-RateLimit-Remaining": "0"}, "rate_limited"),
            (403, {"Retry-After": "120"}, "rate_limited"),
            (429, {}, "rate_limited"),
            (404, {}, "not_found"),
        ]:
            with self.subTest(status=status, headers=headers):
                error = self.assert_error(code, self.client([response({"message": TOKEN}, status=status, headers=headers)]))
                self.assertEqual(error.status, status)
                self.assertEqual(len(self.transport.calls), 1)
                self.assertEqual(self.sleeps, [])

    def test_later_pagination_auth_error_is_not_partial_success(self):
        meta = metadata(101)
        client = self.client([
            response(meta), files_response([file_entry(f"{n}.py") for n in range(100)], next_page=2),
            response({}, status=401, url=FILES_URL + "2"),
        ])
        self.assert_error("authentication_failed", client)

    def test_certificate_errors_are_not_retried(self):
        for error in [ssl.SSLCertVerificationError("bad certificate"),
                      urllib.error.URLError(ssl.SSLCertVerificationError("bad certificate"))]:
            with self.subTest(error=type(error)):
                self.assert_error("tls_error", self.client([error]))
                self.assertEqual(len(self.transport.calls), 1)

    def test_http_redirects_are_refused_without_following_location(self):
        for status in [301, 302, 303, 307, 308]:
            with self.subTest(status=status):
                client = self.client([response({}, status=status, headers={"Location": "https://elsewhere.example/"})])
                self.assert_error("redirect_refused", client)
                self.assertEqual(len(self.transport.calls), 1)

    def test_urllib_redirect_handler_raises_before_redirect_request(self):
        with self.assertRaises(GitHubError) as context:
            _NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://example.com/")
        self.assertEqual(context.exception.code, "redirect_refused")

    def test_changed_response_host_is_refused_even_with_injected_transport(self):
        self.assert_error("untrusted_url", self.client([response(metadata(), url="https://example.com/")]))

    def test_untrusted_pagination_cannot_receive_a_token(self):
        bad_links = [
            "http://api.github.com/repos/Example/project/pulls/42/files?per_page=100&page=2",
            "https://api.github.com.evil.example/repos/Example/project/pulls/42/files?per_page=100&page=2",
            "https://api.github.com@evil.example/repos/Example/project/pulls/42/files?per_page=100&page=2",
            "https://api.github.com:443/repos/Example/project/pulls/42/files?per_page=100&page=2",
            "https://api.github.com/repos/Other/private/pulls/42/files?per_page=100&page=2",
            "https://api.github.com/repos/Example/project/pulls/42/comments?per_page=100&page=2",
            FILES_URL + "2&token=bad", FILES_URL + "2#fragment", FILES_URL + "%32",
        ]
        for link in bad_links:
            with self.subTest(link=link):
                client = self.client([response(metadata(2)), files_response([file_entry()], headers={"Link": f'<{link}>; rel="next"'})])
                self.assert_error("untrusted_url", client)
                self.assertEqual(len(self.transport.calls), 2)
                self.assertTrue(all(call["url"].startswith("https://api.github.com/repos/Example/project/") for call in self.transport.calls))

    def test_untrusted_unused_last_link_is_also_refused(self):
        client = self.client([
            response(metadata()), files_response([file_entry()], headers={"Link": '<https://elsewhere.example/>; rel="last"'}),
        ])
        self.assert_error("untrusted_url", client)

    def test_looping_or_skipped_pagination_is_refused(self):
        for next_page in [1, 3]:
            with self.subTest(next_page=next_page):
                client = self.client([response(metadata(2)), files_response([file_entry()], next_page=next_page)])
                self.assert_error("invalid_pagination", client)

    def test_malformed_sha_is_rejected(self):
        for field in ["head", "base"]:
            for sha in ["a" * 39, "g" * 40, "a" * 41, "a" * 40 + "\n", None]:
                with self.subTest(field=field, sha=sha):
                    meta = metadata()
                    meta[field]["sha"] = sha
                    self.assert_error("invalid_sha", self.client([response(meta)]))

    def test_metadata_identity_mismatches_are_rejected(self):
        mutations = [
            lambda m: m.update(number=43),
            lambda m: m.update(html_url="https://example.com/"),
            lambda m: m.update(url="https://api.github.com/repos/Other/private/pulls/42"),
            lambda m: m["base"]["repo"].update(full_name="Other/project"),
            lambda m: m["base"]["repo"].update(name="other"),
            lambda m: m["base"]["repo"].update(owner={"login": "Other"}),
        ]
        for mutate in mutations:
            meta = metadata()
            mutate(meta)
            with self.subTest(meta=meta):
                self.assert_error("identity_mismatch", self.client([response(meta)]))

    def test_numeric_repository_id_is_required(self):
        for value in [None, "123456", 0, -1, True]:
            with self.subTest(value=value):
                meta = metadata()
                meta["base"]["repo"]["id"] = value
                self.assert_error("invalid_response", self.client([response(meta)]))

    def test_changed_refs_or_identity_reject_snapshot_after_pagination(self):
        mutations = [
            lambda m: m["head"].update(sha="d" * 40),
            lambda m: m["base"].update(sha="e" * 40),
            lambda m: m["base"]["repo"].update(id=999),
            lambda m: m["base"]["repo"].update(node_id="R_replaced"),
            lambda m: m.update(changed_files=2),
        ]
        for mutate in mutations:
            initial = metadata()
            final = deepcopy(initial)
            mutate(final)
            with self.subTest(final=final):
                self.assert_error("snapshot_changed", self.client([response(initial), files_response([file_entry()]), response(final)]))
                self.assertEqual(self.transport.calls[-1]["url"], PR_URL)

    def test_final_metadata_failure_is_not_success(self):
        client = self.client([response(metadata()), files_response([file_entry()]), response({}, status=429)])
        self.assert_error("rate_limited", client)

    def test_unexpected_empty_or_early_page_is_rejected(self):
        for items in [[], [file_entry()]]:
            with self.subTest(items=items):
                self.assert_error("inconsistent_snapshot", self.client([response(metadata(2)), files_response(items)]))
        self.assert_error("inconsistent_snapshot", self.client([response(metadata()), files_response([])], max_pages=1))

    def test_duplicate_filenames_are_rejected(self):
        self.assert_error("inconsistent_snapshot", self.client([response(metadata(2)), files_response([file_entry(), file_entry()])]))

    def test_excess_files_are_not_hidden_by_local_file_limit(self):
        self.assert_error("inconsistent_snapshot", self.client([response(metadata()), files_response([file_entry(), file_entry("other.py")])], max_files=1))

    def test_malformed_json_or_file_array_is_rejected(self):
        self.assert_error("invalid_response", self.client([GitHubResponse(200, {}, b"{", PR_URL)]))
        self.assert_error("invalid_response", self.client([response(metadata()), response({}, url=FILES_URL + "1")]))
        self.assert_error("invalid_response", self.client([response(metadata(101)), files_response([file_entry(f"{n}.py") for n in range(101)])]))

    def test_response_size_limit_is_enforced_for_test_and_production_transport(self):
        self.assert_error("response_too_large", self.client([response(metadata())], max_response_bytes=20))

        class FakeHTTPResponse(io.BytesIO):
            headers = {"Content-Length": "999"}
            def getcode(self):
                return 200
            def geturl(self):
                return PR_URL

        class FakeOpener:
            def open(self, request, timeout):
                return FakeHTTPResponse(b"large payload")

        with patch("urllib.request.build_opener", return_value=FakeOpener()):
            with self.assertRaises(GitHubError) as context:
                _default_transport(url=PR_URL, headers={}, timeout=1, max_response_bytes=10)
            self.assertEqual(context.exception.code, "response_too_large")

    def test_production_transport_uses_get_default_tls_no_proxy_and_no_redirect(self):
        captured = {}

        class FakeHTTPResponse(io.BytesIO):
            headers = {}
            def getcode(self):
                return 200
            def geturl(self):
                return PR_URL

        class FakeOpener:
            def open(self, request, timeout):
                captured["method"] = request.get_method()
                captured["url"] = request.full_url
                captured["timeout"] = timeout
                return FakeHTTPResponse(b"{}")

        def opener(*handlers):
            captured["handlers"] = handlers
            return FakeOpener()

        with patch("urllib.request.build_opener", side_effect=opener):
            result = _default_transport(url=PR_URL, headers={}, timeout=3, max_response_bytes=100)
        self.assertEqual(result.body, b"{}")
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], PR_URL)
        self.assertEqual(captured["timeout"], 3)
        proxy, redirect, https = captured["handlers"]
        self.assertEqual(proxy.proxies, {})
        self.assertIsInstance(redirect, _NoRedirect)
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)

    def test_no_write_methods_are_exposed(self):
        public_methods = [name for name in dir(GitHubClient) if not name.startswith("_") and callable(getattr(GitHubClient, name))]
        self.assertEqual(public_methods, ["fetch_pr_snapshot", "fetch_snapshot"])


if __name__ == "__main__":
    unittest.main()
