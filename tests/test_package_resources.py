"""Package-relative resource lookup under the native directory-plugin namespaces.

The Hermes directory-plugin loader imports this plugin below
``hermes_plugins.<slug>[__home_<digest>]`` (see the host loader), never as a bare
top-level ``review_ledger``. A resource reader that resolves ``files("review_ledger")``
therefore fails in a fresh process with no canonical package, and silently reads
another copy's files when a conflicting canonical package is importable.

These tests load the real production modules under synthetic native-style
namespaces in a subprocess that has no ``site`` (``-S``), which is the condition
that removes every ambient canonical ``review_ledger``. They exercise the public
``protocol()`` and ``load_prompt()`` functions, never ``files()`` directly, so a
green result is evidence about the shipped reader, not about a private helper.
No provider, credential, network call, or live profile is touched.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Runs in an isolated interpreter (``-I -S``). ``sys.argv`` is
# [repo_root, scratch_dir, case].
PROBE = r'''
import importlib
import importlib.util
import shutil
import sys
import types
from pathlib import Path

from importlib.resources import files

ROOT = Path(sys.argv[1])
SCRATCH = Path(sys.argv[2])
CASE = sys.argv[3]

# The host loader installs a synthetic ``hermes_plugins`` namespace parent and
# sets ``__package__``/``__path__`` on each directory package it imports.
if "hermes_plugins" not in sys.modules:
    parent = types.ModuleType("hermes_plugins")
    parent.__path__ = []
    parent.__package__ = "hermes_plugins"
    sys.modules["hermes_plugins"] = parent


def copy_package(dest: Path, marker: str) -> None:
    """One authoritative source snapshot with marker-tagged resources."""
    (dest / "review_ledger" / "resources").mkdir(parents=True, exist_ok=True)
    (dest / "review_ledger" / "prompts").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "__init__.py", dest / "__init__.py")
    for name in ("__init__.py", "models.py", "protocol.py", "critic_contract.py"):
        shutil.copy(ROOT / "review_ledger" / name, dest / "review_ledger" / name)
    for relative in ("resources/protocol.md", "prompts/critic_v1.md"):
        source = (ROOT / "review_ledger" / relative).read_text(encoding="utf-8")
        (dest / "review_ledger" / relative).write_text(
            source + "\nMARKER:" + marker + "\n", encoding="utf-8")


def load_namespace(module_name: str, root: Path):
    """Mirror the loader: register the package without running root side effects."""
    spec = importlib.util.spec_from_file_location(
        module_name, root / "__init__.py", submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(root)]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def protocol_content(package: str) -> str:
    return importlib.import_module(package + ".protocol").protocol()["content"]


def prompt_content(package: str) -> str:
    return importlib.import_module(package + ".critic_contract").load_prompt()


def assert_bare_lookup_fails() -> None:
    try:
        files("review_ledger").joinpath("resources/protocol.md").read_text(encoding="utf-8")
    except ModuleNotFoundError:
        return
    raise AssertionError("bare review_ledger was importable; isolation was not established")


if CASE == "no_canonical":
    assert_bare_lookup_fails()
    plugin = SCRATCH / "plugin"
    copy_package(plugin, "PLUGIN")
    load_namespace("hermes_plugins.review_ledger", plugin)
    package = "hermes_plugins.review_ledger.review_ledger"
    assert protocol_content(package).endswith("MARKER:PLUGIN\n")
    assert prompt_content(package).endswith("MARKER:PLUGIN\n")

elif CASE == "conflicting_canonical":
    canonical = SCRATCH / "canonical"
    copy_package(canonical, "CANONICAL")
    sys.path.insert(0, str(canonical))
    found = importlib.util.find_spec("review_ledger")
    assert found is not None and Path(found.origin) == canonical / "review_ledger" / "__init__.py", found
    plugin = SCRATCH / "plugin"
    copy_package(plugin, "PLUGIN")
    load_namespace("hermes_plugins.review_ledger", plugin)
    package = "hermes_plugins.review_ledger.review_ledger"
    protocol = protocol_content(package)
    prompt = prompt_content(package)
    assert protocol.endswith("MARKER:PLUGIN\n") and "MARKER:CANONICAL" not in protocol
    assert prompt.endswith("MARKER:PLUGIN\n") and "MARKER:CANONICAL" not in prompt

elif CASE == "two_copies":
    first = SCRATCH / "first"
    second = SCRATCH / "second"
    copy_package(first, "A")
    copy_package(second, "B")
    load_namespace("hermes_plugins.review_ledger", first)
    load_namespace("hermes_plugins.review_ledger__home_deadbeef", second)
    a = "hermes_plugins.review_ledger.review_ledger"
    b = "hermes_plugins.review_ledger__home_deadbeef.review_ledger"

    def check_a() -> None:
        assert protocol_content(a).endswith("MARKER:A\n")
        assert prompt_content(a).endswith("MARKER:A\n")

    def check_b() -> None:
        assert protocol_content(b).endswith("MARKER:B\n")
        assert prompt_content(b).endswith("MARKER:B\n")

    check_a()
    check_b()
    check_a()  # A -> B -> A: the first copy must not adopt the second's resources.

else:
    raise SystemExit("unknown case: " + CASE)
'''


def _run_case(case: str, tmp_path: Path) -> None:
    # An allowlist keeps provider, gateway, and plugin credentials/settings out of
    # the child. ``-I`` also ignores PYTHON* variables; only ordinary OS locations
    # needed for interpreter startup are forwarded.
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "WINDIR": os.environ.get("WINDIR", ""),
        "TEMP": os.environ.get("TEMP", str(tmp_path)),
        "TMP": os.environ.get("TMP", str(tmp_path)),
        "TMPDIR": os.environ.get("TMPDIR", str(tmp_path)),
        "PATHEXT": os.environ.get("PATHEXT", ""),
        "COMSPEC": os.environ.get("COMSPEC", ""),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    result = subprocess.run(
        # -S drops site-packages, so no ambient canonical review_ledger exists.
        [sys.executable, "-I", "-S", "-X", "utf8", "-c", textwrap.dedent(PROBE),
         str(ROOT), str(tmp_path), case],
        cwd=str(tmp_path), env=env, text=True, encoding="utf-8",
        capture_output=True, timeout=120, check=False,
    )
    assert result.returncode == 0, (
        f"native namespace probe {case!r} failed ({result.returncode}).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.parametrize("case", ["no_canonical", "conflicting_canonical", "two_copies"])
def test_namespaced_reader_resource_isolation(case, tmp_path):
    _run_case(case, tmp_path)
