"""Real settings, credential readers and collectors against recorded responses."""

import base64
import contextlib
import io
import json
import os
import sqlite3
from unittest import mock

from _support import FIXTURES, IsolatedHomeTest
from tidesi import __main__ as backend
from tidesi import cli
from tidesi.providers.cursor import get_cursor_usage
from tidesi.providers.kimi_code import get_kimi_code_usage, parse_usage_response
from tidesi.providers.kiro import _region_of


class CollectorTest(IsolatedHomeTest):
    def setUp(self):
        super().setUp()
        defaults = dict.fromkeys(("claude", "antigravity", "openai", "kiro", "mistral", "openrouter", "grok", "muse"), False)
        self.write("defaults.json", {"providers": defaults})
        self.write(
            "config.json",
            {
                "providers": dict(defaults, zai=True, copilot=True, deepseek=True),
                "keys": {"zai": "zai-test", "github": "github-test", "deepseek": "deepseek-test"},
                "copilotQuota": 500,
            },
        )
        os.environ.update(
            {
                "ZAI_RESPONSE_FILE": str(FIXTURES / "zai-response.json"),
                "DEEPSEEK_BALANCE_RESPONSE_FILE": str(FIXTURES / "deepseek-response.json"),
            }
        )
        self.write("github-user.json", {"login": "octocat"})
        self.write("github-usage.json", [{"grossQuantity": 125}])
        self.write("copilot-internal-empty.json", {"login": "octocat"})
        self.write("zai-monitor-empty.json", {"success": True, "data": {}})
        os.environ["ZAI_MODEL_USAGE_RESPONSE_FILE"] = str(self.home / "zai-monitor-empty.json")
        os.environ["ZAI_TOOL_USAGE_RESPONSE_FILE"] = str(self.home / "zai-monitor-empty.json")
        self.write(
            "copilot-internal.json",
            {
                "login": "octocat",
                "copilot_plan": "individual",
                "quota_reset_date": "2026-08-01",
                "quota_snapshots": {
                    "premium_interactions": {
                        "has_quota": True,
                        "unlimited": False,
                        "entitlement": 200,
                        "quota_remaining": 180.2,
                        "percent_remaining": 90.1,
                    }
                },
            },
        )
        for variable, path in {
            "COPILOT_USER_RESPONSE_FILE": "github-user.json",
            "COPILOT_USAGE_RESPONSE_FILE": "github-usage.json",
            "COPILOT_INTERNAL_RESPONSE_FILE": "copilot-internal-empty.json",
        }.items():
            os.environ[variable] = str(self.home / path)
        # Status fetching is unrelated to credential/collector plumbing. Its
        # recorded formats are asserted in the normalizer suite.
        patch = mock.patch("tidesi.collect.provider_status", return_value=None)
        patch.start()
        self.addCleanup(patch.stop)

    def run_backend(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(backend.main(list(args)), 0)
        return json.loads(output.getvalue())

    def test_settings_keys_and_outer_envelope(self):
        result = self.run_backend("--all")
        providers = {p["id"]: p for p in result["providers"]}
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(set(providers), {"copilot", "deepseek", "zai"})
        self.assertEqual(result["active"], "zai")
        self.assertEqual(providers["zai"]["historyValues"], {"za": 25, "za7": 0, "zat": 40})
        self.assertEqual(providers["copilot"]["historyValues"], {"gh": 25})
        self.assertEqual(providers["copilot"]["quotaWindows"][0]["detail"], "125 / 500 requests")
        self.assertEqual(providers["deepseek"]["details"]["currency"], "USD")
        self.assertNotRegex(json.dumps(result), "zai-test|github-test|deepseek-test")
        os.environ["COPILOT_INTERNAL_RESPONSE_FILE"] = str(self.home / "copilot-internal.json")
        providers = {p["id"]: p for p in self.run_backend("--all")["providers"]}
        details = providers["copilot"]["details"]
        self.assertEqual((details["quota"], details["used"], details["resetAt"]), (200, 19.8, 1785542400))

    def test_provider_selection_and_opt_in_defaults(self):
        self.assertEqual([p["id"] for p in self.run_backend("--provider", "deepseek,zai")["providers"]], ["deepseek", "zai"])
        self.assertEqual([p["id"] for p in self.run_backend("--provider", "kiro")["providers"]], ["kiro"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(backend.main(["--provider", "nonsense"]), 2)
        os.environ["TIDESI_CONFIG"] = str(self.home / "defaults.json")
        result = self.run_backend("--all")
        self.assertEqual((result["providers"], result["active"]), ([], ""))

    def test_terminal_fetches_through_the_same_settings(self):
        output = io.StringIO()
        with mock.patch.object(cli, "_stdin_envelope_waiting", return_value=False), contextlib.redirect_stdout(output):
            self.assertEqual(cli.main(["--provider", "deepseek", "--json"]), 0)
        provider = json.loads(output.getvalue())["providers"][0]
        self.assertTrue(provider["ok"], provider["error"])
        self.assertEqual(provider["historyValues"], {"ds": 12.5})

    @staticmethod
    def cursor_token(expiry):
        payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
        return f"eyJhbGciOiJIUzI1NiJ9.{payload}.cursor-secret-sig"

    def test_cli_logins_from_files_and_sqlite(self):
        db_path = self.home / ".local/share/kiro-cli/data.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(sqlite3.connect(db_path)) as db:
            db.execute("CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value BLOB)")
            db.execute(
                "INSERT INTO auth_kv VALUES (?, ?)",
                (
                    "kirocli:social:token",
                    json.dumps(
                        {
                            "access_token": "kiro-secret-token",
                            "refresh_token": "kiro-secret-refresh",
                            "provider": "google",
                            "expires_at": "2099-01-01T00:00:00.123456789Z",
                            "profile_arn": "arn:aws:codewhisperer:us-east-1:000000000000:profile/TEST",
                        }
                    ),
                ),
            )
            db.commit()
        self.write(".config/cursor/auth.json", {"accessToken": self.cursor_token(4102444800), "refreshToken": "cursor-secret-refresh"})
        self.write(
            ".kimi-code/credentials/kimi-code.json",
            {
                "access_token": "kimi-secret-token",
                "refresh_token": "kimi-secret-refresh",
                "expires_at": 4102444800,
                "scope": "",
                "token_type": "Bearer",
                "expires_in": 900,
            },
        )
        for variable, path in {
            "KIRO_CLI_USAGE_RESPONSE_FILE": "kiro-cli-response.json",
            "CURSOR_USAGE_RESPONSE_FILE": "cursor-usage-response.json",
            "CURSOR_PLAN_RESPONSE_FILE": "cursor-plan-response.json",
            "CURSOR_AGGREGATED_RESPONSE_FILE": "cursor-aggregated-response.json",
            "CURSOR_EVENTS_RESPONSE_FILE": "cursor-events-response.json",
            "KIMI_CODE_USAGE_RESPONSE_FILE": "kimi-code-response.json",
        }.items():
            os.environ[variable] = str(FIXTURES / path)
        result = self.run_backend("--provider", "kiro,kimi,cursor")
        providers = {p["id"]: p for p in result["providers"]}
        for provider_id in ("kiro", "kimi"):
            self.assertTrue(providers[provider_id]["ok"], providers[provider_id]["error"])
        kiro = providers["kiro"]["details"]
        self.assertEqual(
            (kiro["source"], kiro["planType"], kiro["currentUsage"], kiro["usageLimit"], kiro["resetAt"]), ("cli", "free", 0.13, 50, 1790812800)
        )
        self.assertEqual(providers["kiro"]["quotaWindows"][0]["detail"], "0.13 / 50 credits")
        cursor = providers["cursor"]
        self.assertFalse(cursor["ok"])
        self.assertEqual(cursor["historyValues"], {})
        self.assertIn("agent usage limit unavailable", cursor["error"])
        self.assertEqual((cursor["summary"]["detail"], cursor["details"]["resetAt"], cursor["details"]["source"]), ("Free", 1790495647, "cli"))
        stats = cursor["details"]["stats"]
        self.assertTrue(stats["available"])
        self.assertEqual((stats["totalTokens"], stats["totalRequests"], stats["totalSessions"], stats["dailyUnit"]), (98998, 2, 1, "tokens"))
        self.assertEqual(sum(d["total"] for d in stats["dailySeries"]), 98998)
        kimi = providers["kimi"]
        self.assertEqual([w["label"] for w in kimi["quotaWindows"]], ["5-hour limit", "Weekly limit", "Extra usage"])
        self.assertEqual([w["resetAt"] for w in kimi["quotaWindows"][:2]], [1789063200, 1789344000])
        self.assertEqual(kimi["details"]["codePlan"]["booster"]["balance"], 2.5)
        self.assertEqual(kimi["historyValues"], {"kc": 30, "kcw": 45})
        self.assertNotRegex(json.dumps(result), "secret|test@example.com")

    def test_expired_logins_and_provider_errors(self):
        os.environ["KIMI_CODE_HOME"] = str(self.home / "kimi-expired")
        self.write("kimi-expired/credentials/kimi-code.json", {"access_token": "kimi-secret-token", "expires_at": 1000})
        self.assertEqual(get_kimi_code_usage()["error"], "Kimi Code login expired — run kimi once to refresh it")
        os.environ["CURSOR_AUTH_PATH"] = str(self.write("expired-cursor.json", {"accessToken": self.cursor_token(1000)}))
        self.assertEqual(get_cursor_usage()["error"], "login expired — run cursor-agent login")
        body = {
            "code": "resource_exhausted",
            "message": "insufficient balance",
            "details": [
                {
                    "type": "common.error.v1.ErrorDetail",
                    "debug": {"reason": "REASON_QUOTA_EXCEEDED", "localizedMessage": {"locale": "en-US", "message": "Credits used up."}},
                }
            ],
        }
        exhausted = parse_usage_response(429, json.dumps(body))
        self.assertEqual((exhausted["exhausted"], exhausted["message"]), (True, "Credits used up"))
        self.assertEqual(parse_usage_response(429, "{}")["error"], "Kimi Code rate limited")
        self.assertEqual(_region_of("arn:aws:codewhisperer:evil.example/x:1:profile/a"), "us-east-1")
        self.assertEqual(_region_of("arn:aws:codewhisperer:eu-central-1:1:profile/a"), "eu-central-1")
