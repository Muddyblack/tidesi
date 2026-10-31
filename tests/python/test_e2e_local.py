"""CLI-to-envelope E2Es for providers that read local files, databases or a local executable."""

import json
import time
import unittest
from datetime import datetime, timezone

from _e2e import E2ETest


class SessionFileE2ETest(E2ETest):
    """Providers that total tokens from an agent's on-disk session history."""

    def test_junie_event_log(self):
        session = "session-261001-080000-e2e"
        self.write(".junie/sessions/index.jsonl", json.dumps({"sessionId": session, "createdAt": 1790755200000, "updatedAt": 1790841600000}))
        usage = {"model": "fixture", "inputTokens": 100, "outputTokens": 20}
        event = {
            "kind": "SessionA2uxEvent",
            "timestampMs": 1790841600000,
            "event": {"agentEvent": {"kind": "LlmResponseMetadataEvent", "modelUsage": [usage]}},
        }
        self.write(f".junie/sessions/{session}/events.jsonl", json.dumps(event))
        self.assertEqual(self.provider("junie")["details"]["stats"]["totalTokens"], 120)

    def test_pi_session(self):
        header = {"type": "session", "version": 3, "id": "e2e", "cwd": "/tmp/work/demo"}
        usage = {"input": 100, "output": 20, "cacheRead": 0, "cacheWrite": 0}
        message = {"role": "assistant", "provider": "anthropic", "model": "fixture", "usage": usage}
        turn = {"type": "message", "id": "turn", "timestamp": "2026-10-01T08:00:00Z", "message": message}
        self.write(".pi/agent/sessions/--work-demo--/2026-10-01T08-00-00_e2e.jsonl", "\n".join(map(json.dumps, (header, turn))))
        self.assertEqual(self.provider("pi")["details"]["stats"]["totalTokens"], 120)

    def test_cline_session(self):
        self.write(
            ".cline/data/sessions/e2e/e2e.json",
            {
                "started_at": "2026-09-17T00:00:00Z",
                "workspace_root": "/workspace/demo",
                "metadata": {"aggregateUsage": {"inputTokens": 100, "outputTokens": 10}},
            },
        )
        row = self.provider("cline", CLINE_SESSIONS_DIR=str(self.home / ".cline/data/sessions"))
        self.assertEqual(row["details"]["stats"]["totalTokens"], 110)


