import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from _e2e_harness import ChildProcess


class OpenAICodexModeE2ETest(unittest.TestCase):
    def test_codex_login_and_rate_limits_reach_openai_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex_home = home / ".codex"
            codex_home.mkdir()
            (codex_home / "auth.json").write_text(
                json.dumps(
                    {
                        "tokens": {
                            "access_token": "isolated-codex-token",
                            "account_id": "fixture-account",
                        }
                    }
                ),
                encoding="utf-8",
            )
            now = int(time.time())
            response = home / "codex-limits.json"
            response.write_text(
                json.dumps(
                    {
                        "rateLimits": {
                            "primary": {
                                "usedPercent": 17,
                                "windowDurationMins": 300,
                                "resetsAt": now + 3600,
                            },
                            "secondary": {
                                "usedPercent": 42,
                                "windowDurationMins": 10080,
                                "resetsAt": now + 86400,
                            },
                            "planType": "plus",
                        }
                    }
                ),
                encoding="utf-8",
            )
            child = ChildProcess(
                home,
                extra_env={
                    "CODEX_HOME": str(codex_home),
                    "CODEX_USAGE_RESPONSE_FILE": str(response),
                },
            )
            cache = Path(child.env["TIDESI_CACHE_DIR"])
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "status-openai.json").write_text("{}", encoding="utf-8")
            Path(child.env["TIDESI_CONFIG"]).write_text(json.dumps({"providers": {"openai": True}}), encoding="utf-8")
            result = child.run([sys.executable, "-m", "tidesi", "--provider", "openai"])

        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schemaVersion"], 1)
        self.assertEqual([row["id"] for row in envelope["providers"]], ["openai"])
        provider = envelope["providers"][0]
        self.assertTrue(provider["ok"], provider["error"])
        self.assertTrue(provider["details"]["codexLoggedIn"])
        self.assertEqual(provider["details"]["accountId"], "fixture-account")
        self.assertEqual(provider["summary"]["text"], "17%")
        self.assertEqual(
            [window["key"] for window in provider["quotaWindows"]],
            ["codex_session", "codex_weekly"],
        )


if __name__ == "__main__":
    unittest.main()
