"""SQLite connection and schema recovery for the session index."""

from __future__ import annotations

import functools
import hashlib
import os
import re
import sqlite3
from pathlib import Path
from typing import Final

_BUSY_TIMEOUT_MS: Final = 5_000

# Nix substitutes absolute store paths into a couple of provider modules (the
# libsecret `secret-tool` pin). Those paths move whenever nixpkgs does, so a
# raw byte hash would invalidate the whole cache on a redeploy that changed no
# parser at all. The fingerprint describes the code, so a store executable
# collapses to the bare command it was before packaging.
_STORE_EXE_RE: Final = re.compile(rb"/nix/store/[a-z0-9]{32}-[^/\s\"']+/bin/([A-Za-z0-9_.+-]+)")
_STORE_PATH_RE: Final = re.compile(rb"/nix/store/[a-z0-9]{32}-[^/\s\"']+")


def _normalized_source(data: bytes) -> bytes:
    return _STORE_PATH_RE.sub(b"<store>", _STORE_EXE_RE.sub(rb"\1", data))


# Cached rows are the *product* of the code that parsed and priced them, so
# they have to be discarded whenever that code changes — a better title or a
# corrected rate must not be stuck behind a store whose files never changed.
# That used to be a hand-incremented integer, which meant every such fix
# depended on remembering to bump it. Instead the cache key is derived from
# the code itself: change any of these modules and every row re-collects on
# the next reconcile, with nothing to remember.
_CONTENT_SOURCES: Final = (
    "sessions.py",  # the collectors: what a row *is*
    "billing.py",  # what a row costs
    "pricing.py",  # the rates that cost is computed against
    "billing_mode.py",  # metered vs. plan-covered
    "session_index.py",  # how rows are stored and read back
    "session_index_storage.py",  # cache schema and invalidation behavior
)
_CONTENT_PACKAGES: Final = ("providers",)


@functools.cache
def _schema_version() -> int:
    """A fingerprint of the code that produces cached rows.

    SQLite's ``user_version`` is a signed 32-bit int, so the digest is
    truncated to 31 bits. Collisions would only mean a missed invalidation,
    and at 31 bits that is not a practical concern.
    """
    here = Path(__file__).resolve().parent
    digest = hashlib.blake2b(digest_size=8)
    paths = [here / name for name in _CONTENT_SOURCES]
    for package in _CONTENT_PACKAGES:
        paths.extend(sorted((here / package).glob("*.py")))
    for path in paths:
        try:
            digest.update(_normalized_source(path.read_bytes()))
        except OSError:
            # A module that cannot be read is simply not part of the key;
            # a failure here must never stop the index from opening.
            digest.update(b"\0")
    return int.from_bytes(digest.digest()[:4], "big") & 0x7FFF_FFFF


def _is_corrupt(error: sqlite3.DatabaseError) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in (
            "not a database",
            "database disk image is malformed",
            "malformed database",
            "unsupported file format",
            "session index schema is invalid",
        )
    )


def _try_wal(connection: sqlite3.Connection) -> str:
    try:
        result = connection.execute("PRAGMA journal_mode=WAL").fetchone()
    except sqlite3.DatabaseError as error:
        if _is_corrupt(error):
            raise
        return "unsupported"
    return str(result[0]) if result else "unsupported"


def _discard_stale_rows(connection: sqlite3.Connection) -> None:
    """Throw away an index built by different code so every source re-collects.

    The tables are dropped rather than emptied: the cache is pure derived
    data, so rebuilding it costs one re-collect and removes the need for
    migration logic entirely — a changed column list is handled by the same
    path as a changed parser, with nothing to write and nothing to get wrong.
    """
    row = connection.execute("PRAGMA user_version").fetchone()
    stored = int(row[0]) if row else 0
    version = _schema_version()
    if stored == version:
        return
    connection.execute("DROP TABLE IF EXISTS session_rows")
    connection.execute("DROP TABLE IF EXISTS source_meta")
    connection.execute("DROP TABLE IF EXISTS session_cost_groups")
    # The DROPs opened an implicit transaction. Close it before touching
    # user_version: a PRAGMA write inside a transaction does not stick, and
    # leaving one open makes reconcile's BEGIN IMMEDIATE fail.
    connection.commit()
    connection.execute(f"PRAGMA user_version = {version}")
    connection.commit()


