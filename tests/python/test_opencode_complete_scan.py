import sqlite3
import tempfile
from unittest import mock

from _support import IsolatedHomeTest
from test_opencode_costs import _assistant_message, _create_database
from tidesi import session_cache, sessions
from tidesi.providers import opencode
from tidesi.session_index import SkipSource
from tidesi.session_manifest import SessionManifest


class OpenCodeCompleteScanTest(IsolatedHomeTest):
    def test_full_scan_rejects_expired_enrichment_instead_of_publishing_empty_usage(self):
        with tempfile.TemporaryDirectory() as root:
            path = _create_database(root, messages=[_assistant_message(cost=0.42)])
            with mock.patch.object(opencode, "_ENRICH_TIMEOUT_SECONDS", -1):
                with self.assertRaises(SkipSource):
                    opencode._read_database(path, include_all=True)

    def test_full_scan_rejects_expired_session_query_instead_of_caching_zero(self):
        with tempfile.TemporaryDirectory() as root:
            path = _create_database(root, messages=[_assistant_message(cost=0.42)])
            with mock.patch.object(opencode, "_QUERY_TIMEOUT_SECONDS", -1):
                with self.assertRaises(SkipSource):
                    opencode._read_database(path, include_all=True)

    def test_complete_scan_preserves_the_recorded_cost(self):
        with tempfile.TemporaryDirectory() as root:
            path = _create_database(root, messages=[_assistant_message(cost=0.42)])
            records = opencode._read_database(path, include_all=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].usage[0].provider_cost_usd, 0.42)

    def test_full_scan_rejects_unrecognized_usage_schema(self):
        with tempfile.TemporaryDirectory() as root:
            path = _create_database(root, include_usage_tables=False)
            connection = sqlite3.connect(path)
            try:
                with connection:
                    connection.execute("CREATE TABLE message (id TEXT)")
            finally:
                connection.close()
            with self.assertRaises(SkipSource):
                opencode._read_database(path, include_all=True)

    def test_recognized_empty_usage_is_a_valid_complete_scan(self):
        with tempfile.TemporaryDirectory() as root:
            path = _create_database(root)
            records = opencode._read_database(path, include_all=True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].usage, ())

    def test_timed_out_refresh_preserves_last_good_cost_and_reports_incomplete(self):
        with tempfile.TemporaryDirectory() as root:
            path = _create_database(root, messages=[_assistant_message(cost=0.42)])
            with (
                mock.patch("tidesi.config.cache_dir", return_value=root + "/cache"),
                mock.patch.dict("os.environ", {"OPENCODE_DB": path}),
                mock.patch.object(session_cache, "build_manifests", return_value=[SessionManifest("opencode", 1, 1)]) as manifests,
            ):
                cache = session_cache.SessionCache()

                def collect(_source):
                    return sessions._opencode_entries(include_all=True)

                cache.refresh(lambda: ([], True), collect)
                before = cache.spend_groups()
                manifests.return_value = [SessionManifest("opencode", 2, 2)]
                with mock.patch.object(opencode, "_ENRICH_TIMEOUT_SECONDS", -1):
                    cache.refresh(lambda: ([], True), collect)
                after = cache.spend_groups()
                status = cache.query()["refreshStatus"]
                reopened_status = session_cache.SessionCache().query()["refreshStatus"]
        self.assertAlmostEqual(before["actual"]["totalUSD"], 0.42)
        self.assertEqual(after, before)
        self.assertEqual(status, "incomplete")
        self.assertEqual(reopened_status, "incomplete")
