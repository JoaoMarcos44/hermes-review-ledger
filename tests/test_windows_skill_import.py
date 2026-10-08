"""Portable policy tests plus real native-Windows handle and junction tests."""
import os
from pathlib import Path
import subprocess

import pytest

from review_ledger.models import LedgerError
from review_ledger import windows_import as windows
from review_ledger.skills import _read, MAX_FILE_BYTES


@pytest.mark.parametrize("root", [
    r"\\server\share\package", r"\\?\C:\package", r"\\.\C:\package",
    r"\??\C:\package", r"\Device\HarddiskVolume1\package", r"C:package",
    r"\package", r"C:\package\..\other", r"C:\package:stream",
    "C:\\package\\bad\x00name", r"C:\package\NUL", r"C:\package\con.txt",
    r"C:\package\COM¹", r"C:\package\LPT².md", r"C:\package\trailing.",
    "C:\\package\\trailing ", "C:\\package\\line\nfeed", r"C:\package\wild*",
    r"C:\package\CONIN$", r"C:\package\CONOUT$", r"C:\package\AUX .md",
])
def test_windows_roots_reject_ambiguous_or_nonlocal_names(root):
    with pytest.raises(LedgerError) as error:
        windows._path_parts(root, "SKILL.md", cwd=r"C:\working")
    assert error.value.code == "unsafe_path"


@pytest.mark.parametrize("relative", [
    "../SKILL.md", "/SKILL.md", "./SKILL.md", "a//b.md", "a/../b.md",
    r"a\b.md", "notes.md:secret", "NUL.txt", "a./b.md", "trailing /b.md",
    "CON.txt", "LPT³.txt", "x\x00.md", "C:/notes.md", "a\n/b.md",
])
def test_windows_references_are_literal_components(relative):
    with pytest.raises(LedgerError):
        windows._path_parts(r"C:\package", relative, cwd=r"C:\working")


def test_windows_unicode_and_relative_roots_are_preserved():
    assert windows._path_parts("pacote/日本語/🧪", "references/café.md", cwd=r"C:\working") == (
        "C:", ("working", "pacote", "日本語", "🧪", "references", "café.md"))
    assert windows._path_parts(".", "SKILL.md", cwd=r"C:\working") == ("C:", ("working", "SKILL.md"))


class FakeAPI:
    """Model only the handle boundary; native tests execute the real OS calls."""
    def __init__(self, *, mapping=r"\Device\HarddiskVolume1", reparse=None, fail=None,
                 size=3, raw=b"abc", directory_leaf=False):
        self.mapping = mapping
        self.reparse = reparse
        self.fail = fail
        self.size = size
        self.raw = raw
        self.directory_leaf = directory_leaf
        self.events = []
        self.handles = []
        self.closed = []

    def volume_name(self, drive):
        self.events.append(("mapping", drive))
        return self.mapping

    def open(self, name, parent=None, *, directory):
        if name == self.fail:
            raise OSError("synthetic open failure")
        handle = len(self.handles) + 1
        self.handles.append(handle)
        self.events.append(("open", name, parent, directory, handle))
        return handle

    def inspect(self, handle, *, directory, max_bytes):
        name = next(event[1] for event in self.events if event[0] == "open" and event[4] == handle)
        if name == self.reparse:
            raise LedgerError("unsafe_path", "Reparse point")
        if not directory and (self.size > max_bytes or self.directory_leaf):
            raise LedgerError("unsupported_resource", "Only regular bounded files")
        self.events.append(("inspect", handle, directory))

    def local_volume(self, handle):
        self.events.append(("local", handle))

    def read(self, handle, limit):
        self.events.append(("read", handle, limit))
        return self.raw[:limit]

    def close(self, handle):
        self.closed.append(handle)


def invoke(monkeypatch, api, root=r"C:\package", relative="references/notes.md"):
    monkeypatch.setattr(windows, "_load_api", lambda: api)
    return windows.read_bytes(root, relative, MAX_FILE_BYTES)


def test_handle_relative_walk_never_reopens_resolved_path(monkeypatch):
    api = FakeAPI()
    assert invoke(monkeypatch, api) == b"abc"
    assert [event for event in api.events if event[0] == "open"] == [
        ("open", "\\Device\\HarddiskVolume1\\", None, True, 1),
        ("open", "package", 1, True, 2),
        ("open", "references", 2, True, 3),
        ("open", "notes.md", 3, False, 4),
    ]
    assert api.closed == [4, 3, 2, 1]
    assert api.events.index(("local", 1)) < api.events.index(("open", "package", 1, True, 2))


