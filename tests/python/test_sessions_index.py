"""Persistent, redacted session-index contract tests.

The proposed internal API is ``SessionIndex(cache_path)`` with
``reconcile(sources, parser)`` and ``query(query, limit, offset)``.  The
implementation must remain behind this seam; CLI and frontend contracts are
not part of this module.
"""

import hashlib
import importlib
import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from unittest import mock

from _support import REPO  # noqa: F401  (ensures TOOLS is on sys.path)


@dataclass(frozen=True, slots=True)
class Source:
    path: str
    source_id: str
    mtime_ns: int
    size: int


Row = dict[str, str | int]
Parser = Callable[[Source], list[Row]]


def _index_class() -> type:
    try:
        module = importlib.import_module("tidesi.session_index")
    except ModuleNotFoundError as error:
        if error.name != "tidesi.session_index":
            raise
        raise AssertionError("the approved SessionIndex implementation is not available yet") from error
    return module.SessionIndex


def _source(root: Path, name: str, source_id: str, version: int = 1) -> Source:
    path = root / name
    content = f"transcript-{source_id}-v{version}"
    path.write_text(content, encoding="utf-8")
    return Source(str(path), source_id, version, len(content))


def _parser(calls: list[str]) -> Parser:
    def parse(source: Source) -> list[Row]:
        calls.append(source.source_id)
        return [
            {
                "provider": "test",
                "title": f"Session {source.source_id}",
                "sessionName": "Fixture",
                "state": "idle",
                "lastActivityAt": source.mtime_ns,
                "detail": f"detail {source.source_id}",
                "openKey": "opaque-key",
            }
        ]

    return parse


def _connection(
    stored: list[tuple[str, int, int]],
    checkpoints: list[str],
    events: list[str],
    cached_rows: dict[str, int] | None = None,
) -> mock.MagicMock:
    connection = mock.MagicMock(spec=sqlite3.Connection)
    connection.__enter__.return_value = connection
    # Default: each stored source already has rows cached. A source cached
    # with zero rows is deliberately re-parsed even when its fingerprint
    # matches, so tests about "unchanged" sources have to look non-empty.
    counts = {row[0]: 1 for row in stored} if cached_rows is None else cached_rows

    def execute(statement: str, *parameters) -> list[tuple[str, int, int, int]]:
        if statement.startswith("SELECT source_key, mtime_ns, size"):
            # source_order matches the scan order, so an unchanged manifest
            # stays unmutated.
            return [(*row, order) for order, row in enumerate(stored)]
        if statement.startswith("SELECT source_key, COUNT(*)"):
            return list(counts.items())
        if statement.startswith("PRAGMA wal_checkpoint("):
            checkpoints.append(statement.partition("(")[2][:-1])
            events.append(f"checkpoint:{checkpoints[-1]}")
        return []

    connection.execute.side_effect = execute
    connection.commit.side_effect = lambda: events.append("commit")
    connection.__exit__.side_effect = lambda error_type, _error, _traceback: events.append("commit") if error_type is None else None
    return connection


