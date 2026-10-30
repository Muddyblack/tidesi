import math
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401 — puts the backend package on sys.path
from _support import seed_session_index
from tidesi import config, envelope


def _metered(local_spend):
    """Only the metered groups. The plan-covered split is covered by
    test_billing_mode; these tests are about the actual/estimated rollup."""
    return {key: value for key, value in local_spend.items() if key in ("actual", "estimated")}


class LocalSpendEnvelopeTest(unittest.TestCase):
    def build(self, sessions):
        provider = {
            "id": "claude",
            "ok": True,
            "details": {"stats": {"totalCostUSD": 99.0}},
        }
        with tempfile.TemporaryDirectory() as directory:
            seed_session_index(directory, sessions)
            with (
                mock.patch.object(envelope, "collect", return_value={}),
                mock.patch.object(envelope, "normalize", return_value=provider),
                mock.patch.object(config, "cache_dir", return_value=directory),
            ):
                return envelope.build(["claude"], now=1_700_000_000)

    def test_no_sessions_is_unavailable_and_omits_empty_totals(self):
        result = self.build([])

        self.assertEqual(
            _metered(result["localSpend"]),
            {"actual": {"costStatus": "unavailable"}, "estimated": {"costStatus": "unavailable"}},
        )

    def test_unpriced_zed_threads_reach_usage_and_spend_as_token_only(self):
        result = self.build(
            [
                {
                    "provider": "zed",
                    "source": "zed",
                    "tokens": 1234,
                    "costStatus": "unavailable",
                    "lastActivityAt": 1_789_046_340,
                }
            ]
        )

        self.assertEqual(result["localSpend"]["usageOnly"]["providers"]["zed"]["tokens"], 1234)
        self.assertEqual(
            result["localSpend"]["usageOnly"]["providers"]["zed"]["dailyTokens"][0]["total"],
            1234,
        )
        self.assertEqual(result["localSpend"]["actual"]["costStatus"], "unavailable")
        self.assertEqual(result["localSpend"]["estimated"]["costStatus"], "unavailable")

    def test_zed_usage_only_rolls_up_models_by_dynamic_provider(self):
        result = self.build(
            [
                {
                    "provider": "zed",
                    "source": "zed",
                    "billingProvider": "future-provider",
                    "billingModel": "model/alpha-v1",
                    "tokens": 1234,
                    "costStatus": "unavailable",
                    "lastActivityAt": 1_789_046_340,
                },
                {
                    "provider": "zed",
                    "source": "zed",
                    "billingProvider": "another.provider",
                    "billingModel": "model-beta-v2",
                    "tokens": 4321,
                    "costStatus": "unavailable",
                    "lastActivityAt": 1_789_046_340,
                },
                {
                    "provider": "zed",
                    "source": "zed",
                    "billingProvider": "future-provider",
                    "billingModel": "model/gamma-v3",
                    "tokens": 75,
                    "costStatus": "unavailable",
                    "lastActivityAt": 1_789_046_340,
                },
            ]
        )

        usage = result["localSpend"]["usageOnly"]["providers"]
        self.assertEqual(len(usage), 2)
        alpha = usage["future-provider::zed"]
        beta = usage["another.provider::zed"]
        self.assertEqual(alpha["tokens"], 1309)
        self.assertEqual(alpha["provider"], "future-provider")
        self.assertNotIn("model", alpha)
        self.assertEqual(alpha["source"], "zed")
        self.assertEqual(beta["tokens"], 4321)
        self.assertNotIn("model", beta)
        self.assertEqual(result["localSpend"]["actual"]["costStatus"], "unavailable")
        self.assertEqual(result["localSpend"]["estimated"]["costStatus"], "unavailable")

    def test_exact_session_has_total_and_provider_rollup(self):
        result = self.build(
            [
                {
                    "provider": "opencode",
                    "costUSD": 0.42,
                    "costStatus": "exact",
                    "costProvenance": "actual",
                    "sessionId": "private-session-id",
                    "title": "private title",
                    "path": "/private/path",
                    "prompt": "private prompt",
                }
            ]
        )

        self.assertEqual(
            _metered(result["localSpend"]),
            {
                "actual": {
                    "totalUSD": 0.42,
                    "costStatus": "exact",
                    "costProvenance": "actual",
                    "providers": {"opencode": {"costUSD": 0.42, "costStatus": "exact", "costProvenance": "actual"}},
                },
                "estimated": {"costStatus": "unavailable"},
            },
        )

    def test_opencode_rollups_are_separate_by_trusted_upstream_provider(self):
        result = self.build(
            [
                {
                    "provider": "anthropic",
                    "source": "opencode",
                    "billingProvider": "anthropic",
                    "costUSD": 0.42,
                    "costStatus": "exact",
                    "costProvenance": "estimated",
                },
                {
                    "provider": "openai",
                    "source": "opencode",
                    "billingProvider": "openai",
                    "costUSD": 0.13,
                    "costStatus": "exact",
                    "costProvenance": "actual",
                },
            ]
        )

        self.assertEqual(
            result["localSpend"]["estimated"]["providers"],
            {
                "anthropic::opencode": {
                    "costUSD": 0.42,
                    "costStatus": "exact",
                    "costProvenance": "estimated",
                    "source": "opencode",
                }
            },
        )
        self.assertEqual(
            result["localSpend"]["actual"]["providers"],
            {
                "openai::opencode": {
                    "costUSD": 0.13,
                    "costStatus": "exact",
                    "costProvenance": "actual",
                    "source": "opencode",
                }
            },
        )

    def test_native_and_opencode_same_provider_keep_distinct_rollups(self):
        result = self.build(
            [
                {
                    "provider": "anthropic",
                    "costUSD": 0.21,
                    "costStatus": "exact",
                    "costProvenance": "estimated",
                },
                {
                    "provider": "anthropic",
                    "source": "opencode",
                    "billingProvider": "anthropic",
                    "costUSD": 0.42,
                    "costStatus": "exact",
                    "costProvenance": "estimated",
                },
            ]
        )

        self.assertEqual(
            result["localSpend"]["estimated"]["providers"],
            {
                "anthropic": {
                    "costUSD": 0.21,
                    "costStatus": "exact",
                    "costProvenance": "estimated",
                },
                "anthropic::opencode": {
                    "costUSD": 0.42,
                    "costStatus": "exact",
                    "costProvenance": "estimated",
                    "source": "opencode",
                },
            },
        )

    def test_multi_provider_opencode_session_rolls_up_per_provider(self):
        result = self.build(
            [
                {
                    "provider": "opencode",
                    "source": "opencode",
                    "costUSD": 0.55,
                    "costStatus": "exact",
                    "costProvenance": "actual",
                    "providerCosts": {
                        "ollama-cloud": {"costUSD": 0.42, "costStatus": "exact", "costProvenance": "actual"},
                        "openai": {"costUSD": 0.13, "costStatus": "exact", "costProvenance": "actual"},
                    },
                }
            ]
        )

        self.assertEqual(result["localSpend"]["actual"]["totalUSD"], 0.55)
        self.assertEqual(
            result["localSpend"]["actual"]["providers"],
            {
                "ollama-cloud::opencode": {
                    "costUSD": 0.42,
                    "costStatus": "exact",
                    "costProvenance": "actual",
                    "source": "opencode",
                },
                "openai::opencode": {
                    "costUSD": 0.13,
                    "costStatus": "exact",
                    "costProvenance": "actual",
                    "source": "opencode",
                },
            },
        )

    def test_multi_provider_opencode_mixed_provider_costs_split_by_origin(self):
        result = self.build(
            [
                {
                    "provider": "opencode",
                    "source": "opencode",
                    "costUSD": 0.62,
                    "costStatus": "exact",
                    "costProvenance": "mixed",
                    "costBreakdown": {"actualUSD": 0.42, "estimatedUSD": 0.2},
                    "providerCosts": {
                        "ollama-cloud": {
                            "costUSD": 0.62,
                            "costStatus": "exact",
                            "costProvenance": "mixed",
                            "costBreakdown": {"actualUSD": 0.42, "estimatedUSD": 0.2},
                        }
                    },
                }
            ]
        )

        self.assertEqual(result["localSpend"]["actual"]["providers"]["ollama-cloud::opencode"]["costUSD"], 0.42)
        self.assertEqual(result["localSpend"]["estimated"]["providers"]["ollama-cloud::opencode"]["costUSD"], 0.2)

    def test_exact_and_partial_sessions_sum_with_partial_status(self):
        result = self.build(
            [
                {"provider": "opencode", "costUSD": 0.42, "costStatus": "exact", "costProvenance": "actual"},
                {"provider": "cline", "costUSD": 0.13, "costStatus": "partial", "costProvenance": "actual"},
                {"provider": "cline", "costUSD": 0.07, "costStatus": "exact", "costProvenance": "actual"},
            ]
        )

        self.assertEqual(
            _metered(result["localSpend"]),
            {
                "actual": {
                    "totalUSD": 0.42,
                    "costStatus": "exact",
                    "costProvenance": "actual",
                    "providers": {"opencode": {"costUSD": 0.42, "costStatus": "exact", "costProvenance": "actual"}},
                },
                "estimated": {"costStatus": "unavailable"},
            },
        )

    def test_actual_and_estimated_totals_are_separate(self):
        result = self.build(
            [
                {"provider": "opencode", "costUSD": 0.42, "costStatus": "exact", "costProvenance": "actual"},
                {"provider": "claude", "costUSD": 0.13, "costStatus": "partial", "costProvenance": "estimated"},
                {"provider": "claude", "costUSD": 0.07, "costStatus": "exact", "costProvenance": "estimated"},
            ]
        )

        self.assertEqual(
            _metered(result["localSpend"]),
            {
                "actual": {
                    "totalUSD": 0.42,
                    "costStatus": "exact",
                    "costProvenance": "actual",
                    "providers": {"opencode": {"costUSD": 0.42, "costStatus": "exact", "costProvenance": "actual"}},
                },
                "estimated": {
                    "totalUSD": 0.2,
                    "costStatus": "partial",
                    "costProvenance": "estimated",
                    "providers": {"claude": {"costUSD": 0.2, "costStatus": "partial", "costProvenance": "estimated"}},
                },
            },
        )
        self.assertNotIn("totalUSD", result["localSpend"])

    def test_mixed_session_contributes_each_origin_subtotal(self):
        result = self.build(
            [
                {
                    "provider": "opencode",
                    "costUSD": 0.62,
                    "costStatus": "exact",
                    "costProvenance": "mixed",
                    "costBreakdown": {"actualUSD": 0.42, "estimatedUSD": 0.2},
                    "sessionId": "private-session-id",
                }
            ]
        )

        self.assertEqual(result["localSpend"]["actual"]["totalUSD"], 0.42)
        self.assertEqual(result["localSpend"]["actual"]["costStatus"], "exact")
        self.assertEqual(result["localSpend"]["estimated"]["totalUSD"], 0.2)
        self.assertEqual(result["localSpend"]["estimated"]["costStatus"], "exact")

    def test_mixed_partial_session_marks_both_origin_totals_partial(self):
        result = self.build(
            [
                {
                    "provider": "cline",
                    "costUSD": 0.62,
                    "costStatus": "partial",
                    "costProvenance": "mixed",
                    "costBreakdown": {"actualUSD": 0.42, "estimatedUSD": 0.2},
                }
            ]
        )

        self.assertEqual(result["localSpend"]["actual"], {"costStatus": "unavailable"})
        self.assertEqual(result["localSpend"]["estimated"]["totalUSD"], 0.2)
        self.assertEqual(result["localSpend"]["estimated"]["costStatus"], "partial")

    def test_mixed_rows_without_contributors_are_unavailable(self):
        result = self.build(
            [
                {
                    "provider": "cline",
                    "costUSD": 0.62,
                    "costStatus": "exact",
                    "costProvenance": "mixed",
                }
            ]
        )

        self.assertEqual(result["localSpend"]["actual"], {"costStatus": "unavailable"})
        self.assertEqual(result["localSpend"]["estimated"], {"costStatus": "unavailable"})

    def test_mixed_rows_reject_malformed_subtotals(self):
        for breakdown in (
            {"actualUSD": math.nan, "estimatedUSD": 0.2},
            {"actualUSD": 0.42, "estimatedUSD": math.inf},
            {"actualUSD": "0.42", "estimatedUSD": 0.2},
        ):
            with self.subTest(breakdown=breakdown):
                result = self.build(
                    [
                        {
                            "provider": "cline",
                            "costUSD": 0.62,
                            "costStatus": "exact",
                            "costProvenance": "mixed",
                            "costBreakdown": breakdown,
                        }
                    ]
                )

                self.assertEqual(result["localSpend"]["actual"], {"costStatus": "unavailable"})
                self.assertEqual(result["localSpend"]["estimated"], {"costStatus": "unavailable"})

    def test_mixed_rows_reject_subtotals_that_do_not_match_session_cost(self):
        result = self.build(
            [
                {
                    "provider": "opencode",
                    "costUSD": 0.62,
                    "costStatus": "exact",
                    "costProvenance": "mixed",
                    "costBreakdown": {"actualUSD": 0.42, "estimatedUSD": 0.1},
                }
            ]
        )

        self.assertEqual(result["localSpend"]["actual"], {"costStatus": "unavailable"})
        self.assertEqual(result["localSpend"]["estimated"], {"costStatus": "unavailable"})

    def test_mixed_rows_preserve_zero_component_when_parent_cost_matches(self):
        result = self.build(
            [
                {
                    "provider": "opencode",
                    "costUSD": 0.2,
                    "costStatus": "exact",
                    "costProvenance": "mixed",
                    "costBreakdown": {"actualUSD": 0, "estimatedUSD": 0.2},
                }
            ]
        )

        self.assertEqual(result["localSpend"]["actual"]["totalUSD"], 0)
        self.assertEqual(result["localSpend"]["actual"]["providers"]["opencode"]["costUSD"], 0)
        self.assertEqual(result["localSpend"]["estimated"]["totalUSD"], 0.2)

    def test_mixed_rows_allow_small_decimal_rounding_error(self):
        result = self.build(
            [
                {
                    "provider": "opencode",
                    "costUSD": 0.6200000000005,
                    "costStatus": "exact",
                    "costProvenance": "mixed",
                    "costBreakdown": {"actualUSD": 0.42, "estimatedUSD": 0.2},
                }
            ]
        )

        self.assertEqual(result["localSpend"]["actual"]["totalUSD"], 0.42)
        self.assertEqual(result["localSpend"]["estimated"]["totalUSD"], 0.2)

    def test_unavailable_actual_and_estimated_totals_have_no_numeric_fields(self):
        result = self.build(
            [
                {"provider": "claude", "costUSD": 0.5, "costStatus": "exact", "costProvenance": "unknown"},
                {"provider": "claude", "costUSD": math.nan, "costStatus": "partial", "costProvenance": "estimated"},
            ]
        )

        self.assertEqual(
            _metered(result["localSpend"]),
            {"actual": {"costStatus": "unavailable"}, "estimated": {"costStatus": "unavailable"}},
        )

    def test_invalid_costs_never_contribute(self):
        invalid = [
            {"provider": "claude", "costStatus": "exact", "costUSD": 0},
            {"provider": "claude", "costStatus": "partial", "costUSD": -0.1},
            {"provider": "claude", "costStatus": "exact", "costUSD": math.nan},
            {"provider": "claude", "costStatus": "exact", "costUSD": math.inf},
            {"provider": "claude", "costStatus": "exact", "costUSD": -math.inf},
            {"provider": "claude", "costStatus": "exact"},
            {"provider": "claude", "costStatus": "unavailable", "costUSD": 0.9},
        ]

        result = self.build(invalid)

        self.assertEqual(
            _metered(result["localSpend"]),
            {"actual": {"costStatus": "unavailable"}, "estimated": {"costStatus": "unavailable"}},
        )

    def test_collection_failure_does_not_fail_envelope(self):
        provider = {"id": "claude", "ok": True}
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(envelope, "collect", return_value={}),
                mock.patch.object(envelope, "normalize", return_value=provider),
                mock.patch.object(config, "cache_dir", return_value=directory),
                mock.patch("tidesi.sessions.all_session_rows", side_effect=RuntimeError("broken store")),
            ):
                result = envelope.build(["claude"], now=1_700_000_000)

        self.assertEqual(
            _metered(result["localSpend"]),
            {"actual": {"costStatus": "unavailable"}, "estimated": {"costStatus": "unavailable"}},
        )
        self.assertEqual(result["providers"], [provider])

    def test_existing_envelope_keys_and_provider_spend_are_unchanged(self):
        result = self.build([])

        self.assertEqual(
            set(result),
            {"schemaVersion", "updatedAt", "active", "providers", "localSpend"},
        )
        self.assertEqual(result["schemaVersion"], 1)
        self.assertEqual(result["updatedAt"], 1_700_000_000)
        self.assertEqual(result["active"], "claude")
        self.assertEqual(result["providers"][0]["details"]["stats"]["totalCostUSD"], 99.0)


