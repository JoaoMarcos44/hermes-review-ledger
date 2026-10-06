"""Installer integrity tests use synthetic, disposable profiles only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from review_ledger import installer as i


@pytest.fixture
def profile(tmp_path):
    home = tmp_path / "Perfil João with spaces"
    home.mkdir()
    (home / "config.yaml").write_text("# Keep comments and host isolation\nplugins:\n  isolation: host\n", encoding="utf-8")
    data = home / "plugin-data" / "retained-ledger"
    data.mkdir(parents=True)
    (data / "review-ledger.sqlite3").write_bytes(b"synthetic retained database bytes")
    (data / "approved-lessons.json").write_text('{"approved":true}', encoding="utf-8")
    return home


def contents(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def retained(home):
    return ((home / "config.yaml").read_bytes(), contents(home / "plugin-data"))


def changed_payload(monkeypatch):
    original = i._payload()
    modified = dict(original)
    modified["review_ledger/__init__.py"] += b"\n# Synthetic newer package fixture\n"
    monkeypatch.setattr(i, "_payload", lambda: modified)
    return original, modified


def test_install_reinstall_upgrade_uninstall_preserves_profile(profile, monkeypatch):
    before = retained(profile)
    other = profile / "plugins" / "other-plugin"
    other.mkdir(parents=True)
    (other / "keep.txt").write_text("unrelated", encoding="utf-8")
    assert i.install(profile)["state"] == "installed"
    target = profile / "plugins" / i.NAME
    first = contents(target)
    timestamp = (target / "plugin.yaml").stat().st_mtime_ns
    assert i.install(profile)["state"] == "unchanged"
    assert (target / "plugin.yaml").stat().st_mtime_ns == timestamp
    assert contents(target) == first
    _, new = changed_payload(monkeypatch)
    assert i.install(profile)["state"] == "upgraded"
    assert (target / "review_ledger" / "__init__.py").read_bytes() == new["review_ledger/__init__.py"]
    assert i.status(profile)["state"] == "installed"
    assert i.uninstall(profile)["state"] == "removed"
    assert not target.exists()
    assert i.uninstall(profile)["state"] == "absent"
    assert i.status(profile)["state"] == "absent"
    assert retained(profile) == before
    assert (other / "keep.txt").read_text(encoding="utf-8") == "unrelated"


@pytest.mark.parametrize("case", ["missing", "relative", "no-config", "config-directory"])
def test_explicit_existing_profile_required(tmp_path, case):
    home = tmp_path / "profile"
    if case != "missing":
        home.mkdir()
    if case == "relative":
        home = Path("not-an-absolute-profile")
    if case == "config-directory":
        (home / "config.yaml").mkdir()
    with pytest.raises((i.InstallError, OSError)):
        i.install(home)
    assert not (tmp_path / "profile" / "plugins").exists()


@pytest.mark.parametrize("change", ["unowned", "modified", "extra-file", "extra-directory", "missing-file"])
def test_refuses_unowned_or_changed_install(profile, change):
    target = profile / "plugins" / i.NAME
    if change == "unowned":
        target.mkdir(parents=True)
        (target / "user.txt").write_text("user data", encoding="utf-8")
    else:
        i.install(profile)
        if change == "modified":
            (target / "plugin.yaml").write_text("user edit", encoding="utf-8")
        elif change == "extra-file":
            (target / "notes.txt").write_text("user data", encoding="utf-8")
        elif change == "extra-directory":
            (target / "my-empty-directory").mkdir()
        else:
            (target / "plugin.yaml").unlink()
    before = contents(target), retained(profile)
    for action in (i.install, i.uninstall):
        with pytest.raises(i.InstallError):
            action(profile)
        assert (contents(target), retained(profile)) == before


def test_python_cache_is_removable_owned_runtime_output(profile):
    i.install(profile)
    cache = profile / "plugins" / i.NAME / "review_ledger" / "__pycache__"
    cache.mkdir()
    (cache / "tools.cpython-test.pyc").write_bytes(b"synthetic bytecode")
    assert i.uninstall(profile)["state"] == "removed"


def test_failed_publication_restores_prior_code(profile, monkeypatch):
    i.install(profile)
    target = profile / "plugins" / i.NAME
    before = contents(target), retained(profile)
    changed_payload(monkeypatch)
    replace = i._replace
    def fail_new(source, destination):
        if source.name == "new":
            raise PermissionError("synthetic native publication lock")
        return replace(source, destination)
    monkeypatch.setattr(i, "_replace", fail_new)
    with pytest.raises(PermissionError):
        i.install(profile)
    assert (contents(target), retained(profile)) == before
    assert not (profile / i.STATE_DIR / "pending.json").exists()


def test_failed_first_install_leaves_no_plugin(profile, monkeypatch):
    before = retained(profile)
    monkeypatch.setattr(i, "_replace", lambda *_: (_ for _ in ()).throw(PermissionError("synthetic locked destination")))
    with pytest.raises(PermissionError):
        i.install(profile)
    assert not (profile / "plugins" / i.NAME).exists()
    assert retained(profile) == before


@pytest.mark.parametrize("published", [False, True])
def test_interrupted_upgrade_is_recovered_before_next_install(profile, monkeypatch, published):
    i.install(profile)
    target = profile / "plugins" / i.NAME
    state = profile / i.STATE_DIR
    before = i._owned(target)
    old_payload, new_payload = changed_payload(monkeypatch)
    after = i._manifest(new_payload)
    new = state / "new"
    new.mkdir()
    for name, data in new_payload.items():
        path = new / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    i._write_json(new / i.MARKER, after)
    i._write_json(state / "pending.json", {"before": before, "after": after})
    i._replace(target, state / "old")
    if published:
        i._replace(new, target)
    snapshot = retained(profile)
    monkeypatch.setattr(i, "_payload", lambda: old_payload)
    assert i.install(profile)["state"] == "unchanged"
    assert i._owned(target) == before
    assert retained(profile) == snapshot
    assert not (state / "pending.json").exists()


def test_failure_to_restore_retains_code_for_recovery(profile, monkeypatch):
    i.install(profile)
    target = profile / "plugins" / i.NAME
    original = contents(target)
    changed_payload(monkeypatch)
    replace = i._replace
    def fail(source, destination):
        if source.name in {"new", "old"}:
            raise PermissionError("synthetic OS sharing lock")
        return replace(source, destination)
    monkeypatch.setattr(i, "_replace", fail)
    with pytest.raises(i.InstallError, match="recovery is pending"):
        i.install(profile)
    assert contents(profile / i.STATE_DIR / "old") == original
    assert (profile / i.STATE_DIR / "pending.json").is_file()
    monkeypatch.setattr(i, "_replace", replace)
    assert i.install(profile)["state"] == "upgraded"


def test_cleanup_failure_is_retryable_without_touching_data(profile, monkeypatch):
    i.install(profile)
    before = retained(profile)
    changed_payload(monkeypatch)
    unlink = Path.unlink
    failures = []
    def fail_once(path, *args, **kwargs):
        if "old" in path.parts and path.name == "plugin.yaml" and not failures:
            failures.append(path)
            raise PermissionError("synthetic Windows retained handle")
        return unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_once)
    result = i.install(profile)
    assert result["state"] == "upgraded" and "warning" in result
    assert i.install(profile)["state"] == "unchanged"
    assert not (profile / i.STATE_DIR / "old").exists()
    assert retained(profile) == before


def test_metadata_short_write_never_publishes_partial_journal(tmp_path, monkeypatch):
    path = tmp_path / "pending.json"
    def short_write(_value, handle, **_kwargs):
        handle.write('{"before":')
        raise OSError("synthetic interrupted disk write")
    monkeypatch.setattr(json, "dump", short_write)
    with pytest.raises(OSError):
        i._write_json(path, {"before": None, "after": {}})
    assert not path.exists()
    assert not path.with_name("pending.json.tmp").exists()


def test_interrupted_journal_write_can_be_retried(profile):
    i.install(profile)
    before = retained(profile)
    state = profile / i.STATE_DIR
    (state / "pending.json.tmp").write_text('{"before":', encoding="utf-8")
    assert i.install(profile)["state"] == "unchanged"
    assert not (state / "pending.json.tmp").exists()
    assert retained(profile) == before


def test_interrupted_uninstall_restores_before_continuing(profile):
    i.install(profile)
    target = profile / "plugins" / i.NAME
    state = profile / i.STATE_DIR
    manifest = i._owned(target)
    i._write_json(state / "pending.json", {"before": manifest, "after": None})
    i._replace(target, state / "old")
    assert i.install(profile)["state"] == "unchanged"
    assert i._owned(target) == manifest


def test_incomplete_undiscovered_preparation_does_not_block_retry(profile):
    abandoned = profile / ".review-ledger-prepare-synthetic-interruption" / "plugin"
    abandoned.mkdir(parents=True)
    (abandoned / "plugin.yaml").write_bytes(b"partial")
    assert i.install(profile)["state"] == "installed"
    # Unknown abandoned scratch content is left for the operator to inspect.
    assert (abandoned / "plugin.yaml").read_bytes() == b"partial"


def test_permission_failure_is_clear_and_preserves_config(profile, monkeypatch):
    before = retained(profile)
    monkeypatch.setattr(os, "open", lambda *_a, **_k: (_ for _ in ()).throw(PermissionError("synthetic profile denied")))
    assert i.main(["install", "--profile-dir", str(profile)]) == 1
    assert retained(profile) == before


def test_real_separate_process_lock(profile, tmp_path):
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    root = Path(__file__).resolve().parents[1]
    code = f'''import sys,time
from pathlib import Path
sys.path.insert(0, {str(root)!r})
from review_ledger.installer import _lock
with _lock(Path({str(profile)!r})):
    Path({str(ready)!r}).touch()
    deadline=time.monotonic()+15
    while not Path({str(release)!r}).exists() and time.monotonic()<deadline:
        time.sleep(0.05)
'''
    worker = subprocess.Popen([sys.executable, "-I", "-c", code])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline and worker.poll() is None:
            time.sleep(0.05)
        assert ready.exists(), "Native profile lock worker failed to start"
        with pytest.raises(i.InstallError, match="lock"):
            i.install(profile)
    finally:
        release.touch()
        worker.wait(timeout=20)
    assert worker.returncode == 0
    assert i.install(profile)["state"] == "installed"


def test_linked_destination_refused_on_native_os(profile, tmp_path):
    external = tmp_path / "unrelated"
    external.mkdir()
    (external / "keep.txt").write_text("keep", encoding="utf-8")
    plugins = profile / "plugins"
    if os.name == "nt":
        # Junctions require no Windows Developer Mode/admin symlink privilege.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(plugins), str(external)], check=True, capture_output=True)
    else:
        plugins.symlink_to(external, target_is_directory=True)
    try:
        with pytest.raises(i.InstallError, match="link|junction"):
            i.install(profile)
        assert contents(external) == {"keep.txt": b"keep"}
    finally:
        if os.name == "nt":
            plugins.rmdir()
        else:
            plugins.unlink()


def test_native_open_file_handling(profile):
    i.install(profile)
    target = profile / "plugins" / i.NAME
    before = contents(target), retained(profile)
    if os.name != "nt":
        # POSIX permits unlinking an open file; the profile data stays untouched.
        with (target / "plugin.yaml").open("rb") as handle:
            assert i.uninstall(profile)["state"] == "removed"
            assert handle.read() == before[0]["plugin.yaml"]
    else:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateFileW(str(target / "plugin.yaml"), 0x80000000, 1, None, 3, 0, None)
        assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
        try:
            try:
                result = i.uninstall(profile)
            except OSError:
                assert contents(target) == before[0]
            else:
                # Some Windows filesystems allow directory rename but defer delete.
                assert result["state"] == "removed" and "warning" in result
        finally:
            assert kernel.CloseHandle(handle)
        assert i.uninstall(profile)["state"] in {"removed", "absent"}
    assert retained(profile) == before[1]
