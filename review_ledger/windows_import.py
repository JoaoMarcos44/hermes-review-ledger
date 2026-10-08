"""Fail-closed, handle-relative local text import on native Windows.

Only ordinary drive-letter paths backed by a native local disk volume are
accepted. UNC, device/extended namespaces, SUBST drives and reparse points are
not supported. No path is resolved and subsequently reopened. The drive mapping
is captured once; its native volume root and each literal child are opened with
NtOpenFile. All ancestor handles stay open until the bounded leaf read ends.

Contracts (Microsoft):
https://learn.microsoft.com/windows/win32/api/winternl/nf-winternl-ntopenfile
https://learn.microsoft.com/windows/win32/api/winternl/nf-winternl-ntcreatefile
https://learn.microsoft.com/windows/win32/api/ntdef/ns-ntdef-_object_attributes
https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-querydosdevicew
https://learn.microsoft.com/windows/win32/fileio/naming-a-file
"""
from __future__ import annotations

import ctypes
import os
from pathlib import PureWindowsPath
import re

from .models import LedgerError

# Fixed-width Windows ABI types, also inspectable in portable policy tests.
_U32 = ctypes.c_uint32
_HANDLE = ctypes.c_void_p
_NTSTATUS = ctypes.c_int32


class _UnicodeString(ctypes.Structure):
    _fields_ = [("Length", ctypes.c_uint16), ("MaximumLength", ctypes.c_uint16),
                ("Buffer", ctypes.c_void_p)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [("Length", _U32), ("RootDirectory", _HANDLE),
                ("ObjectName", ctypes.POINTER(_UnicodeString)), ("Attributes", _U32),
                ("SecurityDescriptor", ctypes.c_void_p),
                ("SecurityQualityOfService", ctypes.c_void_p)]


class _StatusValue(ctypes.Union):
    _fields_ = [("Status", _NTSTATUS), ("Pointer", ctypes.c_void_p)]


class _IOStatusBlock(ctypes.Structure):
    _fields_ = [("Value", _StatusValue), ("Information", ctypes.c_size_t)]


class _FileInformation(ctypes.Structure):
    _fields_ = [("Attributes", _U32), ("CreationTime", _U32 * 2),
                ("LastAccessTime", _U32 * 2), ("LastWriteTime", _U32 * 2),
                ("VolumeSerialNumber", _U32), ("SizeHigh", _U32), ("SizeLow", _U32),
                ("NumberOfLinks", _U32), ("IndexHigh", _U32), ("IndexLow", _U32)]


class _VolumeDeviceInformation(ctypes.Structure):
    _fields_ = [("DeviceType", _U32), ("Characteristics", _U32)]


_DIRECTORY = 0x10
_DEVICE = 0x40
_REPARSE_POINT = 0x400
_OFFLINE_OR_RECALL = 0x1000 | 0x40000 | 0x400000
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])\Z", re.I)
_VOLUME = re.compile(r"\\Device\\HarddiskVolume[0-9]+\Z", re.I)


def _component(part):
    if (not part or part in {".", ".."} or part[-1] in ". " or
            any(ord(char) < 32 or char in '<>:"/\\|?*' for char in part) or
            _RESERVED.fullmatch(part.split(".", 1)[0].rstrip(" "))):
        raise LedgerError("unsafe_path", "Windows imports require unambiguous ordinary path components")
    try:
        size = len(part.encode("utf-16-le"))
    except UnicodeError as exc:
        raise LedgerError("unsafe_path", "Invalid Unicode in local import path") from exc
    if size > 510:
        raise LedgerError("unsafe_path", "Windows path components must fit 255 UTF-16 code units")
    return part


