"""CLI-to-envelope E2Es for each selectable data source of the multi-source providers."""

import json
import sys
import time
import unittest

from _e2e import E2ETest


class SourceE2ETest(E2ETest):
    def source(self, provider, mode, **env):
        """Run `provider` pinned to source `mode`; the envelope must report it as selected and active."""
        row = self.provider(provider, **{f"WIDGET_SOURCE_{provider.upper()}": mode}, **env)
        self.assertEqual((row["sources"]["selected"], row["sources"]["active"]), (mode, mode))
        return row


class AntigravityE2ETest(SourceE2ETest):
    # Both modes look the account up through the Linux Secret Service first.
    def setUp(self):
        super().setUp()
        self.fake_cli("secret-tool", "pass")

    @unittest.skipUnless(sys.platform == "linux", "Antigravity account lookup uses the host credential store")
    def test_agy_cli(self):
        group = {"name": "Gemini", "buckets": [{"window": "weekly", "remaining_fraction": 0.75, "id": "gemini-weekly"}]}
        report = {"status": "SUCCESS", "command": {"name": "usage", "data": {"groups": [group]}}}
        self.fake_cli("agy", f"print({json.dumps(report)!r})")
        row = self.source("antigravity", "agy")
        self.assertEqual(row["details"]["models"]["gemini-weekly"]["usedPct"], 25)

    @unittest.skipUnless(sys.platform == "linux", "Antigravity account lookup uses the host credential store")
    def test_aiu_cli(self):
        report = {"email": "fixture@example.test", "models": [{"label": "Gemini", "modelId": "gemini", "remainingPercentage": 0.75}]}
        self.fake_cli("aiu", f"print({json.dumps(report)!r})")
        row = self.source("antigravity", "aiu")
        self.assertEqual(row["details"]["models"]["gemini"]["usedPct"], 25)


class CursorKiroE2ETest(SourceE2ETest):
    def test_cursor_ide_token_is_sent_to_the_dashboard(self):
        db = self.database(
            "cursor.vscdb",
            "CREATE TABLE ItemTable (key TEXT, value BLOB)",
            ("INSERT INTO ItemTable VALUES (?, ?)", ("cursorAuth/accessToken", "E2E_CURSOR_IDE")),
        )
        service = "/https/api2.cursor.sh/aiserver.v1.DashboardService/"
        self.server.routes = {
            service + "GetCurrentPeriodUsage": {"billingCycleStart": "1787817247821", "planUsage": {"totalPercentUsed": 15}},
            service + "GetPlanInfo": {"planInfo": {"planName": "Fixture"}},
            service + "GetAggregatedUsageEvents": {},
            service + "GetFilteredUsageEvents": {"usageEventsDisplay": [], "totalUsageEventsCount": 0},
        }
        row = self.source("cursor", "ide", CURSOR_IDE_DB=str(db))
        self.assertEqual(row["details"]["planName"], "Fixture")
        dashboard = [r for r in self.server.requests if "DashboardService" in r.path]
        self.assertGreaterEqual(len(dashboard), 3)
        self.assertEqual({r.header("authorization") for r in dashboard}, {"Bearer E2E_CURSOR_IDE"})

    def test_kiro_ide_snapshot(self):
        snapshot = {"usageBreakdowns": [{"currentUsage": 3, "usageLimit": 10, "percentageUsed": 30}], "timestamp": 1780000000}
        db = self.database(
            "kiro.vscdb",
            "CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)",
            ("INSERT INTO ItemTable VALUES (?, ?)", ("kiro.resourceNotifications.usageState", json.dumps(snapshot))),
        )
        row = self.source("kiro", "ide", KIRO_IDE_DB=str(db))
        self.assertEqual((row["details"]["currentUsage"], row["details"]["source"]), (3, "ide"))