@pytest.mark.parametrize("mapping", [r"\Device\Mup\server\share", r"\??\C:\hidden", r"\Device\HarddiskVolume1\hidden", r"\Device\NamedPipe", "", r"\Device\HarddiskVolume1x"])
def test_network_subst_and_unrecognized_drive_mappings_rejected_before_open(monkeypatch, mapping):
    api = FakeAPI(mapping=mapping)
    with pytest.raises(LedgerError) as error:
        invoke(monkeypatch, api)
    assert error.value.code == "unsafe_path"
    assert not api.handles


@pytest.mark.parametrize("reparse", ["\\Device\\HarddiskVolume1\\", "package", "references", "notes.md"])
def test_reparse_rejection_closes_all_handles_without_read(monkeypatch, reparse):
    api = FakeAPI(reparse=reparse)
    with pytest.raises(LedgerError):
        invoke(monkeypatch, api)
    assert api.closed == list(reversed(api.handles))
    assert not any(event[0] == "read" for event in api.events)


@pytest.mark.parametrize("failed", ["\\Device\\HarddiskVolume1\\", "package", "references", "notes.md"])
def test_failed_open_closes_previous_handles(monkeypatch, failed):
    api = FakeAPI(fail=failed)
    with pytest.raises(LedgerError) as error:
        invoke(monkeypatch, api)
    assert error.value.code == "unsafe_path"
    assert api.closed == list(reversed(api.handles))


@pytest.mark.parametrize("attributes", [{"size": MAX_FILE_BYTES + 1}, {"directory_leaf": True}])
def test_nonregular_or_oversized_leaf_not_read(monkeypatch, attributes):
    api = FakeAPI(**attributes)
    with pytest.raises(LedgerError) as error:
        invoke(monkeypatch, api)
    assert error.value.code == "unsupported_resource"
    assert api.closed == list(reversed(api.handles))
    assert not any(event[0] == "read" for event in api.events)


def test_bounded_read_rejects_growth_and_closes_handles(monkeypatch):
    api = FakeAPI(raw=b"x" * (MAX_FILE_BYTES + 2))
    with pytest.raises(LedgerError) as error:
        invoke(monkeypatch, api)
    assert error.value.code == "resource_limit"
    assert ("read", 4, MAX_FILE_BYTES + 1) in api.events
    assert api.closed == list(reversed(api.handles))


def test_actual_unavailable_backend_fails_closed(monkeypatch):
    def unavailable():
        raise LedgerError("unsupported_platform", "Windows native APIs unavailable")
    monkeypatch.setattr(windows, "_load_api", unavailable)
    with pytest.raises(LedgerError) as error:
        windows.read_bytes(r"C:\package", "SKILL.md", MAX_FILE_BYTES)
    assert error.value.code == "unsupported_platform"


def native_windows_available():
    if os.name == "nt":
        return True
    # No skip: the non-Windows contract is an explicit fail-closed refusal.
    with pytest.raises(LedgerError) as error:
        windows._load_api()
    assert error.value.code == "unsupported_platform"
    return False


def test_native_windows_unicode_relative_and_bounded_reads(tmp_path, monkeypatch):
    if not native_windows_available():
        return
    root = tmp_path / "pacote_日本語_🧪"
    root.mkdir()
    (root / "SKILL.md").write_text("Unicode café 🧪", encoding="utf-8")
    assert _read(root, "SKILL.md") == "Unicode café 🧪"
    monkeypatch.chdir(tmp_path)
    assert _read(Path(root.name), "SKILL.md") == "Unicode café 🧪"
    (root / "SKILL.md").write_bytes(b"x" * MAX_FILE_BYTES)
    assert len(_read(root, "SKILL.md")) == MAX_FILE_BYTES
    (root / "SKILL.md").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(LedgerError) as error:
        _read(root, "SKILL.md")
    assert error.value.code == "unsupported_resource"


@pytest.mark.parametrize("raw", [b"bad\x00text", b"\xff"])
def test_native_windows_rejects_binary_and_invalid_utf8(tmp_path, raw):
    if not native_windows_available():
        return
    (tmp_path / "SKILL.md").write_bytes(raw)
    with pytest.raises(LedgerError):
        _read(tmp_path, "SKILL.md")
    (tmp_path / "SKILL.md").unlink()  # Failure paths release the leaf handle.


