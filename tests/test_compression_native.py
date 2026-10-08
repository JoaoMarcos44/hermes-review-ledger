"""Opt-in compression tests using the real pinned Hermes loader and dispatcher.

Only the external GitHub transport is replaced with synthetic records. Tests
reuse isolated profile/process fixtures and never contact an inference provider.
"""
from test_hermes_integration import _runtime, hermes_source  # noqa: F401


def test_native_compression_is_disabled_by_default_and_legacy_unchanged(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, r'''
install_fixture(HOME)
config = HOME / "config.yaml"
config.write_text(config.read_text() + "        context_enabled: true\n", encoding="utf-8")
manager, loaded = load_fixture()
run = open_fixture(loaded)
args = {"repository": "Example/project", "run_id": run["id"], "action": "prepare", "query": "synthetic"}
legacy = model_dispatch("ledger_context", args, "session-a")
assert legacy["state"] == "ok", legacy
assert "representation" not in legacy and "limits" not in legacy, legacy
for mode in ("compact", "reference"):
    result = model_dispatch("ledger_context", {**args, "mode": mode}, "session-a")
    assert result["state"] == "error" and result["error"]["code"] == "feature_disabled", result
''')


def test_native_final_string_enforces_operator_and_model_limits(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, r'''
install_fixture(HOME)
config = HOME / "config.yaml"
config.write_text(config.read_text() + "        context_enabled: true\n        compression_enabled: true\n        bundle_budget: 6500\n        bundle_byte_budget: 7000\n        bundle_token_budget: 5000\n", encoding="utf-8")
manager, loaded = load_fixture()
run = open_fixture(loaded)
written = model_dispatch("ledger_record", {"repository": "Example/project", "run_id": run["id"],
    "action": "observation", "generation": run["generation"], "request_key": "native-unicode",
    "data": {"kind": "test", "outcome": "skipped", "summary": "No execution took place.",
             "details": 'é🔬\\"\n' * 700 + " Required final exclusion.",
             "limitations": "Synthetic report only; not executed by the plugin."}}, "session-a")
assert written["state"] == "recorded", written
args = {"repository": "Example/project", "run_id": run["id"], "action": "prepare",
        "query": 'quote " Unicode é🔬', "mode": "compact", "max_chars": 64000,
        "max_bytes": 256000, "max_tokens": 12000}
raw = registry.dispatch("ledger_context", args, session_id="session-a")
assert isinstance(raw, str)
result = json.loads(raw)
assert result["state"] in {"ok", "requires_more_context"}, result
assert len(raw) <= 6500 and len(raw.encode("utf-8")) <= 7000, (len(raw), len(raw.encode("utf-8")))
assert result["limits"]["max_chars"] == 6500, result
assert result["limits"]["max_bytes"] == 7000, result
assert result["limits"]["max_tokens"] == 5000, result
assert result["limits"]["token_budget_verified"] is False, result
canonical_module = importlib.import_module(loaded.module.__name__ + ".review_ledger.models")
assert raw == canonical_module.canonical(result)
assert result["content_digest"] == canonical_module.digest({k: v for k, v in result.items() if k != "content_digest"})
strict = model_dispatch("ledger_context", {**args, "strict_tokens": True}, "session-a")
assert strict["state"] == "error" and strict["error"]["code"] == "token_count_unavailable", strict
smaller = registry.dispatch("ledger_context", {**args, "max_chars": 4000, "max_bytes": 4000}, session_id="session-a")
assert len(smaller) <= 4000 and len(smaller.encode("utf-8")) <= 4000
''')


def test_native_model_cannot_change_operator_controls(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, r'''
install_fixture(HOME)
config = HOME / "config.yaml"
config.write_text(config.read_text() + "        context_enabled: true\n        compression_enabled: true\n", encoding="utf-8")
manager, loaded = load_fixture()
run = open_fixture(loaded)
args = {"repository": "Example/project", "run_id": run["id"], "action": "prepare", "query": "", "mode": "compact"}
for name, value in (("compression_enabled", True), ("counter", "fake"), ("bundle_budget", 64000), ("token_budget_verified", True)):
    result = model_dispatch("ledger_context", {**args, name: value}, "session-a")
    assert result["state"] == "error" and result["error"]["code"] == "invalid_input", result
# Model dispatch deliberately normalizes schema types in Hermes. Validate raw
# plugin-boundary rejection through the genuine registry without that coercion.
for name, value in (("max_chars", True), ("max_bytes", -1), ("max_tokens", False), ("strict_tokens", "false")):
    result = dispatch("ledger_context", {**args, name: value}, "session-a")
    assert result["state"] == "error" and result["error"]["code"] == "invalid_input", result
''')


def test_native_resume_and_independent_detail_preserve_context(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, r'''
install_fixture(HOME)
config = HOME / "config.yaml"
config.write_text(config.read_text() + "        context_enabled: true\n        compression_enabled: true\n", encoding="utf-8")
manager, loaded = load_fixture()
run = open_fixture(loaded)
args = {"repository": "Example/project", "run_id": run["id"], "action": "prepare", "query": "synthetic", "mode": "compact"}
first = model_dispatch("ledger_context", args, "session-a")
assert first["state"] == "ok", first
resumed = model_dispatch("ledger_context", {"repository": "Example/project", "run_id": run["id"],
    "action": "resume", "manifest_id": first["manifest_id"]}, "new-host-session")
assert resumed["state"] == "ok" and "unknown" in resumed["residency"], resumed
assert resumed["protocol"] == first["protocol"], resumed
assert resumed["query"] == first["query"] and resumed["manifest_id"] != first["manifest_id"], resumed
assert "representation" in resumed, resumed
detail = model_dispatch("ledger_context", {"repository": "Example/project", "run_id": run["id"],
    "action": "detail", "kind": "snapshot_completeness", "record_id": run["id"], "mode": "compact"}, "new-host-session")
assert detail["state"] == "detail", detail
assert detail["scope"]["repository"] == "Example/project", detail
assert detail["scope"]["head_sha"] == "a" * 40 and detail["scope"]["base_sha"] == "b" * 40, detail
assert detail["content"]["snapshot"]["files_complete"] is True, detail
''')


def test_native_invalidated_observation_never_returns_as_projected_detail(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, r'''
install_fixture(HOME)
config = HOME / "config.yaml"
config.write_text(config.read_text() + "        context_enabled: true\n        compression_enabled: true\n", encoding="utf-8")
manager, loaded = load_fixture()
run = open_fixture(loaded)
obs = model_dispatch("ledger_record", {"repository": "Example/project", "run_id": run["id"],
    "action": "observation", "generation": run["generation"], "request_key": "record-original",
    "data": {"kind": "test", "outcome": "skipped", "summary": "Do not reuse this invalidated report.",
             "limitations": "Synthetic fixture only."}}, "session-a")
assert obs["state"] == "recorded", obs
invalidated = model_dispatch("ledger_record", {"repository": "Example/project", "run_id": run["id"],
    "action": "invalidate_observation", "generation": run["generation"], "request_key": "invalidate-original",
    "data": {"observation_id": obs["observation_id"], "reason": "Synthetic source correction"}}, "session-a")
assert invalidated["state"] == "invalidated", invalidated
for mode in ("compact", "reference"):
    detail = model_dispatch("ledger_context", {"repository": "Example/project", "run_id": run["id"],
        "action": "detail", "kind": "observation", "record_id": obs["observation_id"], "mode": mode}, "session-b")
    assert detail["state"] == "error" and detail["error"]["code"] == "context_revoked", detail
    assert "Do not reuse this invalidated report." not in json.dumps(detail), detail
''')
