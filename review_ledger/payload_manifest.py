"""Canonical, complete native-plugin payload, shared by builds and installation.

Keep this inventory explicit: discovering only the files that happen to exist
cannot detect an incomplete checkout or a damaged installed wheel. New runtime
modules and resources must be added here in the same source change.
"""
from __future__ import annotations

from pathlib import Path
import stat


PAYLOAD_FILES = (
    "__init__.py",
    "plugin.yaml",
    "review_ledger/__init__.py",
    "review_ledger/__main__.py",
    "review_ledger/compression.py",
    "review_ledger/context.py",
    "review_ledger/critic.py",
    "review_ledger/critic_contract.py",
    "review_ledger/critic_hermes.py",
    "review_ledger/evaluation.py",
    "review_ledger/github.py",
    "review_ledger/improvements.py",
    "review_ledger/installer.py",
    "review_ledger/learning.py",
    "review_ledger/models.py",
    "review_ledger/operator.py",
    "review_ledger/payload_manifest.py",
    "review_ledger/protocol.py",
    "review_ledger/references.py",
    "review_ledger/reports.py",
    "review_ledger/service.py",
    "review_ledger/skills.py",
    "review_ledger/storage.py",
    "review_ledger/token_count.py",
    "review_ledger/tools.py",
    "review_ledger/usage.py",
    "review_ledger/windows_import.py",
    "review_ledger/migrations/001_initial.sql",
    "review_ledger/migrations/002_query_indexes.sql",
    "review_ledger/migrations/003_adaptive_context.sql",
    "review_ledger/migrations/004_critic.sql",
    "review_ledger/migrations/005_review_references.sql",
    "review_ledger/prompts/critic_v1.md",
    "review_ledger/resources/protocol.md",
    "skills/review-ledger/SKILL.md",
)


class PayloadError(ValueError):
    """Source/package contents do not match the canonical plugin payload."""


def _plain(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise PayloadError(f"Refusing a symbolic link or junction in plugin payload: {path}")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise PayloadError(f"Expected a regular {'directory' if directory else 'file'} in plugin payload: {path}")


def _files(directory: Path, suffix: str):
    # pathlib recursive glob can follow Windows junctions. Validate every
    # directory before listing it, and every child before descending into it.
    _plain(directory, directory=True)
    for child in directory.iterdir():
        info = child.lstat()
        is_directory = stat.S_ISDIR(info.st_mode)
        if child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction()):
            raise PayloadError(f"Refusing a symbolic link or junction in plugin payload: {child}")
        if is_directory:
            yield from _files(child, suffix)
        elif child.suffix == suffix:
            _plain(child)
            yield child


def read_payload(root: Path) -> dict[str, bytes]:
    """Read one validated snapshot for both copies shipped in a wheel."""
    _plain(root, directory=True)
    expected = set(PAYLOAD_FILES)
    missing = sorted(name for name in expected if not (root / name).exists())
    if missing:
        raise PayloadError("Incomplete plugin payload; missing " + ", ".join(missing))
    discovered = {"__init__.py", "plugin.yaml"}
    discovered.update(p.relative_to(root).as_posix() for p in _files(root / "review_ledger", ".py"))
    for directory, suffix in (("review_ledger/migrations", ".sql"),
                              ("review_ledger/prompts", ".md"),
                              ("review_ledger/resources", ".md"),
                              ("skills", ".md")):
        discovered.update(p.relative_to(root).as_posix() for p in _files(root / directory, suffix))
    if unexpected := sorted(discovered - expected):
        raise PayloadError("Unrecognized plugin payload files; update the canonical inventory: " + ", ".join(unexpected))
    payload = {}
    for name in sorted(expected):
        path = root / name
        for parent in path.parents:
            _plain(parent, directory=True)
            if parent == root:
                break
        _plain(path)
        payload[name] = path.read_bytes()
    return payload
