"""Full CLI-to-HTTP-to-envelope coverage for provider adapters."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _e2e_harness import ChildProcess, LoopbackHTTPServer, LoopbackResponse, scrub
from _support import FIXTURES
from tidesi.http import route_e2e_url


class E2EHttpRoutingTest(unittest.TestCase):
    def test_routes_remote_url_and_preserves_query(self):
        with mock.patch.dict(os.environ, {"TIDESI_E2E_HTTP_BASE": "http://127.0.0.1:54321"}):
            routed = route_e2e_url("https://api.deepseek.com/user/balance?region=global")

        self.assertEqual(
            routed,
            "http://127.0.0.1:54321/https/api.deepseek.com/user/balance?region=global",
        )

    def test_rejects_invalid_test_server_origins(self):
        invalid_bases = (
            "http://example.com:54321",
            "https://127.0.0.1:54321",
            "http://127.0.0.1:0",
            "http://127.0.0.1:bad",
            "http://127.0.0.1:54321/path",
            "http://127.0.0.1:54321?query=1",
            "http://127.0.0.1:54321#fragment",
            "http://@127.0.0.1:54321",
            "http://user:password@127.0.0.1:54321",
        )
        for base in invalid_bases:
            with self.subTest(base=base), mock.patch.dict(os.environ, {"TIDESI_E2E_HTTP_BASE": base}):
                with self.assertRaisesRegex(ValueError, "loopback"):
                    route_e2e_url("https://api.deepseek.com/user/balance")

    def test_hook_is_inert_without_test_configuration(self):
        with mock.patch.dict(os.environ, {"TIDESI_E2E_HTTP_BASE": ""}):
            url = "https://api.deepseek.com/user/balance?region=global"
            self.assertEqual(route_e2e_url(url), url)

    def test_does_not_rewrite_existing_loopback_services(self):
        with mock.patch.dict(os.environ, {"TIDESI_E2E_HTTP_BASE": "http://127.0.0.1:54321"}):
            url = "http://127.0.0.1:11434/api/tags"
            self.assertEqual(route_e2e_url(url), url)


class DeepSeekProviderE2ETest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.secret = "E2E_SECRET_DEEPSEEK_98765"
        self.response_body = (FIXTURES / "deepseek-response.json").read_text(encoding="utf-8")
        self.current_response = LoopbackResponse(body=self.response_body)

        def response(request):
            if request.path == "/https/api.deepseek.com/user/balance":
                return self.current_response
            return LoopbackResponse(status=404)

        self.server = LoopbackHTTPServer(response)
        self.addCleanup(self.server.close)
        self.child = ChildProcess(self.home, http_base=self.server.base_url)

        settings_path = Path(self.child.env["TIDESI_CONFIG"])
        settings_path.write_text(json.dumps({"providers": {"deepseek": True}}), encoding="utf-8")
        credential_path = self.home / ".config" / "deepseek" / "api-key"
        credential_path.parent.mkdir(parents=True, exist_ok=True)
        credential_path.write_text(self.secret, encoding="utf-8")

    def run_provider(self):
        result = self.child.run([sys.executable, "-m", "tidesi", "--provider", "deepseek"])
        transcript = result.stdout + result.stderr
        self.assertTrue(self.secret not in transcript, "credential leaked into CLI output")
        self.assertEqual(result.returncode, 0, scrub(transcript, self.secret))
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schemaVersion"], 1)
        self.assertEqual([provider["id"] for provider in envelope["providers"]], ["deepseek"])
        return envelope["providers"][0]

    def test_credentials_real_http_request_and_normalized_envelope(self):
        provider = self.run_provider()
        self.assertTrue(provider["ok"], provider["error"])
        self.assertEqual(provider["details"]["currency"], "USD")
        self.assertEqual(provider["historyValues"], {"ds": 12.5})
        self.assertIsInstance(provider["quotaWindows"], list)
        self.assertIsInstance(provider["chartWindows"], list)

        requests = self.server.requests
        self.assertEqual(len(requests), 1, "expected only one DeepSeek HTTP request")
        request = requests[0]
        self.assertEqual(request.path, "/https/api.deepseek.com/user/balance")
        self.assertEqual(request.method, "GET")
        self.assertTrue(
            request.header("authorization") == "Bearer " + self.secret,
            "DeepSeek Authorization header did not contain the seeded credential",
        )
        self.assertEqual(request.header("accept"), "application/json")
        self.assertEqual(request.body, b"")

        for directory in (self.home / "xdg-cache", self.home / "tidesi-cache", self.home / "xdg-data"):
            if not directory.exists():
                continue
            for path in directory.rglob("*"):
                if path.is_file():
                    self.assertTrue(self.secret.encode("utf-8") not in path.read_bytes(), "credential leaked into generated state")

    def test_http_failures_reach_the_normalized_provider_envelope(self):
        cases = (
            ("unauthorized", LoopbackResponse(status=401), "Invalid DeepSeek API key"),
            ("forbidden", LoopbackResponse(status=403), "Invalid DeepSeek API key"),
            ("rate limited", LoopbackResponse(status=429), "DeepSeek HTTP 429"),
            ("server error", LoopbackResponse(status=500), "DeepSeek HTTP 500"),
            ("malformed JSON", LoopbackResponse(body="{bad"), "DeepSeek invalid JSON"),
            ("connection closed", LoopbackResponse(close_connection=True), "DeepSeek network error"),
        )
        for label, response, expected_error in cases:
            with self.subTest(scenario=label):
                self.current_response = response
                previous_count = len(self.server.requests)
                provider = self.run_provider()
                received = self.server.requests[previous_count:]
                self.assertEqual(len(received), 1, "error scenario did not reach the HTTP boundary exactly once")
                self.assertEqual(received[0].path, "/https/api.deepseek.com/user/balance")
                self.assertFalse(provider["ok"])
                self.assertTrue(provider["error"].endswith(expected_error), "unexpected normalized DeepSeek error")


class MuseQuotaProviderE2ETest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.secret = "E2E_SECRET_MUSE_98765"
        subscription = {
            "window": {"used_percent": 26, "resets_at": 1883456000},
            "weekly": {"used_percent": 9, "resets_at": 1884056000},
        }
        self.response_body = "\n".join(
            (
                "event: response.subscription_usage",
                "data: " + json.dumps({"type": "response.subscription_usage", "subscription": subscription}),
                "",
                "",
            )
        )

        def response(request):
            if request.path == "/https/api.meta.ai/v1/responses":
                return LoopbackResponse(
                    body=self.response_body,
                    headers={"Content-Type": "text/event-stream"},
                )
            return LoopbackResponse(status=404)

        self.server = LoopbackHTTPServer(response)
        self.addCleanup(self.server.close)
        self.child = ChildProcess(
            self.home,
            http_base=self.server.base_url,
            extra_env={
                "META_API_KEY": self.secret,
                "MUSE_AUTH_PATH": str(self.home / "muse-auth.json"),
                "MUSE_SETTINGS_PATH": str(self.home / "muse-settings.json"),
                "MUSE_SESSIONS_DIR": str(self.home / "muse-sessions"),
                "MUSE_CATALOG_DIR": str(self.home / "muse-catalog"),
                "MUSE_QUOTA_TTL_SECONDS": "1800",
            },
        )
        Path(self.child.env["TIDESI_CONFIG"]).write_text(
            json.dumps({"providers": {"muse": True}, "museQuota": True}),
            encoding="utf-8",
        )
        (self.home / "muse-auth.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "meta": {
                            "mechanism": "oauth",
                            "api_key": self.secret,
                            "access_token": self.secret,
                            "user_email": "e2e@example.test",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        (self.home / "muse-settings.json").write_text(
            json.dumps({"schema_version": 1, "provider": "meta", "model": "fixture-spark-9.9"}),
            encoding="utf-8",
        )
        catalog_dir = self.home / "muse-catalog"
        catalog_dir.mkdir()
        (catalog_dir / "6d657461__p746268.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider_id": "meta",
                    "profile_id": "tbh",
                    "source": "provider_catalog",
                    "rows": [
                        {
                            "model_id": "fixture-spark-9.9",
                            "display_label": "fixture-spark-9.9",
                            "visibility": "visible",
                            "is_current": True,
                            "is_default": True,
                            "context_limit": 1007997,
                            "output_limit": 128000,
                            "cost": {"input": "1.00", "output": "10.00", "cached": "0.10", "currency": "USD"},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        events = [
            {
                "payload_type": "runtime.session.metadata",
                "payload": {"kind": "metadata", "record": {"workspace_root": "/tmp/e2e-project"}},
            },
            {
                "payload_type": "run.model.configured",
                "payload": {"kind": "run_model", "record": {"model_id": "fixture-spark-9.9", "provider_id": "meta"}},
            },
            {"kind": "user_prompt_display"},
            {
                "kind": "model_completed",
                "model": "fixture-spark-9.9",
                "usage": {"input_tokens": 1000, "output_tokens": 200, "cached_tokens": 400, "reasoning_tokens": 10},
            },
            {"kind": "assistant_message_committed"},
            {"kind": "terminal"},
        ]
        records = []
        for index, event in enumerate(events):
            payload = (
                event if "payload_type" in event else {"payload_type": "runtime.session", "payload": {"kind": "run", "run_id": "r1", "event": event}}
            )
            records.append(
                dict(
                    payload,
                    recorded_at=1785000000000000 + index * 1000000,
                    schema_version=1,
                    stream={"kind": "session", "id": "aaa"},
                )
            )
        session_dir = self.home / "muse-sessions" / "2026" / "09" / "04" / "aaa"
        session_dir.mkdir(parents=True)
        (session_dir / "session.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

    def test_opt_in_direct_urlopen_uses_loopback_and_returns_quota(self):
        result = self.child.run([sys.executable, "-m", "tidesi", "--provider", "muse"])
        transcript = result.stdout + result.stderr
        self.assertTrue(self.secret not in transcript, "credential leaked into CLI output")
        self.assertEqual(result.returncode, 0, scrub(transcript, self.secret))

        envelope = json.loads(result.stdout)
        provider = envelope["providers"][0]
        self.assertEqual(provider["id"], "muse")
        self.assertTrue(provider["ok"], provider["error"])
        self.assertEqual(provider["details"]["quotaError"], "")
        self.assertEqual(provider["details"]["current"]["pct"], 26)
        self.assertEqual(provider["details"]["weekly"]["pct"], 9)

        requests = self.server.requests
        self.assertEqual(len(requests), 1, "expected only one Muse quota request")
        request = requests[0]
        self.assertEqual(request.path, "/https/api.meta.ai/v1/responses")
        self.assertEqual(request.method, "POST")
        self.assertTrue(
            request.header("authorization") == "Bearer " + self.secret,
            "Muse Authorization header did not contain the seeded credential",
        )
        self.assertEqual(request.header("accept"), "text/event-stream")
        self.assertEqual(request.header("content-type"), "application/json")
        body = json.loads(request.body)
        self.assertEqual(body["model"], "fixture-spark-9.9")
        self.assertEqual(body["input"], "ping")
        self.assertTrue(body["stream"])
        self.assertEqual(body["max_output_tokens"], 16)
        self.assertFalse(body["store"])

        self.assertTrue((self.home / "tidesi-cache" / "muse-quota.json").is_file(), "quota response was not cached")
        for directory in (self.home / "xdg-cache", self.home / "tidesi-cache", self.home / "xdg-data"):
            if not directory.exists():
                continue
            for path in directory.rglob("*"):
                if path.is_file():
                    self.assertTrue(self.secret.encode("utf-8") not in path.read_bytes(), "credential leaked into generated state")


if __name__ == "__main__":
    unittest.main()
