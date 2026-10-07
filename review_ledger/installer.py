"""Install this package's complete directory plugin into one explicit profile.

This deliberately does not edit Hermes configuration, resolve dependencies, read
credentials, or invoke a shell. Stop Hermes sessions before changing their code.
The lock coordinates this installer, not other programs sharing the same user.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile

from . import __version__


NAME = "review-ledger"
OWNER = "hermes-review-ledger"
MARKER = ".review-ledger-install.json"
STATE_DIR = ".review-ledger-installer"


class InstallError(Exception):
    """A readable, non-destructive installation refusal."""


def _plain(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise InstallError(f"Refusing a symbolic link or junction: {path}")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise InstallError(f"Expected a regular {'directory' if directory else 'file'}: {path}")


def _exists(path: Path) -> bool:
    return os.path.lexists(path)


def _profile(value: Path | str) -> Path:
    home = Path(value).expanduser()
    if not home.is_absolute():
        raise InstallError("--profile-dir must be an absolute path to an existing Hermes profile")
    _plain(home, directory=True)
    # Canonicalize OS aliases such as macOS /var before deriving our fixed paths.
    home = home.resolve(strict=True)
    _plain(home / "config.yaml")
    plugins = home / "plugins"
    if _exists(plugins):
        _plain(plugins, directory=True)
    return home


@contextmanager
def _lock(home: Path):
    path = home / ".review-ledger-install.lock"
    if _exists(path):
        _plain(path)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
    except FileExistsError:
        descriptor = os.open(path, flags)
        created = False
    with os.fdopen(descriptor, "r+b") as handle:
        opened = os.fstat(handle.fileno())
        _plain(path)
        current = path.lstat()
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)):
            raise InstallError("Profile lock must be a regular file without physical aliases; no lock content was changed")
        # Never initialize an existing file. Native locks can cover byte zero
        # beyond EOF, so an interrupted first creation remains usable as-is.
        if created:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InstallError("Another installer holds this profile lock; retry after it finishes") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    # Keep the lock file: unlinking it would let another process lock a new inode.


def _json(path: Path) -> dict:
    _plain(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise InstallError(f"Invalid installer metadata: {path}") from exc
    if not isinstance(value, dict):
        raise InstallError(f"Invalid installer metadata: {path}")
    return value


def _write_json(path: Path, value: dict) -> None:
    # All callers write inside a new stage or while holding the profile lock.
    if _exists(path):
        raise InstallError(f"Installer metadata already exists: {path}")
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if _exists(temporary):
            temporary.unlink()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _payload() -> dict[str, bytes]:
    package = Path(__file__).resolve().parent
    root = package / "_plugin_payload"
    if not root.is_dir():
        root = package.parent  # A complete source checkout or source distribution.
    names = ["__init__.py", "plugin.yaml"]
    names += [p.relative_to(root).as_posix() for p in (root / "review_ledger").glob("*.py")]
    names += [p.relative_to(root).as_posix() for p in (root / "review_ledger" / "migrations").glob("*.sql")]
    names += [p.relative_to(root).as_posix() for p in (root / "skills").rglob("*.md")]
    names += [p.relative_to(root).as_posix() for p in (root / "review_ledger" / "resources").glob("*.md")]
    names += [p.relative_to(root).as_posix() for p in (root / "review_ledger" / "prompts").glob("*.md")]
    required = {"review_ledger/tools.py", "review_ledger/migrations/001_initial.sql",
                "skills/review-ledger/SKILL.md", "review_ledger/resources/protocol.md", "review_ledger/critic.py",
                "review_ledger/critic_hermes.py", "review_ledger/critic_contract.py",
                "review_ledger/prompts/critic_v1.md", "review_ledger/migrations/004_critic.sql"}
    if not required.issubset(names):
        raise InstallError("Incomplete plugin payload; reinstall the package or use the complete source checkout")
    payload = {}
    for name in sorted(names):
        path = root / name
        # Reject links in payload directories as well as in its leaf files.
        for parent in path.parents:
            _plain(parent, directory=True)
            if parent == root:
                break
        _plain(path)
        payload[name] = path.read_bytes()
    return payload


def _manifest(payload: dict[str, bytes]) -> dict:
    return {"installer": OWNER, "format": 1, "version": __version__,
            "files": {name: _digest(data) for name, data in sorted(payload.items())}}


def _owned(root: Path, expected: dict | None = None, *, allow_missing: bool = False) -> dict:
    _plain(root, directory=True)
    if not _exists(root / MARKER):
        raise InstallError(f"Existing directory is not owned by this installer: {root}; move it aside yourself first")
    manifest = _json(root / MARKER)
    files = manifest.get("files")
    if (set(manifest) != {"installer", "format", "version", "files"}
            or manifest.get("installer") != OWNER or manifest.get("format") != 1
            or not isinstance(manifest.get("version"), str)
            or not isinstance(files, dict) or not files):
        raise InstallError(f"Unrecognized ownership manifest: {root}")
    directories = {"."}
    for name, digest in files.items():
        path = PurePosixPath(name)
        if (not isinstance(name, str) or path.is_absolute() or ".." in path.parts
                or path.as_posix() != name or "\\" in name or ":" in name or name == MARKER
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdef" for c in digest)):
            raise InstallError(f"Invalid owned file manifest: {root}")
        directories.update(str(p) for p in path.parents)
    found = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            _plain(path, directory=True)
            if relative not in directories and not (
                    path.name == "__pycache__" and path.parent.relative_to(root).as_posix() in directories):
                raise InstallError(f"Unowned directory found; nothing will be replaced or removed: {path}")
        else:
            _plain(path)
            if relative == MARKER:
                continue
            if path.parent.name == "__pycache__" and path.suffix == ".pyc":
                continue
            if relative not in files or _digest(path.read_bytes()) != files[relative]:
                raise InstallError(f"Modified or unowned file; nothing will be replaced or removed: {path}")
            found.add(relative)
    if (not allow_missing and found != set(files)) or (expected is not None and manifest != expected):
        raise InstallError(f"Installed code or ownership metadata has changed: {root}")
    return manifest


def _state(home: Path) -> Path:
    state = home / STATE_DIR
    owner = {"installer": OWNER, "format": 1}
    if not _exists(state):
        with tempfile.TemporaryDirectory(prefix=".review-ledger-prepare-", dir=home) as directory:
            candidate = Path(directory) / "state"
            candidate.mkdir(mode=0o700)
            _write_json(candidate / "owner.json", owner)
            os.replace(candidate, state)
    _plain(state, directory=True)
    if _json(state / "owner.json") != owner:
        raise InstallError(f"Unrecognized installer state: {state}")
    if {p.name for p in state.iterdir()} - {"owner.json", "pending.json", "pending.json.tmp", "new", "old"}:
        raise InstallError(f"Unowned files in installer state; inspect without deleting them: {state}")
    # An interrupted atomic journal write never preceded a code move.
    if _exists(state / "pending.json.tmp"):
        _plain(state / "pending.json.tmp")
        (state / "pending.json.tmp").unlink()
    return state


def _replace(source: Path, target: Path) -> None:
    os.replace(source, target)


def _discard(path: Path) -> None:
    if _exists(path):
        _plain(path, directory=True)
        # Only reserved old/new management directories reach this helper. A
        # final rmdir interrupted after marker deletion leaves no file to own.
        if not _exists(path / MARKER) and not any(path.iterdir()):
            path.rmdir()
            return
        # Delete the marker last. If Windows locks a file, a later invocation can
        # validate and finish the partially cleaned, still-owned code directory.
        _owned(path, allow_missing=True)
        children = sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True)
        for child in children:
            if child.name == MARKER and child.parent == path:
                continue
            if child.is_dir():
                child.rmdir()
            else:
                child.unlink()
        (path / MARKER).unlink()
        path.rmdir()


def _recover(state: Path, target: Path) -> None:
    """An interrupted operation is rolled back, never inferred to be approved."""
    pending = state / "pending.json"
    if not _exists(pending):
        return
    journal = _json(pending)
    if set(journal) != {"before", "after"}:
        raise InstallError(f"Unrecognized recovery record: {pending}")
    before, after = journal["before"], journal["after"]
    if any(value is not None and not isinstance(value, dict) for value in (before, after)):
        raise InstallError(f"Invalid recovery record: {pending}")
    old, new = state / "old", state / "new"
    if _exists(old):
        if before is None:
            raise InstallError(f"Unexpected backup; inspect installer state: {state}")
        _owned(old, before)
        if _exists(target):
            if after is None or _exists(new):
                raise InstallError(f"Recovery target changed; preserve all code copies under {state}")
            _owned(target, after)
            _replace(target, new)
        _replace(old, target)
    elif before is not None:
        _owned(target, before)
    elif _exists(target):
        if after is None or _exists(new):
            raise InstallError(f"Recovery target changed; inspect {state}")
        _owned(target, after)
        _replace(target, new)
    # Code is restored before cleaning the staging copy. A cleanup failure is safe.
    pending.unlink()
    _discard(new)


def _cleanup(state: Path) -> str | None:
    try:
        _discard(state / "old")
        _discard(state / "new")
    except OSError:
        return f"Code operation completed; a closed-file cleanup is pending in {state}. Close Hermes and rerun."
    return None


def _result(state: str, home: Path, **extra) -> dict:
    return {"state": state, "profile_dir": str(home), "plugin_dir": str(home / "plugins" / NAME),
            "configuration_changed": False, "data_preserved": True, **extra}


def _change(profile_dir: Path | str, remove: bool) -> dict:
    home = _profile(profile_dir)
    payload = None if remove else _payload()
    after = None if remove else _manifest(payload)
    target = home / "plugins" / NAME
    with _lock(home):
        state = _state(home)
        _recover(state, target)
        before = _owned(target) if _exists(target) else None
        warning = _cleanup(state)
        if warning:
            raise InstallError(warning)
        if before == after:
            return _result("absent" if remove else "unchanged", home)
        if not remove:
            target.parent.mkdir(exist_ok=True)
            _plain(target.parent, directory=True)
            # A killed preparation may leave an undiscovered temp folder. It
            # cannot occupy the next operation's stage or replace existing code.
            with tempfile.TemporaryDirectory(prefix=".review-ledger-prepare-", dir=home) as directory:
                new = Path(directory) / "plugin"
                new.mkdir()
                for name, data in payload.items():
                    path = new / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                _write_json(new / MARKER, after)
                _owned(new, after)
                os.replace(new, state / "new")
        pending = state / "pending.json"
        _write_json(pending, {"before": before, "after": after})
        try:
            if before is not None:
                _owned(target, before)
                _replace(target, state / "old")
            elif _exists(target):
                raise InstallError(f"Another program created the destination: {target}")
            if not remove:
                _replace(state / "new", target)
            pending.unlink()  # Commit: the previous code is now disposable.
        except BaseException as exc:
            try:
                _recover(state, target)
            except (OSError, InstallError) as recovery:
                raise InstallError(
                    f"Code recovery is pending in {state}; stop Hermes, fix file access and rerun the command. "
                    "Do not delete the old/new copies. Profile data and configuration were not changed."
                ) from recovery
            raise exc
        warning = _cleanup(state)
        return _result("removed" if remove else ("upgraded" if before else "installed"), home,
                       **({"warning": warning} if warning else {}))


def install(profile_dir: Path | str) -> dict:
    return _change(profile_dir, False)


def uninstall(profile_dir: Path | str) -> dict:
    return _change(profile_dir, True)


def status(profile_dir: Path | str) -> dict:
    home = _profile(profile_dir)
    target = home / "plugins" / NAME
    if _exists(home / STATE_DIR / "pending.json"):
        raise InstallError("An interrupted installation needs recovery; rerun install or uninstall after stopping Hermes")
    if not _exists(target):
        return _result("absent", home)
    manifest = _owned(target)
    return _result("installed", home, version=manifest["version"], files=len(manifest["files"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Review Ledger code into one existing Hermes profile; no config changes.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "status", "uninstall"):
        command = commands.add_parser(name)
        command.add_argument("--profile-dir", required=True, type=Path,
                             help="absolute directory of an existing profile containing config.yaml")
    args = parser.parse_args(argv)
    try:
        result = {"install": install, "status": status, "uninstall": uninstall}[args.command](args.profile_dir)
    except (InstallError, OSError) as exc:
        print(f"Review Ledger: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "install":
        print("Code is installed. Enable/configure review-ledger explicitly in this same profile, then restart Hermes.")
        print("The full operator CLI requires the profile's existing plugins.isolation to be in_process; this installer never changes it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
