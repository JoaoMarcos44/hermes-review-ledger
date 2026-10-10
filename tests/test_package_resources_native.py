"""Persistent real-Hermes regression for namespaced package resource loading.

Runs the genuine PluginManager, registry, and model-dispatch path against an
installed plugin copy under the native ``hermes_plugins.<slug>`` namespace.
A scoped import tripwire (clearing already-imported canonical modules and
installing a MetaPathFinder that rejects only top-level ``review_ledger``
lookups) ensures the baseline ``files("review_ledger")`` reader must fail,
while the actual namespaced readers and native host APIs remain real.

This seam does not prove a fresh uninstalled process; the source-level ``-I -S``
tests in ``test_package_resources.py`` prove true absence separately.
"""
from __future__ import annotations

from test_hermes_integration import _runtime, hermes_source  # noqa: F401


def test_native_namespaced_resources_under_real_plugin_manager(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, r'''
import hashlib
import importlib
import sys
from importlib.resources import files

install_fixture(HOME)
config = HOME / "config.yaml"
config.write_text(config.read_text() + "        context_enabled: true\n        compression_enabled: true\n", encoding="utf-8")

# Tripwire: clear canonical modules and reject top-level review_ledger lookups.
for name in list(sys.modules):
    if name == "review_ledger" or name.startswith("review_ledger."):
        del sys.modules[name]

class _RejectCanonicalFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "review_ledger" or fullname.startswith("review_ledger."):
            raise ModuleNotFoundError(
                f"No module named {fullname!r} (tripwire)", name=fullname)
        return None

sys.meta_path.insert(0, _RejectCanonicalFinder())

# Verify only the intended missing canonical lookup is accepted by the tripwire.
try:
    files("review_ledger")
except ModuleNotFoundError as exc:
    assert exc.name == "review_ledger", exc
else:
    raise AssertionError("tripwire failed: bare review_ledger was importable")

manager, loaded = load_fixture()
assert loaded.module.__name__.startswith("hermes_plugins."), loaded.module.__name__
run = open_fixture(loaded)
source_protocol = (ROOT / "review_ledger" / "resources" / "protocol.md").read_text(encoding="utf-8")

# Full prepare: protocol content is loaded from the namespaced package.
full = model_dispatch("ledger_context", {
    "repository": "Example/project", "run_id": run["id"],
    "action": "prepare", "query": "synthetic"
}, "session-a")
assert full["state"] == "ok", full
assert full["protocol"]["version"] == "3", full
assert full["protocol"]["content"] == source_protocol, full
assert full["protocol"]["sha256"] == hashlib.sha256(source_protocol.encode("utf-8")).hexdigest(), full

# Compact prepare: representation metadata is present.
compact = model_dispatch("ledger_context", {
    "repository": "Example/project", "run_id": run["id"],
    "action": "prepare", "query": "synthetic", "mode": "compact"
}, "session-a")
assert compact["state"] == "ok", compact
assert "representation" in compact, compact

# Reference prepare: representation metadata is present.
reference = model_dispatch("ledger_context", {
    "repository": "Example/project", "run_id": run["id"],
    "action": "prepare", "query": "synthetic", "mode": "reference"
}, "session-a")
assert reference["state"] == "ok", reference
assert "representation" in reference, reference

# Exact-manifest resume: protocol is preserved across resume.
resumed = model_dispatch("ledger_context", {
    "repository": "Example/project", "run_id": run["id"],
    "action": "resume", "manifest_id": full["manifest_id"]
}, "session-b")
assert resumed["state"] == "ok", resumed
assert resumed["protocol"] == full["protocol"], resumed

# Protocol detail in reference mode: legitimately metadata until full detail.
ref_detail = model_dispatch("ledger_context", {
    "repository": "Example/project", "run_id": run["id"],
    "action": "detail", "kind": "protocol", "mode": "reference"
}, "session-b")
assert ref_detail["state"] == "requires_more_context", ref_detail
assert "content" not in ref_detail, ref_detail
assert ref_detail["detail"]["kind"] == "protocol", ref_detail

# Protocol detail in full mode: full content is returned.
full_detail = model_dispatch("ledger_context", {
    "repository": "Example/project", "run_id": run["id"],
    "action": "detail", "kind": "protocol"
}, "session-b")
assert full_detail["state"] == "detail", full_detail
assert full_detail["content"] == full["protocol"], full_detail
assert full_detail["content_sha256"] == ref_detail["content_sha256"], full_detail
assert ref_detail["complete"] is False, ref_detail

# Sibling load_prompt bytes/hash equal package source.
plugin_tools = importlib.import_module(loaded.module.__name__ + ".review_ledger.critic_contract")
loaded_prompt = plugin_tools.load_prompt()
source_prompt = (ROOT / "review_ledger" / "prompts" / "critic_v1.md").read_text(encoding="utf-8")
assert loaded_prompt == source_prompt, "load_prompt bytes differ from package source"
assert hashlib.sha256(loaded_prompt.encode("utf-8")).hexdigest() == hashlib.sha256(source_prompt.encode("utf-8")).hexdigest()
''')
