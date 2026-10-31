import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from _e2e_harness import ChildProcess, LoopbackHTTPServer, LoopbackResponse, install_fake_cli


class SourceModesE2ETest(unittest.TestCase):
    def write_provider_config(self, child, provider):
        Path(child.env["TIDESI_CONFIG"]).write_text(json.dumps({"providers": {provider: True}}), encoding="utf-8")

    def assert_source_envelope(self, child, provider, source):
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

    @unittest.skipUnless(sys.platform == "linux", "Antigravity account lookup uses the host credential store")
    def test_antigravity_aiu_cli_reaches_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bindir = home / "bin"
            report = {"email": "fixture@example.test", "models": [{"label": "Gemini", "modelId": "gemini", "remainingPercentage": 0.75}]}
            install_fake_cli(bindir, "aiu", f"import json\nprint({json.dumps(json.dumps(report))})")
            install_fake_cli(bindir, "secret-tool", "pass")
            child = ChildProcess(
                home,
                extra_env={"WIDGET_SOURCE_ANTIGRAVITY": "aiu"},
                bin_dir=bindir,
            )
            self.write_provider_config(child, "antigravity")
            row = self.assert_source_envelope(child, "antigravity", "aiu")
        self.assertEqual(row["details"]["models"]["gemini"]["usedPct"], 25)

    @unittest.skipUnless(sys.platform == "linux", "Antigravity account lookup uses the host credential store")
    def test_antigravity_server_reaches_envelope_over_loopback(self):
        if not os.path.isdir("/proc/self") and importlib.util.find_spec("psutil") is None:
            self.skipTest("Antigravity process discovery needs /proc or optional psutil")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            token = "e2e-csrf-token"

            def response(request):
                if request.path.endswith("GetUnleashData"):
                    return LoopbackResponse(body="{}")
                if request.path.endswith("GetUserStatus"):
                    return LoopbackResponse(
                        body=json.dumps(
                            {
                                "userStatus": {
                                    "email": "server@example.test",
                                    "cascadeModelConfigData": {
                                        "clientModelConfigs": [
                                            {
                                                "label": "Gemini",
                                                "modelOrAlias": {"model": "gemini-server"},
                                                "quotaInfo": {"remainingFraction": 0.6},
                                            }
                                        ]
                                    },
                                }
                            }
                        )
                    )
                return LoopbackResponse(status=404)

            with LoopbackHTTPServer(response) as server:
                child = ChildProcess(
                    home,
                    extra_env={"WIDGET_SOURCE_ANTIGRAVITY": "server"},
                    http_base=server.base_url,
                )
                self.write_provider_config(child, "antigravity")
                fake = home / "antigravity-language-server.py"
                fake.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
                process = subprocess.Popen(
                    [sys.executable, str(fake), "--csrf_token", token, "--extension_server_port", server.base_url.rsplit(":", 1)[1]],
                    cwd=home,
                    env=child.env,
                )
                try:
                    row = self.assert_source_envelope(child, "antigravity", "server")
                finally:
                    process.terminate()
                    process.wait(timeout=5)
            self.assertEqual([request.path.rsplit("/", 1)[-1] for request in server.requests], ["GetUnleashData", "GetUserStatus"])
            self.assertTrue(all(request.header("x-codeium-csrf-token") == token for request in server.requests))
        self.assertEqual(row["details"]["models"]["gemini-server"]["usedPct"], 40)

    def test_kilo_cli_login_reaches_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "xdg-data" / "kilo").mkdir(parents=True)
            (home / "xdg-data" / "kilo" / "auth.json").write_text(json.dumps({"kilo": {"access": "isolated-cli-token"}}), encoding="utf-8")
            response = [
                {"result": {"data": {"json": {"totalBalance_mUsd": 5_000_000}}}},
                {"result": {"data": {"json": None}}},
            ]
            response_file = home / "kilo-response.json"
            response_file.write_text(json.dumps(response), encoding="utf-8")
            child = ChildProcess(
                home,
                extra_env={"WIDGET_SOURCE_KILO": "cli", "KILO_USAGE_RESPONSE_FILE": str(response_file)},
            )
            self.write_provider_config(child, "kilo")
            row = self.assert_source_envelope(child, "kilo", "cli")
        self.assertEqual(row["details"]["credits"]["remaining"], 5)

    def test_ollama_opencode_login_reaches_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            auth = home / "xdg-data" / "opencode" / "auth.json"
            auth.parent.mkdir(parents=True)
            auth.write_text(json.dumps({"ollama-cloud": {"type": "api", "key": "isolated-opencode-key"}}), encoding="utf-8")
            response = home / "ollama-response.json"
            response.write_text(json.dumps({"limits": {"session": {"usage": 0.25}}}), encoding="utf-8")
            child = ChildProcess(
                home,
                extra_env={"WIDGET_SOURCE_OLLAMA": "opencode", "OLLAMA_RESPONSE_FILE": str(response)},
            )
            self.write_provider_config(child, "ollama")
            row = self.assert_source_envelope(child, "ollama", "opencode")
        self.assertEqual(row["quotaWindows"][0]["pct"], 25)

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
                row = self.assert_source_envelope(child, "cursor", "ide")
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
            row = self.assert_source_envelope(child, "kiro", "ide")
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
                row = self.assert_source_envelope(child, "copilot", "editor")
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
                row = self.assert_source_envelope(child, "copilot", "cli")
                self.assert_copilot_request(server, row, token)

    def test_codex_login_and_rate_limits_reach_openai_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex_home = home / ".codex"
            codex_home.mkdir()
            (codex_home / "auth.json").write_text(
                json.dumps(
                    {
                        "tokens": {
                            "access_token": "isolated-codex-token",
                            "account_id": "fixture-account",
                        }
                    }
                ),
                encoding="utf-8",
            )
            now = int(time.time())
            response = home / "codex-limits.json"
            response.write_text(
                json.dumps(
                    {
                        "rateLimits": {
                            "primary": {
                                "usedPercent": 17,
                                "windowDurationMins": 300,
                                "resetsAt": now + 3600,
                            },
                            "secondary": {
                                "usedPercent": 42,
                                "windowDurationMins": 10080,
                                "resetsAt": now + 86400,
                            },
                            "planType": "plus",
                        }
                    }
                ),
                encoding="utf-8",
            )
            child = ChildProcess(
                home,
                extra_env={
                    "CODEX_HOME": str(codex_home),
                    "CODEX_USAGE_RESPONSE_FILE": str(response),
                },
            )
            cache = Path(child.env["TIDESI_CACHE_DIR"])
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "status-openai.json").write_text("{}", encoding="utf-8")
            Path(child.env["TIDESI_CONFIG"]).write_text(json.dumps({"providers": {"openai": True}}), encoding="utf-8")
            result = child.run([sys.executable, "-m", "tidesi", "--provider", "openai"])

        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schemaVersion"], 1)
        self.assertEqual([row["id"] for row in envelope["providers"]], ["openai"])
        provider = envelope["providers"][0]
        self.assertTrue(provider["ok"], provider["error"])
        self.assertTrue(provider["details"]["codexLoggedIn"])
        self.assertEqual(provider["details"]["accountId"], "fixture-account")
        self.assertEqual(provider["summary"]["text"], "17%")
        self.assertEqual(
            [window["key"] for window in provider["quotaWindows"]],
            ["codex_session", "codex_weekly"],
        )


if __name__ == "__main__":
    unittest.main()
