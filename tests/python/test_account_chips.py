import re
import unittest
from pathlib import Path

from _support import REPO
from tidesi.contract import effort_chip, plan_chip


class AccountChipTest(unittest.TestCase):
    def test_effort_reads_the_same_on_every_provider(self):
        tones = {"minimal": "good", "low": "good", "medium": "muted", "high": "accent", "xhigh": "warn", "max": "warn"}
        for level, tone in tones.items():
            with self.subTest(level=level):
                chip = effort_chip(level)
                self.assertEqual(chip["tone"], tone)
                self.assertTrue(chip["text"].startswith("effort: "))
                self.assertEqual(chip["tooltip"], f"Reasoning effort: {level}")
        self.assertEqual(effort_chip("HIGH")["tone"], "accent")
        self.assertEqual(effort_chip("turbo")["tone"], "muted")
        self.assertIsNone(effort_chip(""))
        self.assertIsNone(effort_chip(None))

    def test_plan_is_upper_case_and_grey_when_free(self):
        self.assertEqual(plan_chip("pro")["text"], "PRO")
        self.assertEqual(plan_chip("pro")["tone"], "plan")
        self.assertEqual(plan_chip("Free")["tone"], "muted")
        self.assertEqual(plan_chip("pro")["color"], "")
        self.assertIsNone(plan_chip(""))

    def test_providers_use_the_shared_plan_and_effort_chips(self):
        # A provider building its own plan or effort chip is how they drifted apart.
        offenders = []
        for path in sorted((Path(REPO) / "backend" / "tidesi" / "normalize").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            if re.search(r'chip\([^)]*,\s*"plan"', source) or re.search(r'chip\(tr\("effort', source):
                offenders.append(path.name)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
