import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from _support import REPO  # noqa: F401
from tidesi import session_index, session_manifest, sessions
from tidesi.providers import zed_sessions

try:
    import zstandard
except ImportError:
    zstandard = None


def _create_database(path: Path, *, optional_columns=True, wal=False):
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
    columns = "id TEXT PRIMARY KEY, summary TEXT NOT NULL, updated_at TEXT NOT NULL, data_type TEXT NOT NULL, data BLOB NOT NULL"
    if optional_columns:
        columns += ", parent_id TEXT, folder_paths TEXT, folder_paths_order TEXT, created_at TEXT"
    connection.execute(f"CREATE TABLE threads ({columns})")
    return connection


def _thread(*, usage=None, model=None, subagent_context=None, extra=None):
    value = {
        "title": "Stored thread title",
        "messages": [{"private": "transcript should never be exposed"}],
        "updated_at": "2026-10-20T12:30:00Z",
        "cumulative_token_usage": usage or {},
        "request_token_usage": {},
        "model": model or {},
    }
    if subagent_context is not None:
        value["subagent_context"] = subagent_context
    if extra:
        value.update(extra)
    return value


def _payload(value, kind="json"):
    encoded = json.dumps(value).encode("utf-8")
    if kind == "zstd":
        if zstandard is None:
            raise RuntimeError("zstandard is not installed")
        return zstandard.ZstdCompressor().compress(encoded)
    return encoded


def _insert(connection, *, session_id, title, updated_at, data, kind="json", parent_id=None, folders=None, order=None):
    names = ["id", "summary", "updated_at", "data_type", "data"]
    values = [session_id, title, updated_at, kind, _payload(data, kind)]
    columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
    if "parent_id" in columns:
        names.append("parent_id")
        values.append(parent_id)
    if "folder_paths" in columns:
        names.extend(("folder_paths", "folder_paths_order"))
        values.extend((folders, order))
    if "created_at" in columns:
        names.append("created_at")
        values.append(updated_at)
    placeholders = ", ".join("?" for _ in names)
    connection.execute(f"INSERT INTO threads ({', '.join(names)}) VALUES ({placeholders})", values)


