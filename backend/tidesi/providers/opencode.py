from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .. import billing, paths
from ..contract import finite_number
from ..session_index import SkipSource
from ..sqlite_readonly import connect_readonly as _connect_readonly
from ..sqlite_readonly import table_columns as _table_columns

_MAX_ROWS = 60
_QUERY_TIMEOUT_SECONDS = 2.0
# Bounds the per-session usage enrichment as a whole. Sessions past it are
# still listed, just without usage, so a huge database never hides sessions.
_ENRICH_TIMEOUT_SECONDS = 30.0
_REQUIRED_COLUMNS = {"id", "title", "directory", "time_created", "time_updated"}
_MESSAGE_COLUMNS = {"id", "session_id", "data"}
_V2_MESSAGE_COLUMNS = {"id", "session_id", "type", "seq", "data"}
_PART_COLUMNS = {"id", "message_id", "data"}
SQLiteValue = str | int | float | None


@dataclass(frozen=True)
class OpenCodeSession:
    session_id: str
    title: str
    directory: str
    created_at: int
    last_activity: int
    database_path: str
    usage: tuple[billing.UsageBucket, ...] = ()


def _existing_file(value: str) -> str:
    candidate = Path(value).expanduser()
    return str(candidate) if candidate.is_file() else ""


def explicit_database_path(value: str) -> str:
    """Resolve ``OPENCODE_DB`` to an existing file, absolute or relative.

    An absolute path (or one that already exists as given) wins. A relative
    name is searched under every data home, both directly and under its
    ``opencode`` subdirectory, so the same value resolves identically no
    matter the cwd or which caller asks.
    """
    path = _existing_file(value)
    if path:
        return path
    relative = Path(value).expanduser()
    if relative.is_absolute():
        return ""
    for base in paths.data_home_dirs():
        for directory in (Path(base), Path(base) / "opencode"):
            path = _existing_file(str(directory / relative))
            if path:
                return path
    return ""


