"""Keep feature runtime modules and the native manifest's dependency declarations in the reviewed payload inventory."""
from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
import yaml

from review_ledger.payload_manifest import PAYLOAD_FILES


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", [
    "review_ledger/workflow.py",
    "review_ledger/token_report.py",
    "review_ledger/hermes_usage.py",
])
def test_feature_runtime_modules_are_packaged(module):
    assert module in PAYLOAD_FILES, "New runtime code must be declared in the canonical payload"


def test_native_manifest_declares_project_runtime_dependencies():
    """The shipped manifest must declare the same runtime dependencies as pyproject.toml.

    The installer copies only ``PAYLOAD_FILES``, so the installed directory plugin has
    ``plugin.yaml`` but no root ``pyproject.toml``; the manifest is then the only
    declaration Hermes can read (``pm.plugin_declarations.read_python_declaration``).
    Missing dependencies are surfaced but never installed at discovery
    (``plugins_loader._warn_python_dependencies``), so an undeclared import-time
    requirement (``review_ledger/skills.py`` imports ``yaml``) is invisible to native
    dependency admission when its manifest declaration is absent.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
    assert sorted(manifest.get("python_dependencies") or []) == sorted(project["project"]["dependencies"])
