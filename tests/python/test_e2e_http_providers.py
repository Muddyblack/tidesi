"""Hermetic CLI-to-envelope HTTP coverage for key-backed providers."""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from _e2e_harness import ChildProcess, LoopbackHTTPServer, LoopbackResponse, scrub


class HttpProviderE2ETest(unittest.TestCase):
    def run_provider(self, provider, secret, routes, setup=None, extra_env=None):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)

            def response(request):
                for path, body in routes.items():
                    if request.path.startswith(path):
                        return LoopbackResponse(body=json.dumps(body))
                return LoopbackResponse(status=404)

            with LoopbackHTTPServer(response) as server:
                child = ChildProcess(home, http_base=server.base_url, extra_env=extra_env)
                Path(child.env["TIDESI_CONFIG"]).write_text(json.dumps({"providers": {provider: True}}), encoding="utf-8")
                if setup is not None:
                    setup(home)
                if extra_env is None:
                    key_path = Path(child.env["XDG_CONFIG_HOME"]) / provider / "api-key"
                    key_path.parent.mkdir(parents=True, exist_ok=True)
                    key_path.write_text(secret, encoding="utf-8")
                result = child.run([sys.executable, "-m", "tidesi", "--provider", provider])

            transcript = result.stdout + result.stderr
            self.assertNotIn(secret, transcript, "credential leaked into CLI output")
            self.assertEqual(result.returncode, 0, scrub(transcript, secret))
            envelope = json.loads(result.stdout)
            self.assertEqual(envelope["schemaVersion"], 1)
            self.assertEqual([row["id"] for row in envelope["providers"]], [provider])
            self.assertTrue(envelope["providers"][0]["ok"], envelope["providers"][0]["error"])
            self.assertTrue(server.requests, f"{provider} did not make a loopback request")
            self.assertTrue(any(request.header("authorization") for request in server.requests))
            return envelope["providers"][0], server.requests

    def test_mistral_api_key_reaches_models_api_and_envelope(self):
        provider, requests = self.run_provider(
            "mistral",
            "E2E_MISTRAL_SECRET",
            {"/https/api.mistral.ai/v1/models": {"data": [{"id": "fixture-model"}]}},
            extra_env={"MISTRAL_API_KEY": "E2E_MISTRAL_SECRET"},
        )
        self.assertEqual(provider["details"]["availableModels"], ["fixture-model"])
        self.assertEqual(requests[0].path, "/https/api.mistral.ai/v1/models")
        self.assertEqual(requests[0].header("authorization"), "Bearer E2E_MISTRAL_SECRET")

    def test_openrouter_key_and_balance_requests_reach_envelope(self):
        provider, requests = self.run_provider(
            "openrouter",
            "E2E_OPENROUTER_SECRET",
            {
                "/https/openrouter.ai/api/v1/key": {"data": {"label": "fixture", "usage": 2.5}},
                "/https/openrouter.ai/api/v1/credits": {"data": {"total_credits": 10, "total_usage": 2}},
            },
            extra_env={"OPENROUTER_API_KEY": "E2E_OPENROUTER_SECRET"},
        )
        self.assertEqual(provider["details"]["label"], "fixture")
        self.assertEqual({request.path for request in requests}, {"/https/openrouter.ai/api/v1/key", "/https/openrouter.ai/api/v1/credits"})

    def test_ollama_cloud_key_reaches_balance_api_and_envelope(self):
        provider, requests = self.run_provider(
            "ollama",
            "E2E_OLLAMA_SECRET",
            {"/https/ollama.com/api/balance": {"limits": {"session": {"usage": 0.3, "models": []}}}},
            extra_env={"OLLAMA_API_KEY": "E2E_OLLAMA_SECRET"},
        )
        self.assertEqual(requests[0].path, "/https/ollama.com/api/balance")
        self.assertTrue(provider["quotaWindows"])

    def test_zai_token_reaches_quota_api_and_envelope(self):
        provider, requests = self.run_provider(
            "zai",
            "E2E_ZAI_SECRET",
            {
                "/https/api.z.ai/api/monitor/usage/quota/limit": {
                    "success": True,
                    "data": {"limits": [{"type": "TOKENS_LIMIT", "percentage": 25, "used": 250, "limit": 1000}]},
                }
            },
            extra_env={"ZAI_TOKEN": "E2E_ZAI_SECRET"},
        )
        self.assertTrue(provider["ok"])
        self.assertEqual(requests[0].path.split("?", 1)[0], "/https/api.z.ai/api/monitor/usage/quota/limit")

    def test_kimi_api_key_reaches_moonshot_balance_and_envelope(self):
        provider, requests = self.run_provider(
            "kimi",
            "E2E_KIMI_SECRET",
            {"/https/api.moonshot.ai/v1/users/me/balance": {"data": {"available_balance": 7.5, "voucher_balance": 2, "cash_balance": 5.5}}},
            extra_env={"KIMI_API_KEY": "E2E_KIMI_SECRET"},
        )
        self.assertEqual(provider["details"]["availableBalance"], 7.5)
        self.assertEqual(requests[0].path, "/https/api.moonshot.ai/v1/users/me/balance")

    def test_claude_oauth_token_reaches_usage_api_and_envelope(self):
        def setup(home):
            config = home / ".claude"
            config.mkdir()
            (config / ".credentials.json").write_text(
                json.dumps({"claudeAiOauth": {"accessToken": "E2E_CLAUDE_SECRET", "expiresAt": 4102444800000}}), encoding="utf-8"
            )

        provider, requests = self.run_provider(
            "claude",
            "E2E_CLAUDE_SECRET",
            {"/https/api.anthropic.com/api/oauth/usage": {"five_hour": {"utilization": 20}, "seven_day": {"utilization": 8}}},
            setup=setup,
        )
        self.assertEqual(requests[0].path, "/https/api.anthropic.com/api/oauth/usage")
        self.assertEqual(requests[0].header("authorization"), "Bearer E2E_CLAUDE_SECRET")
        self.assertTrue(provider["ok"])

    def test_openai_organization_key_reaches_usage_api_and_envelope(self):
        provider, requests = self.run_provider(
            "openai",
            "E2E_OPENAI_SECRET",
            {"/https/api.openai.com/v1/organization/usage/completions": {"data": []}},
            extra_env={"OPENAI_API_KEY": "E2E_OPENAI_SECRET"},
        )
        self.assertTrue(any(request.path.startswith("/https/api.openai.com/v1/organization/usage/completions") for request in requests))
        self.assertEqual(provider["id"], "openai")

    def test_copilot_token_reaches_internal_quota_api_and_envelope(self):
        provider, requests = self.run_provider(
            "copilot",
            "E2E_COPILOT_SECRET",
            {
                "/https/api.github.com/copilot_internal/user": {
                    "login": "fixture",
                    "quota_snapshots": {"premium_interactions": {"entitlement": 100, "remaining": 80}},
                }
            },
            extra_env={"GITHUB_TOKEN": "E2E_COPILOT_SECRET"},
        )
        self.assertTrue(any(request.path == "/https/api.github.com/copilot_internal/user" for request in requests))
        self.assertTrue(provider["details"]["hasKey"])

    def test_kilo_api_key_reaches_trpc_and_envelope(self):
        body = [
            {"result": {"data": {"json": {"creditBlocks": [{"amount_mUsd": 1000000, "balance_mUsd": 750000}]}}}},
            {"result": {"data": {"json": {"subscription": {"tier": "tier_49", "currentPeriodUsageUsd": 3, "currentPeriodBaseCreditsUsd": 10}}}}},
        ]
        provider, requests = self.run_provider(
            "kilo",
            "E2E_KILO_SECRET",
            {"/https/app.kilo.ai/api/trpc/": body},
            extra_env={"KILO_API_KEY": "E2E_KILO_SECRET"},
        )
        self.assertEqual(requests[0].method, "GET")
        self.assertIn("user.getCreditBlocks", requests[0].path)
        self.assertTrue(provider["details"]["credits"])

    def test_grok_oauth_token_reaches_billing_api_and_envelope(self):
        def setup(home):
            auth = home / ".grok"
            auth.mkdir(parents=True)
            (auth / "auth.json").write_text(json.dumps({"account": {"key": "E2E_GROK_SECRET"}}), encoding="utf-8")

        provider, requests = self.run_provider(
            "grok",
            "E2E_GROK_SECRET",
            {
                "/https/api.x.ai/v1/me": {"user_id": "u", "team_id": "t"},
                "/https/management-api.x.ai/auth/teams": {"teams": [{"teamId": "t", "name": "fixture"}]},
                "/https/cli-chat-proxy.grok.com/v1/billing": {"config": {}},
            },
            setup=setup,
        )
        self.assertTrue(any(request.path.startswith("/https/cli-chat-proxy.grok.com/v1/billing") for request in requests))
        self.assertEqual(provider["id"], "grok")

    def test_cursor_agent_login_reaches_dashboard_and_envelope(self):
        def setup(home):
            auth = Path(home / "xdg-config" / "cursor" / "auth.json")
            auth.parent.mkdir(parents=True)
            auth.write_text(json.dumps({"accessToken": "E2E_CURSOR_SECRET"}), encoding="utf-8")

        provider, requests = self.run_provider(
            "cursor",
            "E2E_CURSOR_SECRET",
            {
                "/https/api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage": {
                    "billingCycleStart": "1787817247821",
                    "planUsage": {"totalPercentUsed": 15, "autoPercentUsed": 10, "apiPercentUsed": 5},
                },
                "/https/api2.cursor.sh/aiserver.v1.DashboardService/GetPlanInfo": {"planName": "Fixture"},
                "/https/api2.cursor.sh/aiserver.v1.DashboardService/GetAggregatedUsageEvents": {},
                "/https/api2.cursor.sh/aiserver.v1.DashboardService/GetFilteredUsageEvents": {"usageEventsDisplay": [], "totalUsageEventsCount": 0},
            },
            setup=setup,
        )
        self.assertGreaterEqual(len(requests), 3)
        self.assertTrue(all(request.method == "POST" for request in requests if "DashboardService" in request.path))
        self.assertEqual(provider["id"], "cursor")

    def test_kiro_cli_login_reaches_usage_limits_and_envelope(self):
        def setup(home):
            database = home / "xdg-data" / "kiro-cli" / "data.sqlite3"
            database.parent.mkdir(parents=True)
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE auth_kv (key TEXT, value TEXT)")
                connection.execute("CREATE TABLE state (key TEXT, value TEXT)")
                connection.execute(
                    "INSERT INTO auth_kv VALUES (?, ?)",
                    (
                        "kirocli:token",
                        json.dumps({"access_token": "E2E_KIRO_SECRET", "profile_arn": "arn:aws:codewhisperer:us-east-1:123:profile/demo"}),
                    ),
                )
            connection.close()

        provider, requests = self.run_provider(
            "kiro",
            "E2E_KIRO_SECRET",
            {
                "/https/q.us-east-1.amazonaws.com/getUsageLimits": {
                    "usageBreakdownList": [{"resourceType": "CREDIT", "currentUsageWithPrecision": 12, "usageLimitWithPrecision": 100}]
                }
            },
            setup=setup,
        )
        self.assertEqual(requests[0].path.split("?", 1)[0], "/https/q.us-east-1.amazonaws.com/getUsageLimits")
        self.assertTrue(provider["ok"])


if __name__ == "__main__":
    unittest.main()
