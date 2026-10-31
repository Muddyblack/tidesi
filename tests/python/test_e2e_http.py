"""CLI-to-envelope E2Es for providers that talk to an upstream HTTP API."""

import json
import os
import sqlite3
import sys
import unittest
from contextlib import closing
from unittest import mock

from _e2e import E2ETest, Response
from _support import FIXTURES
from tidesi.http import route_e2e_url


class RouteE2EUrlTest(unittest.TestCase):
    BASE = {"TIDESI_E2E_HTTP_BASE": "http://127.0.0.1:54321"}

    def test_routes_remote_url_and_keeps_query(self):
        with mock.patch.dict(os.environ, self.BASE):
            self.assertEqual(
                route_e2e_url("https://api.deepseek.com/user/balance?region=global"),
                "http://127.0.0.1:54321/https/api.deepseek.com/user/balance?region=global",
            )

    def test_leaves_loopback_urls_alone(self):
        with mock.patch.dict(os.environ, self.BASE):
            self.assertEqual(route_e2e_url("http://127.0.0.1:11434/api/tags"), "http://127.0.0.1:11434/api/tags")

    def test_is_inert_when_unset(self):
        with mock.patch.dict(os.environ, {"TIDESI_E2E_HTTP_BASE": ""}):
            self.assertEqual(route_e2e_url("https://api.deepseek.com/x"), "https://api.deepseek.com/x")

    def test_rejects_anything_but_a_plain_loopback_origin(self):
        for base in ("http://example.com:1", "https://127.0.0.1:1", "http://127.0.0.1", "http://127.0.0.1:1/path", "http://u:p@127.0.0.1:1"):
            with self.subTest(base=base), mock.patch.dict(os.environ, {"TIDESI_E2E_HTTP_BASE": base}):
                with self.assertRaisesRegex(ValueError, "loopback"):
                    route_e2e_url("https://api.deepseek.com/x")


class DeepSeekE2ETest(E2ETest):
    URL = "/https/api.deepseek.com/user/balance"
    SECRET = "E2E_SECRET_DEEPSEEK"

    def run_deepseek(self, **kwargs):
        self.write(".config/deepseek/api-key", self.SECRET)
        return self.provider("deepseek", secrets=[self.SECRET], **kwargs)

    def test_key_file_reaches_request_and_envelope(self):
        self.server.routes[self.URL] = (FIXTURES / "deepseek-response.json").read_text(encoding="utf-8")
        row = self.run_deepseek()
        self.assertEqual(row["details"]["currency"], "USD")
        self.assertEqual(row["historyValues"], {"ds": 12.5})

        (request,) = self.server.requests
        self.assertEqual((request.method, request.path, request.body), ("GET", self.URL, b""))
        self.assertEqual(request.header("authorization"), "Bearer " + self.SECRET)
        self.assert_not_persisted(self.SECRET)

    def test_http_failures_become_normalized_errors(self):
        cases = (
            (Response(status=401), "Invalid DeepSeek API key"),
            (Response(status=403), "Invalid DeepSeek API key"),
            (Response(status=429), "DeepSeek HTTP 429"),
            (Response(status=500), "DeepSeek HTTP 500"),
            (Response("{bad"), "DeepSeek invalid JSON"),
            (Response(drop=True), "DeepSeek network error"),
        )
        for response, error in cases:
            with self.subTest(error=error):
                self.server.routes[self.URL] = response
                self.server.requests.clear()
                row = self.run_deepseek(ok=False)
                self.assertTrue(row["error"].endswith(error), row["error"])
                self.assertEqual(len(self.server.requests), 1)


