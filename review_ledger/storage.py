"""Profile-local SQLite, bounded transactions, artifacts, and verified backups."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

from .models import Actor, IDENTIFIER, LedgerError, Scope, canonical, digest, integer, text

SCHEMA_VERSION = 1
MAX_ARTIFACT_BYTES = 64 * 1024
MIN_SQLITE_VERSION = (3, 35, 0)
DEFAULT_JOURNAL_MODE = "delete"
# Recognize the documented backports individually, not every release >=3.44.6.
# https://sqlite.org/wal.html#walreset
WAL_RESET_FIXED_BACKPORTS = frozenset({(3, 44, 6), (3, 50, 7)})


def sqlite_has_wal_reset_fix(version: tuple[int, int, int]) -> bool:
    return version >= (3, 51, 3) or version in WAL_RESET_FIXED_BACKPORTS


def require_wal_runtime() -> None:
    if not sqlite_has_wal_reset_fix(sqlite3.sqlite_version_info):
        version = ".".join(map(str, sqlite3.sqlite_version_info))
        raise LedgerError(
            "unsupported_sqlite_wal",
            f"Existing WAL database requires SQLite >=3.51.3 or the documented "
            f"3.44.6/3.50.7 backports; runtime is {version}. Stop all ledger sessions "
            "and use a supported runtime to back up the database before any offline "
            "journal migration. No automatic journal conversion or runtime upgrade is performed.",
        )


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id(kind: str) -> str:
    return f"{kind}_{uuid4().hex}"


class Store:
    """No connection or filesystem mutation occurs during construction."""

    def __init__(self, data_dir: Path, profile_key: str, *, busy_timeout_ms: int = 2000):
        self.data_dir = Path(data_dir).absolute()
        self.profile_key = text(profile_key, "profile key", 2048)
        self.busy_timeout_ms = max(1, min(busy_timeout_ms, 5000))
        self.path = self.data_dir / "review-ledger.sqlite3"

    def _directory(self, name: str | None = None) -> Path:
        root = self.data_dir
        if root.is_symlink():
            raise LedgerError("unsafe_path", "Ledger data directory must not be a symlink")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination = root if name is None else root / name
        if destination.is_symlink():
            raise LedgerError("unsafe_path", "Ledger subdirectory must not be a symlink")
        destination.mkdir(exist_ok=True, mode=0o700)
        if not destination.resolve().is_relative_to(root.resolve()):
            raise LedgerError("unsafe_path", "Ledger path escapes its data directory")
        return destination

    def _preflight_journal(self):
        """Reject legacy WAL before SQLite can recover/checkpoint it on open/close.

        SQLite header bytes 18 and 19 identify WAL even after sidecars disappear.
        A sidecar is also a conservative refusal signal. This is not a lock:
        operators must stop older clients before upgrading, and must not change
        journal modes externally while any ledger session is running.
        """
        if sqlite_has_wal_reset_fix(sqlite3.sqlite_version_info):
            return
        try:
            with self.path.open("rb") as stream:
                header = stream.read(20)
        except FileNotFoundError:
            header = b""
        if (header.startswith(b"SQLite format 3\x00") and 2 in header[18:20]) or any(
            self.path.with_name(self.path.name + suffix).exists() for suffix in ("-wal", "-shm")
        ):
            require_wal_runtime()

    @staticmethod
    def _journal_mode(conn) -> str:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        if mode == "wal":
            require_wal_runtime()
        return mode

    @contextmanager
    def connect(self):
        self._directory()
        if self.path.is_symlink():
            raise LedgerError("unsafe_path", "Ledger database must not be a symlink")
        if sqlite3.sqlite_version_info < MIN_SQLITE_VERSION:
            raise LedgerError("unsupported_sqlite", "SQLite 3.35 or newer is required")
        self._preflight_journal()
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
            conn.execute("PRAGMA synchronous=FULL")
            self._journal_mode(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise LedgerError("schema_too_new", "Database schema is newer than this plugin; no changes made")
            self._initialize(conn)
            yield conn
        except sqlite3.OperationalError as exc:
            code = "storage_busy" if "locked" in str(exc) or "busy" in str(exc) else "storage_error"
            raise LedgerError(code, "SQLite operation failed; retry a busy operation with the same request key") from exc
        except sqlite3.IntegrityError as exc:
            raise LedgerError("integrity_conflict", "A scoped reference or database invariant was violated") from exc
        finally:
            conn.close()

    def _initialize(self, conn):
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            with self.transaction(conn):
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise LedgerError("schema_too_new", "Database schema is newer than this plugin")
                if version == 0:
                    objects = conn.execute("SELECT name FROM sqlite_master").fetchall()
                    if objects:
                        raise LedgerError("unknown_schema", "Unversioned nonempty database is not a ledger")
                    migration = (Path(__file__).parent / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
                    for statement in migration.split(";"):
                        if statement.strip():
                            conn.execute(statement)
                    conn.execute("INSERT INTO ledger_meta VALUES ('profile_key',?)", (self.profile_key,))
                    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        stored = conn.execute("SELECT value FROM ledger_meta WHERE key='profile_key'").fetchone()
        if not stored or stored[0] != self.profile_key:
            raise LedgerError("profile_mismatch", "This database belongs to a different resolved profile")
        # New/rollback databases explicitly use DELETE. Preserve legacy WAL only
        # on known-fixed runtimes, without attempting a concurrent mode migration.
        # Journal changes remain behind the schema and profile fences above.
        if self._journal_mode(conn) != "wal":
            mode = conn.execute(f"PRAGMA journal_mode={DEFAULT_JOURNAL_MODE}").fetchone()[0]
            if mode.lower() != DEFAULT_JOURNAL_MODE:
                raise LedgerError("unsupported_journal", "Rollback DELETE journal mode is required for this database")

    def begin(self, conn):
        for attempt in range(3):
            try:
                conn.execute("BEGIN IMMEDIATE")
                return
            except sqlite3.OperationalError as exc:
                if ("locked" not in str(exc) and "busy" not in str(exc)) or attempt == 2:
                    raise
                time.sleep(0.02 * (attempt + 1))

    @contextmanager
    def transaction(self, conn):
        """Own one short writer transaction, including rollback on interruption."""
        self.begin(conn)
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

    def write(self, scope: Scope, operation: str, operation_scope: str, key: str,
              payload: dict, fn: Callable, replay_validator: Callable | None = None):
        text(key, "request_key", 128)
        fingerprint = digest(payload)
        with self.connect() as conn, self.transaction(conn):
            self.repository(conn, scope)
            old = conn.execute(
                "SELECT payload_hash,result_json FROM idempotency WHERE repository_id=? AND operation=? AND scope=? AND request_key=?",
                (scope.repository_id, operation, operation_scope, key),
            ).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise LedgerError("idempotency_conflict", "The request key was already used with a different payload")
                if replay_validator is not None:
                    replay_validator(conn)
                return json.loads(old[1])
            result = fn(conn)
            result["receipt_notice"] = "Operation receipt; use ledger_status for current ownership and snapshot state."
            conn.execute("INSERT INTO idempotency VALUES (?,?,?,?,?,?,?)",
                         (scope.repository_id, operation, operation_scope, key, fingerprint, canonical(result), now()))
            return result

    @staticmethod
    def repository(conn, scope: Scope):
        repo = conn.execute("SELECT * FROM repositories WHERE id=? AND name=? COLLATE NOCASE",
                            (scope.repository_id, scope.repository_name)).fetchone()
        if repo is None:
            raise LedgerError("scope_not_found", "Repository is not present in this profile and scope")
        return repo

    @staticmethod
    def run(conn, scope: Scope, run_id: str):
        row = conn.execute("SELECT * FROM runs WHERE repository_id=? AND id=?", (scope.repository_id, run_id)).fetchone()
        if row is None:
            raise LedgerError("scope_not_found", "Run is not present in this repository and profile")
        return row

    @staticmethod
    def owner(conn, scope: Scope, run_id: str, actor: Actor, generation: int):
        integer(generation, "generation", 1, 2**63 - 1)
        run = Store.run(conn, scope, run_id)
        if run["owner_session"] != actor.session_id or run["generation"] != generation:
            raise LedgerError("ownership_conflict", "Current session and ownership generation are required")
        if run["status"] not in ("active", "paused"):
            raise LedgerError("run_not_writable", "Completed or superseded investigations cannot be changed")
        return run

    @staticmethod
    def audit(conn, scope: Scope, entity: str, action: str, actor: str, details: dict):
        conn.execute("INSERT INTO audit_events VALUES (?,?,?,?,?,?,?)",
                     (new_id("event"), scope.repository_id, entity, action, actor, canonical(details), now()))

    def put_artifact(self, content: str) -> str:
        text(content, "artifact content", MAX_ARTIFACT_BYTES)
        raw = content.encode("utf-8")
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise LedgerError("resource_limit", "Artifact exceeds 64 KiB")
        directory = self._directory("artifacts")
        ident = new_id("artifact")
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if directory.is_symlink() or not directory.resolve().is_relative_to(self.data_dir.resolve()):
                raise LedgerError("unsafe_path", "Artifact directory changed during publication")
            os.replace(temporary, directory / ident)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return ident

    def artifact_status(self, ident: str) -> dict:
        if not isinstance(ident, str) or not IDENTIFIER.fullmatch(ident) or not ident.startswith("artifact_"):
            raise LedgerError("invalid_input", "Invalid artifact ID")
        directory = self.data_dir / "artifacts"
        path = directory / ident
        if directory.is_symlink() or path.is_symlink() or not path.resolve().is_relative_to(self.data_dir.resolve()):
            raise LedgerError("unsafe_path", "Artifact path escapes the ledger")
        if not path.is_file():
            return {"id": ident, "state": "missing"}
        return {"id": ident, "state": "present", "bytes": path.stat().st_size}

    def remove_unreferenced_artifact(self, ident: str):
        status = self.artifact_status(ident)
        with self.connect() as conn:
            referenced = conn.execute("SELECT 1 FROM observations WHERE artifact_id=? LIMIT 1", (ident,)).fetchone()
        if referenced is None and status["state"] == "present":
            (self.data_dir / "artifacts" / ident).unlink(missing_ok=True)

    def backup(self) -> dict:
        directory = self._directory("backups")
        ident = new_id("backup")
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=directory)
        os.close(fd)
        try:
            # sqlite3's own context manager commits/rolls back, but never closes.
            # Close both temporary databases before native cleanup or publication.
            with self.connect() as source, closing(sqlite3.connect(temporary)) as destination:
                deadline = time.monotonic() + 10
                def progress(status, remaining, total):
                    if time.monotonic() > deadline:
                        raise LedgerError("backup_timeout", "SQLite backup copy exceeded its ten-second budget; no backup was published")
                source.backup(destination, pages=128, sleep=0.01, progress=progress)
                # The backup API copies WAL header flags. This private copy has
                # no other connections, so publish a standalone rollback file;
                # never change the source database's journal mode here.
                mode = destination.execute(f"PRAGMA journal_mode={DEFAULT_JOURNAL_MODE}").fetchone()[0]
                if mode.lower() != DEFAULT_JOURNAL_MODE:
                    raise LedgerError("backup_invalid", "Backup could not use rollback DELETE journal mode")
            # Restore the produced bytes to an independent temporary location and verify.
            with tempfile.TemporaryDirectory(prefix="ledger-restore-") as restore_dir:
                restored = Path(restore_dir) / "restored.sqlite3"
                shutil.copyfile(temporary, restored)
                with closing(sqlite3.connect(restored)) as check:
                    if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or check.execute("PRAGMA foreign_key_check").fetchone():
                        raise LedgerError("backup_invalid", "Restored backup failed consistency checks")
                    if check.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                        raise LedgerError("backup_invalid", "Restored backup has the wrong schema")
                    if check.execute("PRAGMA journal_mode").fetchone()[0].lower() != DEFAULT_JOURNAL_MODE:
                        raise LedgerError("backup_invalid", "Restored backup has the wrong journal mode")
            final = directory / f"{ident}.sqlite3"
            os.replace(temporary, final)
            return {"state": "backed_up", "path": str(final), "restore_verified": True,
                    "includes": "SQLite ledger only; optional artifacts are not bundled"}
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