class SessionIndexTest(unittest.TestCase):
    def test_source_filtering_returns_canonical_descriptors_and_exact_page_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            source_ids = ("antigravity", "openai", "cline", "opencode", "claude", "zed")
            sources = [_source(root, f"{source_id}.jsonl", source_id, index + 1) for index, source_id in enumerate(source_ids)]

            def parse(source: Source) -> list[Row]:
                return [
                    {
                        "provider": source.source_id,
                        "title": f"Safe {source.source_id} preview",
                        "sessionName": "Fixture",
                        "state": "idle",
                        "lastActivityAt": source.mtime_ns,
                        "detail": "safe detail",
                        "openKey": "opaque-key",
                    }
                ]

            index = _index_class()(cache)
            index.reconcile(sources, parse)
            result = index.query("preview", source_ids=["opencode", "openai"], limit=1, offset=1)

        self.assertEqual(
            result["sources"],
            [
                {"id": "cline", "label": "Cline"},
                {"id": "openai", "label": "Codex"},
                {"id": "claude", "label": "Claude Code"},
                {"id": "opencode", "label": "OpenCode"},
                {"id": "antigravity", "label": "Antigravity"},
                {"id": "zed", "label": "Zed Editor"},
            ],
        )
        self.assertEqual([row["provider"] for row in result["sessions"]], ["openai"])
        self.assertEqual(result["total"], 2)
        self.assertTrue(result["totalExact"])
        self.assertEqual(result["offset"], 1)
        self.assertEqual(result["limit"], 1)
        self.assertFalse(result["hasMore"])

    def test_source_metadata_and_rows_exclude_private_fixture_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root, "private-transcript.jsonl", "raw-session-id")
            index = _index_class()(root / "sessions.sqlite3")
            index.reconcile(
                [source],
                lambda _source: [
                    {
                        "provider": "openai",
                        "title": "Safe preview",
                        "sessionName": "Codex",
                        "state": "idle",
                        "lastActivityAt": 1,
                        "detail": "Safe detail",
                        "openKey": "opaque-key",
                        "fullTitle": "safe expandable title",
                        "path": "/private/session/path",
                        "rawId": "raw-session-id",
                        "transcript": "private transcript body",
                        "credential": "secret-token",
                    }
                ],
            )
            result = index.query(source_ids=[])

        encoded = json.dumps(result, sort_keys=True)
        for private_value in (
            "/private/session/path",
            "raw-session-id",
            "private transcript body",
            "secret-token",
            "opaque-key",
        ):
            self.assertNotIn(private_value, encoded)
        # fullTitle is an intentionally exposed, opt-in expansion of the
        # (already-shown, already-clipped) row title — not raw session data —
        # so unlike the fields above it is expected to survive redaction.
        self.assertEqual(result["sessions"][0]["fullTitle"], "safe expandable title")
        self.assertEqual(result["sources"], [{"id": "openai", "label": "Codex"}])

    def test_unchanged_manifest_does_not_run_checkpoint_maintenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root, "one.jsonl", "one")
            source_key = hashlib.sha256(source.source_id.encode("utf-8")).hexdigest()
            checkpoints: list[str] = []
            events: list[str] = []
            connection = _connection([(source_key, source.mtime_ns, source.size)], checkpoints, events)
            index = _index_class()(root / "sessions.sqlite3")
            with (
                mock.patch.object(index, "_open", return_value=connection),
                mock.patch.object(index, "query", return_value={"sessions": [], "total": 0}) as query,
            ):
                index.reconcile([source], _parser([]))

        self.assertEqual(events, [])
        self.assertEqual(checkpoints, [])
        query.assert_not_called()

    def test_source_cached_with_no_rows_is_reparsed_even_when_unchanged(self):
        """A collector that once succeeded while seeing nothing (run where it
        could not reach the store) must not hide that source forever: its
        fingerprint matches, so the only thing distinguishing it is that it
        cached no rows."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root, "one.jsonl", "one")
            source_key = hashlib.sha256(source.source_id.encode("utf-8")).hexdigest()
            connection = _connection(
                [(source_key, source.mtime_ns, source.size)],
                [],
                [],
                cached_rows={source_key: 0},
            )
            index = _index_class()(root / "sessions.sqlite3")
            parsed: list[str] = []

            def parse(scanned: Source) -> list[Row]:
                parsed.append(scanned.source_id)
                return []

            with (
                mock.patch.object(index, "_open", return_value=connection),
                mock.patch.object(index, "query", return_value={"sessions": [], "total": 0}),
            ):
                index.reconcile([source], parse)

        self.assertEqual(parsed, ["one"])

    def test_changed_manifest_runs_passive_checkpoint_after_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root, "one.jsonl", "one")
            checkpoints: list[str] = []
            events: list[str] = []
            connection = _connection([], checkpoints, events)
            index = _index_class()(root / "sessions.sqlite3")
            calls: list[str] = []
            with (
                mock.patch.object(index, "_open", return_value=connection),
                mock.patch.object(index, "query", return_value={"sessions": [], "total": 0}) as query,
            ):
                index.reconcile([source], _parser(calls))

        self.assertEqual(calls, ["one"])
        self.assertEqual(events, ["commit", "checkpoint:PASSIVE"])
        self.assertEqual(checkpoints, ["PASSIVE"])
        self.assertNotIn("TRUNCATE", checkpoints)
        query.assert_not_called()

    def test_unchanged_source_is_reused_across_index_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            source = _source(root, "one.jsonl", "raw-session-one")
            first_calls: list[str] = []
            _index_class()(cache).reconcile([source], _parser(first_calls))
            second_calls: list[str] = []
            second_index = _index_class()(cache)
            second_index.reconcile([source], _parser(second_calls))
            result = second_index.query()

        self.assertEqual(first_calls, ["raw-session-one"])
        self.assertEqual(second_calls, [])
        self.assertEqual(result["total"], 1)

    def test_added_changed_and_deleted_sources_reconcile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            original = _source(root, "one.jsonl", "one")
            removed = _source(root, "two.jsonl", "two")
            calls: list[str] = []
            index = _index_class()(cache)
            index.reconcile([original, removed], _parser(calls))
            changed = _source(root, "one.jsonl", "one", version=2)
            added = _source(root, "three.jsonl", "three")
            calls.clear()
            index.reconcile([changed, added], _parser(calls))
            result = index.query()

        self.assertEqual(calls, ["one", "three"])
        self.assertEqual(
            [row["title"] for row in result["sessions"]],
            ["Session one", "Session three"],
        )
        self.assertEqual(result["total"], 2)

    def test_completed_source_is_visible_while_next_source_is_still_collecting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            fast = _source(root, "fast.jsonl", "fast")
            slow = _source(root, "slow.jsonl", "slow")
            index = _index_class()(cache)

            def row(source: Source, version: str) -> Row:
                return {
                    "provider": "claude",
                    "title": f"{source.source_id} {version}",
                    "sessionName": "Fixture",
                    "state": "idle",
                    "lastActivityAt": source.mtime_ns,
                    "detail": "safe",
                    "openKey": "",
                }

            index.reconcile([slow, fast], lambda source: [row(source, "old")])
            changed_fast = _source(root, "fast.jsonl", "fast", version=2)
            changed_slow = _source(root, "slow.jsonl", "slow", version=2)
            slow_started = threading.Event()
            fast_committed = threading.Event()
            finish_slow = threading.Event()
            failures: list[Exception] = []

            def parse(source: Source) -> list[Row]:
                if source.source_id == "slow":
                    slow_started.set()
                    if not finish_slow.wait(5):
                        raise TimeoutError("slow source was not released")
                return [row(source, "new")]

            def refresh() -> None:
                try:
                    index.reconcile([changed_slow, changed_fast], parse)
                except Exception as error:
                    failures.append(error)

            open_index = index._open

            class ObservedConnection:
                def __init__(self, connection):
                    self.connection = connection

                def __getattr__(self, name):
                    return getattr(self.connection, name)

                def __enter__(self):
                    self.connection.__enter__()
                    return self

                def __exit__(self, error_type, error, traceback):
                    result = self.connection.__exit__(error_type, error, traceback)
                    if error_type is None:
                        fast_committed.set()
                    return result

            def traced_open(*, discard_stale=False):
                return ObservedConnection(open_index(discard_stale=discard_stale))

            worker = threading.Thread(target=refresh)
            with mock.patch.object(index, "_open", side_effect=traced_open):
                worker.start()
                try:
                    self.assertTrue(slow_started.wait(5))
                    self.assertTrue(fast_committed.wait(5))
                    interim = {entry["title"] for entry in index.query(limit=10)["sessions"]}
                finally:
                    finish_slow.set()
                    worker.join(5)

            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            self.assertEqual(interim, {"fast new", "slow old"})
            self.assertEqual({entry["title"] for entry in index.query(limit=10)["sessions"]}, {"fast new", "slow new"})

    def test_query_preserves_filter_order_and_exact_pagination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            sources = [_source(root, f"{name}.jsonl", name, version) for version, name in enumerate(("old", "needle-a", "needle-b"), 1)]
            index = _index_class()(cache)
            index.reconcile(sources, _parser([]))
            result = index.query("needle", limit=1, offset=1)

        self.assertEqual([row["title"] for row in result["sessions"]], ["Session needle-a"])
        self.assertEqual(result["total"], 2)
        self.assertTrue(result["totalExact"])
        self.assertEqual(result["offset"], 1)
        self.assertEqual(result["limit"], 1)
        self.assertFalse(result["hasMore"])

    def test_persisted_database_contains_only_redacted_rows_and_hashed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            source = _source(root, "private-transcript.jsonl", "raw-session-id")
            _index_class()(cache).reconcile(
                [source],
                lambda _source: [
                    {
                        "provider": "test",
                        "title": "Safe title",
                        "sessionName": "Fixture",
                        "state": "idle",
                        "lastActivityAt": 1,
                        "detail": "Safe detail",
                    }
                ],
            )
            persisted = cache.read_bytes()

        for secret in (str(source.path), source.source_id, "transcript-raw-session-id-v1"):
            self.assertNotIn(secret.encode(), persisted)
        self.assertNotIn(b"opaque-key", persisted)
        self.assertNotIn(b"openKey", persisted)
        self.assertRegex(persisted, rb"[0-9a-f]{64}")

    def test_corrupt_database_degrades_to_rebuild_and_miss(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            source = _source(root, "one.jsonl", "one")
            _index_class()(cache).reconcile([source], _parser([]))
            cache.write_bytes(b"not a sqlite database")
            calls: list[str] = []
            index = _index_class()(cache)
            index.reconcile([source], _parser(calls))
            result = index.query()

        self.assertEqual(calls, ["one"])
        self.assertEqual(result["total"], 1)


def _titled_parser(title: str) -> Parser:
    def parse(source: Source) -> list[Row]:
        return [
            {
                "provider": "test",
                "title": title,
                "sessionName": "Fixture",
                "state": "idle",
                "lastActivityAt": source.mtime_ns,
                "detail": "safe",
                "openKey": "opaque-key",
            }
        ]

    return parse


class ContributionTableTest(unittest.TestCase):
    """The materialized local-spend table is maintained by reconcile and read
    by spend_groups, which must agree with the full-row path."""

    def _cost_parser(self, rows):
        def parse(source: Source) -> list[Row]:
            return rows

        return parse

    def test_reconcile_materializes_contributions_and_spend_groups_reads_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            source = _source(root, "one.jsonl", "one")
            index = _index_class()(cache)
            index.reconcile(
                [source],
                self._cost_parser(
                    [
                        {
                            "provider": "opencode",
                            "source": "opencode",
                            "costUSD": 0.42,
                            "costStatus": "exact",
                            "costProvenance": "actual",
                            "lastActivityAt": 1_789_046_340,
                        }
                    ]
                ),
            )
            groups = index.spend_groups()

        self.assertEqual(groups["actual"]["totalUSD"], 0.42)
        self.assertEqual(groups["actual"]["providers"]["opencode::opencode"]["costUSD"], 0.42)

    def test_zed_local_tokens_do_not_become_spend_without_account_reported_billing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root, "zed.db", "zed")
            index = _index_class()(root / "sessions.sqlite3")
            index.reconcile(
                [source],
                self._cost_parser(
                    [
                        {
                            "provider": "zed",
                            "source": "zed",
                            "billingProvider": "openai",
                            "billingModel": "gpt-6-luna",
                            "tokens": 1234,
                            "costStatus": "unavailable",
                            "lastActivityAt": 1_789_046_340,
                        }
                    ]
                ),
            )
            groups = index.spend_groups()
            usage = index.usage_groups()

        for group_name in ("actual", "estimated", "subscription", "subscriptionActual"):
            self.assertFalse(groups[group_name].get("providers"))
        row = usage["providers"]["openai::zed"]
        self.assertEqual(row["tokens"], 1234)
        self.assertEqual(row["provider"], "openai")
        self.assertEqual(row["source"], "zed")

    def test_usage_groups_include_unpriced_tokens_without_recounting_costed_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = _source(root, "zed.db", "zed")
            index = _index_class()(root / "sessions.sqlite3")
            index.reconcile(
                [source],
                self._cost_parser(
                    [
                        {
                            "provider": "zed",
                            "source": "zed",
                            "billingProvider": "zed.dev",
                            "billingModel": "gpt-6-luna",
                            "tokens": 1234,
                            "costStatus": "unavailable",
                            "lastActivityAt": 1_789_046_340,
                        },
                        {
                            "provider": "zed",
                            "source": "zed",
                            "tokens": 500,
                            "costUSD": 0.25,
                            "costStatus": "exact",
                            "costProvenance": "estimated",
                            "lastActivityAt": 1_789_046_340,
                        },
                    ]
                ),
            )
            usage = index.usage_groups()

        rollup = usage["providers"]["zed.dev::zed"]
        self.assertEqual(rollup["tokens"], 1234)
        self.assertEqual(rollup["provider"], "zed.dev")
        self.assertNotIn("model", rollup)
        self.assertEqual(sum(point["total"] for point in rollup["dailyTokens"]), 1234)

    def test_removed_source_drops_its_contributions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            kept = _source(root, "one.jsonl", "one")
            removed = _source(root, "two.jsonl", "two")
            index = _index_class()(cache)
            index.reconcile(
                [kept, removed],
                self._cost_parser(
                    [
                        {
                            "provider": "cline",
                            "source": "cline",
                            "costUSD": 1.0,
                            "costStatus": "exact",
                            "costProvenance": "estimated",
                        }
                    ]
                ),
            )
            index.reconcile([kept], self._cost_parser([]))
            groups = index.spend_groups()

        self.assertEqual(groups["estimated"]["totalUSD"], 1.0)

    def test_spend_groups_returns_none_when_the_cache_is_stale(self):
        storage = importlib.import_module("tidesi.session_index_storage")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            source = _source(root, "one.jsonl", "one")
            index = _index_class()(cache)
            index.reconcile(
                [source],
                self._cost_parser(
                    [
                        {
                            "provider": "cline",
                            "source": "cline",
                            "costUSD": 1.0,
                            "costStatus": "exact",
                            "costProvenance": "estimated",
                        }
                    ]
                ),
            )
            with mock.patch.object(storage, "_schema_version", lambda: 123456):
                groups = index.spend_groups()

        self.assertIsNone(groups)


class SchemaVersionInvalidationTest(unittest.TestCase):
    """Cached rows are the product of the parser that produced them, so a
    parser change has to invalidate them even when no session file moved.
    The cache key is a hash of that code, so this happens with nothing to
    remember to bump."""

    def test_the_key_tracks_the_code_that_produces_rows(self):
        storage = importlib.import_module("tidesi.session_index_storage")
        storage._schema_version.cache_clear()
        first = storage._schema_version()
        # Stable across calls, and an int SQLite's user_version can hold.
        self.assertEqual(first, storage._schema_version())
        self.assertTrue(0 <= first <= 0x7FFF_FFFF)

        # Editing any module that determines a row's content changes the key.
        storage._schema_version.cache_clear()
        with mock.patch.object(storage, "_CONTENT_SOURCES", ("contract.py",)):
            self.assertNotEqual(storage._schema_version(), first)
        storage._schema_version.cache_clear()

    def test_a_key_change_forces_every_source_to_recollect(self):
        storage = importlib.import_module("tidesi.session_index_storage")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            source = _source(root, "one.jsonl", "openai")

            _index_class()(cache).reconcile([source], _titled_parser("old parser title"))
            self.assertEqual([row["title"] for row in _index_class()(cache).rows()], ["old parser title"])

            # Same source, unchanged on disk: without a bump it is not re-read.
            _index_class()(cache).reconcile([source], _titled_parser("new parser title"))
            self.assertEqual([row["title"] for row in _index_class()(cache).rows()], ["old parser title"])

            with mock.patch.object(storage, "_schema_version", lambda: 123456):
                _index_class()(cache).reconcile([source], _titled_parser("new parser title"))
                self.assertEqual([row["title"] for row in _index_class()(cache).rows()], ["new parser title"])

    def test_an_unchanged_version_keeps_the_cached_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "sessions.sqlite3"
            source = _source(root, "one.jsonl", "openai")
            _index_class()(cache).reconcile([source], _titled_parser("cached"))

            self.assertEqual([row["title"] for row in _index_class()(cache).rows()], ["cached"])


if __name__ == "__main__":
    unittest.main()
