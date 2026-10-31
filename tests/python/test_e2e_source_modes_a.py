import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _e2e_harness import ChildProcess, LoopbackHTTPServer, LoopbackResponse, install_fake_cli


class ProviderSourceModeE2ETest(unittest.TestCase):
    def write_provider_config(self, child, provider):
        Path(child.env["TIDESI_CONFIG"]).write_text(json.dumps({"providers": {provider: True}}), encoding="utf-8")

    def assert_source_envelope(self, child, provider, source):
        result = child.run([sys.executable, "-m", "tidesi", "--provider", provider])
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schemaVersion"], 1)
        self.assertEqual([row["id"] for row in envelope["providers"]], [provider])
        row = envelope["providers"][0]
        self.assertTrue(row["ok"], row["error"])
        self.assertEqual(row["sources"]["selected"], source)
        self.assertEqual(row["sources"]["active"], source)
        return row

    @unittest.skipUnless(sys.platform == "linux", "Antigravity account lookup uses the host credential store")
    def test_antigravity_aiu_cli_reaches_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            bindir = home / "bin"
            report = {"email": "fixture@example.test", "models": [{"label": "Gemini", "modelId": "gemini", "remainingPercentage": 0.75}]}
            install_fake_cli(bindir, "aiu", f"import json\nprint({json.dumps(json.dumps(report))})")
            install_fake_cli(bindir, "secret-tool", "pass")
            child = ChildProcess(
                home,
                extra_env={"WIDGET_SOURCE_ANTIGRAVITY": "aiu"},
                bin_dir=bindir,
            )
            self.write_provider_config(child, "antigravity")
            row = self.assert_source_envelope(child, "antigravity", "aiu")
        self.assertEqual(row["details"]["models"]["gemini"]["usedPct"], 25)

    @unittest.skipUnless(sys.platform == "linux", "Antigravity account lookup uses the host credential store")
    def test_antigravity_server_reaches_envelope_over_loopback(self):
        if not os.path.isdir("/proc/self") and importlib.util.find_spec("psutil") is None:
            self.skipTest("Antigravity process discovery needs /proc or optional psutil")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            token = "e2e-csrf-token"

            def response(request):
                if request.path.endswith("GetUnleashData"):
                    return LoopbackResponse(body="{}")
                if request.path.endswith("GetUserStatus"):
                    return LoopbackResponse(
                        body=json.dumps(
                            {
                                "userStatus": {
                                    "email": "server@example.test",
                                    "cascadeModelConfigData": {
                                        "clientModelConfigs": [
                                            {
                                                "label": "Gemini",
                                                "modelOrAlias": {"model": "gemini-server"},
                                                "quotaInfo": {"remainingFraction": 0.6},
                                            }
                                        ]
                                    },
                                }
                            }
                        )
                    )
                return LoopbackResponse(status=404)

            with LoopbackHTTPServer(response) as server:
                child = ChildProcess(
                    home,
                    extra_env={"WIDGET_SOURCE_ANTIGRAVITY": "server"},
                    http_base=server.base_url,
                )
                self.write_provider_config(child, "antigravity")
                fake = home / "antigravity-language-server.py"
                fake.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
                process = subprocess.Popen(
                    [sys.executable, str(fake), "--csrf_token", token, "--extension_server_port", server.base_url.rsplit(":", 1)[1]],
                    cwd=home,
                    env=child.env,
                )
                try:
                    row = self.assert_source_envelope(child, "antigravity", "server")
                finally:
                    process.terminate()
                    process.wait(timeout=5)
            self.assertEqual([request.path.rsplit("/", 1)[-1] for request in server.requests], ["GetUnleashData", "GetUserStatus"])
            self.assertTrue(all(request.header("x-codeium-csrf-token") == token for request in server.requests))
        self.assertEqual(row["details"]["models"]["gemini-server"]["usedPct"], 40)

    def test_kilo_cli_login_reaches_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / "xdg-data" / "kilo").mkdir(parents=True)
            (home / "xdg-data" / "kilo" / "auth.json").write_text(json.dumps({"kilo": {"access": "isolated-cli-token"}}), encoding="utf-8")
            response = [
                {"result": {"data": {"json": {"totalBalance_mUsd": 5_000_000}}}},
                {"result": {"data": {"json": None}}},
            ]
            response_file = home / "kilo-response.json"
            response_file.write_text(json.dumps(response), encoding="utf-8")
            child = ChildProcess(
                home,
                extra_env={"WIDGET_SOURCE_KILO": "cli", "KILO_USAGE_RESPONSE_FILE": str(response_file)},
            )
            self.write_provider_config(child, "kilo")
            row = self.assert_source_envelope(child, "kilo", "cli")
        self.assertEqual(row["details"]["credits"]["remaining"], 5)

    def test_ollama_opencode_login_reaches_envelope(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            auth = home / "xdg-data" / "opencode" / "auth.json"
            auth.parent.mkdir(parents=True)
            auth.write_text(json.dumps({"ollama-cloud": {"type": "api", "key": "isolated-opencode-key"}}), encoding="utf-8")
            response = home / "ollama-response.json"
            response.write_text(json.dumps({"limits": {"session": {"usage": 0.25}}}), encoding="utf-8")
            child = ChildProcess(
                home,
                extra_env={"WIDGET_SOURCE_OLLAMA": "opencode", "OLLAMA_RESPONSE_FILE": str(response)},
            )
            self.write_provider_config(child, "ollama")
            row = self.assert_source_envelope(child, "ollama", "opencode")
        self.assertEqual(row["quotaWindows"][0]["pct"], 25)


if __name__ == "__main__":
    unittest.main()
