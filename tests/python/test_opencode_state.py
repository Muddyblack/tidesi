import json
import sqlite3
from unittest import mock

from _support import IsolatedHomeTest
from test_opencode_costs import _assistant_message, _create_database
from tidesi import session_manifest, sessions
from tidesi.normalize.opencode import normalize_opencode
from tidesi.providers import opencode, opencode_usage_cache
from tidesi.session_index import SkipSource


class OpenCodeStateTest(IsolatedHomeTest):
    def setUp(self):
        super().setUp()
        self.database = _create_database(str(self.home), messages=[_assistant_message(
            cost=0.42, tokens={"input": 800, "output": 100, "reasoning": 10, "cache": {"read": 200, "write": 20}}
        )])
        self.env = mock.patch.dict("os.environ", {"OPENCODE_DB": self.database})
        self.env.start()
        self.addCleanup(self.env.stop)

    def modern_summary(self, journal=True):
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                for field in ("cost", "tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_write", "tokens_reasoning"):
                    connection.execute(f"ALTER TABLE session ADD COLUMN {field} REAL")
                connection.execute(
                    "UPDATE session SET cost = 0.42, tokens_input = 800, tokens_output = 100, "
                    "tokens_cache_read = 200, tokens_cache_write = 20, tokens_reasoning = 10"
                )
                if journal:
                    connection.execute("CREATE TABLE event_sequence (aggregate_id TEXT PRIMARY KEY, seq INTEGER)")
                    connection.execute("INSERT INTO event_sequence VALUES ('session-1', 1)")
        finally:
            connection.close()

    def test_modern_summary_counters_never_replace_original_provider_and_model(self):
        self.modern_summary()
        usage = opencode.usage_snapshot()
        normalized = normalize_opencode({"now": 2_000, "inputs": {"usage": usage, "account": {"mode": "zen"}}})
        self.assertFalse(normalized["details"]["stats"]["available"])
        self.assertEqual(normalized["details"]["accountMode"], "zen")
        self.assertEqual(usage["sessions"][0]["costUSD"], 0.42)
        self.assertEqual(usage["sessions"][0]["usage"][0]["provider"], "anthropic")
        self.assertEqual(usage["sessions"][0]["usage"][0]["model"], "gpt-test")

    def test_unchanged_complete_legacy_scan_is_reused_across_calls(self):
        first = opencode._read_database(self.database, include_all=True)
        with mock.patch.object(opencode, "_read_usage", side_effect=AssertionError("unchanged ledger reread")):
            second = opencode._read_database(self.database, include_all=True)
        self.assertEqual(second, first)

    def test_unchanged_modern_sessions_reuse_attributed_buckets(self):
        self.modern_summary()
        first = opencode._read_database(self.database, include_all=True)
        with mock.patch.object(opencode, "_read_usage", side_effect=AssertionError("unchanged session reread")):
            second = opencode._read_database(self.database, include_all=True)
        self.assertEqual(second, first)

    def test_unchanged_failed_scan_is_not_retried_until_database_changes(self):
        with mock.patch.object(opencode, "_read_usage", side_effect=SkipSource("fixture timeout")) as read:
            for _ in range(2):
                with self.assertRaises(SkipSource):
                    opencode._read_database(self.database, include_all=True)
        self.assertEqual(read.call_count, 1)

    def test_cache_write_failure_does_not_hide_successfully_collected_usage(self):
        self.modern_summary()
        with mock.patch.object(opencode_usage_cache, "_write", side_effect=OSError("read-only cache")):
            records = opencode._read_database(self.database, include_all=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].usage[0].output_tokens, 100)

    def test_cache_write_failure_preserves_incomplete_scan_signal(self):
        with (
            mock.patch.object(opencode, "_read_usage", side_effect=SkipSource("fixture timeout")),
            mock.patch.object(opencode_usage_cache, "_write", side_effect=OSError("read-only cache")),
        ):
            with self.assertRaises(SkipSource):
                opencode._read_database(self.database, include_all=True)

    def test_wal_only_change_invalidates_usage_and_session_manifest(self):
        self.modern_summary()
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            first = opencode._read_database(self.database, include_all=True)
            before = session_manifest._opencode_records()
            with connection:
                connection.execute("UPDATE session SET tokens_output = 500")
                message = _assistant_message(cost=0.42, tokens={"output": 500})
                connection.execute("UPDATE message SET data = ?", (message[2],))
            after = session_manifest._opencode_records()
            changed = opencode._read_database(self.database, include_all=True)
        finally:
            connection.close()
        self.assertNotEqual(before, after)
        self.assertEqual(first[0].usage[0].output_tokens, 100)
        self.assertEqual(changed[0].usage[0].output_tokens, 500)

    def test_numeric_cache_does_not_store_session_identity_or_titles(self):
        self.modern_summary()
        opencode._read_database(self.database, include_all=True)
        caches = list((self.home / "cache").glob("opencode-usage-*.json"))
        self.assertEqual(len(caches), 1)
        encoded = caches[0].read_text()
        for secret in ("session-1", "Synthetic session", self.database, "synthetic/project"):
            self.assertNotIn(secret, encoded)
        self.assertIsInstance(json.loads(encoded), dict)

    def test_provider_view_counts_zen_go_only_while_spend_keeps_all_routed_providers(self):
        self.modern_summary()
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                connection.execute("DELETE FROM message")
                connection.executemany("INSERT INTO message VALUES (?, ?, ?)", [
                    _assistant_message("zen", provider_id="opencode", model="zen-model", tokens={"output": 100}, cost=0.1),
                    _assistant_message("go", provider_id="opencode-go", model="go-model", tokens={"output": 200}, cost=0.2),
                    _assistant_message("openai", provider_id="openai", model="openai-model", tokens={"output": 900}, cost=0.9),
                ])
        finally:
            connection.close()
        usage = opencode.usage_snapshot()
        provider = normalize_opencode({"now": 2_000, "inputs": {"usage": usage, "account": {"mode": "zen"}}})
        self.assertEqual(provider["details"]["stats"]["totalTokens"], 300)
        go = normalize_opencode({"now": 2_000, "inputs": {"usage": usage, "account": {
            "mode": "go", "goUsage": {"usage": {"weekly": {"status": "ok", "percent": 50, "resetsAt": "2026-10-30T00:00:00Z"}}},
        }}})
        self.assertEqual(go["details"]["stats"]["totalTokens"], 300)
        self.assertEqual(go["summary"]["text"], "50%")
        entry = sessions._opencode_entries(include_all=True)[0]
        self.assertEqual(set(entry["providerCosts"]), {"opencode", "opencode-go", "openai"})
        self.assertAlmostEqual(entry["providerCosts"]["openai"]["costUSD"], 0.9)
        self.assertAlmostEqual(entry["costUSD"], 1.2)

    def test_incomplete_scan_resumes_without_reparsing_completed_sessions(self):
        self.modern_summary()
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                connection.execute(
                    "INSERT INTO session (id, title, directory, time_created, time_updated) VALUES ('session-2', 'Fixture', '/fixture', 1, 1)"
                )
                connection.execute("INSERT INTO message VALUES (?, ?, ?)", _assistant_message("message-2", session_id="session-2", cost=0.8))
        finally:
            connection.close()
        read_usage = opencode._read_usage
        clock = [0.0]

        def expire_after_first(connection, session_id, **kwargs):
            buckets = read_usage(connection, session_id, **kwargs)
            clock[0] = 100.0
            return buckets

        with (
            mock.patch.object(opencode_usage_cache.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(opencode, "_read_usage", side_effect=expire_after_first),
        ):
            with self.assertRaises(SkipSource):
                opencode._read_database(self.database, include_all=True)
        with mock.patch.object(opencode, "_read_usage", wraps=read_usage) as resumed:
            records = opencode._read_database(self.database, include_all=True)
        self.assertEqual([call.args[1] for call in resumed.call_args_list], ["session-2"])
        self.assertAlmostEqual(sum(bucket.provider_cost_usd for record in records for bucket in record.usage), 1.22)

    def test_attribution_change_invalidates_buckets_even_when_session_totals_and_timestamps_do_not_change(self):
        self.modern_summary()
        first = opencode._read_database(self.database, include_all=True)
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                message = _assistant_message(provider_id="openai", model="changed-model", cost=0.42, tokens={"output": 100})
                connection.execute("UPDATE message SET data = ?", (message[2],))
                connection.execute("UPDATE event_sequence SET seq = seq + 1")
        finally:
            connection.close()
        changed = opencode._read_database(self.database, include_all=True)
        self.assertEqual(first[0].usage[0].provider, "anthropic")
        self.assertEqual(changed[0].usage[0].provider, "openai")
        self.assertEqual(changed[0].usage[0].model, "changed-model")

    def test_database_without_journal_invalidates_on_source_change_even_if_summary_is_unchanged(self):
        self.modern_summary(journal=False)
        opencode._read_database(self.database, include_all=True)
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                message = _assistant_message(provider_id="openai", model="changed-model", cost=0.42)
                connection.execute("UPDATE message SET data = ?", (message[2],))
        finally:
            connection.close()
        changed = opencode._read_database(self.database, include_all=True)
        self.assertEqual(changed[0].usage[0].provider, "openai")
        self.assertEqual(changed[0].usage[0].model, "changed-model")