def _path_parts(root, relative, *, cwd=None):
    """Validate lexically before any filesystem lookup; never call resolve()."""
    raw = os.fspath(root)
    if not isinstance(raw, str) or not isinstance(relative, str):
        raise LedgerError("unsafe_path", "Local import paths must be text")
    raw = raw.replace("/", "\\")
    if raw.startswith("\\") or ".." in raw.split("\\"):
        raise LedgerError("unsafe_path", "UNC, device, rooted and parent-traversal paths are unsupported")
    parsed = PureWindowsPath(raw)
    if parsed.drive and (not re.fullmatch(r"[A-Za-z]:", parsed.drive) or not parsed.root):
        raise LedgerError("unsafe_path", "A drive-qualified import path must be absolute")
    if not parsed.drive:
        base = PureWindowsPath(os.getcwd() if cwd is None else cwd)
        if not re.fullmatch(r"[A-Za-z]:", base.drive) or base.root != "\\":
            raise LedgerError("unsafe_path", "The working directory must use an ordinary local drive")
        parsed = base / parsed
    # PureWindowsPath preserves '..', but the working directory must also pass
    # component validation. Its collapsed '.' components carry no authority.
    root_parts = tuple(_component(part) for part in parsed.parts[1:])
    if "\\" in relative:
        raise LedgerError("unsafe_path", "References require slash-separated literal relative names")
    reference_parts = tuple(_component(part) for part in relative.split("/"))
    parts = root_parts + reference_parts
    if len(parts) > 256 or sum(len(part.encode("utf-16-le")) + 2 for part in parts) > 65528:
        raise LedgerError("unsafe_path", "Windows import paths exceed the bounded component or length limit")
    return parsed.drive.upper(), parts


