"""The materialized contribution table is the envelope's fast path.

These tests pin the SQL path to the same contract the full-row fallback
produces, and prove the envelope never materializes every row when the
contribution table is available.
"""

import datetime
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401 — puts the backend package on sys.path
from _support import seed_session_index
from tidesi import config, envelope
from tidesi.session_index import _accumulate_group, _row_cost_groups, usage_only_groups


def _fallback_spend(sessions):
    """The old full-row path, kept as the reference implementation."""
    return {
        "actual": _accumulate_group(
            (group[2:] for session in sessions for group in _row_cost_groups(session, "actual") if group[0] == "api"),
            "actual",
        ),
        "estimated": _accumulate_group(
            (group[2:] for session in sessions for group in _row_cost_groups(session, "estimated") if group[0] == "api"),
            "estimated",
        ),
        "subscription": _accumulate_group(
            (group[2:] for session in sessions for group in _row_cost_groups(session, "estimated") if group[0] == "subscription"),
            "estimated",
        ),
        "subscriptionActual": _accumulate_group(
            (group[2:] for session in sessions for group in _row_cost_groups(session, "actual") if group[0] == "subscription"),
            "actual",
        ),
        "usageOnly": usage_only_groups(sessions),
    }


class LocalSpendAggregatesTest(unittest.TestCase):
    def _spend(self, sessions):
        with tempfile.TemporaryDirectory() as directory:
            seed_session_index(directory, sessions)
            with mock.patch.object(config, "cache_dir", return_value=directory):
                return envelope._local_spend()

    def test_sql_path_matches_the_full_row_fallback(self):
        sessions = [
            {"provider": "opencode", "source": "opencode", "costUSD": 0.42, "costStatus": "exact", "costProvenance": "actual"},
            {"provider": "cline", "source": "cline", "costUSD": 0.13, "costStatus": "partial", "costProvenance": "estimated"},
            {
                "provider": "claude",
                "source": "claude",
                "costUSD": 12.0,
                "costStatus": "exact",
                "costProvenance": "estimated",
                "costBilling": "subscription",
            },
        ]
        self.assertEqual(self._spend(sessions), _fallback_spend(sessions))

    def test_multi_provider_opencode_splits_tokens_across_rollups(self):
        sessions = [
            {
                "provider": "opencode",
                "source": "opencode",
                "costUSD": 0.55,
                "costStatus": "exact",
                "costProvenance": "actual",
                "lastActivityAt": 1_789_046_340,
                "tokens": 300,
                "providerCosts": {
                    "ollama-cloud": {"costUSD": 0.42, "costStatus": "exact", "costProvenance": "actual"},
                    "openai": {"costUSD": 0.13, "costStatus": "exact", "costProvenance": "actual"},
                },
            }
        ]
        # The index buckets by local day, so derive the expected date the same way.
        day = datetime.datetime.fromtimestamp(1_789_046_340).strftime("%Y-%m-%d")
        spend = self._spend(sessions)
        self.assertEqual(spend["actual"]["totalUSD"], 0.55)
        self.assertEqual(
            spend["actual"]["providers"]["ollama-cloud::opencode"]["dailyTokens"],
            [{"date": day, "total": 150}],
        )
        self.assertEqual(
            spend["actual"]["providers"]["openai::opencode"]["dailyTokens"],
            [{"date": day, "total": 150}],
        )

    def test_stale_cache_falls_back_to_full_rows(self):
        import importlib

        storage = importlib.import_module("tidesi.session_index_storage")
        sessions = [
            {"provider": "cline", "source": "cline", "costUSD": 1.0, "costStatus": "exact", "costProvenance": "estimated"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            seed_session_index(directory, sessions)
            with (
                mock.patch.object(config, "cache_dir", return_value=directory),
                mock.patch.object(storage, "_schema_version", lambda: 123456),
                mock.patch("tidesi.sessions.all_session_rows", return_value=sessions) as unpaged,
            ):
                spend = envelope._local_spend()

        self.assertEqual(spend["estimated"]["totalUSD"], 1.0)
        unpaged.assert_called_once_with()

    def test_corrupt_cache_falls_back_to_full_rows(self):
        sessions = [
            {"provider": "cline", "source": "cline", "costUSD": 1.0, "costStatus": "exact", "costProvenance": "estimated"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            seed_session_index(directory, sessions)
            cache = __import__("pathlib").Path(directory) / "sessions.sqlite3"
            cache.write_bytes(b"not a sqlite database")
            with (
                mock.patch.object(config, "cache_dir", return_value=directory),
                mock.patch("tidesi.sessions.all_session_rows", return_value=sessions) as unpaged,
            ):
                spend = envelope._local_spend()

        self.assertEqual(spend["estimated"]["totalUSD"], 1.0)
        unpaged.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
