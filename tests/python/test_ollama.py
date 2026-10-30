"""Ollama Cloud credential resolution and changing usage response shapes."""

import json
import os
from unittest import mock

from _support import IsolatedHomeTest
from tidesi.http import HttpResult
from tidesi.normalize.ollama import normalize_ollama
from tidesi.providers.ollama import get_ollama_usage


class OllamaTest(IsolatedHomeTest):
    def test_opencode_key_is_used_only_for_ollama_cloud(self):
        self.write(
            ".local/share/opencode/auth.json",
            json.dumps(
                {
                    "ollama": {"type": "api", "key": "local-key"},
                    "ollama-cloud": {"type": "oauth", "access": "wrong"},
                }
            ),
        )
        self.assertEqual(get_ollama_usage(), {})
        self.write(
            ".local/share/opencode/auth.json",
            json.dumps(
                {
                    "ollama-cloud": {"type": "api", "key": "opencode-key"},
                }
            ),
        )
        with mock.patch("tidesi.providers.ollama.fetch_json", return_value=HttpResult(200, '{"limits":{}}')) as fetch:
            get_ollama_usage()
            self.assertEqual(fetch.call_args.kwargs["headers"]["Authorization"], "Bearer opencode-key")
            os.environ["OLLAMA_API_KEY"] = "env-key"
            get_ollama_usage()
            self.assertEqual(fetch.call_args.kwargs["headers"]["Authorization"], "Bearer env-key")
            os.environ["WIDGET_OLLAMA_API_KEY"] = "widget-key"
            get_ollama_usage()
            self.assertEqual(fetch.call_args.kwargs["headers"]["Authorization"], "Bearer widget-key")

    def test_no_inference_call_and_status_error(self):
        os.environ["OLLAMA_API_KEY"] = "test-key"
        with mock.patch("tidesi.providers.ollama.fetch_json", return_value=HttpResult(404, "")) as fetch:
            self.assertIn("unavailable", get_ollama_usage()["error"])
            self.assertEqual(fetch.call_args.args[0], "https://ollama.com/api/balance")
            self.assertIsNone(fetch.call_args.kwargs.get("data"))
        for status, expected in ((0, "offline"), (429, "rate limited")):
            with self.subTest(status=status), mock.patch("tidesi.providers.ollama.fetch_json", return_value=HttpResult(status, "")):
                self.assertEqual(get_ollama_usage()["error"], expected)

    def test_opencode_uses_xdg_location_even_on_windows(self):
        # Our platform helper normally resolves data_home to LOCALAPPDATA on
        # Windows, but OpenCode uses ~/.local/share on every platform.
        os.environ.pop("XDG_DATA_HOME", None)
        self.write(".local/share/opencode/auth.json", {"ollama-cloud": {"type": "api", "key": "xdg-key"}})
        with (
            mock.patch("tidesi.paths.IS_WINDOWS", True),
            mock.patch("tidesi.providers.ollama.fetch_json", return_value=HttpResult(200, '{"limits":{}}')) as fetch,
        ):
            get_ollama_usage()
            self.assertEqual(fetch.call_args.kwargs["headers"]["Authorization"], "Bearer xdg-key")

    def test_empty_or_error_response_is_not_a_missing_key(self):
        os.environ["OLLAMA_API_KEY"] = "test-key"
        for body in ("{}", "[]", '{"error":"unexpected server content"}'):
            with self.subTest(body=body), mock.patch("tidesi.providers.ollama.fetch_json", return_value=HttpResult(200, body)):
                self.assertEqual(get_ollama_usage(), {"error": "Ollama Cloud: invalid usage response"})

    def test_legacy_and_monthly_buckets(self):
        legacy = {
            "limits": {
                "session": {"usage": 0.25, "models": [{"name": "glm", "request_count": 3}]},
                "weekly": {"usage": 0.5, "models": []},
            },
            "activity": {"cost": "1.25"},
        }
        result = normalize_ollama({"now": 100, "inputs": {"usage": legacy}})
        self.assertTrue(result["ok"])
        self.assertEqual([w["pct"] for w in result["quotaWindows"]], [25, 50])
        self.assertEqual(result["details"]["models"]["session"][0]["requestCount"], 3)
        self.assertEqual(result["details"]["activityCost"], "1.25")
        self.assertEqual(result["historyValues"], {"ollama_session": 25, "ollama_weekly": 50})
        self.assertEqual([w["key"] for w in result["chartWindows"]], ["ollama_session", "ollama_session", "ollama_weekly", "ollama_weekly"])
        monthly = normalize_ollama({"now": 100, "inputs": {"usage": {"limits": {"monthly": {"usage": 0.4}}}}})
        self.assertTrue(monthly["ok"])
        self.assertEqual(monthly["quotaWindows"][0]["label"], "Monthly")
        self.assertEqual(monthly["summary"]["pct"], 40)
        self.assertEqual(monthly["historyValues"], {"ollama_monthly": 40})
        self.assertTrue(all(w["key"] == "ollama_monthly" for w in monthly["chartWindows"]))
        self.assertTrue(all(w["resetAt"] == 0 for w in monthly["quotaWindows"]))

    def test_unknown_shape_is_unavailable_not_zero(self):
        result = normalize_ollama({"now": 100, "inputs": {"usage": {"limits": {"monthly": {"credits": 10}}}}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["quotaWindows"], [])

    def test_balance_legacy_remaining_percent_becomes_used_percent_with_resets(self):
        balance = {
            "included": {
                "session": {"remaining_percent": 75, "resets_at": "2026-10-23T07:00:00Z"},
                "weekly": {"remaining_percent": 40, "resets_at": "2026-10-25T00:00:00Z"},
            },
            "purchased": {"balance_usd": 25},
        }
        result = normalize_ollama({"now": 100, "inputs": {"usage": balance}})
        self.assertTrue(result["ok"])
        self.assertEqual([w["pct"] for w in result["quotaWindows"][:2]], [25, 60])
        self.assertTrue(all(w["resetAt"] > 0 for w in result["quotaWindows"][:2]))
        self.assertFalse(result["details"]["resetUnavailable"])
        self.assertEqual(result["historyValues"], {"ollama_session": 25, "ollama_weekly": 60})
        self.assertFalse(result["quotaWindows"][-1]["showMeter"])

    def test_balance_included_credits_use_the_actual_allowance_and_reset(self):
        balance = {
            "included": {"balance_usd": 72.5, "allowance_usd": 100, "period": {"until": "2026-10-25T00:00:00Z"}},
            "purchased": {"balance_usd": 25},
        }
        result = normalize_ollama({"now": 100, "inputs": {"usage": balance}})
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(result["summary"]["pct"], 27.5)
        self.assertEqual(result["quotaWindows"][0]["label"], "Included credits")
        self.assertIn("72.50", result["quotaWindows"][0]["detail"])
        self.assertGreater(result["quotaWindows"][0]["resetAt"], 0)

    def test_activity_totals_are_not_invented_quota_limits(self):
        result = normalize_ollama({"now": 100, "inputs": {"usage": {"totals": {"request_count": 15}, "buckets": []}}})
        self.assertFalse(result["ok"])
        self.assertEqual(result["quotaWindows"], [])

    def test_legacy_zero_cost_does_not_imply_free_usage(self):
        limits = {"session": {"usage": 0.058}, "weekly": {"usage": 0.183}}
        legacy = normalize_ollama({"now": 100, "inputs": {"usage": {"limits": limits, "activity": {"cost": "0.00000"}}}})
        self.assertEqual(legacy["details"]["activityCost"], "")
        monthly = normalize_ollama({"now": 100, "inputs": {"usage": {"limits": {"monthly": {"usage": 0.1}}, "activity": {"cost": "0.00000"}}}})
        self.assertEqual(monthly["details"]["activityCost"], "0.00000")

    def test_malformed_values_do_not_become_available_zero(self):
        for value in (None, True, "0.5", -0.1, float("nan"), float("inf")):
            with self.subTest(value=value):
                result = normalize_ollama({"now": 100, "inputs": {"usage": {"limits": {"session": {"usage": value}}}}})
                self.assertFalse(result["ok"])
                self.assertEqual(result["historyValues"], {})