class MuseQuotaE2ETest(E2ETest):
    SECRET = "E2E_SECRET_MUSE"
    MODEL = "fixture-spark-9.9"

    def test_opt_in_quota_request_reaches_envelope(self):
        subscription = {"window": {"used_percent": 26, "resets_at": 1883456000}, "weekly": {"used_percent": 9, "resets_at": 1884056000}}
        event = {"type": "response.subscription_usage", "subscription": subscription}
        self.server.routes["/https/api.meta.ai/v1/responses"] = Response(
            f"event: response.subscription_usage\ndata: {json.dumps(event)}\n\n", content_type="text/event-stream"
        )
        self.write("muse-auth.json", {"providers": {"meta": {"mechanism": "oauth", "api_key": self.SECRET, "access_token": self.SECRET}}})
        self.write("muse-settings.json", {"schema_version": 1, "provider": "meta", "model": self.MODEL})
        self.write(
            "muse-catalog/6d657461__p746268.json",
            {
                "schema_version": 1,
                "provider_id": "meta",
                "profile_id": "tbh",
                "source": "provider_catalog",
                "rows": [{"model_id": self.MODEL, "display_label": self.MODEL, "visibility": "visible", "is_current": True, "is_default": True}],
            },
        )
        row = self.provider(
            "muse",
            secrets=[self.SECRET],
            config={"museQuota": True},
            META_API_KEY=self.SECRET,
            MUSE_AUTH_PATH=str(self.home / "muse-auth.json"),
            MUSE_SETTINGS_PATH=str(self.home / "muse-settings.json"),
            MUSE_SESSIONS_DIR=str(self.home / "muse-sessions"),
            MUSE_CATALOG_DIR=str(self.home / "muse-catalog"),
        )
        self.assertEqual(row["details"]["quotaError"], "")
        self.assertEqual(row["details"]["current"]["pct"], 26)
        self.assertEqual(row["details"]["weekly"]["pct"], 9)

        (request,) = self.server.requests
        self.assertEqual((request.method, request.path), ("POST", "/https/api.meta.ai/v1/responses"))
        self.assertEqual(request.header("authorization"), "Bearer " + self.SECRET)
        self.assertEqual(request.header("accept"), "text/event-stream")
        body = json.loads(request.body)
        self.assertEqual((body["model"], body["input"], body["stream"], body["store"]), (self.MODEL, "ping", True, False))
        self.assertTrue((self.home / "tidesi-cache" / "muse-quota.json").is_file(), "quota response was not cached")
        self.assert_not_persisted(self.SECRET)


