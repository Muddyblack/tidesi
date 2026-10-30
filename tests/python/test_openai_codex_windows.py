"""A Codex plan that reports only some of its windows gets rows and pill slots
for exactly those — no empty "Codex 5-hour 0%" for a plan without one."""

import time
import unittest

import _support  # noqa: F401  (sys.path)
from tidesi.normalize import normalize


def envelope(*limits):
    now = int(time.time())
    windows = {name: {"usedPercent": pct, "windowDurationMins": mins, "resetsAt": now + 3600} for name, pct, mins in limits}
    return {
        "id": "openai",
        "now": now,
        "inputs": {"credentials": {"codexLoggedIn": True, "email": "a@b.c"}, "codex": {"rateLimits": windows}},
    }


class CodexWindowsTest(unittest.TestCase):
    def test_expired_login_is_visible_instead_of_silent_stale_credentials(self):
        raw = envelope()
        raw["inputs"]["codexError"] = "token expired"
        provider = normalize(raw)
        self.assertTrue(provider["stale"])
        self.assertIn("expired", provider["error"])
        self.assertIn("codex login", provider["error"])

    def test_weekly_only_plan(self):
        p = normalize(envelope(("primary", 92, 10080)))
        self.assertEqual([w["key"] for w in p["quotaWindows"] if w["key"].startswith("codex")], ["codex_weekly"])
        self.assertEqual([s["pct"] for s in p["slots"]], [92])
        self.assertEqual(p["summary"]["text"], "92%")

    def test_both_windows(self):
        p = normalize(envelope(("primary", 10, 300), ("secondary", 40, 10080)))
        self.assertEqual([w["key"] for w in p["quotaWindows"] if w["key"].startswith("codex")], ["codex_session", "codex_weekly"])
        self.assertEqual([s["pct"] for s in p["slots"]], [10, 40])
        self.assertEqual(p["summary"]["text"], "10%")

    def test_empty_session_placeholder_does_not_create_rows_or_history(self):
        for legacy in (False, True):
            for reset in (None, 0):
                with self.subTest(legacy=legacy, reset=reset):
                    raw = envelope(("secondary", 45, 10080))
                    if legacy:
                        raw["inputs"]["codex"] = {
                            "rate_limit": {
                                "primary_window": {"used_percent": 0, "limit_window_seconds": 18000, "reset_at": reset},
                                "secondary_window": {"used_percent": 45, "limit_window_seconds": 604800, "reset_at": raw["now"] + 3600},
                            }
                        }
                    else:
                        raw["inputs"]["codex"]["rateLimits"]["primary"] = {
                            "usedPercent": 0,
                            "windowDurationMins": 300,
                            "resetsAt": reset,
                        }
                    p = normalize(raw)
                    self.assertEqual([w["key"] for w in p["quotaWindows"]], ["codex_weekly"])
                    self.assertEqual([s["pct"] for s in p["slots"]], [45])
                    self.assertEqual(p["summary"]["text"], "45%")
                    self.assertFalse(p["details"]["codex"]["session"]["available"])
                    self.assertNotIn("cp", p["historyValues"])
                    self.assertTrue(all(w["key"] == "cw" for w in p["chartWindows"]))

    def test_zero_session_with_reset_is_kept_including_spark(self):
        raw = envelope(("primary", 0, 300), ("secondary", 45, 10080))
        raw["inputs"]["codex"]["rateLimitsByLimitId"] = {
            "spark": {
                "limitName": "Spark",
                "primary": {
                    "usedPercent": 0,
                    "windowDurationMins": 300,
                    "resetsAt": raw["now"] + 3600,
                },
            },
        }
        p = normalize(raw)
        self.assertEqual(len(p["quotaWindows"]), 3)
        self.assertTrue(all(w["available"] for w in p["quotaWindows"]))
        self.assertEqual([s["pct"] for s in p["slots"]], [0, 45])

    def test_session_with_usage_but_no_reset_is_kept(self):
        raw = envelope(("primary", 12, 300), ("secondary", 45, 10080))
        raw["inputs"]["codex"]["rateLimits"]["primary"].pop("resetsAt")
        p = normalize(raw)
        self.assertEqual([s["pct"] for s in p["slots"]], [12, 45])


if __name__ == "__main__":
    unittest.main()
