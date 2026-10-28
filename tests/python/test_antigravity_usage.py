import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from _support import REPO  # noqa: F401
from tidesi import config
from tidesi.providers import antigravity_usage as reader
from tidesi.providers.antigravity_usage import SkipSource, read_database, read_usage


def _varint(value):
    output = bytearray()
    while value > 127:
        output.append((value & 127) | 128)
        value >>= 7
    output.append(value)
    return bytes(output)


def _field(number, wire, value):
    tag = _varint(number << 3 | wire)
    return tag + (_varint(len(value)) + value if wire == 2 else _varint(value))


def _string(number, value):
    return _field(number, 2, value.encode())


def _turn(model, label, response_id, counts, timestamp=None):
    usage = b"".join(_field(number, 0, value) for number, value in counts.items()) + _string(11, response_id)
    chat = _string(19, model) + _string(21, label) + _field(4, 2, usage)
    if timestamp is not None:
        stamp = _field(1, 0, timestamp)
        chat += _field(9, 2, _field(4, 2, stamp))
    return _field(1, 2, chat)


class AntigravityUsageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "antigravity-cli" / "conversations" / "session.db"
        self.database.parent.mkdir(parents=True)

    def _database(self, generations):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("CREATE TABLE gen_metadata (idx INTEGER, data BLOB, size INTEGER)")
            connection.execute("CREATE TABLE steps (idx INTEGER, step_type INTEGER, metadata BLOB)")
            connection.execute("CREATE TABLE trajectory_metadata_blob (id INTEGER, data BLOB)")
            connection.executemany("INSERT INTO gen_metadata VALUES (?, ?, ?)", generations)
            connection.execute("INSERT INTO trajectory_metadata_blob VALUES (1, ?)", (_field(2, 2, _field(1, 0, 1234)),))

    def test_maps_counts_attributes_models_and_deduplicates(self):
        first = _turn("gemini-real-id", "same label", "response-a", {1: 2, 2: 3, 5: 4, 9: 5, 10: 6}, 1235)
        second = _turn("gemini-real-id", "same label", "response-a", {1: 2, 2: 3, 5: 4, 9: 5, 10: 6})
        self._database([(0, first, len(first)), (1, second, len(second))])

        records = read_database(self.database)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].usage[0].provider, "google")
        self.assertEqual(records[0].usage[0].model, "gemini-real-id")
        self.assertEqual(
            (
                records[0].usage[0].input_tokens,
                records[0].usage[0].cache_read_tokens,
                records[0].usage[0].output_tokens,
                records[0].usage[0].reasoning_tokens,
            ),
            (5, 4, 5, 6),
        )
        self.assertEqual(records[0].last_activity, 1235)

    def test_unresolved_ambiguous_and_routing_labels_stay_unpriced(self):
        rows = [
            _turn("gemini-one", "ambiguous", "a", {2: 3}),
            _turn("gemini-two", "ambiguous", "b", {2: 4}),
            _turn("gemini-default", "unrecognized", "c", {2: 5}),
        ]
        self._database([(index, row, len(row)) for index, row in enumerate(rows)])

        records = read_database(self.database)

        self.assertEqual([bucket.model for record in records for bucket in record.usage], ["gemini-one", "gemini-two", "gemini-default"])

    def test_recovers_missing_exact_model_from_same_database_label(self):
        identified = _turn("gemini-exact", "known label", "a", {2: 3})
        missing = _field(1, 2, _string(21, "known label") + _field(4, 2, _field(2, 0, 7) + _string(11, "b")))
        self._database([(0, identified, len(identified)), (1, missing, len(missing))])

        records = read_database(self.database)

        self.assertEqual([bucket.model for bucket in records[0].usage], ["gemini-exact", "gemini-exact"])

    def test_malformed_protobuf_rejects_instead_of_returning_empty_usage(self):
        self._database([(0, b"\x0a\x80", 2)])

        with self.assertRaises(SkipSource):
            read_database(self.database)

    def test_oversized_generation_is_skipped_and_the_rest_still_counts(self):
        kept = _turn("gemini-exact", "label", "a", {2: 7})
        self._database([(0, b"x" * (4 * 1024 * 1024 + 1), 0), (1, kept, len(kept))])

        records = read_database(self.database)

        self.assertEqual([bucket.input_tokens for bucket in records[0].usage], [7])

    def test_missing_root_is_empty(self):
        self.assertEqual(read_usage(self.root / "absent"), [])

    def test_unchanged_database_uses_private_numeric_cache(self):
        turns = [
            _turn("gemini-exact", "private display", "private-response-a", {2: 7}),
            _turn("gemini-exact", "private display", "private-response-b", {2: 5}),
        ]
        self._database([(index, turn, len(turn)) for index, turn in enumerate(turns)])
        cache = self.root / "cache"
        with mock.patch.object(config, "cache_dir", return_value=str(cache)):
            cold = read_usage(self.root)
            with mock.patch.object(reader, "_extract", side_effect=AssertionError("cache miss")):
                warm = read_usage(self.root)

        cold_tokens = sum(bucket.input_tokens for session in cold for bucket in session.usage)
        warm_tokens = sum(bucket.input_tokens for session in warm for bucket in session.usage)
        self.assertEqual(cold_tokens, warm_tokens)
        self.assertEqual(cold, warm)
        self.assertEqual(len(warm[0].usage), 2)
        payload = "".join(path.read_text(encoding="utf-8") for path in cache.glob("*.json"))
        self.assertNotIn("session", payload)
        self.assertNotIn(str(self.database), payload)
        self.assertNotIn("private-response", payload)
        self.assertNotIn("private display", payload)
        if os.name != "nt":
            self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in cache.glob("*.json")))

    def test_wal_signature_change_forces_database_rescan(self):
        turn = _turn("gemini-exact", "label", "response", {2: 7})
        self._database([(0, turn, len(turn))])
        cache = self.root / "cache"
        with mock.patch.object(config, "cache_dir", return_value=str(cache)):
            read_usage(self.root)
            wal = Path(str(self.database) + "-wal")
            wal.write_bytes(b"")
            with mock.patch.object(reader, "_extract", wraps=reader._extract) as extract:
                read_usage(self.root)

        extract.assert_called_once()

    def test_corrupt_cache_reparses_database(self):
        turn = _turn("gemini-exact", "label", "response", {2: 7})
        self._database([(0, turn, len(turn))])
        cache = self.root / "cache"
        with mock.patch.object(config, "cache_dir", return_value=str(cache)):
            read_usage(self.root)
            cache_file = next(cache.glob("*.json"))
            cache_file.write_text("{broken", encoding="utf-8")
            with mock.patch.object(reader, "_extract", wraps=reader._extract) as extract:
                read_usage(self.root)

        extract.assert_called_once()

    def test_cold_and_warm_reads_have_identical_model_breakdowns(self):
        turns = [
            _turn("gemini-exact", "label", "response-a", {2: 7, 5: 4}),
            _turn("gemini-exact", "label", "response-b", {2: 5, 9: 3}),
        ]
        self._database([(index, turn, len(turn)) for index, turn in enumerate(turns)])
        with mock.patch.object(config, "cache_dir", return_value=str(self.root / "cache")):
            cold = read_usage(self.root)
            warm = read_usage(self.root)
        self.assertEqual(warm, cold)


if __name__ == "__main__":
    unittest.main()