class CopilotE2ETest(SourceE2ETest):
    QUOTA = {"login": "fixture-user", "quota_snapshots": {"premium_interactions": {"entitlement": 100, "remaining": 80}}}

    def assert_token_sent(self, row, token):
        self.assertEqual(row["details"]["username"], "fixture-user")
        quota = [r for r in self.server.requests if r.path == "/https/api.github.com/copilot_internal/user"]
        self.assertTrue(quota)
        self.assertEqual({r.header("authorization") for r in quota}, {f"Bearer {token}"})

    def test_editor_login(self):
        self.write("xdg-config/github-copilot/apps.json", {"github.com:fixture": {"user": "fixture-user", "oauth_token": "E2E_EDITOR"}})
        self.server.routes["/https/api.github.com/copilot_internal/user"] = self.QUOTA
        self.assert_token_sent(self.source("copilot", "editor"), "E2E_EDITOR")

    def test_cli_login_uses_gh_token(self):
        self.fake_cli("gh", "print('E2E_GH')")
        self.write(".copilot/config.json", {"lastLoggedInUser": {"login": "fixture-user"}})
        self.server.routes["/https/api.github.com/copilot_internal/user"] = self.QUOTA
        self.assert_token_sent(self.source("copilot", "cli"), "E2E_GH")


class CliLoginE2ETest(SourceE2ETest):
    def test_kilo_cli_login(self):
        self.write("xdg-data/kilo/auth.json", {"kilo": {"access": "E2E_KILO_CLI"}})
        response = self.write(
            "kilo-response.json",
            [{"result": {"data": {"json": {"totalBalance_mUsd": 5_000_000}}}}, {"result": {"data": {"json": None}}}],
        )
        row = self.source("kilo", "cli", KILO_USAGE_RESPONSE_FILE=str(response))
        self.assertEqual(row["details"]["credits"]["remaining"], 5)

    def test_ollama_opencode_login(self):
        self.write("xdg-data/opencode/auth.json", {"ollama-cloud": {"type": "api", "key": "E2E_OPENCODE"}})
        response = self.write("ollama-response.json", {"limits": {"session": {"usage": 0.25}}})
        row = self.source("ollama", "opencode", OLLAMA_RESPONSE_FILE=str(response))
        self.assertEqual(row["quotaWindows"][0]["pct"], 25)

    def test_codex_login_and_rate_limits(self):
        self.write(".codex/auth.json", {"tokens": {"access_token": "E2E_CODEX", "account_id": "fixture-account"}})
        now = int(time.time())
        limits = {
            "primary": {"usedPercent": 17, "windowDurationMins": 300, "resetsAt": now + 3600},
            "secondary": {"usedPercent": 42, "windowDurationMins": 10080, "resetsAt": now + 86400},
            "planType": "plus",
        }
        response = self.write("codex-limits.json", {"rateLimits": limits})
        row = self.provider("openai", CODEX_HOME=str(self.home / ".codex"), CODEX_USAGE_RESPONSE_FILE=str(response))
        self.assertTrue(row["details"]["codexLoggedIn"])
        self.assertEqual((row["details"]["accountId"], row["summary"]["text"]), ("fixture-account", "17%"))
        self.assertEqual([w["key"] for w in row["quotaWindows"]], ["codex_session", "codex_weekly"])


class ZedCredentialStoreE2ETest(E2ETest):
    @unittest.skipUnless(sys.platform == "linux", "exercises Zed's Linux Secret Service adapter")
    def test_secret_service_login_authenticates_the_api_call(self):
        token = "E2E_ZED_TOKEN"
        entry = (
            "[/org/freedesktop/secrets/item/zed]\nlabel = zed-github-account\n"
            f"attribute.url = https://zed.dev\nattribute.username = e2e-zed-user\nsecret = {token}\n"
        )
        args = self.home / "secret-tool-args.json"
        self.fake_cli(
            "secret-tool", f"import json, pathlib, sys\npathlib.Path({str(args)!r}).write_text(json.dumps(sys.argv[1:]))\nprint({entry!r}, end='')"
        )
        plan = {"plan_v3": "zed_pro", "usage": {"edit_predictions": {"used": 321, "limit": 1000}}}
        self.server.routes["/https/cloud.zed.dev/client/users/me"] = {"plan": plan}
        row = self.provider("zed", secrets=[token])
        self.assertEqual((row["details"]["plan"], row["details"]["used"]), ("Pro", 321))
        (request,) = self.server.requests
        self.assertEqual(request.header("authorization"), f"e2e-zed-user {token}")
        self.assertEqual(json.loads(args.read_text(encoding="utf-8")), ["search", "--all", "--unlock", "url", "https://zed.dev"])


if __name__ == "__main__":
    unittest.main()
