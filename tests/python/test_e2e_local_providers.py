"""CLI E2Es for providers backed by local files or a local executable."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from _e2e_harness import ChildProcess, install_fake_cli


class LocalProviderE2ETest(unittest.TestCase):
    def invoke(self, provider, seed, extra_env=None, bin_dir=None):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = seed(home)
            if extra_env:
                env.update(extra_env)
            child = ChildProcess(home, extra_env=env, bin_dir=bin_dir)
            Path(child.env["TIDESI_CONFIG"]).write_text(json.dumps({"providers": {provider: True}}), encoding="utf-8")
            cache = Path(child.env["TIDESI_CACHE_DIR"])
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "pricing-models-v2.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "source": "https://models.dev/api.json",
                        "fetchedAt": time.time(),
                        "checkedAt": time.time(),
                        "providers": {},
                        "error": "",
                    }
                ),
                encoding="utf-8",
            )
            (cache / "status-cline.json").write_text("{}", encoding="utf-8")
            result = child.run([sys.executable, "-m", "tidesi", "--provider", provider])
            self.assertEqual(result.returncode, 0, result.stderr)
            envelope = json.loads(result.stdout)
            self.assertEqual(envelope["schemaVersion"], 1)
            self.assertEqual([row["id"] for row in envelope["providers"]], [provider])
            row = envelope["providers"][0]
            self.assertTrue(row["ok"], row["error"])
            return row

    def test_junie_event_log_reaches_cli_envelope(self):
        def seed(home):
            sid = "session-261001-080000-e2e"
            root = home / ".junie/sessions"
            (root / sid).mkdir(parents=True)
            index = {"sessionId": sid, "createdAt": 1790755200000, "updatedAt": 1790841600000}
            (root / "index.jsonl").write_text(json.dumps(index) + "\n", encoding="utf-8")
            usage = {"model": "fixture", "inputTokens": 100, "outputTokens": 20}
            agent_event = {"kind": "LlmResponseMetadataEvent", "modelUsage": [usage]}
            event = {"kind": "SessionA2uxEvent", "timestampMs": 1790841600000, "event": {"agentEvent": agent_event}}
            (root / sid / "events.jsonl").write_text(json.dumps(event), encoding="utf-8")
            return {}

        self.assertEqual(self.invoke("junie", seed)["details"]["stats"]["totalTokens"], 120)

    def test_pi_session_file_reaches_cli_envelope(self):
        def seed(home):
            path = home / ".pi/agent/sessions/--work-demo--/2026-10-01T08-00-00_e2e.jsonl"
            path.parent.mkdir(parents=True)
            header = {"type": "session", "version": 3, "id": "e2e", "cwd": "/tmp/work/demo"}
            usage = {"input": 100, "output": 20, "cacheRead": 0, "cacheWrite": 0}
            assistant = {"role": "assistant", "provider": "anthropic", "model": "fixture", "usage": usage}
            message = {"type": "message", "id": "turn", "timestamp": "2026-10-01T08:00:00Z", "message": assistant}
            path.write_text("\n".join(map(json.dumps, (header, message))), encoding="utf-8")
            return {}

        self.assertEqual(self.invoke("pi", seed)["details"]["stats"]["totalTokens"], 120)

    def test_cline_session_file_reaches_cli_envelope(self):
        def seed(home):
            root = home / ".cline/data/sessions"
            path = root / "e2e/e2e.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps(
                    {
                        "started_at": "2026-09-17T00:00:00Z",
                        "workspace_root": "/workspace/demo",
                        "metadata": {"aggregateUsage": {"inputTokens": 100, "outputTokens": 10}},
                    }
                ),
                encoding="utf-8",
            )
            return {"CLINE_SESSIONS_DIR": str(root)}

        self.assertEqual(self.invoke("cline", seed)["details"]["stats"]["totalTokens"], 110)

    def test_jetbrains_quota_file_reaches_cli_envelope(self):
        def seed(home):
            path = home / "quota.xml"
            quota = json.dumps({"type": "Available", "tariffQuota": {"current": 7, "maximum": 10}}).replace('"', "&quot;")
            path.write_text(f'<application><option name="quotaInfo" value="{quota}" /></application>', encoding="utf-8")
            return {"JETBRAINS_QUOTA_FILE": str(path)}

        self.assertEqual(self.invoke("jetbrains", seed)["quotaWindows"][0]["pct"], 70)

    def test_coderabbit_report_cli_is_invoked_and_normalized(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bindir = home / "bin"
            marker = home / "invoked.json"
            output = (
                "CodeRabbit Usage — current billing period\n\nOrganization: Example Org\n"
                "Usage billing: active\nUser: example\nYour reviews: 12\nPeriod resets: 2026-11-01\n"
            )
            script = f"import json, pathlib, sys\npathlib.Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\nprint({output!r})"
            install_fake_cli(bindir, "coderabbit", script)
            child = ChildProcess(home, bin_dir=bindir)
            Path(child.env["TIDESI_CONFIG"]).write_text('{"providers":{"coderabbit":true}}', encoding="utf-8")
            result = child.run([sys.executable, "-m", "tidesi", "--provider", "coderabbit"])
            self.assertEqual(result.returncode, 0, result.stderr)
            row = json.loads(result.stdout)["providers"][0]
            self.assertTrue(row["ok"], row["error"])
            self.assertEqual(row["details"]["reviews"], 12)
            self.assertEqual(json.loads(marker.read_text(encoding="utf-8")), ["usage"])


if __name__ == "__main__":
    unittest.main()