class SqliteE2ETest(E2ETest):
    OPENCODE_SCHEMA = (
        "CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT, time_created INTEGER, time_updated INTEGER)",
        "CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, data TEXT)",
        "CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, data TEXT)",
    )

    def test_opencode_database(self):
        now_ms = int(time.time() * 1000)
        message = {"role": "assistant", "providerID": "opencode", "modelID": "fixture-model", "cost": 0.42, "tokens": {"input": 80, "output": 20}}
        db = self.database(
            "opencode.db",
            *self.OPENCODE_SCHEMA,
            ("INSERT INTO session VALUES (?, ?, ?, ?, ?)", ("s", "Fixture session", str(self.home), now_ms, now_ms)),
            ("INSERT INTO message VALUES (?, ?, ?)", ("m", "s", json.dumps(message))),
        )
        stats = self.provider("opencode", OPENCODE_DB=str(db))["details"]["stats"]
        self.assertTrue(stats["available"])
        self.assertEqual(stats["totalTokens"], 100)

    def test_mimo_database(self):
        message = {"role": "assistant", "providerID": "mimo", "modelID": "mimo-fixture", "tokens": {"input": 87, "output": 13}}
        db = self.database(
            "mimo.db",
            "CREATE TABLE session (id TEXT, title TEXT, directory TEXT, time_created INTEGER, time_updated INTEGER)",
            "CREATE TABLE message (id TEXT, session_id TEXT, agent_id TEXT, data TEXT)",
            "CREATE TABLE part (id TEXT, message_id TEXT, data TEXT)",
            ("INSERT INTO session VALUES (?, ?, ?, ?, ?)", ("s", "fixture", str(self.home), 1700000000000, 1700000000000)),
            ("INSERT INTO message VALUES (?, ?, ?, ?)", ("m", "s", "main", json.dumps(message))),
        )
        self.assertEqual(self.provider("mimo", MIMO_DB=str(db))["details"]["stats"]["totalTokens"], 100)

    def test_windsurf_state_database(self):
        plan = {"planName": "E2E Plan", "quotaUsage": {"dailyRemainingPercent": 23, "weeklyRemainingPercent": 47}}
        db = self.database(
            "state.vscdb",
            "CREATE TABLE ItemTable (key TEXT, value TEXT)",
            ("INSERT INTO ItemTable VALUES (?, ?)", ("windsurf.settings.cachedPlanInfo", json.dumps(plan))),
        )
        row = self.provider("windsurf", WINDSURF_STATE_DB=str(db))
        self.assertEqual(row["details"]["plan"], "E2E Plan")
        self.assertEqual(row["quotaWindows"][0]["pct"], 77)

    def test_zed_threads_database_lists_sessions_without_leaking_content(self):
        updated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        thread = {
            "title": "Private thread title",
            "updated_at": updated,
            "cumulative_token_usage": {"input_tokens": 12, "output_tokens": 3},
            "model": {"provider": "zed.dev", "model": "zed-session-fixture"},
            "messages": [{"text": "PRIVATE_ZED_TRANSCRIPT"}],
        }
        db = self.database(
            "xdg-data/zed/threads/threads.db",
            "CREATE TABLE threads (id TEXT PRIMARY KEY, summary TEXT, updated_at TEXT, data_type TEXT, data BLOB, "
            "parent_id TEXT, folder_paths TEXT, folder_paths_order TEXT, created_at TEXT)",
            (
                "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "PRIVATE_ZED_SESSION_ID",
                    "Zed E2E session",
                    updated,
                    "json",
                    json.dumps(thread).encode(),
                    None,
                    "/private/workspace/zed-project",
                    "0",
                    updated,
                ),
            ),
        )
        result = self.cli("--sessions", "--source", "zed", TIDESI_ZED_THREADS_DB=str(db))
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual((response["total"], response["sources"]), (1, [{"id": "zed", "label": "Zed Editor"}]))
        session = response["sessions"][0]
        self.assertEqual((session["provider"], session["title"], session["detail"], session["tokens"]), ("zed", "Zed E2E session", "zed-project", 15))
        for private in ("PRIVATE_ZED_SESSION_ID", "PRIVATE_ZED_TRANSCRIPT", "/private/workspace"):
            self.assertNotIn(private, result.stdout)


class FileAndExecutableE2ETest(E2ETest):
    def test_jetbrains_quota_file(self):
        quota = json.dumps({"type": "Available", "tariffQuota": {"current": 7, "maximum": 10}}).replace('"', "&quot;")
        path = self.write("quota.xml", f'<application><option name="quotaInfo" value="{quota}" /></application>')
        self.assertEqual(self.provider("jetbrains", JETBRAINS_QUOTA_FILE=str(path))["quotaWindows"][0]["pct"], 70)

    def test_zed_response_file(self):
        plan = {
            "plan_v3": "zed_pro",
            "usage": {"edit_predictions": {"used": 321, "limit": 1000}},
            "subscription_period": {"ended_at": "2030-01-01T00:00:00Z"},
        }
        path = self.write("zed-response.json", {"plan": plan})
        row = self.provider("zed", ZED_USAGE_RESPONSE_FILE=str(path))
        self.assertEqual((row["details"]["plan"], row["details"]["used"]), ("Pro", 321))

    def test_coderabbit_cli_is_invoked_with_usage(self):
        marker = self.home / "invoked.json"
        output = (
            "CodeRabbit Usage — current billing period\n\nOrganization: Example Org\n"
            "Usage billing: active\nUser: example\nYour reviews: 12\nPeriod resets: 2026-11-01\n"
        )
        self.fake_cli(
            "coderabbit", f"import json, pathlib, sys\npathlib.Path({str(marker)!r}).write_text(json.dumps(sys.argv[1:]))\nprint({output!r})"
        )
        self.assertEqual(self.provider("coderabbit")["details"]["reviews"], 12)
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8")), ["usage"])

    def test_selfhosted_ollama_probes_version_and_loaded_models_only(self):
        self.server.routes = {"/api/version": {"version": "fixture"}, "/api/ps": {"models": [{"name": "fixture-model"}]}}
        row = self.provider("selfhosted", WIDGET_SELFHOSTED_ENDPOINT=self.server.url, WIDGET_SELFHOSTED_ENGINE="ollama")
        self.assertEqual([request.path for request in self.server.requests], ["/api/version", "/api/ps"])
        self.assertEqual(row["details"]["models"][0]["name"], "fixture-model")


if __name__ == "__main__":
    unittest.main()
