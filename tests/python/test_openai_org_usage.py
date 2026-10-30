from unittest import mock

from _support import IsolatedHomeTest
from tidesi import collect
from tidesi.http import HttpResult
from tidesi.normalize import normalize


class OrganizationUsageTest(IsolatedHomeTest):
    def setUp(self) -> None:
        super().setUp()
        self.creds = {"openaiApiKey": "fixture-secret", "codexAccessToken": "fixture-token", "codexLoggedIn": True}
        self.limits = {"rateLimits": {"primary": {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": 1_800_003_600}}}
        for patch in (
            mock.patch.object(collect, "get_openai_credentials", side_effect=lambda: self.creds),
            mock.patch.object(collect, "get_codex_rate_limits", side_effect=lambda *_: self.limits),
            mock.patch.object(collect, "get_codex_stats", return_value={}),
            mock.patch.object(collect, "provider_status", return_value=None),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_organization_failure_preserves_status_without_echoing_the_response(self) -> None:
        with mock.patch.object(collect, "fetch_json", return_value=HttpResult(403, '{"error":"fixture-secret"}')):
            raw = collect.collect_openai(1_800_000_000)
        self.assertIsNone(raw["inputs"]["orgUsage"])
        self.assertIn("403", raw["inputs"]["orgUsageError"])
        self.assertNotIn("fixture-secret", raw["inputs"]["orgUsageError"])

    def test_failed_org_usage_is_visible_without_hiding_healthy_codex_limits(self) -> None:
        with mock.patch.object(collect, "fetch_json", return_value=HttpResult(403, "denied")):
            provider = normalize(collect.collect_openai(1_800_000_000))
        self.assertEqual(provider["summary"]["pct"], 42)
        self.assertFalse(provider["details"]["organizationUsageAvailable"])
        self.assertIn("403", provider["details"]["organizationUsageError"])
        self.assertEqual(provider["error"], "")
        warning = provider["quotaWindows"][-1]
        self.assertFalse(warning["showMeter"])
        self.assertIn("403", warning["detail"])

    def test_api_key_alone_does_not_imply_organization_usage_is_available(self) -> None:
        self.creds = {"openaiApiKey": "fixture-secret"}
        self.limits = {"_codexNoLimits": True}
        with mock.patch.object(collect, "fetch_json", return_value=HttpResult(401, "denied")):
            provider = normalize(collect.collect_openai(1_800_000_000))
        self.assertFalse(provider["details"]["organizationUsageAvailable"])
        self.assertTrue(provider["stale"])
        self.assertIn("401", provider["error"])
        self.assertIn("401", provider["quotaWindows"][0]["detail"])

    def test_valid_empty_usage_is_a_successful_zero_not_an_error(self) -> None:
        self.creds = {"openaiApiKey": "fixture-secret"}
        self.limits = {"_codexNoLimits": True}
        with mock.patch.object(collect, "fetch_json", return_value=HttpResult(200, '{"data":[]}')):
            provider = normalize(collect.collect_openai(1_800_000_000))
        self.assertTrue(provider["details"]["organizationUsageAvailable"])
        self.assertEqual(provider["details"]["organizationUsageError"], "")
        self.assertEqual(provider["error"], "")

    def test_malformed_success_is_not_reported_as_available(self) -> None:
        for body in ("[]", "{}", '{"data":{}}', '{"data":[1]}', '{"data":[{"results":[1]}]}'):
            with self.subTest(body=body), mock.patch.object(collect, "fetch_json", return_value=HttpResult(200, body)):
                raw = collect.collect_openai(1_800_000_000)
            self.assertIsNone(raw["inputs"]["orgUsage"])
            self.assertTrue(raw["inputs"]["orgUsageError"])
