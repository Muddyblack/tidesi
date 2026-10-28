import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from _support import REPO  # noqa: F401
from tidesi.providers import kiro

USAGE = {"usageBreakdowns": [{"currentUsage": 3}]}


class KiroUsageStateTest(unittest.TestCase):
    def _db(self, rows):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        path = Path(d.name) / "state.vscdb"
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
        conn.executemany("INSERT INTO ItemTable VALUES (?, ?)", rows)
        conn.commit()
        conn.close()
        return str(path)

    def test_standalone_record(self):
        db = self._db([(kiro._MARKER_KEY, json.dumps(USAGE))])
        self.assertEqual(kiro._query_sqlite(db), USAGE)

    def test_nested_in_agent_record(self):
        db = self._db([(kiro._AGENT_KEY, json.dumps({kiro._MARKER_KEY: USAGE, "other": 1}))])
        self.assertEqual(kiro._query_sqlite(db), USAGE)

    def test_standalone_wins_over_nested(self):
        db = self._db([(kiro._MARKER_KEY, json.dumps(USAGE)), (kiro._AGENT_KEY, json.dumps({kiro._MARKER_KEY: {}}))])
        self.assertEqual(kiro._query_sqlite(db), USAGE)

    def test_invalid_standalone_falls_back_to_nested(self):
        db = self._db([(kiro._MARKER_KEY, "not json"), (kiro._AGENT_KEY, json.dumps({kiro._MARKER_KEY: USAGE}))])
        self.assertEqual(kiro._query_sqlite(db), USAGE)

    def test_missing_or_invalid_is_none(self):
        self.assertIsNone(kiro._query_sqlite(self._db([])))
        self.assertIsNone(kiro._query_sqlite(self._db([(kiro._AGENT_KEY, json.dumps({"x": 1}))])))
        self.assertIsNone(kiro._query_sqlite(self._db([(kiro._AGENT_KEY, "not json")])))
        self.assertIsNone(kiro._query_sqlite(self._db([(kiro._AGENT_KEY, json.dumps([1]))])))


if __name__ == "__main__":
    unittest.main()
