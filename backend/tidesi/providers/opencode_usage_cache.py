"""Cache numeric usage by database/WAL state; never cache model-price guesses."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from .. import billing, config, usage_cache
from ..session_index import SkipSource
from . import opencode

_SUMMARY_FIELDS = ("cost", "tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_write", "tokens_reasoning")


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _state(database: str, parser: str, import_tables) -> str:
    metadata = usage_cache.database_state(database)
    if metadata is None:
        raise SkipSource("OpenCode usage scan incomplete: database unreadable")
    return _hash(json.dumps((parser, metadata, import_tables)))


def _signatures(connection: sqlite3.Connection, state: str, import_tables) -> dict[str, str]:
    journal = {"aggregate_id", "seq"} <= opencode._table_columns(connection, "event_sequence")
    if not import_tables and journal and set(_SUMMARY_FIELDS) <= opencode._table_columns(connection, "session"):
        sequences = dict(connection.execute("SELECT aggregate_id, seq FROM event_sequence"))
        fields = ", ".join((*_SUMMARY_FIELDS, "time_created", "time_updated"))
        return {row[0]: _hash(json.dumps((row[1:], sequences.get(row[0])))) for row in connection.execute("SELECT id, " + fields + " FROM session")}
    return {row[0]: state for row in connection.execute("SELECT id FROM session")}


def read_usage(connection: sqlite3.Connection, database: str, session_ids: list[str], import_tables=()):
    deadline = time.monotonic() + opencode._ENRICH_TIMEOUT_SECONDS
    key = _hash(str(Path(database).resolve()) + json.dumps(import_tables))
    cache = Path(config.cache_dir()) / f"opencode-usage-{key}.json"
    source = usage_cache.source_fingerprint(opencode.__file__, __file__)
    state = _state(database, source, import_tables)
    stat = os.stat(database)
    # Entries survive database writes (each session has its own signature), but
    # not a parser change or a different file at the same path.
    parser = _hash(source + str((stat.st_dev, stat.st_ino)))
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cached = None
    entries = cached.get("entries", {}) if isinstance(cached, dict) and cached.get("parser") == parser else {}
    connection.execute("BEGIN")
    signatures = _signatures(connection, state, import_tables)
    result = {}
    incomplete = False
    changed = False
    try:
        for session_id in session_ids:
            session_key = _hash(session_id)
            signature = signatures.get(session_id, state)
            entry = entries.get(session_key, {})
            if entry.get("signature") != signature:
                entry = {}
            if "usage" in entry:
                result[session_id] = tuple(billing.UsageBucket(row[0], row[1], session_id, *row[2:]) for row in entry["usage"])
                continue
            if entry.get("retryAt", 0) > time.time():
                incomplete = True
                continue
            if time.monotonic() >= deadline:
                raise SkipSource("OpenCode usage scan incomplete; completed sessions cached for the next refresh")
            failures = entry.get("failures", 0)
            timeout = min(120, opencode._QUERY_TIMEOUT_SECONDS * 2 ** min(failures, 6))
            try:
                buckets = opencode._read_usage(connection, session_id, import_tables=import_tables, require_complete=True, timeout_seconds=timeout)
            except SkipSource:
                entries[session_key] = {"signature": signature, "failures": failures + 1, "retryAt": time.time() + 60}
                incomplete = True
            else:
                entries[session_key] = {
                    "signature": signature,
                    "usage": [[bucket.provider, bucket.model, *bucket[3:]] for bucket in buckets],
                }
                result[session_id] = buckets
            changed = True
        if incomplete:
            raise SkipSource("OpenCode usage scan incomplete; retrying uncached sessions after a cooldown")
    finally:
        if changed:
            active = {_hash(session_id) for session_id in session_ids}
            usage_cache.write_private_json(cache, {"parser": parser, "entries": {key: entry for key, entry in entries.items() if key in active}})
    return result