class _WindowsAPI:
    def __init__(self):
        # These are Windows system DLLs, loaded lazily and never from a package
        # dependency or an operator-supplied directory.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        native = ctypes.WinDLL("ntdll", use_last_error=True)
        self._query_dos = kernel.QueryDosDeviceW
        self._query_dos.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, _U32]
        self._query_dos.restype = _U32
        self._open = native.NtOpenFile
        self._open.argtypes = [ctypes.POINTER(_HANDLE), _U32, ctypes.POINTER(_ObjectAttributes),
                               ctypes.POINTER(_IOStatusBlock), _U32, _U32]
        self._open.restype = _NTSTATUS
        self._nt_error = native.RtlNtStatusToDosError
        self._nt_error.argtypes = [_NTSTATUS]
        self._nt_error.restype = _U32
        self._info = kernel.GetFileInformationByHandle
        self._info.argtypes = [_HANDLE, ctypes.POINTER(_FileInformation)]
        self._info.restype = ctypes.c_int32
        self._type = kernel.GetFileType
        self._type.argtypes = [_HANDLE]
        self._type.restype = _U32
        self._volume = native.NtQueryVolumeInformationFile
        self._volume.argtypes = [_HANDLE, ctypes.POINTER(_IOStatusBlock),
                                ctypes.c_void_p, _U32, ctypes.c_int32]
        self._volume.restype = _NTSTATUS
        self._read = kernel.ReadFile
        self._read.argtypes = [_HANDLE, ctypes.c_void_p, _U32, ctypes.POINTER(_U32), ctypes.c_void_p]
        self._read.restype = ctypes.c_int32
        self._close = kernel.CloseHandle
        self._close.argtypes = [_HANDLE]
        self._close.restype = ctypes.c_int32

    def _check_nt(self, status):
        if status != 0:  # Synchronous operations must return STATUS_SUCCESS.
            raise ctypes.WinError(self._nt_error(status))

    def volume_name(self, drive):
        buffer = ctypes.create_unicode_buffer(32768)
        if not self._query_dos(drive, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        # The first string is the current mapping; later strings are old ones.
        return buffer.value

    def open(self, name, parent=None, *, directory):
        encoded_length = len(name.encode("utf-16-le"))
        buffer = ctypes.create_unicode_buffer(name)
        unicode = _UnicodeString(encoded_length, encoded_length + 2, ctypes.addressof(buffer))
        attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent,
                                       ctypes.pointer(unicode), 0x40 | 0x1000, None, None)
        status_block = _IOStatusBlock()
        handle = _HANDLE()
        # FILE_READ_ATTRIBUTES | SYNCHRONIZE, plus FILE_TRAVERSE for directory
        # parents or FILE_READ_DATA for the leaf. No backup-privilege intent.
        access = 0x80 | 0x100000 | (0x20 if directory else 0x1)
        # Type-neutral open, then check the opened object itself. OPEN_REPARSE
        # prevents leaf reparse processing; OBJ_DONT_REPARSE protects the native
        # anchor. NO_RECALL avoids fetching offline/virtualized file contents.
        options = 0x20 | 0x200000 | 0x400000
        status = self._open(ctypes.byref(handle), access, ctypes.byref(attributes),
                            ctypes.byref(status_block), 0x1, options)  # SHARE_READ only
        if status != 0:
            if status >= 0 and handle.value:
                self.close(handle.value)
            self._check_nt(status)
        if not handle.value:
            raise OSError("Native open did not return a handle")
        return handle.value

    def inspect(self, handle, *, directory, max_bytes):
        if self._type(handle) != 1:  # FILE_TYPE_DISK, never a pipe or character device
            raise LedgerError("unsupported_resource", "Only local disk text files are supported")
        info = _FileInformation()
        if not self._info(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.Attributes & (_REPARSE_POINT | _OFFLINE_OR_RECALL):
            raise LedgerError("unsafe_path", "Reparse points and offline resources are unsupported")
        if info.Attributes & _DEVICE or bool(info.Attributes & _DIRECTORY) != directory:
            raise LedgerError("unsupported_resource", "Only ordinary directories and regular text files are supported")
        if not directory and (info.SizeHigh << 32 | info.SizeLow) > max_bytes:
            raise LedgerError("unsupported_resource", "Only regular text files of at most 64 KiB are supported")

    def local_volume(self, handle):
        device = _VolumeDeviceInformation()
        status_block = _IOStatusBlock()
        self._check_nt(self._volume(handle, ctypes.byref(status_block), ctypes.byref(device),
                                    ctypes.sizeof(device), 4))  # FileFsDeviceInformation
        if (status_block.Information != ctypes.sizeof(device) or
                device.Characteristics & 0x10 or device.DeviceType != 7):
            raise LedgerError("unsafe_path", "Only local disk volumes are supported")

    def read(self, handle, limit):
        buffer = ctypes.create_string_buffer(limit)
        count = _U32()
        if not self._read(handle, buffer, limit, ctypes.byref(count), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if count.value > limit:
            raise OSError("Native read exceeded its buffer")
        return buffer.raw[:count.value]

    def close(self, handle):
        if not self._close(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _load_api():
    if os.name != "nt":
        raise LedgerError("unsupported_platform", "Windows native local import is unavailable")
    try:
        return _WindowsAPI()
    except (AttributeError, OSError) as exc:
        raise LedgerError("unsupported_platform", "Required Windows native import APIs are unavailable") from exc


def read_bytes(root, relative, max_bytes):
    """Read bounded bytes through the verified leaf handle, closing every handle."""
    drive, parts = _path_parts(root, relative)
    api = _load_api()
    handles = []
    try:
        native_volume = api.volume_name(drive)
        if not _VOLUME.fullmatch(native_volume):
            raise LedgerError("unsafe_path", "Network, SUBST and nonstandard drive mappings are unsupported")
        anchor = api.open(native_volume + "\\", directory=True)
        handles.append(anchor)
        api.inspect(anchor, directory=True, max_bytes=max_bytes)
        api.local_volume(anchor)
        for index, part in enumerate(parts):
            directory = index < len(parts) - 1
            child = api.open(part, handles[-1], directory=directory)
            handles.append(child)
            api.inspect(child, directory=directory, max_bytes=max_bytes)
        raw = api.read(handles[-1], max_bytes + 1)
        if len(raw) > max_bytes:
            raise LedgerError("resource_limit", "Imported text exceeds the per-file limit")
        return raw
    except OSError as exc:
        raise LedgerError("unsafe_path", "Cannot import selected regular text without following links") from exc
    finally:
        # Attempt every close even if one close fails; never silently suppress a
        # native resource error or abandon the remaining ancestor handles.
        close_error = None
        for handle in reversed(handles):
            try:
                api.close(handle)
            except OSError as exc:
                close_error = exc
        if close_error is not None:
            raise LedgerError("unsafe_path", "Cannot release native local import handles") from close_error