def _ensure_schema(connection: sqlite3.Connection, *, discard_stale: bool = False) -> None:
    # Before the CREATEs, not after: a fingerprint mismatch drops the tables,
    # and they are then recreated below with whatever columns this build
    # declares. That is the whole migration story.
    if discard_stale:
        _discard_stale_rows(connection)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS source_meta (source_key TEXT PRIMARY KEY, "
        "mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL, "
        "source_order INTEGER NOT NULL DEFAULT 0)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS session_rows (source_key TEXT NOT NULL, "
        "row_order INTEGER NOT NULL, provider TEXT NOT NULL, title TEXT NOT NULL, "
        "session_name TEXT NOT NULL, state TEXT NOT NULL, "
        "last_activity_at INTEGER NOT NULL, detail TEXT NOT NULL, "
        "open_key TEXT NOT NULL DEFAULT '', "
        "full_title TEXT NOT NULL DEFAULT '', "
        "source TEXT NOT NULL DEFAULT '', "
        "cost_usd REAL, "
        "cost_status TEXT NOT NULL DEFAULT 'unavailable', "
        "cost_provenance TEXT, "
        "cost_breakdown TEXT, "
        "billing_provider TEXT NOT NULL DEFAULT '', "
        "provider_costs TEXT, "
        "cost_billing TEXT NOT NULL DEFAULT 'api', "
        "tokens INTEGER NOT NULL DEFAULT 0, "
        "billing_model TEXT NOT NULL DEFAULT '', "
        "PRIMARY KEY (source_key, row_order))"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_session_rows_activity ON session_rows (last_activity_at DESC)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_session_rows_provider ON session_rows (provider)")
    # Materialized local-spend contributions, one row per (session, billing,
    # provenance, rollup). No primary key: a session row can contribute more
    # than once per provenance (multi-provider OpenCode sessions).
    connection.execute(
        "CREATE TABLE IF NOT EXISTS session_cost_groups (source_key TEXT NOT NULL, "
        "row_order INTEGER NOT NULL, billing TEXT NOT NULL, provenance TEXT NOT NULL, "
        "rollup TEXT NOT NULL, cost REAL NOT NULL, status TEXT NOT NULL, "
        "day TEXT NOT NULL DEFAULT '', token_share REAL NOT NULL DEFAULT 0)"
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_session_cost_groups_source ON session_cost_groups (source_key)")
    required = {
        "source_meta": {"source_key", "mtime_ns", "size", "source_order"},
        "session_rows": {
            "source_key",
            "row_order",
            "provider",
            "title",
            "session_name",
            "state",
            "last_activity_at",
            "detail",
            "open_key",
            "full_title",
            "source",
            "cost_usd",
            "cost_status",
            "cost_provenance",
            "cost_breakdown",
            "billing_provider",
            "provider_costs",
            "cost_billing",
            "tokens",
            "billing_model",
        },
        "session_cost_groups": {
            "source_key",
            "row_order",
            "billing",
            "provenance",
            "rollup",
            "cost",
            "status",
            "day",
            "token_share",
        },
    }
    for table, columns in required.items():
        actual = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if not columns.issubset(actual):
            raise sqlite3.DatabaseError("session index schema is invalid")


def _secure_permissions(cache_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.chmod(str(cache_path) + suffix, 0o600)
        except OSError:
            pass


def open_index(cache_path: Path, *, discard_stale: bool = False) -> sqlite3.Connection:
    """Open a writable index connection, replacing a corrupt database.

    ``discard_stale`` drops rows produced by an older parser. Only a reconcile
    passes it: a read doing the wipe would blank the list and leave it blank
    until the next poll, which is exactly what the user sees on screen.
    """
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(cache_path), timeout=5.0)
        _secure_permissions(cache_path)
        connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("casefold", 1, str.casefold)
        _try_wal(connection)
        _secure_permissions(cache_path)
        _ensure_schema(connection, discard_stale=discard_stale)
    except sqlite3.DatabaseError as error:
        if connection is not None:
            connection.close()
        if not _is_corrupt(error):
            raise
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(cache_path) + suffix).unlink()
            except FileNotFoundError:
                continue
        connection = sqlite3.connect(str(cache_path), timeout=5.0)
        _secure_permissions(cache_path)
        connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function("casefold", 1, str.casefold)
        _try_wal(connection)
        _secure_permissions(cache_path)
        _ensure_schema(connection, discard_stale=discard_stale)
    return connection
