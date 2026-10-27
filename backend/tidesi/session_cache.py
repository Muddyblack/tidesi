"""Persistent cache boundary for the merged session listing."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from . import config
from .session_index import (
    SessionIndex,
    SessionQueryResult,
    SessionRow,
    SessionSource,
    SkipSource,
    redact_rows,
)
from .session_manifest import build_manifests

CollectorBatch = tuple[list[Mapping[str, str | int]], bool]
Collector = Callable[[], CollectorBatch]
# Rows for one named source; raising means "keep what the index already has".
SourceCollector = Callable[[str], Sequence[Mapping[str, str | int]]]


class SessionCacheResult(SessionQueryResult, total=False):
    cacheStatus: str
    cacheAgeSeconds: int | None
    refreshStatus: str
    removedSourceCount: int


class IncompleteRefresh(Exception):
    """Carry the direct result when one collector could not refresh."""

    def __init__(self, rows: list[Mapping[str, str | int]]) -> None:
        super().__init__("session collector refresh was incomplete")
        self.rows = rows


class SessionCache:
    """Refresh and query one persistent merged redacted session listing."""

    def __init__(self) -> None:
        self._cache_path = Path(config.cache_dir()) / "sessions.sqlite3"
        self._had_cache = self._cache_path.exists()
        self._index = SessionIndex(self._cache_path)
        self.refresh_status = "not-run"
        self.removed_sources = 0

    def query(
        self,
        query: str = "",
        limit: int | None = None,
        offset: int = 0,
        source_ids: Sequence[str] | None = None,
    ) -> SessionCacheResult:
        """Return a page from the existing index without scanning session stores."""
        page_limit = 60 if limit is None else limit
        if not self._had_cache:
            return {
                "updatedAt": 0,
                "sessions": [],
                "total": 0,
                "totalExact": False,
                "offset": offset,
                "limit": page_limit,
                "hasMore": False,
                "sources": [],
                "cacheStatus": "no-cache",
                "cacheAgeSeconds": None,
            }
        try:
            result: SessionCacheResult = self._index.query(
                query,
                limit=page_limit,
                offset=offset,
                source_ids=source_ids,
            )
        except (OSError, sqlite3.Error):
            return self._empty_result(offset, page_limit, "failed")
        age = self._cache_age()
        result["cacheAgeSeconds"] = age
        source_count, incomplete = self._source_state()
        if source_count == 0:
            result["cacheStatus"] = "no-cache"
            # Other cache readers can create the schema before any collector
            # runs. Its fresh file timestamp is not evidence of a session scan.
            result["cacheAgeSeconds"] = None
            result["totalExact"] = False
        elif result["total"] == 0:
            result["cacheStatus"] = "empty"
        else:
            result["cacheStatus"] = "stale" if age >= 600 else "ready"
        result["refreshStatus"] = "incomplete" if incomplete else self.refresh_status
        result["removedSourceCount"] = self.removed_sources
        return result

    def rows(self) -> list[SessionRow]:
        """Every indexed row, unpaged — spend rollups must never sum a page."""
        try:
            return self._index.rows()
        except (OSError, sqlite3.Error):
            return []

    def spend_groups(self) -> dict | None:
        """The four local-spend groups from the materialized contribution table.

        None when there is no cache or the cache was built by different code —
        the envelope then falls back to reading every row.
        """
        if not self._had_cache:
            return None
        try:
            return self._index.spend_groups()
        except (OSError, sqlite3.Error):
            return None

    def refresh(self, collect: Collector, collect_source: SourceCollector | None = None) -> list[SessionRow] | None:
        """Reconcile the index per source, returning direct rows only when the
        index itself cannot be used.

        Each collector is fingerprinted on its own, so a poll re-parses only the
        stores that actually changed. A collector that raises keeps its previous
        rows instead of blanking them.
        """
        if collect_source is None:
            return self._refresh_all(collect)

        before = self.query(limit=1)
        failed = False

        def parse(source: SessionSource) -> Sequence[Mapping[str, str | int]]:
            nonlocal failed
            try:
                return collect_source(source.source_id)
            except Exception:
                failed = True
                raise SkipSource(source.source_id) from None

        try:
            self._index.reconcile(build_manifests(), parse)
        except (OSError, sqlite3.Error):
            self.refresh_status = "failed"
            return self._direct(collect)
        self._had_cache = self._cache_path.exists()
        if not failed:
            self._mark_fresh()
        after = self.query(limit=1)
        self.removed_sources = len({source["id"] for source in before["sources"]} - {source["id"] for source in after["sources"]})
        self.refresh_status = "incomplete" if failed else "removed-source" if self.removed_sources else "refreshed"
        return None

    def _refresh_all(self, collect: Collector) -> list[SessionRow] | None:
        """Single-source fallback for callers with no per-source collector."""
        scanned: list[Mapping[str, str | int]] = []

        def parse(_source: SessionSource) -> Sequence[Mapping[str, str | int]]:
            rows, complete = collect()
            scanned[:] = rows
            if not complete:
                raise IncompleteRefresh(rows)
            return rows

        try:
            self._index.reconcile(build_manifests(), parse)
        except IncompleteRefresh as error:
            self.refresh_status = "incomplete"
            return redact_rows(error.rows)
        except (OSError, sqlite3.Error):
            self.refresh_status = "failed"
            if scanned:
                return redact_rows(scanned)
            return self._direct(collect)
        self._had_cache = self._cache_path.exists()
        self._mark_fresh()
        self.refresh_status = "refreshed"
        return None

    def _direct(self, collect: Collector) -> list[SessionRow]:
        """The index is unusable, so querying it would show an empty tab."""
        rows, _complete = collect()
        return redact_rows(rows)

    def _mark_fresh(self) -> None:
        """Restart the cache age after a complete scan. A scan that found
        nothing changed writes nothing to SQLite, so the file mtime alone would
        keep reporting the old age and every client would rescan right away."""
        try:
            os.utime(self._cache_path)
        except OSError:
            pass

    def _cache_age(self) -> int:
        mtimes: list[float] = []
        for suffix in ("", "-wal"):
            try:
                mtimes.append(os.stat(str(self._cache_path) + suffix).st_mtime)
            except OSError:
                continue
        return max(0, int(time.time() - max(mtimes))) if mtimes else 0

    def _source_state(self) -> tuple[int, bool]:
        try:
            connection = sqlite3.connect(f"{self._cache_path.resolve().as_uri()}?mode=ro", uri=True)
        except sqlite3.Error:
            return 0, False
        try:
            row = connection.execute("SELECT SUM(size >= 0), MAX(size < 0) FROM source_meta").fetchone()
            return (int(row[0] or 0), bool(row[1])) if row is not None else (0, False)
        except sqlite3.Error:
            return 0, False
        finally:
            connection.close()

    @staticmethod
    def _empty_result(offset: int, limit: int, status: str) -> SessionCacheResult:
        return {
            "updatedAt": 0,
            "sessions": [],
            "total": 0,
            "totalExact": False,
            "offset": offset,
            "limit": limit,
            "hasMore": False,
            "sources": [],
            "cacheStatus": status,
            "cacheAgeSeconds": None,
        }
