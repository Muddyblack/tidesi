"""Replay every recorded provider response through the in-process normalizers."""

import json
import re
import unittest

from _support import fixture, provider_fixtures, raw_fixture
from tidesi.config import ALL_PROVIDERS

SECRET = re.compile(
    r"secret|sk-ant-|sk-oauth|gh-secret|zai-secret|xai-secret|ds-secret|or-secret|codex-secret",
    re.I,
)


class FixtureContractTest(unittest.TestCase):
    def test_every_fixture_satisfies_the_provider_contract(self):
        names = provider_fixtures()
        self.assertEqual({raw_fixture(name)["id"] for name in names}, set(ALL_PROVIDERS))
        required = {
            "id",
            "label",
            "accent",
            "ok",
            "stale",
            "error",
            "updatedAt",
            "summary",
            "quotaWindows",
            "chartWindows",
            "slots",
            "historyValues",
            "details",
        }
        for name in names:
            with self.subTest(fixture=name):
                result = fixture(name)
                self.assertEqual(result["id"], raw_fixture(name)["id"])
                self.assertIsInstance(result["label"], str)
                self.assertTrue(required <= result.keys())
                self.assertTrue({"pct", "text", "detail", "hasChart"} <= result["summary"].keys())
                self.assertIsInstance(result["quotaWindows"], list)
                self.assertIsInstance(result["historyValues"], dict)
                self.assertIsInstance(result["error"], str)
                self.assertFalse(SECRET.search(json.dumps(result)))
                status = result["details"]["status"]
                self.assertIsInstance(status["indicator"], str)
                self.assertIsInstance(status["url"], str)
                self.assertIsInstance(status["components"], list)
                self.assertIsInstance(status["incidents"], list)
                for window in result["quotaWindows"]:
                    reset = window["resetAt"]
                    self.assertIn(type(reset), (int, float))
                    self.assertTrue(reset == 0 or reset > 1_000_000_000)
                series = set(result["historyValues"])
                for window in result["quotaWindows"]:
                    # The period comparison reads the window's length.
                    if "periodMs" in window:
                        self.assertIs(type(window["periodMs"]), int)
                        self.assertGreaterEqual(window["periodMs"], 3600000)
                        self.assertLessEqual(window["periodMs"], 31 * 86400000)
                    # The ETA reads the row's historyKey series against the
                    # row's pct, so the key has to be one this result records.
                    if "historyKey" not in window:
                        continue
                    self.assertIsInstance(window["historyKey"], str)
                    self.assertTrue(window["historyKey"])
                    if window["available"] and window["showMeter"] and window["pct"] > 0:
                        self.assertIn(window["historyKey"], series, window["key"])
                        self.assertEqual(result["historyValues"][window["historyKey"]], window["pct"], window["key"])
                for chart in result["chartWindows"]:
                    self.assertIsInstance(chart["id"], str)
                    self.assertIsInstance(chart["key"], str)
                    for key in ("label", "granularity"):
                        self.assertIsInstance(chart[key], str)
                    for key in ("raw", "resets"):
                        self.assertIs(type(chart[key]), bool)
                    for key in ("size", "periodMs", "resetAt"):
                        self.assertIn(type(chart[key]), (int, float))
                    self.assertGreater(chart["size"], 0)
                    if chart["resets"]:
                        self.assertGreater(chart["periodMs"], 0)
                    else:
                        self.assertEqual(chart["periodMs"], 0)
                    if series:
                        self.assertIn(chart["key"], series)

    def test_success_and_error_fixture_values(self):
        cases = {
            "claude-success": (True, "", {"s": 23, "w": 61, "weekly_scoped": 44}),
            "openai-codex-success": (True, "", {"cp": 58, "cw": 30, "cx_spark_s": 90, "cx_spark_w": 9}),
            "antigravity-success": (True, "", {"ag": 60, "agg": 40, "age": 100}),
            "kiro-success": (True, "", {"kr": 25}),
            "mistral-success": (True, "", {"mv": 12.5}),
            "openrouter-success": (True, "", {"or": 32.5}),
            "grok-billing": (True, "", {"gr": 42}),
            "zai-success": (True, "", {"za": 25, "za7": 0, "zat": 40}),
            "copilot-success": (True, "", {"gh": 25}),
            "deepseek-success": (True, "", {"ds": 12.5}),
            "kimi-success": (True, "", {"km": 49.58894}),
            "cursor-success": (True, "", {"cu": 62.5, "cua": 25, "cup": 75}),
            "muse-success": (True, "", {"mu": 150000}),
            "claude-missing-credentials": (False, "Claude not logged in", {}),
            "openai-missing-credentials": (False, "OpenAI: no API key or Codex login", {}),
            "zai-invalid-token": (False, "Z.AI: Invalid Z.AI token", {}),
        }
        for name, expected in cases.items():
            with self.subTest(fixture=name):
                result = fixture(name)
                self.assertEqual((result["ok"], result["error"], result["historyValues"]), expected)

    def test_regression_details_from_recorded_responses(self):
        claude = fixture("claude-success")
        self.assertEqual((claude["details"]["session"]["pct"], claude["details"]["weekly"]["pct"]), (23, 61))
        self.assertEqual([w["id"] for w in claude["chartWindows"]], ["session", "day", "weekly", "monthly"])
        self.assertEqual(claude["details"]["status"]["components"], ["API (degraded performance)"])

        codex = fixture("openai-legacy-windows")["details"]["codex"]
        self.assertEqual((codex["session"]["pct"], codex["weekly"]["pct"]), (20, 70))
        self.assertEqual(codex["additional"][0]["name"], "GPT-5")

        zai = fixture("zai-absolute-reset")
        self.assertEqual(zai["details"]["token"]["resetAt"], 1785007200)
        self.assertTrue(all(w["resetAt"] == 0 or 1785000000 < w["resetAt"] < 1790184000 for w in zai["quotaWindows"]))

        today = fixture("zai-today")["quotaWindows"][-1]
        self.assertEqual(
            (today["label"], today["detail"], today["note"], today["showMeter"]), ("Today (Aug 11)", "41.18M tokens", "370 calls", False)
        )

        kimi = fixture("kimi-code-success")
        self.assertEqual([w["label"] for w in kimi["quotaWindows"]], ["5-hour limit", "Weekly limit", "Extra usage"])
        self.assertEqual(kimi["historyValues"], {"kc": 30, "kcw": 45})


if __name__ == "__main__":
    unittest.main()