class ZedSessionReaderTest(unittest.TestCase):
    def test_platform_default_paths_and_override(self):
        with tempfile.TemporaryDirectory() as root:
            home = Path(root)
            linux_data = home / "xdg-data"
            with (
                mock.patch.dict(os.environ, {"HOME": root, "XDG_DATA_HOME": str(linux_data)}, clear=True),
                mock.patch.object(zed_sessions.paths, "IS_MACOS", False),
                mock.patch.object(zed_sessions.paths, "IS_WINDOWS", False),
            ):
                self.assertEqual(zed_sessions.discover_database_paths(), [str(linux_data / "zed/threads/threads.db")])

            with (
                mock.patch.dict(os.environ, {"HOME": root, "XDG_DATA_HOME": str(linux_data)}, clear=True),
                mock.patch.object(zed_sessions.paths, "IS_MACOS", True),
                mock.patch.object(zed_sessions.paths, "IS_WINDOWS", False),
            ):
                self.assertEqual(
                    zed_sessions.discover_database_paths(),
                    [str(home / "Library/Application Support/Zed/threads/threads.db")],
                )

            local = home / "AppData/Local"
            with (
                mock.patch.dict(os.environ, {"HOME": root, "LOCALAPPDATA": str(local)}, clear=True),
                mock.patch.object(zed_sessions.paths, "IS_MACOS", False),
                mock.patch.object(zed_sessions.paths, "IS_WINDOWS", True),
            ):
                self.assertEqual(zed_sessions.discover_database_paths(), [str(local / "Zed/threads/threads.db")])

            override = home / "custom-user-data/threads.db"
            with mock.patch.dict(os.environ, {"TIDESI_ZED_THREADS_DB": str(override)}, clear=True):
                self.assertEqual(zed_sessions.discover_database_paths(), [str(override)])

    def test_reads_json_and_zstd_and_ignores_subagents(self):
        if zstandard is None:
            self.skipTest("zstandard dependency is not installed")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "threads.db"
            connection = _create_database(path)
            _insert(
                connection,
                session_id="private-root-json-id",
                title="JSON summary",
                updated_at="2026-10-20T12:00:00Z",
                data=_thread(
                    usage={
                        "input_tokens": 10,
                        "output_tokens": 2,
                        "cache_read_input_tokens": 3,
                        "cache_creation_input_tokens": 4,
                    },
                    model={"provider": "Future.Provider", "model": "new-model-2026"},
                ),
                folders="/workspace/project-a\n/workspace/project-z",
                order="1,0",
            )
            _insert(
                connection,
                session_id="private-root-zstd-id",
                title="Compressed summary",
                updated_at="2026-10-21T12:00:00Z",
                kind="zstd",
                data=_thread(usage={"input_tokens": 8, "output_tokens": 1}, model={"provider": "another-provider", "model": "future/model-v3"}),
                folders="/workspace/zstd-project",
                order="0",
            )
            _insert(
                connection,
                session_id="private-child-id",
                title="Child thread",
                updated_at="2026-10-22T12:00:00Z",
                data=_thread(usage={"input_tokens": 999}),
                parent_id="private-root-json-id",
            )
            _insert(
                connection,
                session_id="private-context-child-id",
                title="Child identified in blob",
                updated_at="2026-10-23T12:00:00Z",
                data=_thread(usage={"input_tokens": 999}, subagent_context={"parent_thread_id": "private-root-json-id", "depth": 1}),
            )
            connection.commit()
            connection.close()

            records = zed_sessions._read_database(str(path), include_all=True)

        self.assertEqual([record.title for record in records], ["Compressed summary", "JSON summary"])
        self.assertEqual(records[0].model_provider, "another-provider")
        self.assertEqual(records[0].model_id, "future/model-v3")
        self.assertEqual(records[0].tokens, 9)
        self.assertEqual((records[0].input_tokens, records[0].output_tokens), (8, 1))
        self.assertEqual(records[1].model_provider, "future.provider")
        self.assertEqual(records[1].model_id, "new-model-2026")
        self.assertEqual(records[1].tokens, 19)
        self.assertEqual(
            (records[1].input_tokens, records[1].output_tokens, records[1].cache_read_tokens, records[1].cache_write_tokens),
            (10, 2, 3, 4),
        )
        self.assertEqual(records[1].workspace, "project-z")
        encoded = json.dumps(records, default=lambda value: value.__dict__)

        self.assertNotIn("/workspace/project-z", encoded)
        self.assertNotIn("transcript should never be exposed", encoded)

    def test_unknown_content_size_zstd_frame_with_large_window_decodes(self):
        if zstandard is None:
            self.skipTest("zstandard dependency is not installed")
        thread = _thread(usage={"input_tokens": 11, "output_tokens": 7}, extra={"large_payload": "x" * 200_000})
        encoded = zstandard.ZstdCompressor(write_content_size=False).compress(json.dumps(thread).encode("utf-8"))
        frame = zstandard.get_frame_parameters(encoded)
        self.assertGreater(frame.window_size, zed_sessions._MAX_THREAD_DATA_BYTES // 1024)

        decoded = zed_sessions._decode_thread("zstd", encoded)

        self.assertIsNotNone(decoded)
        self.assertEqual(zed_sessions._total_tokens(decoded), 18)

    def test_optional_columns_and_request_usage_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "legacy.db"
            connection = _create_database(path, optional_columns=False)
            _insert(
                connection,
                session_id="legacy-id",
                title="Legacy row",
                updated_at="2026-10-24T10:00:00+00:00",
                data=_thread(
                    model={"provider": "custom-provider", "model": "custom-model"},
                    extra={
                        "request_token_usage": {
                            "user-a": {"input_tokens": 3, "output_tokens": 1},
                            "user-b": {"input_tokens": 5, "cache_read_input_tokens": 2},
                        }
                    },
                ),
            )
            connection.commit()
            connection.close()

            records = zed_sessions._read_database(str(path), include_all=True)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].workspace, "")
        self.assertEqual(records[0].tokens, 11)
        self.assertEqual((records[0].input_tokens, records[0].output_tokens, records[0].cache_read_tokens), (8, 1, 2))
        self.assertEqual(records[0].last_activity, int(datetime(2026, 10, 24, 10, tzinfo=timezone.utc).timestamp()))

    def test_cumulative_usage_takes_precedence_over_request_map(self):
        usage = {"input_tokens": 7, "output_tokens": 2}
        thread = _thread(
            usage=usage,
            extra={"request_token_usage": {"request": {"input_tokens": 100, "output_tokens": 100}}},
        )
        self.assertEqual(zed_sessions._total_tokens(thread), 9)

    def test_malformed_payload_keeps_metadata_but_no_usage(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bad-row.db"
            connection = _create_database(path)
            connection.execute(
                "INSERT INTO threads (id, summary, updated_at, data_type, data) VALUES (?, ?, ?, ?, ?)",
                ("bad-private-id", "Still listed", "2026-10-25T10:00:00Z", "zstd", b"not zstandard"),
            )
            connection.commit()
            connection.close()

            records = zed_sessions._read_database(str(path), include_all=True)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Still listed")
        self.assertEqual(records[0].tokens, 0)
        self.assertEqual(records[0].model_provider, "")

    def test_live_wal_is_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "live.db"
            writer = _create_database(path, wal=True)
            _insert(
                writer,
                session_id="wal-private-id",
                title="WAL session",
                updated_at="2026-10-26T10:00:00Z",
                data=_thread(usage={"input_tokens": 5}),
            )
            writer.commit()
            self.assertTrue(Path(str(path) + "-wal").exists())
            before = path.stat().st_mtime_ns

            records = zed_sessions._read_database(str(path), include_all=True)
            with zed_sessions._connect_readonly(str(path)) as reader:
                with self.assertRaises(sqlite3.OperationalError):
                    reader.execute("CREATE TABLE forbidden (id TEXT)")

            self.assertEqual([record.title for record in records], ["WAL session"])
            self.assertEqual(path.stat().st_mtime_ns, before)
            writer.close()

    def test_manifest_tracks_database_and_wal_but_not_shm(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "threads.db"
            path.touch()
            Path(str(path) + "-wal").write_bytes(b"wal")
            Path(str(path) + "-shm").write_bytes(b"shm")
            with mock.patch.object(zed_sessions, "discover_database_paths", return_value=[str(path)]):
                records = session_manifest._zed_records()

        self.assertEqual([record[1] for record in records], ["zed-session-reader", str(path), str(path) + "-wal"])
        self.assertEqual(records[0][3], 3)

    def test_session_row_keeps_model_dynamic_cost_unavailable_and_no_resume_key(self):
        record = zed_sessions.ZedSession(
            session_id="private-session-id",
            title="A useful title",
            workspace="project",
            last_activity=1_800_000_000,
            model_provider="future-provider",
            model_id="future-model-id",
            tokens=123,
        )
        with mock.patch.object(zed_sessions, "read_sessions", return_value=[record]):
            entry = sessions._zed_entries()[0]

        self.assertEqual(entry["source"], "zed")
        self.assertEqual(entry["provider"], "zed")
        self.assertEqual(entry["billingProvider"], "future-provider")
        self.assertEqual(entry["billingModel"], "future-model-id")
        self.assertEqual(entry["sessionName"], "via Zed")
        self.assertEqual(entry["tokens"], 123)
        self.assertEqual(entry["costStatus"], "unavailable")
        self.assertNotIn("costUSD", entry)
        self.assertEqual(entry["openKey"], "")
        encoded = json.dumps(entry)
        for private in ("private-session-id", "/private", "transcript", "credential"):
            self.assertNotIn(private, encoded)

    def test_zed_hosted_model_retains_dynamic_identity_without_pricing_local_tokens(self):
        record = zed_sessions.ZedSession(
            session_id="private-session-id",
            title="Hosted GPT session",
            workspace="project",
            last_activity=1_800_000_000,
            model_provider="zed.dev",
            model_id="gpt-6-luna",
            tokens=1_350_000,
            input_tokens=1_000_000,
            output_tokens=100_000,
            cache_read_tokens=200_000,
            cache_write_tokens=50_000,
        )
        catalog = {"openai": {"gpt-6-luna": {"input": 0.1, "output": 0.5, "cached": 0.01}}}
        with (
            mock.patch.object(zed_sessions, "read_sessions", return_value=[record]),
            mock.patch.object(sessions.pricing, "cached_catalog", return_value=catalog),
        ):
            entry = sessions._zed_entries()[0]

        self.assertEqual(entry["billingProvider"], "openai")
        self.assertEqual(entry["billingModel"], "gpt-6-luna")
        self.assertNotIn("costUSD", entry)
        self.assertEqual(entry["costStatus"], "unavailable")
        self.assertEqual(entry["tokens"], 1_350_000)

    def test_source_is_registered_and_matches_collector_order(self):
        self.assertEqual(sessions.SESSION_COLLECTORS["zed"], "_zed_entries")
        self.assertIn(("zed", "Zed Editor"), session_index.SOURCE_REGISTRY)
        with mock.patch.object(session_manifest, "build_manifests", wraps=session_manifest.build_manifests) as build:
            manifests = build()
        self.assertEqual([manifest.source_id for manifest in manifests], list(sessions.SESSION_COLLECTORS))


if __name__ == "__main__":
    unittest.main()