if __name__ == "__main__":
    unittest.main()


class LocalSpendPagingTest(unittest.TestCase):
    """The rollup must see every indexed session, not one 60-row page."""

    def test_totals_every_row_beyond_one_page(self):
        rows = [
            {
                "provider": "cline",
                "source": "cline",
                "costUSD": 1.0,
                "costStatus": "exact",
                "costProvenance": "estimated",
            }
            for _ in range(100)
        ]
        with tempfile.TemporaryDirectory() as directory:
            seed_session_index(directory, rows)
            with mock.patch.object(config, "cache_dir", return_value=directory):
                spend = envelope._local_spend()

        self.assertEqual(spend["estimated"]["totalUSD"], 100.0)

    def test_reads_the_index_unpaged(self):
        with tempfile.TemporaryDirectory() as directory:
            seed_session_index(directory, [])
            with (
                mock.patch.object(config, "cache_dir", return_value=directory),
                mock.patch("tidesi.sessions.all_session_rows", side_effect=AssertionError("local spend read a page")) as unpaged,
            ):
                envelope._local_spend()

        unpaged.assert_not_called()

    def test_falls_back_to_full_rows_when_the_cache_is_absent(self):
        rows = [
            {
                "provider": "cline",
                "source": "cline",
                "costUSD": 1.0,
                "costStatus": "exact",
                "costProvenance": "estimated",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(config, "cache_dir", return_value=directory),
                mock.patch("tidesi.sessions.all_session_rows", return_value=rows) as unpaged,
            ):
                spend = envelope._local_spend()

        self.assertEqual(spend["estimated"]["totalUSD"], 1.0)
        unpaged.assert_called_once_with()


class LocalSpendDailyTest(unittest.TestCase):
    """The per-day series feeds the Spend tab's expandable row chart, so it
    has to add up to exactly the total shown on that row — it is built from
    the same contributions rather than re-derived from provider stats, which
    report $0 for plan-covered work."""

    def _rows(self, *stamps):
        return [
            {
                "provider": "cline",
                "source": "cline",
                "costUSD": 1.5,
                "costStatus": "exact",
                "costProvenance": "estimated",
                "lastActivityAt": stamp,
            }
            for stamp in stamps
        ]

    def test_daily_series_buckets_by_local_day_and_sums_to_the_total(self):
        # Two sessions on one day, one on the next.
        day_one = 1_789_046_340
        rows = self._rows(day_one, day_one + 600, day_one + 86_400)
        with tempfile.TemporaryDirectory() as directory:
            seed_session_index(directory, rows)
            with mock.patch.object(config, "cache_dir", return_value=directory):
                spend = envelope._local_spend()

        entry = spend["estimated"]["providers"]["cline::cline"]
        daily = entry["dailyUSD"]
        self.assertEqual(len(daily), 2)
        self.assertEqual([point["usd"] for point in daily], [3.0, 1.5])
        self.assertEqual(sum(point["usd"] for point in daily), entry["costUSD"])
        self.assertEqual([point["date"] for point in daily], sorted(point["date"] for point in daily))

    def test_sessions_without_a_timestamp_omit_the_series_but_keep_the_total(self):
        rows = [
            {
                "provider": "cline",
                "source": "cline",
                "costUSD": 2.0,
                "costStatus": "exact",
                "costProvenance": "estimated",
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            seed_session_index(directory, rows)
            with mock.patch.object(config, "cache_dir", return_value=directory):
                spend = envelope._local_spend()

        entry = spend["estimated"]["providers"]["cline::cline"]
        self.assertEqual(entry["costUSD"], 2.0)
        self.assertNotIn("dailyUSD", entry)

    def test_plan_covered_work_gets_its_own_daily_series(self):
        rows = [
            {
                "provider": "claude",
                "source": "claude",
                "costUSD": 12.0,
                "costStatus": "exact",
                "costProvenance": "estimated",
                "costBilling": "subscription",
                "lastActivityAt": 1_789_046_340,
            }
        ]
        with tempfile.TemporaryDirectory() as directory:
            seed_session_index(directory, rows)
            with mock.patch.object(config, "cache_dir", return_value=directory):
                spend = envelope._local_spend()

        entry = spend["subscription"]["providers"]["claude::claude"]
        self.assertEqual([point["usd"] for point in entry["dailyUSD"]], [12.0])
        self.assertEqual(spend["estimated"]["costStatus"], "unavailable")
