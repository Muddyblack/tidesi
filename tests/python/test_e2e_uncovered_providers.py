import json
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from _e2e_harness import ChildProcess, LoopbackHTTPServer, LoopbackResponse, install_fake_cli


class RemainingProviderE2ETest(unittest.TestCase):
    def configured_child(self, home, provider, extra_env=None, bin_dir=None):
        child = ChildProcess(home, extra_env=extra_env, bin_dir=bin_dir)
        Path(child.env["TIDESI_CONFIG"]).write_text(json.dumps({"providers": {provider: True}}), encoding="utf-8")
        return child

    def assert_provider_result(self, result, provider):
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schemaVersion"], 1)
        self.assertEqual([row["id"] for row in envelope["providers"]], [provider])
        row = envelope["providers"][0]
        self.assertTrue(row["ok"], row["error"])
        return row

    @unittest.skipUnless(sys.platform == "linux", "Antigravity account lookup uses the host credential store")
    def test_antigravity_fake_agy_report_reaches_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bindir = home / "bin"
            report = {
                "status": "SUCCESS",
                "command": {
                    "name": "usage",
                    "data": {
                        "groups": [
                            {
                                "name": "Gemini",
                                "buckets": [{"window": "weekly", "remaining_fraction": 0.75, "id": "gemini-weekly"}],
                            }
                        ]
                    },
                },
            }
            install_fake_cli(bindir, "agy", f"import json\nprint({json.dumps(json.dumps(report))})")
            install_fake_cli(bindir, "secret-tool", "pass")
            child = self.configured_child(
                home,
                "antigravity",
                {"WIDGET_SOURCE_ANTIGRAVITY": "agy"},
                bindir,
            )
            row = self.assert_provider_result(child.run([sys.executable, "-m", "tidesi", "--provider", "antigravity"]), "antigravity")
        self.assertEqual(row["details"]["models"]["gemini-weekly"]["usedPct"], 25)

    def test_selfhosted_ollama_probe_uses_loopback_and_no_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)

            def response(request):
                if request.path == "/api/version":
                    return LoopbackResponse(body='{"version":"fixture"}')
                if request.path == "/api/ps":
                    return LoopbackResponse(body='{"models":[{"name":"fixture-model"}]}')
                return LoopbackResponse(status=404)

            with LoopbackHTTPServer(response) as server:
                endpoint = server.base_url.replace("127.0.0.1", "localhost.")
                child = self.configured_child(
                    home,
                    "selfhosted",
                    {
                        "WIDGET_SELFHOSTED_ENDPOINT": endpoint,
                        "WIDGET_SELFHOSTED_ENGINE": "ollama",
                    },
                )
                row = self.assert_provider_result(child.run([sys.executable, "-m", "tidesi", "--provider", "selfhosted"]), "selfhosted")
            self.assertEqual([request.path for request in server.requests], ["/api/version", "/api/ps"])
        self.assertEqual(row["details"]["models"][0]["name"], "fixture-model")

    def test_opencode_sqlite_session_reaches_cli_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            database = home / "opencode.db"
            now_ms = int(time.time() * 1000)
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.executescript(
                    "CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT, time_created INTEGER, time_updated INTEGER);"
                    "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT);"
                    "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, data TEXT);"
                )
                connection.execute(
                    "INSERT INTO session VALUES (?, ?, ?, ?, ?)",
                    ("e2e-session", "Fixture session", str(home), now_ms, now_ms),
                )
                connection.execute(
                    "INSERT INTO message VALUES (?, ?, ?)",
                    (
                        "e2e-message",
                        "e2e-session",
                        json.dumps(
                            {
                                "role": "assistant",
                                "providerID": "opencode",
                                "modelID": "fixture-model",
                                "cost": 0.42,
                                "tokens": {"input": 80, "output": 20},
                            }
                        ),
                    ),
                )
            child = self.configured_child(home, "opencode", {"OPENCODE_DB": str(database)})
            cache = Path(child.env["TIDESI_CACHE_DIR"])
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "pricing-models-v2.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "source": "https://models.dev/api.json",
                        "fetchedAt": time.time(),
                        "checkedAt": time.time(),
                        "providers": {},
                        "error": "",
                    }
                ),
                encoding="utf-8",
            )
            row = self.assert_provider_result(child.run([sys.executable, "-m", "tidesi", "--provider", "opencode"]), "opencode")
        self.assertTrue(row["details"]["stats"]["available"])
        self.assertEqual(row["details"]["stats"]["totalTokens"], 100)


if __name__ == "__main__":
    unittest.main()
