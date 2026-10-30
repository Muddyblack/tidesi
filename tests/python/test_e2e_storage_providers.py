import json
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from _e2e_harness import ChildProcess, LoopbackHTTPServer, LoopbackResponse, install_fake_cli


class StorageProviderE2ETest(unittest.TestCase):
    def invoke(self, home, provider, extra_env):
        child = ChildProcess(home, extra_env=extra_env)
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
        Path(child.env["TIDESI_CONFIG"]).write_text(json.dumps({"providers": {provider: True}}), encoding="utf-8")
        result = child.run([sys.executable, "-m", "tidesi", "--provider", provider])
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schemaVersion"], 1)
        self.assertEqual([row["id"] for row in envelope["providers"]], [provider])
        row = envelope["providers"][0]
        self.assertTrue(row["ok"], row["error"])
        return row

    def test_mimo_database_reaches_cli_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            database = home / "mimo.db"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.executescript(
                    "CREATE TABLE session (id TEXT, title TEXT, directory TEXT, time_created INTEGER, time_updated INTEGER);"
                    "CREATE TABLE message (id TEXT, session_id TEXT, agent_id TEXT, data TEXT);"
                    "CREATE TABLE part (id TEXT, message_id TEXT, data TEXT);"
                )
                connection.execute(
                    "INSERT INTO session VALUES (?, ?, ?, ?, ?)",
                    ("e2e-mimo", "fixture", str(home), 1700000000000, 1700000000000),
                )
                connection.execute(
                    "INSERT INTO message VALUES (?, ?, ?, ?)",
                    (
                        "message",
                        "e2e-mimo",
                        "main",
                        json.dumps(
                            {
                                "role": "assistant",
                                "providerID": "mimo",
                                "modelID": "mimo-fixture",
                                "tokens": {"input": 87, "output": 13},
                            }
                        ),
                    ),
                )
            row = self.invoke(home, "mimo", {"MIMO_DB": str(database)})
        self.assertEqual(row["details"]["stats"]["totalTokens"], 100)

    def test_windsurf_state_database_reaches_cli_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            database = home / "state.vscdb"
            plan = {"planName": "E2E Plan", "quotaUsage": {"dailyRemainingPercent": 23, "weeklyRemainingPercent": 47}}
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
                connection.execute(
                    "INSERT INTO ItemTable VALUES (?, ?)",
                    ("windsurf.settings.cachedPlanInfo", json.dumps(plan)),
                )
            row = self.invoke(home, "windsurf", {"WINDSURF_STATE_DB": str(database)})
        self.assertEqual(row["details"]["plan"], "E2E Plan")
        self.assertEqual(row["quotaWindows"][0]["pct"], 77)

    def test_zed_response_fixture_reaches_cli_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            response = home / "zed-response.json"
            response.write_text(
                json.dumps(
                    {
                        "plan": {
                            "plan_v3": "zed_pro",
                            "usage": {"edit_predictions": {"used": 321, "limit": 1000}},
                            "subscription_period": {"ended_at": "2030-01-01T00:00:00Z"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            row = self.invoke(home, "zed", {"ZED_USAGE_RESPONSE_FILE": str(response)})
        self.assertEqual(row["details"]["plan"], "Pro")
        self.assertEqual(row["details"]["used"], 321)

    @unittest.skipUnless(sys.platform == "linux", "exercises Zed's Linux Secret Service adapter")
    def test_zed_secret_service_login_reaches_authenticated_api_and_envelope(self):
        token = "E2E_ZED_SECRET_SERVICE_TOKEN"
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bindir = home / "bin"
            args_path = home / "secret-tool-args.json"
            secret_entry = (
                "[/org/freedesktop/secrets/item/zed]\n"
                "label = zed-github-account\n"
                "attribute.url = https://zed.dev\n"
                "attribute.username = e2e-zed-user\n"
                f"secret = {token}\n"
            )
            script = (
                f"import json, pathlib, sys\npathlib.Path({str(args_path)!r}).write_text("
                "json.dumps(sys.argv[1:]), encoding='utf-8')\n"
                f"print({secret_entry!r}, end='')"
            )
            install_fake_cli(bindir, "secret-tool", script)
            response = {
                "plan": {
                    "plan_v3": "zed_pro",
                    "usage": {"edit_predictions": {"used": 321, "limit": 1000}},
                    "subscription_period": {"ended_at": "2030-01-01T00:00:00Z"},
                }
            }
            with LoopbackHTTPServer(lambda _request: LoopbackResponse(body=json.dumps(response))) as server:
                child = ChildProcess(home, http_base=server.base_url, bin_dir=bindir)
                Path(child.env["TIDESI_CONFIG"]).write_text(json.dumps({"providers": {"zed": True}}), encoding="utf-8")
                result = child.run([sys.executable, "-m", "tidesi", "--provider", "zed"])
            self.assertEqual(len(server.requests), 1)
            request = server.requests[0]
            self.assertEqual((request.method, request.path), ("GET", "/https/cloud.zed.dev/client/users/me"))
            self.assertEqual(request.header("authorization"), f"e2e-zed-user {token}")
            self.assertEqual(
                json.loads(args_path.read_text(encoding="utf-8")),
                ["search", "--all", "--unlock", "url", "https://zed.dev"],
            )

        transcript = result.stdout + result.stderr
        self.assertNotIn(token, transcript)
        self.assertEqual(result.returncode, 0, transcript)
        provider = json.loads(result.stdout)["providers"][0]
        self.assertTrue(provider["ok"], provider["error"])
        self.assertEqual(provider["details"]["plan"], "Pro")
        self.assertEqual(provider["details"]["used"], 321)


if __name__ == "__main__":
    unittest.main()