def test_native_windows_junction_ancestor_and_reference_refused(tmp_path):
    if not native_windows_available():
        return
    root = tmp_path / "package"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("Unapproved outside data", encoding="utf-8")
    junction = root / "junction"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], check=True, capture_output=True)
    try:
        for package, relative in [(junction, "SKILL.md"), (root, "junction/SKILL.md")]:
            with pytest.raises(LedgerError) as error:
                _read(package, relative)
            assert error.value.code == "unsafe_path"
    finally:
        junction.rmdir()


def test_native_windows_directory_leaf_refused_and_handle_released(tmp_path):
    if not native_windows_available():
        return
    (tmp_path / "SKILL.md").mkdir()
    with pytest.raises(LedgerError) as error:
        _read(tmp_path, "SKILL.md")
    assert error.value.code == "unsupported_resource"
    (tmp_path / "SKILL.md").rmdir()


def test_native_api_loading_failures_report_unsupported_platform(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(windows, "os", SimpleNamespace(name="nt"))
    def missing_api():
        raise AttributeError("NtOpenFile is unavailable")
    monkeypatch.setattr(windows, "_WindowsAPI", missing_api)
    with pytest.raises(LedgerError) as error:
        windows._load_api()
    assert error.value.code == "unsupported_platform"


def test_failure_during_read_closes_all_handles(monkeypatch):
    api = FakeAPI()
    def failure(*args):
        raise OSError("synthetic read failure")
    api.read = failure
    with pytest.raises(LedgerError):
        invoke(monkeypatch, api)
    assert api.closed == list(reversed(api.handles))


def test_failed_close_does_not_abandon_ancestors(monkeypatch):
    api = FakeAPI()
    def close(handle):
        api.closed.append(handle)
        if handle == 3:
            raise OSError("synthetic close failure")
    api.close = close
    with pytest.raises(LedgerError, match="release"):
        invoke(monkeypatch, api)
    assert api.closed == [4, 3, 2, 1]


@pytest.mark.parametrize("directory", [True, False])
def test_ntopenfile_uses_explicit_nofollow_flags_and_single_parent_handle(directory):
    import ctypes
    api = windows._WindowsAPI.__new__(windows._WindowsAPI)
    captured = {}
    def open_file(result, access, attributes, status, sharing, options):
        attrs = ctypes.cast(attributes, ctypes.POINTER(windows._ObjectAttributes)).contents
        name = attrs.ObjectName.contents
        captured.update(access=access, sharing=sharing, options=options,
                        parent=attrs.RootDirectory, attributes=attrs.Attributes,
                        name=ctypes.wstring_at(name.Buffer), length=name.Length,
                        max_length=name.MaximumLength)
        ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p))[0] = 123
        return 0
    api._open = open_file
    assert api.open("café_🧪", 42, directory=directory) == 123
    assert captured == {"access": 0x100080 | (0x20 if directory else 1),
                        "sharing": 1, "options": 0x600020, "parent": 42,
                        "attributes": 0x1040, "name": "café_🧪",
                        "length": len("café_🧪".encode("utf-16-le")),
                        "max_length": len("café_🧪".encode("utf-16-le")) + 2}


def test_windows_abi_structures_use_fixed_width_fields():
    import ctypes
    assert ctypes.sizeof(windows._FileInformation) == 52
    assert ctypes.sizeof(windows._VolumeDeviceInformation) == 8
    assert ctypes.sizeof(windows._IOStatusBlock) == 2 * ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(windows._ObjectAttributes) == (48 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24)


@pytest.mark.parametrize("attributes, directory, code", [
    (0x400, False, "unsafe_path"), (0x410, True, "unsafe_path"),
    (0x1000, False, "unsafe_path"), (0x40000, False, "unsafe_path"),
    (0x400000, False, "unsafe_path"), (0x40, False, "unsupported_resource"),
    (0x10, False, "unsupported_resource"), (0, True, "unsupported_resource"),
])
def test_native_metadata_policy_rejects_unsafe_handle_attributes(attributes, directory, code):
    import ctypes
    api = windows._WindowsAPI.__new__(windows._WindowsAPI)
    api._type = lambda handle: 1
    def information(handle, out):
        info = ctypes.cast(out, ctypes.POINTER(windows._FileInformation)).contents
        info.Attributes = attributes
        return True
    api._info = information
    with pytest.raises(LedgerError) as error:
        api.inspect(123, directory=directory, max_bytes=MAX_FILE_BYTES)
    assert error.value.code == code


