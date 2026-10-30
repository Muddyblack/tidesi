import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from tidesi.normalize.claude import _remote
from tidesi.providers import claude_remote


class ClaudeRemoteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, filename, **fields):
        with open(os.path.join(self.tmp.name, filename), "w", encoding="utf-8") as f:
            json.dump(fields, f)

    def test_only_live_sessions_with_a_bridge_count(self):
        base = {"cwd": "/work/demo", "status": "idle", "updatedAt": 5}
        self.write("1.json", pid=1, name="a", bridgeSessionId="session_AAA", **base)
        self.write("2.json", pid=2, name="no-bridge", **base)
        self.write("3.json", pid=3, name="dead", bridgeSessionId="session_BBB", **base)
        self.write("4.json", pid=4, name="odd", bridgeSessionId="nope", **base)
        with open(os.path.join(self.tmp.name, "5.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        with mock.patch.object(claude_remote, "_is_process_alive", lambda pid: pid != 3):
            rows = claude_remote.remote_sessions(self.tmp.name)
        self.assertEqual([(r["name"], r["folder"], r["url"]) for r in rows], [("a", "demo", "https://claude.ai/code/session_AAA")])

    def test_missing_directory_is_empty(self):
        self.assertEqual(claude_remote.remote_sessions(os.path.join(self.tmp.name, "nope")), [])

    def test_sessions_follow_the_config_dir(self):
        os.mkdir(os.path.join(self.tmp.name, "sessions"))
        with open(os.path.join(self.tmp.name, "sessions", "1.json"), "w", encoding="utf-8") as f:
            json.dump({"pid": 1, "name": "a", "cwd": "/work/demo", "status": "idle", "bridgeSessionId": "session_AAA"}, f)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.tmp.name}):
            with mock.patch.object(claude_remote, "_is_process_alive", lambda pid: True):
                rows = claude_remote.remote_sessions()
        self.assertEqual([r["name"] for r in rows], ["a"])

    def test_tab_only_when_a_session_is_open(self):
        self.assertIsNone(_remote([]))
        self.assertIsNone(_remote(None))
        view = _remote([{"name": "a", "folder": "demo", "status": "busy", "url": "https://claude.ai/code/session_AAA"}])
        self.assertEqual(view["state"], "online")
        self.assertEqual(view["instances"], [{"name": "a", "detail": "demo", "state": "busy", "url": "https://claude.ai/code/session_AAA"}])


if __name__ == "__main__":
    unittest.main()
