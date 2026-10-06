"""Opt-in tests against real upstream Hermes, never a fake PluginContext.

Run with HERMES_SOURCE_DIR pointing at an official hermes-agent checkout.
An absent checkout is an explicit skip; a configured but broken runtime fails.
Every case runs in a subprocess with temporary profile directories and no
inherited credentials. No Hermes installation, user profile, or core is edited.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
    "ledger_open", "ledger_status", "ledger_run", "ledger_record",
    "ledger_recall", "ledger_lesson", "ledger_export",
}


@pytest.fixture
def hermes_source() -> Path:
    configured = os.environ.get("HERMES_SOURCE_DIR")
    if not configured:
        if os.environ.get("HERMES_INTEGRATION_REQUIRED") == "1":
            pytest.fail("Required real Hermes integration has no HERMES_SOURCE_DIR")
        pytest.skip("Real Hermes integration requires explicit HERMES_SOURCE_DIR")
    source = Path(configured).resolve()
    assert (source / "hermes_cli" / "plugins.py").is_file(), (
        f"Configured HERMES_SOURCE_DIR is not a Hermes checkout: {source}"
    )
    return source


def _runtime(hermes_source: Path, tmp_path: Path, body: str) -> str:
    """Use genuine loader/registries in an otherwise isolated Python process."""
    home = tmp_path / "profile-a"
    home.mkdir()
    bundled = tmp_path / "empty-bundled"
    bundled.mkdir()
    os_home = tmp_path / "os-home"
    os_home.mkdir()
    # An allowlist, rather than a credential-name heuristic, prevents provider,
    # gateway, and plugin credentials/settings leaking from a developer shell.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(os_home),
        "USERPROFILE": str(os_home),
        "HERMES_HOME": str(home),
        "HERMES_BUNDLED_PLUGINS": str(bundled),
        "HERMES_ENABLE_PROJECT_PLUGINS": "0",
        "HERMES_SAFE_MODE": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": str(tmp_path),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
        "APPDATA": str(os_home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(os_home / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(os_home / ".config"),
        "XDG_CACHE_HOME": str(os_home / ".cache"),
        "XDG_DATA_HOME": str(os_home / ".local" / "share"),
        "TZ": "UTC",
        "LANG": "C.UTF-8",
    }
    # Windows subprocess startup may require these ordinary OS locations.
    for key in ("SYSTEMROOT", "WINDIR"):
        if key in os.environ:
            env[key] = os.environ[key]
    preamble = f"""
import argparse
import contextlib
import io
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
sys.dont_write_bytecode = True
sys.path.insert(0, {str(hermes_source)!r})
ROOT = Path({str(ROOT)!r})
HOME = Path({str(home)!r})
EXPECTED_TOOLS = {EXPECTED_TOOLS!r}
assert Path.home().resolve() == Path({str(os_home)!r}).resolve()
assert Path(tempfile.gettempdir()).resolve() == Path({str(tmp_path)!r}).resolve()
assert sys.flags.utf8_mode == 1
from hermes_cli.plugins import PluginContext, PluginManager, get_plugin_manager
from tools.registry import registry
from hermes_constants import set_hermes_home_override, reset_hermes_home_override

def install_fixture(home):
    # The installer selects an existing profile explicitly and must leave its
    # operator-owned settings untouched. Only these temporary profiles are used.
    home.mkdir(parents=True, exist_ok=True)
    config = home / "config.yaml"
    config.write_text(
        "plugins:\\n  isolation: in_process\\n  enabled: [review-ledger]\\n"
        "  entries:\\n    review-ledger:\\n      settings:\\n"
        "        authorized_repositories: [Example/project]\\n", encoding="utf-8")
    original_config = config.read_bytes()
    sys.path.insert(0, str(ROOT))
    try:
        from review_ledger.installer import install
        result = install(home)
    finally:
        sys.path.pop(0)
    target = home / "plugins" / "review-ledger"
    assert isinstance(result, dict), result
    assert target.is_dir()
    assert config.read_bytes() == original_config
    assert not (home / "plugin-data").exists()
    return target