class KeyedProviderE2ETest(E2ETest):
    """A credential is read from env or a login file and reaches the right upstream endpoint."""

    def run_keyed(self, provider, secret, routes, **env):
        self.server.routes.update(routes)
        row = self.provider(provider, secrets=[secret], **env)
        requests = self.server.requests
        self.assertTrue(requests, f"{provider} made no upstream request")
        self.assertTrue(any(secret in request.header("authorization") for request in requests), "credential not sent")
        return row, requests

    def test_mistral(self):
        row, requests = self.run_keyed(
            "mistral",
            "E2E_MISTRAL",
            {"/https/api.mistral.ai/v1/models": {"data": [{"id": "fixture-model"}]}},
            MISTRAL_API_KEY="E2E_MISTRAL",
        )
        self.assertEqual(row["details"]["availableModels"], ["fixture-model"])
        self.assertEqual(requests[0].path, "/https/api.mistral.ai/v1/models")

    def test_openrouter(self):
        row, requests = self.run_keyed(
            "openrouter",
            "E2E_OPENROUTER",
            {
                "/https/openrouter.ai/api/v1/key": {"data": {"label": "fixture", "usage": 2.5}},
                "/https/openrouter.ai/api/v1/credits": {"data": {"total_credits": 10, "total_usage": 2}},
            },
            OPENROUTER_API_KEY="E2E_OPENROUTER",
        )
        self.assertEqual(row["details"]["label"], "fixture")
        self.assertEqual({r.path for r in requests}, {"/https/openrouter.ai/api/v1/key", "/https/openrouter.ai/api/v1/credits"})

    def test_ollama_cloud(self):
        row, requests = self.run_keyed(
            "ollama",
            "E2E_OLLAMA",
            {"/https/ollama.com/api/balance": {"limits": {"session": {"usage": 0.3, "models": []}}}},
            OLLAMA_API_KEY="E2E_OLLAMA",
        )
        self.assertEqual(requests[0].path, "/https/ollama.com/api/balance")
        self.assertTrue(row["quotaWindows"])

    def test_zai(self):
        limits = [{"type": "TOKENS_LIMIT", "percentage": 25, "used": 250, "limit": 1000}]
        _, requests = self.run_keyed(
            "zai",
            "E2E_ZAI",
            {"/https/api.z.ai/api/monitor/usage/quota/limit": {"success": True, "data": {"limits": limits}}},
            ZAI_TOKEN="E2E_ZAI",
        )
        self.assertEqual(requests[0].path.split("?")[0], "/https/api.z.ai/api/monitor/usage/quota/limit")

    def test_kimi(self):
        balance = {"available_balance": 7.5, "voucher_balance": 2, "cash_balance": 5.5}
        row, requests = self.run_keyed("kimi", "E2E_KIMI", {"/https/api.moonshot.ai/v1/users/me/balance": {"data": balance}}, KIMI_API_KEY="E2E_KIMI")
        self.assertEqual(row["details"]["availableBalance"], 7.5)
        self.assertEqual(requests[0].path, "/https/api.moonshot.ai/v1/users/me/balance")

    def test_copilot_token(self):
        quota = {"login": "fixture", "quota_snapshots": {"premium_interactions": {"entitlement": 100, "remaining": 80}}}
        row, requests = self.run_keyed("copilot", "E2E_COPILOT", {"/https/api.github.com/copilot_internal/user": quota}, GITHUB_TOKEN="E2E_COPILOT")
        self.assertEqual(requests[0].path, "/https/api.github.com/copilot_internal/user")
        self.assertTrue(row["details"]["hasKey"])

    def test_kilo(self):
        trpc = [
            {"result": {"data": {"json": {"creditBlocks": [{"amount_mUsd": 1000000, "balance_mUsd": 750000}]}}}},
            {"result": {"data": {"json": {"subscription": {"tier": "tier_49", "currentPeriodUsageUsd": 3, "currentPeriodBaseCreditsUsd": 10}}}}},
        ]
        row, requests = self.run_keyed("kilo", "E2E_KILO", {"/https/app.kilo.ai/api/trpc/": trpc}, KILO_API_KEY="E2E_KILO")
        self.assertIn("user.getCreditBlocks", requests[0].path)
        self.assertTrue(row["details"]["credits"])

    def test_openai_organization_key(self):
        self.write(".codex/auth.json", {})
        _, requests = self.run_keyed(
            "openai",
            "E2E_OPENAI",
            {"/https/api.openai.com/v1/organization/usage/completions": {"data": []}},
            OPENAI_API_KEY="E2E_OPENAI",
        )
        self.assertTrue(requests[0].path.startswith("/https/api.openai.com/v1/organization/usage/completions"))

    @unittest.skipIf(sys.platform == "darwin", "the Claude login lives in the macOS Keychain there")
    def test_claude_oauth_login(self):
        self.write(".claude/.credentials.json", {"claudeAiOauth": {"accessToken": "E2E_CLAUDE", "expiresAt": 4102444800000}})
        _, requests = self.run_keyed(
            "claude",
            "E2E_CLAUDE",
            {"/https/api.anthropic.com/api/oauth/usage": {"five_hour": {"utilization": 20}, "seven_day": {"utilization": 8}}},
        )
        self.assertEqual(requests[0].path, "/https/api.anthropic.com/api/oauth/usage")

    def test_grok_login(self):
        self.write(".grok/auth.json", {"account": {"key": "E2E_GROK"}})
        _, requests = self.run_keyed(
            "grok",
            "E2E_GROK",
            {
                "/https/api.x.ai/v1/me": {"user_id": "u", "team_id": "t"},
                "/https/management-api.x.ai/auth/teams": {"teams": [{"teamId": "t", "name": "fixture"}]},
                "/https/cli-chat-proxy.grok.com/v1/billing": {"config": {}},
            },
        )
        self.assertTrue(any(r.path.startswith("/https/cli-chat-proxy.grok.com/v1/billing") for r in requests))

    def test_cursor_agent_login(self):
        self.write("xdg-config/cursor/auth.json", {"accessToken": "E2E_CURSOR"})
        service = "/https/api2.cursor.sh/aiserver.v1.DashboardService/"
        _, requests = self.run_keyed(
            "cursor",
            "E2E_CURSOR",
            {
                service + "GetCurrentPeriodUsage": {"billingCycleStart": "1787817247821", "planUsage": {"totalPercentUsed": 15}},
                service + "GetPlanInfo": {"planName": "Fixture"},
                service + "GetAggregatedUsageEvents": {},
                service + "GetFilteredUsageEvents": {"usageEventsDisplay": [], "totalUsageEventsCount": 0},
            },
            CURSOR_AUTH_PATH=str(self.home / "xdg-config/cursor/auth.json"),
        )
        self.assertGreaterEqual(len(requests), 3)
        self.assertEqual({r.method for r in requests if "DashboardService" in r.path}, {"POST"})

    def test_kiro_cli_login(self):
        database = self.home / "xdg-data/kiro-cli/data.sqlite3"
        database.parent.mkdir(parents=True)
        token = {"access_token": "E2E_KIRO", "profile_arn": "arn:aws:codewhisperer:us-east-1:123:profile/demo"}
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("CREATE TABLE auth_kv (key TEXT, value TEXT)")
            db.execute("CREATE TABLE state (key TEXT, value TEXT)")
            db.execute("INSERT INTO auth_kv VALUES (?, ?)", ("kirocli:token", json.dumps(token)))
        usage = {"usageBreakdownList": [{"resourceType": "CREDIT", "currentUsageWithPrecision": 12, "usageLimitWithPrecision": 100}]}
        _, requests = self.run_keyed("kiro", "E2E_KIRO", {"/https/q.us-east-1.amazonaws.com/getUsageLimits": usage})
        self.assertEqual(requests[0].path.split("?")[0], "/https/q.us-east-1.amazonaws.com/getUsageLimits")


if __name__ == "__main__":
    unittest.main()
