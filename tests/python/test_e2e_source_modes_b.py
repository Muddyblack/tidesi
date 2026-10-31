import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from _e2e_harness import ChildProcess, LoopbackHTTPServer, LoopbackResponse, install_fake_cli


class SourceModesE2ETest(unittest.TestCase):
    def run_child(self, child, provider, source):
        result = child.run([sys.executable, "-m", "tidesi", "--provider", provider])
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schemaVersion"], 1)
        self.assertEqual([row["id"] for row in envelope["providers"]], [provider])
        row = envelope["providers"][0]
        self.assertTrue(row["ok"], row["error"])
        self.assertEqual(row["sources"]["selected"], source)
        self.assertEqual(row["sources"]["active"], source)
        return row

    def write_provider_config(self, child, provider):
        Path(child.env["TIDESI_CONFIG"]).write_text(json.dumps({"providers": {provider: True}}), encoding="utf-8")

    def test_cursor_ide_database_credential_reaches_request_and_envelope(self):
        secret = "E2E_CURSOR_IDE_SECRET"
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            database = home / "cursor.vscdb"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
                connection.execute("INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/accessToken", secret))
            responses = {
                "/https/api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage": {
                    "billingCycleStart": "1787817247821",
                    "planUsage": {"totalPercentUsed": 15, "autoPercentUsed": 10, "apiPercentUsed": 5},
                },
                "/https/api2.cursor.sh/aiserver.v1.DashboardService/GetPlanInfo": {"planInfo": {"planName": "Fixture"}},
                "/https/api2.cursor.sh/aiserver.v1.DashboardService/GetAggregatedUsageEvents": {},
                "/https/api2.cursor.sh/aiserver.v1.DashboardService/GetFilteredUsageEvents": {"usageEventsDisplay": [], "totalUsageEventsCount": 0},
            }
            with LoopbackHTTPServer(lambda request: LoopbackResponse(body=json.dumps(responses.get(request.path, {})))) as server:
                child = ChildProcess(
                    home,
                    http_base=server.base_url,
                    extra_env={"CURSOR_IDE_DB": str(database), "WIDGET_SOURCE_CURSOR": "ide"},
                )
                self.write_provider_config(child, "cursor")
                row = self.run_child(child, "cursor", "ide")
                self.assertEqual(row["details"]["planName"], "Fixture")
                self.assertGreaterEqual(len(server.requests), 3)
                dashboard = [request for request in server.requests if "DashboardService" in request.path]
                self.assertTrue(dashboard)
                self.assertTrue(all(request.header("authorization") == f"Bearer {secret}" for request in dashboard))

    def test_kiro_ide_snapshot_reaches_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            database = home / "kiro.vscdb"
            snapshot = {"usageBreakdowns": [{"currentUsage": 3, "usageLimit": 10, "percentageUsed": 30}], "timestamp": 1780000000}
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
                connection.execute("INSERT INTO ItemTable VALUES (?, ?)", ("kiro.resourceNotifications.usageState", json.dumps(snapshot)))
            child = ChildProcess(
                home,
                extra_env={"KIRO_IDE_DB": str(database), "WIDGET_SOURCE_KIRO": "ide"},
            )
            self.write_provider_config(child, "kiro")
            row = self.run_child(child, "kiro", "ide")
        self.assertEqual(row["details"]["currentUsage"], 3)
        self.assertEqual(row["details"]["source"], "ide")

    def assert_copilot_request(self, server, row, token):
        self.assertEqual(row["details"]["username"], "fixture-user")
        internal = [request for request in server.requests if request.path == "/https/api.github.com/copilot_internal/user"]
        self.assertTrue(internal)
        self.assertTrue(all(request.header("authorization") == f"Bearer {token}" for request in internal))

    def copilot_response(self):
        body = {"login": "fixture-user", "quota_snapshots": {"premium_interactions": {"entitlement": 100, "remaining": 80}}}
        return body

    def test_copilot_editor_login_reaches_request_and_envelope(self):
        token = "E2E_COPILOT_EDITOR_SECRET"
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            plugin = home / "xdg-config" / "github-copilot"
            plugin.mkdir(parents=True)
            (plugin / "apps.json").write_text(json.dumps({"github.com:fixture": {"user": "fixture-user", "oauth_token": token}}), encoding="utf-8")
            with LoopbackHTTPServer(lambda _request: LoopbackResponse(body=json.dumps(self.copilot_response()))) as server:
                child = ChildProcess(
                    home,
                    http_base=server.base_url,
                    extra_env={"WIDGET_SOURCE_COPILOT": "editor"},
                )
                self.write_provider_config(child, "copilot")
                row = self.run_child(child, "copilot", "editor")
                self.assert_copilot_request(server, row, token)

    def test_copilot_cli_gh_token_reaches_request_and_envelope(self):
        token = "E2E_COPILOT_GH_SECRET"
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bindir = home / "bin"
            install_fake_cli(bindir, "gh", f"import sys\nprint({token!r})\n")
            copilot = home / ".copilot"
            copilot.mkdir()
            (copilot / "config.json").write_text(json.dumps({"lastLoggedInUser": {"login": "fixture-user"}}), encoding="utf-8")
            with LoopbackHTTPServer(lambda _request: LoopbackResponse(body=json.dumps(self.copilot_response()))) as server:
                child = ChildProcess(
                    home,
                    http_base=server.base_url,
                    bin_dir=bindir,
                    extra_env={"WIDGET_SOURCE_COPILOT": "cli"},
                )
                self.write_provider_config(child, "copilot")
                row = self.run_child(child, "copilot", "cli")
                self.assert_copilot_request(server, row, token)


if __name__ == "__main__":
    unittest.main()