def test_native_windows_replacement_before_child_open_cannot_escape(tmp_path, monkeypatch):
    if not native_windows_available():
        return
    root = tmp_path / "package"
    root.mkdir()
    references = root / "references"
    references.mkdir()
    (references / "notes.md").write_text("Approved inside text", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.md").write_text("Unapproved outside text", encoding="utf-8")
    moved = root / "moved"
    api = windows._load_api()
    original = api.open
    def replacing_open(name, parent=None, *, directory):
        if name == "references":
            references.rename(moved)
            subprocess.run(["cmd", "/c", "mklink", "/J", str(references), str(outside)],
                           check=True, capture_output=True)
        return original(name, parent, directory=directory)
    api.open = replacing_open
    monkeypatch.setattr(windows, "_load_api", lambda: api)
    try:
        with pytest.raises(LedgerError) as error:
            _read(root, "references/notes.md")
        assert error.value.code == "unsafe_path"
    finally:
        if references.is_junction():
            references.rmdir()


def test_native_windows_open_ancestor_stays_bound_and_all_handles_close(tmp_path, monkeypatch):
    if not native_windows_available():
        return
    root = tmp_path / "package"
    root.mkdir()
    references = root / "references"
    references.mkdir()
    (references / "notes.md").write_text("Approved inside text", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.md").write_text("Unapproved outside text", encoding="utf-8")
    moved = root / "moved"
    api = windows._load_api()
    original = api.open
    opened = []
    closed = []
    close = api.close
    def racing_open(name, parent=None, *, directory):
        handle = original(name, parent, directory=directory)
        opened.append(handle)
        if name == "references":
            try:
                references.rename(moved)
            except PermissionError:
                pass  # SHARE_DELETE is intentionally denied while pinned.
            else:
                # If filesystem rename semantics allow this, RootDirectory must
                # still refer to the retained original, never this new junction.
                subprocess.run(["cmd", "/c", "mklink", "/J", str(references), str(outside)],
                               check=True, capture_output=True)
        return handle
    def record_close(handle):
        close(handle)
        closed.append(handle)
    api.open = racing_open
    api.close = record_close
    monkeypatch.setattr(windows, "_load_api", lambda: api)
    try:
        assert _read(root, "references/notes.md") == "Approved inside text"
        assert closed == list(reversed(opened))
    finally:
        if references.is_junction():
            references.rmdir()
            moved.rename(references)
    references.rename(moved)  # Proves the retained directory handle is closed.
    (moved / "notes.md").unlink()  # Proves the leaf handle is closed.


def test_native_windows_backend_failure_reaches_skill_reader(tmp_path, monkeypatch):
    if not native_windows_available():
        return
    def unavailable():
        raise LedgerError("unsupported_platform", "Synthetic unavailable native backend")
    monkeypatch.setattr(windows, "_load_api", unavailable)
    with pytest.raises(LedgerError) as error:
        _read(tmp_path, "SKILL.md")
    assert error.value.code == "unsupported_platform"


def test_windows_path_component_budget_is_bounded():
    with pytest.raises(LedgerError, match="bounded"):
        windows._path_parts("C:\\" + "\\".join(["a"] * 257), "SKILL.md")
    with pytest.raises(LedgerError, match="255 UTF-16"):
        windows._path_parts("C:\\" + "🧪" * 128, "SKILL.md")


@pytest.mark.parametrize("device_type, characteristics, information", [
    (7, 0x10, 8), (2, 0, 8), (7, 0, 0), (0, 0, 8),
])
def test_native_volume_policy_rejects_network_nondisk_or_incomplete_metadata(device_type, characteristics, information):
    import ctypes
    api = windows._WindowsAPI.__new__(windows._WindowsAPI)
    def volume(handle, status, result, size, kind):
        assert size == 8 and kind == 4
        info = ctypes.cast(result, ctypes.POINTER(windows._VolumeDeviceInformation)).contents
        info.DeviceType = device_type
        info.Characteristics = characteristics
        ctypes.cast(status, ctypes.POINTER(windows._IOStatusBlock)).contents.Information = information
        return 0
    api._volume = volume
    with pytest.raises(LedgerError) as error:
        api.local_volume(123)
    assert error.value.code == "unsafe_path"