def load_fixture():
    manager = get_plugin_manager()
    assert isinstance(manager, PluginManager)
    manager.discover_and_load()
    loaded = manager._plugins["review-ledger"]
    assert loaded.enabled and loaded.error is None, loaded.error
    assert set(loaded.tools_registered) == EXPECTED_TOOLS
    return manager, loaded

def dispatch(name, args, session_id=None, **context):
    result = registry.dispatch(name, args, session_id=session_id, **context)
    assert isinstance(result, str), result
    return json.loads(result)

def model_dispatch(name, args, session_id):
    from model_tools import handle_function_call
    return json.loads(handle_function_call(
        name, args, task_id="terminal-runtime-id", session_id=session_id))

def offline_github(loaded):
    # Only the external HTTP transport is synthetic. Hermes discovery,
    # profile/config resolution, dispatcher, plugin handlers, and storage are real.
    github = importlib.import_module(loaded.module.__name__ + ".review_ledger.github")
    metadata = {{
        "number": 42, "title": "Synthetic integration fixture",
        "url": "https://api.github.com/repos/Example/project/pulls/42",
        "html_url": "https://github.com/Example/project/pull/42",
        "changed_files": 1, "head": {{"sha": "a" * 40}},
        "base": {{"sha": "b" * 40, "repo": {{
            "id": 123456, "node_id": "R_fixture123456", "name": "project",
            "full_name": "Example/project", "owner": {{"login": "Example"}},
            "url": "https://api.github.com/repos/Example/project",
            "html_url": "https://github.com/Example/project",
        }}}},
    }}
    files = [{{"filename": "src/example.py", "sha": "c" * 40,
              "status": "modified", "additions": 1, "deletions": 1, "changes": 2,
              "patch": "@@ -1 +1 @@\\n-old\\n+new"}}]
    calls = []
    def transport(*, url, **kwargs):
        assert url in {{metadata["url"], metadata["url"] + "/files?per_page=100&page=1"}}, url
        calls.append(url)
        payload = files if "/files?" in url else metadata
        return github.GitHubResponse(200, {{}}, json.dumps(payload).encode(), url)
    github._default_transport = transport
    os.environ["REVIEW_LEDGER_GITHUB_TOKEN"] = "synthetic-integration-token"
    return calls

def open_fixture(loaded, session="session-a", key="open-1"):
    calls = offline_github(loaded)
    result = model_dispatch("ledger_open", {{
        "repository": "Example/project", "pull_number": 42, "request_key": key,
    }}, session)
    assert result.get("state") in {{"created", "reused"}}, result
    assert len(calls) == 3, calls
    return result["run"]
"""
    result = subprocess.run(
        # -I ignores PYTHON* environment settings, so request UTF-8 explicitly.
        [sys.executable, "-I", "-X", "utf8", "-c",
         textwrap.dedent(preamble) + "\n" + textwrap.dedent(body)],
        cwd=tmp_path,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (
        f"Real Hermes integration failed ({result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout


def test_actual_plugin_doctor(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, """
from hermes_cli.plugin_dev import doctor_plugin
report = doctor_plugin(install_fixture(HOME))
assert report.ok, report.format_text()
assert set(report.registered_tools) == EXPECTED_TOOLS, report.format_text()
assert not report.findings, report.format_text()
print(report.format_text())
""")


def test_real_discovery_and_skill_serving(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, """
target = install_fixture(HOME)
manager, loaded = load_fixture()
# Loading the plugin must not initialize durable ledger state.
assert not (HOME / "plugin-data").exists()
from tools.skills_tool import skills_list, skill_view
listed = json.loads(skills_list())
assert listed["success"] is True, listed
qualified = loaded.manifest.name + ":review-ledger"
assert qualified in {row["name"] for row in listed["skills"]}, listed
served = json.loads(skill_view(qualified, task_id="integration", preprocess=False))
assert served["success"] is True, served
assert "ledger_open" in served["content"], served
assert "ledger_record" in served["content"], served
assert Path(served["skill_dir"]).is_relative_to(target)
assert not (HOME / "skills" / "review-ledger").exists()
assert not (HOME / "plugin-data").exists()
""")


def test_real_registry_rejects_absent_trusted_session(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, """
install_fixture(HOME)
manager, loaded = load_fixture()
for name in sorted(EXPECTED_TOOLS):
    result = dispatch(name, {}, task_id="runtime-id-is-not-owner")
    assert result["error"]["code"] == "trusted_session_required", (name, result)
