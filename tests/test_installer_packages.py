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


def test_v1_identity_and_distribution_metadata(installed_package):
    """Product identity, schema, and deliberate procedure revisions are independent."""
    package = installed_package
    for command in (
        [package.python, "-I", "-m", "review_ledger", "--version"],
        [package.console, "--version"],
    ):
        assert _run(command, cwd=package.outside, env=package.environment).strip() == "1.0.2"
    script = """
from importlib.metadata import version
import json
from review_ledger import __version__
from review_ledger.storage import SCHEMA_VERSION
from review_ledger.protocol import VERSION
print(json.dumps([version('hermes-review-ledger'), __version__, SCHEMA_VERSION, VERSION]))
"""
    assert json.loads(_run([package.python, "-I", "-c", script],
                          cwd=package.outside, env=package.environment)) == ["1.0.2", "1.0.2", 5, "3"]
    assert "version: 1.0.2\n" in (package.source / "plugin.yaml").read_text()
    assert (package.source / "docs" / "installation.md").is_file()
    assert (package.source / "scripts" / "benchmark_compression.py").is_file()


@pytest.mark.parametrize("old_version", ["0.4.0", "1.0.0", "1.0.1"])
def test_installed_cli_upgrades_owned_prior_versions_and_preserves_data(installed_package, tmp_path, old_version):
    """Owned synthetic prior-version payloads exercise the existing manifest format.

    This is an installer fixture, not a claim to reproduce an historical release
    or to validate a database migration (covered by schema tests separately).
    """
    package = installed_package
    profile = tmp_path / "old private profile"
    _profile(profile)
    target = profile / "plugins" / "review-ledger"
    target.mkdir(parents=True)
    old_payload = {
        "__init__.py": f"# Synthetic {old_version} plugin fixture\n".encode(),
        "plugin.yaml": f"name: review-ledger\nversion: {old_version}\n".encode(),
        "review_ledger/__init__.py": f'__version__ = "{old_version}"\n'.encode(),
    }
    for name, data in old_payload.items():
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    marker = target / ".review-ledger-install.json"
    marker.write_text(json.dumps({
        "installer": "hermes-review-ledger", "format": 1, "version": old_version,
        "files": {name: _digest(data) for name, data in old_payload.items()},
    }), encoding="utf-8")
    data_dir = profile / "plugin-data" / "review-ledger"
    data_dir.mkdir(parents=True)
    kept = data_dir / "review-ledger.sqlite3"
    # Deliberately opaque: code installation must not open or migrate this file.
    kept.write_bytes(b"synthetic schema-4 data preserved byte-for-byte\x00")
    previous = kept.read_bytes()
    output = _run([package.console, "upgrade", "--profile-dir", profile],
                  cwd=package.outside, env=package.environment)
    assert '"state": "upgraded"' in output
    for name, digest in package.expected_hashes.items():
        assert _digest((target / name).read_bytes()) == digest
    assert json.loads(marker.read_text())["version"] == "1.0.2"
    status = _run([package.console, "status", "--profile-dir", profile],
                  cwd=package.outside, env=package.environment)
    assert json.loads(status)["version"] == "1.0.2"
    _run([package.console, "uninstall", "--profile-dir", profile],
         cwd=package.outside, env=package.environment)
    assert not target.exists()
    assert kept.read_bytes() == previous
    assert (profile / "config.yaml").read_bytes() == CONFIG
    _unchanged_default_profile(package.environment)


def test_rebuild_uses_current_source_and_removes_stale_build_files(tmp_path):
    environment = _environment(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    source = tmp_path / "source"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(
        ".git", "build", "dist", "*.egg-info", "__pycache__", ".pytest_cache", "_plugin_payload",
    ))
    output = tmp_path / "dist"
    command = [sys.executable, "-I", "-X", "utf8", "-m", "build", "--wheel",
               "--no-isolation", "--outdir", output, source]
    _run(command, cwd=outside, env=environment)
    module = source / "review_ledger" / "__init__.py"
    original_time = module.stat().st_mtime_ns
    revised = module.read_bytes() + b"\n# Synthetic same-version reviewed revision\n"
    module.write_bytes(revised)
    os.utime(module, ns=(original_time - 60_000_000_000, original_time - 60_000_000_000))
    # A module removed by an intervening source update can remain in build/lib.
    stale = source / "build" / "lib" / "review_ledger" / "obsolete_module.py"
    assert stale.parent.is_dir()
    stale.write_bytes(b"# Synthetic removed historical module\n")
    _run(command, cwd=outside, env=environment)
    wheels = list(output.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as archive:
        assert archive.read("review_ledger/__init__.py") == revised
        assert archive.read(PAYLOAD_PREFIX + "review_ledger/__init__.py") == revised
        assert not any(name.endswith("obsolete_module.py") for name in archive.namelist())


@pytest.mark.parametrize("case", ["source", "source-alias", "package-alias", "build-ancestor-alias"])
def test_build_cleanup_refuses_source_overlap_and_links(tmp_path, case):
    environment = _environment(tmp_path)
    source = tmp_path / "source"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(
        ".git", "build", "dist", "*.egg-info", "__pycache__", ".pytest_cache", "_plugin_payload",
    ))
    outside = tmp_path / "preserved"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"unrelated operator data")
    output = source
    if case != "source":
        output = tmp_path / "output"
        link, target = output, source
        if case == "package-alias":
            output.mkdir()
            link, target = output / "review_ledger", outside
        elif case == "build-ancestor-alias":
            link, target = output, outside
            output = output / "nested"
            kept = outside / "nested" / "review_ledger" / "keep.txt"
            kept.parent.mkdir(parents=True)
            kept.write_bytes(b"unrelated build-ancestor target")
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                           check=True, capture_output=True)
        else:
            link.symlink_to(target, target_is_directory=True)
    before = {p.relative_to(source): p.read_bytes() for p in (source / "review_ledger").rglob("*.py")}
    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", str(source / "setup.py"),
         "build_py", "--build-lib", str(output)],
        cwd=source, env=environment, text=True, encoding="utf-8", capture_output=True,
        timeout=30, check=False,
    )
    assert result.returncode != 0
    assert "Build output must not" in result.stderr
    assert {p.relative_to(source): p.read_bytes() for p in (source / "review_ledger").rglob("*.py")} == before
    assert sentinel.read_bytes() == b"unrelated operator data"
    if case == "build-ancestor-alias":
        assert kept.read_bytes() == b"unrelated build-ancestor target"


def test_build_refuses_an_incomplete_runtime_payload(tmp_path):
    environment = _environment(tmp_path)
    source = tmp_path / "source"
    shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(
        ".git", "build", "dist", "*.egg-info", "__pycache__", ".pytest_cache", "_plugin_payload",
    ))
    (source / "review_ledger" / "storage.py").unlink()
    output = tmp_path / "dist"
    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-m", "build", "--wheel",
         "--no-isolation", "--outdir", str(output), str(source)],
        cwd=tmp_path, env=environment, text=True, encoding="utf-8", capture_output=True,
        timeout=30, check=False,
    )
    assert result.returncode != 0
    assert "Incomplete plugin payload" in result.stderr
    assert not list(output.glob("*.whl"))
