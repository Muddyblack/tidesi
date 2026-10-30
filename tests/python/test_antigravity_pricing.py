import sqlite3
from contextlib import closing
from unittest import mock

from _support import IsolatedHomeTest
from test_antigravity_usage import _field, _turn
from tidesi import billing, envelope, session_cache, sessions
from tidesi.providers import antigravity_sessions, antigravity_usage
from tidesi.session_manifest import SessionManifest, _antigravity_records

SESSION = "11111111-1111-4111-8111-111111111111"


class AntigravityPricingTest(IsolatedHomeTest):
    def database(self, model="gemini-test"):
        path = self.home / ".gemini/antigravity-cli/conversations" / (SESSION + ".db")
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("CREATE TABLE gen_metadata (idx INTEGER PRIMARY KEY, data BLOB)")
            connection.execute("CREATE TABLE trajectory_metadata_blob (data BLOB)")
            stamp = _field(2, 2, _field(1, 0, 1700000000))
            connection.execute("INSERT INTO trajectory_metadata_blob VALUES (?)", (stamp,))
            row = _turn(model, "Exact model", "response-1", {1: 100, 2: 900, 5: 2000, 9: 300, 10: 100}, 1700000100)
            connection.execute("INSERT INTO gen_metadata VALUES (0, ?)", (row,))
        return path

    def test_generation_database_restores_subscription_estimate_without_transcripts(self):
        self.database()
        catalog = {"google": {"gemini-test": {"input": 2, "output": 10, "cached": 0.5}}}
        with mock.patch("tidesi.pricing.cached_catalog", return_value=catalog):
            entries = sessions._antigravity_entries(include_all=True)
        self.assertEqual(len(entries), 1)
        self.assertAlmostEqual(entries[0]["costUSD"], 0.007)
        self.assertEqual(entries[0]["costStatus"], "exact")
        self.assertEqual(entries[0]["costProvenance"], "estimated")
        self.assertEqual(entries[0]["costBilling"], "subscription")
        self.assertEqual(entries[0]["tokens"], 3400)

    def test_native_usage_attaches_to_existing_transcript_session_without_double_counting(self):
        self.database()
        transcript = self.home / ".gemini/antigravity-cli/brain" / SESSION / ".system_generated/logs/transcript.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            '{"step_index":0,"type":"USER_INPUT","created_at":"2023-11-14T22:13:20Z","content":"Fixture title","input_tokens":999999}\n',
            encoding="utf-8",
        )
        records = antigravity_sessions.read_recent_sessions(include_all=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "Fixture title")
        self.assertEqual(billing.total_tokens(records[0].usage), 3400)

    def test_generation_database_and_wal_are_in_session_fingerprint(self):
        path = self.database()
        records = _antigravity_records(str(self.home / ".gemini"))
        self.assertIn(str(path), [row[1] for row in records])
        self.assertIn(str(path) + "-wal", [row[1] for row in records])

    def test_unknown_native_model_remains_unavailable_without_assigning_a_default_rate(self):
        self.database("gemini-unpriced")
        with mock.patch("tidesi.pricing.cached_catalog", return_value={"google": {"gemini-test": {"input": 2, "output": 10}}}):
            entry = sessions._antigravity_entries(include_all=True)[0]
        self.assertEqual(entry["costStatus"], "unavailable")
        self.assertNotIn("costUSD", entry)

    def test_native_estimate_reaches_usage_and_spend_plan_rollup(self):
        self.database()
        catalog = {"google": {"gemini-test": {"input": 2, "output": 10, "cached": 0.5}}}
        with (
            mock.patch("tidesi.pricing.cached_catalog", return_value=catalog),
            mock.patch.object(session_cache, "build_manifests", return_value=[SessionManifest("antigravity", 1, 1)]),
        ):
            cache = session_cache.SessionCache()
            cache.refresh(lambda: ([], True), lambda source: sessions._antigravity_entries(include_all=True))
            spend = envelope._local_spend()
        self.assertAlmostEqual(spend["subscription"]["providers"]["antigravity"]["costUSD"], 0.007)
        self.assertEqual(spend["subscription"]["providers"]["antigravity"]["costProvenance"], "estimated")
        self.assertEqual(spend["actual"]["costStatus"], "unavailable")

    def test_known_and_unknown_models_keep_the_known_price_with_partial_coverage(self):
        path = self.database()
        with closing(sqlite3.connect(path)) as connection, connection:
            row = _turn("gemini-unpriced", "Other model", "response-2", {2: 500}, 1700000200)
            connection.execute("INSERT INTO gen_metadata VALUES (1, ?)", (row,))
        catalog = {"google": {"gemini-test": {"input": 2, "output": 10, "cached": 0.5}}}
        with mock.patch("tidesi.pricing.cached_catalog", return_value=catalog):
            entry = sessions._antigravity_entries(include_all=True)[0]
        self.assertAlmostEqual(entry["costUSD"], 0.007)
        self.assertEqual(entry["costStatus"], "partial")
        self.assertEqual(entry["tokens"], 3900)

    def test_skipped_generation_keeps_partial_coverage_in_cold_and_cached_spend(self):
        path = self.database()
        oversized = _turn("gemini-test", "Exact model", "response-2", {2: 100}) + _field(100, 2, b"x" * (4 * 1024 * 1024))
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("INSERT INTO gen_metadata VALUES (1, ?)", (oversized,))
        catalog = {"google": {"gemini-test": {"input": 2, "output": 10, "cached": 0.5}}}
        with mock.patch("tidesi.pricing.cached_catalog", return_value=catalog):
            cold = sessions._antigravity_entries(include_all=True)[0]
            with mock.patch.object(antigravity_usage, "_extract", side_effect=AssertionError("cache miss")):
                warm = sessions._antigravity_entries(include_all=True)[0]
        self.assertEqual(cold["costStatus"], "partial")
        self.assertEqual(warm, cold)
        self.assertAlmostEqual(cold["costUSD"], 0.007)
        self.assertEqual(cold["tokens"], 3400)
        spend = envelope._local_spend_group([warm], "estimated", billing="subscription")
        self.assertEqual(spend["costStatus"], "partial")
        self.assertAlmostEqual(spend["totalUSD"], 0.007)

    def test_skipped_generation_coverage_attaches_to_transcript_session(self):
        path = self.database()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("INSERT INTO gen_metadata VALUES (1, ?)", (b"x" * (4 * 1024 * 1024 + 1),))
        transcript = self.home / ".gemini/antigravity-cli/brain" / SESSION / ".system_generated/logs/transcript.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text('{"step_index":0,"type":"USER_INPUT","content":"Fixture title"}\n', encoding="utf-8")
        catalog = {"google": {"gemini-test": {"input": 2, "output": 10, "cached": 0.5}}}
        with mock.patch("tidesi.pricing.cached_catalog", return_value=catalog):
            entry = sessions._antigravity_entries(include_all=True)[0]
        self.assertEqual(entry["title"], "Fixture title")
        self.assertEqual(entry["costStatus"], "partial")

    def test_rollout_and_effort_variants_take_the_public_model_rate(self):
        rates = {"claude-opus-4-6": {}, "gemini-3.7-flash": {}, "gemini-3-flash-preview": {}, "gemini-3.1-pro-preview": {}}
        cases = {
            "claude-opus-4-6-thinking": "claude-opus-4-6",
            "gemini-3.7-flash-tiered": "gemini-3.7-flash",
            "gemini-3p7-flash-exp-c": "gemini-3.7-flash",
            "gemini-3-flash-a": "gemini-3-flash-preview",
            "gemini-3.1-pro-low": "gemini-3.1-pro-preview",
            "gemini-default": None,
            "gemini-unpriced": None,
        }
        for model, public in cases.items():
            with self.subTest(model=model):
                self.assertEqual(antigravity_usage.price_model(model, rates), public)

    def test_a_variant_id_is_priced_and_keeps_its_raw_name(self):
        self.database("gemini-test-tiered")
        catalog = {"google": {"gemini-test": {"input": 2, "output": 10, "cached": 0.5}}}
        with mock.patch("tidesi.pricing.cached_catalog", return_value=catalog):
            entry = sessions._antigravity_entries(include_all=True)[0]
        self.assertAlmostEqual(entry["costUSD"], 0.007)
        self.assertEqual(entry["costStatus"], "exact")
        records = antigravity_sessions.read_recent_sessions(include_all=True)
        self.assertEqual(records[0].usage[0].model, "gemini-test-tiered")
