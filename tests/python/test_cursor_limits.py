"""Do not confuse missing Cursor agent quota with an unused billing allowance."""

import unittest

from _support import raw_fixture
from tidesi.normalize.cursor import normalize_cursor


class CursorLimitsTest(unittest.TestCase):
    def test_free_and_hobby_without_allowance_have_no_quota_or_history(self):
        for plan in ("Free", "Hobby", " free "):
            with self.subTest(plan=plan):
                raw = raw_fixture("cursor-success")
                res = raw["inputs"]["usage"]
                res["plan"]["planInfo"]["planName"] = plan
                res["usage"]["planUsage"] = {
                    "totalPercentUsed": 0,
                    "autoPercentUsed": 0,
                    "apiPercentUsed": 0,
                }
                result = normalize_cursor(raw)
                self.assertFalse(result["ok"])
                self.assertEqual(result["quotaWindows"], [])
                self.assertEqual(result["historyValues"], {})
                self.assertFalse(result["summary"]["hasChart"])
                self.assertTrue(result["details"]["stats"]["available"])
                self.assertTrue(result["details"]["loggedIn"])

    def test_missing_meter_is_not_zero(self):
        raw = raw_fixture("cursor-success")
        raw["inputs"]["usage"]["usage"]["planUsage"] = {}
        result = normalize_cursor(raw)
        self.assertFalse(result["ok"])
        self.assertEqual(result["historyValues"], {})

    def test_unused_paid_allowance_is_still_zero(self):
        raw = raw_fixture("cursor-success")
        pu = raw["inputs"]["usage"]["usage"]["planUsage"]
        pu.update(totalPercentUsed=0, includedSpend=0, autoPercentUsed=0, apiPercentUsed=0)
        result = normalize_cursor(raw)
        self.assertTrue(result["ok"])
        self.assertEqual(result["historyValues"], {"cu": 0, "cua": 0, "cup": 0})

    def test_spend_can_supply_missing_percentage(self):
        raw = raw_fixture("cursor-success")
        del raw["inputs"]["usage"]["usage"]["planUsage"]["totalPercentUsed"]
        result = normalize_cursor(raw)
        self.assertTrue(result["ok"])
        self.assertEqual(result["historyValues"], {"cu": 62.5, "cua": 25, "cup": 75})