ctx = PluginContext(loaded.manifest, manager)
# The public plugin-to-plugin bridge does not synthesize session identity.
bridged = json.loads(ctx.dispatch_tool("ledger_status", {}, task_id="runtime-id"))
assert bridged["error"]["code"] == "trusted_session_required", bridged
assert not (HOME / "plugin-data").exists()
""")


def test_real_cli_command_attachment(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, """
install_fixture(HOME)
manager, loaded = load_fixture()
from hermes_cli.main import _attach_plugin_cli_command
registered = list(manager._cli_commands.values())
assert len(registered) == 1, registered
parser = argparse.ArgumentParser(prog="hermes")
subparsers = parser.add_subparsers(dest="command", required=True)
for entry in registered:
    _attach_plugin_cli_command(subparsers, entry)
output = io.StringIO()
with contextlib.redirect_stdout(output):
    try:
        parser.parse_args([registered[0]["name"], "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("CLI help did not follow argparse's help route")
assert registered[0]["name"] in output.getvalue(), output.getvalue()
assert not (HOME / "plugin-data").exists()
""")


def test_real_host_profile_state_property_is_dynamic(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, """
install_fixture(HOME)
manager, loaded = load_fixture()
ctx = PluginContext(loaded.manifest, manager)
plugin_tools = importlib.import_module(loaded.module.__name__ + ".review_ledger.tools")
first = ctx.state.data_dir
assert first.parent == HOME / "plugin-data"
assert plugin_tools.ledger_for_context(ctx).store.data_dir == first
other_home = HOME.parent / "profile-b"
token = set_hermes_home_override(other_home)
try:
    assert ctx.state.data_dir.parent == other_home / "plugin-data"
    assert ctx.state.data_dir != first
    assert plugin_tools.ledger_for_context(ctx).store.data_dir == ctx.state.data_dir
finally:
    reset_hermes_home_override(token)
assert ctx.state.data_dir == first
assert not first.exists()
assert not (other_home / "plugin-data").exists()
""")


def test_real_model_dispatch_binds_session_and_blocks_model_owner_fields(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, """
install_fixture(HOME)
manager, loaded = load_fixture()
run = open_fixture(loaded)
assert run["owner_session"] == "session-a", run
assert run["can_write"] is True
query = {"repository": "Example/project", "run_id": run["id"]}
other = model_dispatch("ledger_status", query, "session-b")
assert other["state"] == "ok", other
assert other["run"]["can_write"] is False
spoof = model_dispatch("ledger_status", {**query, "session_id": "session-a"}, "session-b")
assert spoof["error"]["code"] == "invalid_input", spoof
write = {**query, "generation": run["generation"], "action": "observation",
         "request_key": "record-1", "data": {"kind": "inspection", "outcome": "inspection",
         "summary": "Synthetic runtime check", "limitations": "Offline fixture only"}}
rejected = model_dispatch("ledger_record", write, "session-b")
assert rejected["error"]["code"] == "ownership_conflict", rejected
accepted = model_dispatch("ledger_record", write, "session-a")
assert accepted.get("observation_id"), accepted
assert model_dispatch("ledger_status", query, "session-a")["observations"][0]["provenance"] == "agent_reported"
""")


def test_real_profiles_keep_same_repository_separate(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, """
install_fixture(HOME)
manager_a, loaded_a = load_fixture()
run_a = open_fixture(loaded_a)
home_b = HOME.parent / "profile-b"
install_fixture(home_b)
token = set_hermes_home_override(home_b)
try:
    manager_b, loaded_b = load_fixture()
    assert manager_b is not manager_a
    assert loaded_b.module is not loaded_a.module
    run_b = open_fixture(loaded_b)
    assert run_b["id"] != run_a["id"]
    cross = model_dispatch("ledger_status", {
        "repository": "Example/project", "run_id": run_a["id"]}, "session-a")
    assert cross["error"]["code"] == "scope_not_found", cross
    assert len(list((home_b / "plugin-data").rglob("review-ledger.sqlite3"))) == 1
finally:
    reset_hermes_home_override(token)
original = model_dispatch("ledger_status", {
    "repository": "Example/project", "run_id": run_a["id"]}, "session-a")
assert original["run"]["id"] == run_a["id"]
assert len(list((HOME / "plugin-data").rglob("review-ledger.sqlite3"))) == 1
""")


def test_real_cli_transfer_invalidates_previous_owner_generation(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, """
install_fixture(HOME)
manager, loaded = load_fixture()
run = open_fixture(loaded)
from hermes_cli.main import _attach_plugin_cli_command
parser = argparse.ArgumentParser(prog="hermes")
subparsers = parser.add_subparsers(dest="command", required=True)
for entry in manager._cli_commands.values():
    _attach_plugin_cli_command(subparsers, entry)
args = parser.parse_args(["review-ledger", "transfer", "Example/project", run["id"],
    "--generation", str(run["generation"]), "--session", "session-b",
    "--reason", "Synthetic explicit operator transfer", "--request-key", "transfer-1"])
out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = args.func(args)
result = json.loads(out.getvalue())
assert code == 0 and result["state"] == "transferred", result
assert result["run"]["generation"] == run["generation"] + 1
assert result["run"]["owner_session"] == "session-b"
old = model_dispatch("ledger_run", {
    "repository": "Example/project", "run_id": run["id"], "generation": run["generation"],
    "action": "complete", "request_key": "old-owner-complete"}, "session-a")
assert old["error"]["code"] == "ownership_conflict", old
new = model_dispatch("ledger_status", {
    "repository": "Example/project", "run_id": run["id"]}, "session-b")
assert new["run"]["can_write"] is True
""")


def test_real_cli_approval_and_revocation_are_separate_from_model_tools(hermes_source, tmp_path):
    _runtime(hermes_source, tmp_path, """
install_fixture(HOME)
manager, loaded = load_fixture()
run = open_fixture(loaded)
base = {"repository": "Example/project", "run_id": run["id"], "generation": run["generation"]}
recorded = model_dispatch("ledger_record", {
    **base, "action": "observation", "request_key": "source-1",
    "data": {"kind": "inspection", "outcome": "inspection", "summary": "Fixture inspected",
             "limitations": "Synthetic observation, not verification of external software"}}, "session-a")
assert recorded["state"] == "recorded", recorded
proposal = model_dispatch("ledger_lesson", {
    **base, "action": "propose", "request_key": "proposal-1", "data": {
        "question": "Does the conditional fixture still apply?",
        "conditions": ["Only for this synthetic integration fixture"],
        "exclusions": ["Any production review"],
        "verification": "Inspect fresh source evidence before applying the strategy",
        "sources": [{"observation_id": recorded["observation_id"], "relation": "supports"}],
    }}, "session-a")
assert proposal["state"] == "candidate", proposal
query = {"repository": "Example/project", "run_id": run["id"]}
assert model_dispatch("ledger_recall", query, "session-a")["lessons"] == []
attempt = model_dispatch("ledger_lesson", {
    **base, "action": "approve", "request_key": "model-approval",
    "data": {"version_id": proposal["version_id"]}}, "session-a")
assert attempt["error"]["code"] == "invalid_input", attempt
from hermes_cli.main import _attach_plugin_cli_command
parser = argparse.ArgumentParser(prog="hermes")
subs = parser.add_subparsers(dest="command", required=True)
for entry in manager._cli_commands.values():
    _attach_plugin_cli_command(subs, entry)
def operator(action, key):
    args = parser.parse_args(["review-ledger", action, "Example/project", proposal["version_id"],
        "--reason", "Explicit synthetic operator decision", "--request-key", key])
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = args.func(args)
    result = json.loads(output.getvalue())
    assert code == 0, result
    return result
assert operator("approve", "operator-approve")["state"] == "active"
lessons = model_dispatch("ledger_recall", query, "session-b")["lessons"]
assert len(lessons) == 1 and lessons[0]["id"] == proposal["version_id"], lessons
assert operator("suspend", "operator-suspend")["state"] == "suspended"
assert model_dispatch("ledger_recall", query, "session-b")["lessons"] == []
""")
