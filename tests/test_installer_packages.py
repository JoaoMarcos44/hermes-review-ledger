"""Build and install the shipped package without a checkout on the import path.

Build tools come from the test extra. Tests never fetch build dependencies or
packages, and every profile, runtime database, and configuration is synthetic.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
PAYLOAD_PREFIX = "review_ledger/_plugin_payload/"
CONFIG = b"# Synthetic operator-owned config\nplugins:\n  enabled: []\n"


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _profile(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "config.yaml").write_bytes(CONFIG)


def _environment(root: Path) -> dict[str, str]:
    """Do not inherit credentials, package indexes, user sites, or PYTHONPATH."""
    home = root / "home"
    home.mkdir()
    temp = root / "tmp"
    temp.mkdir()
    default_profile = home / ".hermes"
    _profile(default_profile)
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "USERPROFILE": str(home),
        "HERMES_HOME": str(default_profile),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUTF8": "1",
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "TMPDIR": str(temp),
        "TEMP": str(temp),
        "TMP": str(temp),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "LOCALAPPDATA": str(home / "AppData" / "Local"),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "TZ": "UTC",
        "LANG": "C.UTF-8",
    }
    for key in ("SYSTEMROOT", "WINDIR"):
        if key in os.environ:
            environment[key] = os.environ[key]
    return environment


def _run(command: list[str | Path], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        [str(part) for part in command], cwd=cwd, env=env,
        text=True, encoding="utf-8", capture_output=True,
        timeout=180, check=False,
    )
    assert result.returncode == 0, (
        f"Package command failed ({result.returncode}): {command!r}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout


def _unchanged_default_profile(env: dict[str, str]) -> None:
    profile = Path(env["HERMES_HOME"])
    assert {path.relative_to(profile).as_posix() for path in profile.rglob("*")} == {
        "config.yaml",
    }
    assert (profile / "config.yaml").read_bytes() == CONFIG


@dataclass(frozen=True)
class InstalledPackage:
    sdist: Path
    wheel: Path
    source: Path
    python: Path
    console: Path
    environment: dict[str, str]
    outside: Path
    expected_hashes: dict[str, str]


@pytest.fixture(scope="module")
def installed_package(tmp_path_factory) -> InstalledPackage:
    workspace = tmp_path_factory.mktemp("package")
    outside = workspace / "outside"
    outside.mkdir()
    environment = _environment(workspace)
    source = workspace / "source"
    shutil.copytree(
        ROOT, source,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", "dist", "build",
            "*.egg-info", "ci-artifacts", "_plugin_payload",
        ),
    )
    payload_files = [source / "__init__.py", source / "plugin.yaml"]
    payload_files.extend((source / "review_ledger").glob("*.py"))
    payload_files.extend((source / "review_ledger" / "migrations").glob("*.sql"))
    payload_files.extend((source / "review_ledger" / "prompts").glob("*.md"))
    payload_files.extend((source / "review_ledger" / "resources").glob("*.md"))
    payload_files.extend(path for path in (source / "skills").rglob("*") if path.is_file())
    expected_hashes = {
        path.relative_to(source).as_posix(): _digest(path.read_bytes())
        for path in payload_files
    }
    # Keep the required files explicit as well as comparing all current files.
    assert {
        "__init__.py", "plugin.yaml", "skills/review-ledger/SKILL.md",
        "review_ledger/__init__.py", "review_ledger/__main__.py",
        "review_ledger/installer.py", "review_ledger/tools.py",
        "review_ledger/storage.py", "review_ledger/migrations/001_initial.sql",
        "review_ledger/migrations/004_critic.sql", "review_ledger/prompts/critic_v1.md",
        "review_ledger/migrations/003_adaptive_context.sql", "review_ledger/resources/protocol.md",
        "review_ledger/critic.py", "review_ledger/critic_hermes.py",
    } <= expected_hashes.keys()

    distributions = workspace / "dist"
    _run(
        [sys.executable, "-I", "-X", "utf8", "-m", "build", "--sdist",
         "--no-isolation", "--outdir", distributions, source],
        cwd=outside, env=environment,
    )
    sdists = list(distributions.glob("*.tar.gz"))
    assert len(sdists) == 1, sdists
    extracted = workspace / "unpacked"
    extracted.mkdir()
    with tarfile.open(sdists[0], "r:gz") as archive:
        archive.extractall(extracted, filter="data")
    source_roots = list(extracted.iterdir())
    assert len(source_roots) == 1 and source_roots[0].is_dir(), source_roots
    sdist_source = source_roots[0]
    for relative in ("docs/assets/ledger-pixel.gif", "docs/assets/ledger-pixel.png", "docs/assets/README.md"):
        assert (sdist_source / relative).read_bytes() == (source / relative).read_bytes()
    _run(
        [sys.executable, "-I", "-X", "utf8", "-m", "build", "--wheel",
         "--no-isolation", "--outdir", distributions, sdist_source],
        cwd=outside, env=environment,
    )
    wheels = list(distributions.glob("*.whl"))
    assert len(wheels) == 1, wheels

    # -I and an unrelated cwd prevent either checkout from satisfying imports.
    virtualenv = workspace / "isolated venv"
    _run(
        [sys.executable, "-I", "-X", "utf8", "-m", "venv", virtualenv],
        cwd=outside, env=environment,
    )
    scripts = virtualenv / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    console = scripts / ("hermes-review-ledger.exe" if os.name == "nt" else "hermes-review-ledger")
    _run(
        [python, "-I", "-X", "utf8", "-m", "pip", "install", "--no-index",
         "--no-deps", "--no-cache-dir", "--disable-pip-version-check", wheels[0]],
        cwd=outside, env=environment,
    )
    assert console.is_file(), "The wheel must expose hermes-review-ledger"
    imported_path = Path(json.loads(_run(
        [python, "-I", "-X", "utf8", "-c",
         "import json, review_ledger; print(json.dumps(review_ledger.__file__))"],
        cwd=outside, env=environment,
    )))
    assert imported_path.resolve().is_relative_to(virtualenv.resolve())
    assert not imported_path.resolve().is_relative_to(ROOT)
    _unchanged_default_profile(environment)
    return InstalledPackage(
        sdists[0], wheels[0], sdist_source, python, console,
        environment, outside, expected_hashes,
    )


def test_sdist_and_wheel_contain_complete_plugin_payload(installed_package):
    package = installed_package
    for relative, expected in package.expected_hashes.items():
        assert _digest((package.source / relative).read_bytes()) == expected, relative
    with zipfile.ZipFile(package.wheel) as archive:
        names = set(archive.namelist())
        for relative, expected in package.expected_hashes.items():
            name = PAYLOAD_PREFIX + relative
            assert name in names, f"Native plugin resource missing from wheel: {relative}"
            assert _digest(archive.read(name)) == expected, relative
            if relative.startswith("review_ledger/"):
                assert _digest(archive.read(relative)) == expected, relative
        assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
        assert not any(name.startswith(PAYLOAD_PREFIX + "tests/") for name in names)


def test_pip_install_and_cli_help_do_not_register_plugin(installed_package):
    package = installed_package
    for command in (
        [package.python, "-I", "-X", "utf8", "-m", "review_ledger", "--help"],
        [package.console, "--help"],
    ):
        output = _run(command, cwd=package.outside, env=package.environment)
        assert "install" in output
        assert "uninstall" in output
        _unchanged_default_profile(package.environment)


@pytest.mark.parametrize("entry_point", ["module", "console"])
def test_installed_cli_requires_an_explicit_existing_profile(
    installed_package, tmp_path, entry_point,
):
    package = installed_package
    missing = tmp_path / "missing-profile"
    unconfigured = tmp_path / "unconfigured-profile"
    unconfigured.mkdir()
    command = (
        [package.python, "-I", "-X", "utf8", "-m", "review_ledger"]
        if entry_point == "module" else [package.console]
    )
    for options in (
        [], ["--profile-dir", str(missing)],
        ["--profile-dir", "relative-profile"],
        ["--profile-dir", str(unconfigured)],
    ):
        result = subprocess.run(
            [str(part) for part in [*command, "install", *options]],
            cwd=package.outside, env=package.environment,
            text=True, encoding="utf-8", capture_output=True,
            timeout=30, check=False,
        )
        assert result.returncode != 0, result.stdout
        assert "Traceback" not in result.stderr, result.stderr
        assert not missing.exists()
        assert not (package.outside / "relative-profile").exists()
        assert list(unconfigured.iterdir()) == []
        _unchanged_default_profile(package.environment)


@pytest.mark.parametrize("entry_point", ["module", "console"])
def test_installed_cli_installs_reinstalls_and_removes_code_only(
    installed_package, tmp_path, entry_point,
):
    package = installed_package
    profile = tmp_path / "Perfil João 中文 with spaces"
    _profile(profile)
    # Installation and removal preserve existing durable state byte for byte.
    state = profile / "plugin-data" / "review-ledger"
    state.mkdir(parents=True)
    sentinels = {
        state / "review-ledger.sqlite3": b"Synthetic existing database sentinel\x00\xff",
        state / "artifacts" / "fixture.txt": b"Synthetic preserved artifact\n",
        state / "backups" / "fixture.sqlite3": b"Synthetic preserved backup\n",
    }
    for path, content in sentinels.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    command = (
        [package.python, "-I", "-X", "utf8", "-m", "review_ledger"]
        if entry_point == "module" else [package.console]
    )
    target = profile / "plugins" / "review-ledger"
    for _ in range(2):
        _run(
            [*command, "install", "--profile-dir", profile],
            cwd=package.outside, env=package.environment,
        )
        for relative, expected in package.expected_hashes.items():
            assert _digest((target / relative).read_bytes()) == expected, relative
        assert (profile / "config.yaml").read_bytes() == CONFIG
        for path, content in sentinels.items():
            assert path.read_bytes() == content
        assert not (profile / "skills").exists()
        assert not (target / "tests").exists()
        assert not (target / "pyproject.toml").exists()
        _unchanged_default_profile(package.environment)

    # Import the installed directory plugin in its own package namespace. This
    # is a resource/runtime smoke check, not a fake Hermes context or doctor.
    runtime_script = """
import importlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
target = Path(sys.argv[1])
assert not any(Path(item).resolve() == Path(sys.argv[3]).resolve()
               for item in sys.path if item)
spec = importlib.util.spec_from_file_location(
    "synthetic_installed_plugin", target / "__init__.py",
    submodule_search_locations=[str(target)],
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
assert callable(module.register)
tools = importlib.import_module(spec.name + ".review_ledger.tools")
assert tools.SKILL_PATH == target / "skills" / "review-ledger" / "SKILL.md"
assert tools.SKILL_PATH.is_file()
storage = importlib.import_module(spec.name + ".review_ledger.storage")
store = storage.Store(Path(sys.argv[2]), "synthetic-package-profile")
with store.connect() as connection:
    assert connection.execute("PRAGMA user_version").fetchone()[0] == storage.SCHEMA_VERSION
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
print(json.dumps({"skill": str(tools.SKILL_PATH), "migration_loaded": True}))
"""
    smoke = json.loads(_run(
        [package.python, "-I", "-X", "utf8", "-c", runtime_script,
         target, tmp_path / "runtime-state", ROOT],
        cwd=package.outside, env=package.environment,
    ))
    assert smoke["migration_loaded"] is True
    _run(
        [*command, "uninstall", "--profile-dir", profile],
        cwd=package.outside, env=package.environment,
    )
    assert not target.exists()
    assert (profile / "config.yaml").read_bytes() == CONFIG
    for path, content in sentinels.items():
        assert path.read_bytes() == content
    _unchanged_default_profile(package.environment)