def _cli_database_path() -> str:
    binary = shutil.which("opencode")
    if not binary:
        return ""
    try:
        result = subprocess.run(
            [binary, "db", "path"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    output = result.stdout.strip().splitlines()
    return _existing_file(output[-1].strip()) if output else ""


def discover_database_paths() -> list[str]:
    explicit = os.environ.get("OPENCODE_DB", "").strip()
    if explicit:
        path = explicit_database_path(explicit)
        return [path] if path else []
    candidates: list[str] = []
    for base in paths.data_home_dirs():
        for directory in (Path(base), Path(base) / "opencode"):
            candidates.extend(str(path) for path in sorted(directory.glob("opencode*.db")) if path.is_file())
    if candidates:
        return list(dict.fromkeys(candidates))
    # Only when the standard data directories hold nothing: `opencode db path`
    # spawns the whole CLI, which is slow and opens the database read-write.
    # That bumps its mtime, so asking every poll made the session index treat
    # OpenCode as changed every time and re-scan it forever.
    cli_path = _cli_database_path()
    return [cli_path] if cli_path else []


def _schema_is_supported(connection: sqlite3.Connection) -> bool:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(session)").fetchall() if len(row) > 1}
    return _REQUIRED_COLUMNS <= columns


def _usage_schema(connection: sqlite3.Connection) -> tuple[bool, bool, bool]:
    message = _table_columns(connection, "message")
    v2_message = _table_columns(connection, "session_message")
    part = _table_columns(connection, "part")
    return _MESSAGE_COLUMNS <= message, _V2_MESSAGE_COLUMNS <= v2_message, _PART_COLUMNS <= part


def _seconds(value: SQLiteValue) -> int:
    number = finite_number(value)
    if number is None:
        return 0
    if number >= 100_000_000_000:
        number /= 1000
    return int(number)


def _text(value: SQLiteValue) -> str:
    return value if type(value) is str else ""


def _provider_id(value: SQLiteValue) -> str:
    provider = _text(value).strip().lower()
    if not provider or len(provider) > 64 or not re.fullmatch(r"[a-z0-9._-]+", provider):
        return ""
    return provider


def _nonnegative(value: SQLiteValue) -> float:
    """A token count from SQLite: anything unusable counts as none."""
    return finite_number(value, minimum=0) or 0


def _aggregate_bucket(row, session_id: str) -> billing.UsageBucket | None:
    provider = _provider_id(row[0])
    model = _text(row[1])
    if not provider or not model:
        return None
    provider_cost = finite_number(row[2], minimum=0)
    input_tokens = _nonnegative(row[3])
    output_tokens = _nonnegative(row[4])
    cache_read_tokens = _nonnegative(row[5])
    cache_write_tokens = _nonnegative(row[6])
    reasoning_tokens = _nonnegative(row[7])
    token_counts = (input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, reasoning_tokens)
    if provider_cost == 0 and sum(token_counts) > 0:
        provider_cost = None
    return billing.UsageBucket(
        provider,
        model,
        session_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        reasoning_tokens=reasoning_tokens,
        provider_cost_usd=provider_cost,
        source="opencode",
    )


def _sum_nonnegative(alias: str, path: str) -> str:
    """SQL fragment: SUM of finite non-negative numbers at `path`, else 0."""
    return (
        f"SUM(CASE WHEN json_type(json_extract({alias}, '{path}')) IN ('integer', 'real') "
        f"AND json_extract({alias}, '{path}') >= 0 "
        f"AND json_extract({alias}, '{path}') <= {sys.float_info.max} "
        f"THEN json_extract({alias}, '{path}') ELSE 0 END)"
    )


def _bucket_condition(alias: str) -> str:
    """SQL fragment: rows that would produce a usage bucket (valid cost or positive tokens)."""
    cost = (
        f"json_type(json_extract({alias}, '$.cost')) IN ('integer', 'real') "
        f"AND json_extract({alias}, '$.cost') >= 0 "
        f"AND json_extract({alias}, '$.cost') <= {sys.float_info.max}"
    )
    tokens = " OR ".join(
        f"(json_type(json_extract({alias}, '$.tokens.{path}')) IN ('integer', 'real') "
        f"AND json_extract({alias}, '$.tokens.{path}') > 0 "
        f"AND json_extract({alias}, '$.tokens.{path}') <= {sys.float_info.max})"
        for path in ("input", "output", "cache.read", "cache.write", "reasoning")
    )
    return f"({cost} OR {tokens})"


def _v1_message_query(include_parts: bool) -> str:
    """V1 assistant-message usage, aggregated per (provider, model).

    Messages whose step-finish part would produce its own bucket are excluded
    only when the part table exists; without it every message is its own
    bucket, matching the pre-aggregation reader.
    """
    dedup = ""
    if include_parts:
        dedup = (
            "  AND NOT EXISTS (\n"
            "      SELECT 1 FROM part AS p\n"
            "      WHERE p.message_id = m.id AND json_valid(p.data)\n"
            "        AND json_extract(p.data, '$.type') = 'step-finish'\n"
            f"        AND {_bucket_condition('p.data')}\n"
            "  )"
        )
    return f"""
SELECT json_extract(m.data, '$.providerID'), json_extract(m.data, '$.modelID'),
       {_sum_nonnegative("m.data", "$.cost")},
       {_sum_nonnegative("m.data", "$.tokens.input")},
       {_sum_nonnegative("m.data", "$.tokens.output")},
       {_sum_nonnegative("m.data", "$.tokens.cache.read")},
       {_sum_nonnegative("m.data", "$.tokens.cache.write")},
       {_sum_nonnegative("m.data", "$.tokens.reasoning")}
FROM message AS m
WHERE m.session_id = ? AND json_valid(m.data) AND json_extract(m.data, '$.role') = 'assistant'
  AND {_bucket_condition("m.data")}{dedup}
GROUP BY json_extract(m.data, '$.providerID'), json_extract(m.data, '$.modelID')
ORDER BY MIN(m.id)
"""


_V2_MESSAGE_QUERY = f"""
SELECT json_extract(data, '$.model.providerID'), json_extract(data, '$.model.id'),
       {_sum_nonnegative("data", "$.cost")},
       {_sum_nonnegative("data", "$.tokens.input")},
       {_sum_nonnegative("data", "$.tokens.output")},
       {_sum_nonnegative("data", "$.tokens.cache.read")},
       {_sum_nonnegative("data", "$.tokens.cache.write")},
       {_sum_nonnegative("data", "$.tokens.reasoning")}
FROM session_message
WHERE session_id = ? AND type = 'assistant' AND json_valid(data) AND {_bucket_condition("data")}
GROUP BY json_extract(data, '$.model.providerID'), json_extract(data, '$.model.id')
ORDER BY MIN(seq)
"""
_V1_PART_QUERY = f"""
SELECT json_extract(m.data, '$.providerID'), json_extract(m.data, '$.modelID'),
       {_sum_nonnegative("p.data", "$.cost")},
       {_sum_nonnegative("p.data", "$.tokens.input")},
       {_sum_nonnegative("p.data", "$.tokens.output")},
       {_sum_nonnegative("p.data", "$.tokens.cache.read")},
       {_sum_nonnegative("p.data", "$.tokens.cache.write")},
       {_sum_nonnegative("p.data", "$.tokens.reasoning")}
FROM part AS p JOIN message AS m ON m.id = p.message_id
WHERE m.session_id = ? AND json_valid(m.data) AND json_valid(p.data)
  AND json_extract(m.data, '$.role') = 'assistant'
  AND json_extract(p.data, '$.type') = 'step-finish'
  AND {_bucket_condition("p.data")}
GROUP BY json_extract(m.data, '$.providerID'), json_extract(m.data, '$.modelID')
ORDER BY MIN(p.id)
"""


def _native_message_filter(connection, alias, import_tables):
    """Exclude imported messages, retaining native continuations of imported sessions.

    Legacy imports without tracked message IDs are excluded as a whole: their
    origin cannot safely be distinguished. Names come only from our reader.
    """
    predicates = []
    for table in import_tables:
        columns = _table_columns(connection, table)
        if not columns:
            continue
        if "message_ids" in columns:
            imported = (
                "CASE WHEN json_valid(i.message_ids) THEN "
                f"(COALESCE(json_array_length(i.message_ids), 0) = 0 OR {alias}.id IN "
                "(SELECT value FROM json_each(i.message_ids))) ELSE 1 END"
            )
        else:
            imported = "1"
        predicates.append(f"NOT EXISTS (SELECT 1 FROM {table} i WHERE i.session_id = {alias}.session_id AND ({imported}))")
    return " AND ".join(predicates) or "1"


def _read_usage(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    import_tables=(),
    require_complete: bool = False,
    timeout_seconds: float | None = None,
) -> tuple[billing.UsageBucket, ...]:
    v1_message, v2_message, v1_part = _usage_schema(connection)
    if not (v1_message or v2_message):
        if require_complete and (_table_columns(connection, "message") or _table_columns(connection, "session_message")):
            raise SkipSource("OpenCode usage scan incomplete: unsupported usage schema")
        return ()
    deadline = time.monotonic() + (_QUERY_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds)

    def query(sql: str) -> tuple[bool, list]:
        if import_tables:
            alias = "session_message" if "FROM session_message" in sql else "m"
            predicate = _native_message_filter(connection, alias, import_tables)
            sql = sql.replace("\nGROUP BY", f" AND ({predicate})\nGROUP BY")
        if time.monotonic() >= deadline:
            if require_complete:
                raise SkipSource("OpenCode usage scan incomplete: query deadline exceeded")
            return False, []
        try:
            return True, connection.execute(sql, (session_id,)).fetchall()
        except sqlite3.Error as error:
            if require_complete:
                raise SkipSource("OpenCode usage scan incomplete: query did not complete") from error
            return False, []

    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        v2_ok, v2_rows = query(_V2_MESSAGE_QUERY) if v2_message else (True, [])
        if not v2_ok:
            return ()
        v2_buckets = tuple(bucket for bucket in (_aggregate_bucket(row, session_id) for row in v2_rows) if bucket is not None)
        if v2_buckets:
            return v2_buckets
        v1_ok, message_rows = query(_v1_message_query(v1_part)) if v1_message else (True, [])
        part_ok, part_rows = query(_V1_PART_QUERY) if v1_part and v1_message else (True, [])
        if not v1_ok or not part_ok:
            return ()
    finally:
        connection.set_progress_handler(None, 0)

    message_buckets = tuple(bucket for bucket in (_aggregate_bucket(row, session_id) for row in message_rows) if bucket is not None)
    part_buckets = tuple(bucket for bucket in (_aggregate_bucket(row, session_id) for row in part_rows) if bucket is not None)
    return message_buckets + part_buckets


def _read_database(database_path: str, *, include_all: bool = False, import_tables=()) -> list[OpenCodeSession]:
    try:
        with _connect_readonly(database_path) as connection:
            if not _schema_is_supported(connection):
                return []
            query = "SELECT id, title, directory, time_created, time_updated FROM session"
            if import_tables:
                known_tables = [table for table in import_tables if _table_columns(connection, table)]
                if known_tables:
                    imported = " OR ".join(f"EXISTS (SELECT 1 FROM {table} i WHERE i.session_id = session.id)" for table in known_tables)
                    native = []
                    for table in ("message", "session_message"):
                        if not _table_columns(connection, table):
                            continue
                        predicate = _native_message_filter(connection, "m", import_tables)
                        role = "m.type = 'assistant'" if table == "session_message" else "json_extract(m.data, '$.role') = 'assistant'"
                        native.append(f"EXISTS (SELECT 1 FROM {table} m WHERE m.session_id = session.id AND {role} AND ({predicate}))")
                    query += f" WHERE NOT ({imported}) OR ({' OR '.join(native) or '0'})"
            query += " ORDER BY time_updated DESC"
            if include_all:
                rows = connection.execute(query).fetchall()
            else:
                rows = connection.execute(query + " LIMIT ?", (_MAX_ROWS,)).fetchall()
            usage_by_session = {}
            if include_all:
                from .opencode_usage_cache import read_usage

                usage_by_session = read_usage(connection, database_path, [_text(row[0]) for row in rows], import_tables)
            enrich_deadline = time.monotonic() + _ENRICH_TIMEOUT_SECONDS
            for row in () if include_all else rows:
                if time.monotonic() >= enrich_deadline:
                    break
                session_id = _text(row[0])
                if session_id:
                    usage_by_session[session_id] = (
                        _read_usage(connection, session_id, import_tables=import_tables) if import_tables else _read_usage(connection, session_id)
                    )
    except (OSError, sqlite3.Error, ValueError):
        return []

    records = []
    for row in rows:
        session_id = _text(row[0])
        directory = _text(row[2])
        if not session_id or not directory:
            continue
        records.append(
            OpenCodeSession(
                session_id=session_id,
                title=_text(row[1]),
                directory=directory,
                created_at=_seconds(row[3]),
                last_activity=_seconds(row[4]),
                database_path=database_path,
                usage=usage_by_session.get(session_id, ()),
            )
        )
    return records


def read_recent_sessions(*, include_all: bool = False) -> list[OpenCodeSession]:
    records: list[OpenCodeSession] = []
    for database_path in discover_database_paths():
        records.extend(_read_database(database_path, include_all=include_all))
    return records


def read_session_targets(*, include_all: bool = True) -> list[OpenCodeSession]:
    return read_recent_sessions(include_all=include_all)


def usage_snapshot(*, records=None) -> dict:
    """Return the complete local usage ledger in a JSON-shaped structure.

    OpenCode stores the provider-reported cost beside token counts.  When a
    provider does not store cost, the shared catalog can estimate it by the
    exact provider/model pair.  Keeping both the bucket and its pricing
    provenance here lets the normalizer explain partial or unavailable costs
    instead of turning them into a misleading zero.
    """
    try:
        from .. import pricing

        pricing.load_catalog()
        catalog = pricing.cached_catalog()
    except Exception:  # a stale/offline catalog must not hide local usage
        catalog = {}

    sessions = []
    for record in read_recent_sessions(include_all=True) if records is None else records:
        buckets = []
        for bucket in record.usage:
            priced = billing.aggregate_session_usage([bucket], catalog)
            row = {
                "provider": bucket.provider,
                "model": bucket.model,
                "input": bucket.input_tokens,
                "output": bucket.output_tokens,
                "cacheRead": bucket.cache_read_tokens,
                "cacheWrite": bucket.cache_write_tokens,
                "reasoning": bucket.reasoning_tokens,
                "costUSD": priced.get("costUSD"),
                "costStatus": priced.get("costStatus", "unavailable"),
                "costProvenance": priced.get("costProvenance", ""),
            }
            if "costBreakdown" in priced:
                row["costBreakdown"] = priced["costBreakdown"]
            buckets.append(row)
        session_cost = billing.aggregate_session_usage(record.usage, catalog)
        sessions.append(
            {
                "id": record.session_id,
                "title": record.title,
                "directory": record.directory,
                "createdAt": record.created_at,
                "lastActivity": record.last_activity,
                "usage": buckets,
                "costUSD": session_cost.get("costUSD"),
                "costStatus": session_cost.get("costStatus", "unavailable"),
                "costProvenance": session_cost.get("costProvenance", ""),
            }
        )
    return {"sessions": sessions}
