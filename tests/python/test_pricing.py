"""Catalog units, provider isolation, cache refresh/failure and collector wiring."""

import copy
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

from _support import IsolatedHomeTest, raw_fixture
from tidesi import billing, collect, pricing, sessions
from tidesi.http import HttpResult
from tidesi.normalize.claude import normalize_claude
from tidesi.normalize.openai import normalize_openai


def catalog():
    # Synthetic upstream data: assertions exercise conversion, not live prices.
    return {
        "claude-test": {
            "litellm_provider": "anthropic",
            "mode": "chat",
            "input_cost_per_token": 0.000002,
            "output_cost_per_token": 0.000008,
            "cache_read_input_token_cost": 0.0000002,
        },
        "gpt-test": {
            "litellm_provider": "openai",
            "mode": "responses",
            "input_cost_per_token": 0.000004,
            "output_cost_per_token": 0.000012,
        },
    }


def models_dev_catalog():
    return {
        "ollama-cloud": {
            "id": "ollama-cloud",
            "models": {
                "deepseek-v4.1-flash": {
                    "id": "deepseek-v4.1-flash",
                    "cost": {"input": 0.15, "output": 0.6, "cache_read": 0.003},
                },
                "deepseek-v4.1-flash-preview": {
                    "id": "deepseek-v4.1-flash-preview",
                    "cost": {"input": 0.2, "output": 0.8},
                },
            },
        },
        "google": {
            "id": "google",
            "models": {
                "gemini-2.5-pro": {
                    "id": "gemini-2.5-pro",
                    "cost": {"input": 1.25, "output": 10},
                },
            },
        },
        "openai": {
            "id": "openai",
            "models": {
                "gpt-test": {
                    "id": "gpt-test",
                    "cost": {"input": 0.25, "output": 0.75},
                },
            },
        },
        "opencode": {
            "id": "opencode",
            "models": {
                "big-pickle": {
                    "id": "big-pickle",
                    "cost": {"input": 0, "output": 0},
                },
            },
        },
    }


class PricingTest(IsolatedHomeTest):
    def setUp(self):
        super().setUp()
        self.clock = mock.patch("tidesi.pricing.time.time", return_value=1_800_000_000).start()
        self.addCleanup(mock.patch.stopall)
        self.fetch = mock.patch("tidesi.pricing.fetch_json", return_value=HttpResult(200, json.dumps(catalog()))).start()

    def test_models_dev_parser_preserves_units_and_dynamic_provider_ids(self):
        tables = pricing.parse_models_dev_catalog(models_dev_catalog())

        self.assertEqual(
            tables["ollama-cloud"]["deepseek-v4.1-flash"],
            {"input": 0.15, "output": 0.6, "cached": 0.003},
        )
        self.assertEqual(tables["google"]["gemini-2.5-pro"], {"input": 1.25, "output": 10})
        self.assertEqual(tables["opencode"]["big-pickle"], {"input": 0, "output": 0})

    def test_models_dev_parser_rejects_malformed_catalogs(self):
        for data in (None, [], {}, {"google": {"models": []}}):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    pricing.parse_models_dev_catalog(data)

    def test_catalog_parsers_enforce_provider_model_and_model_id_bounds(self):
        provider_count = pricing.MAX_PROVIDERS + 1
        models_dev = {
            f"provider-{index}": {
                "models": {
                    "model": {"cost": {"input": 1, "output": 1}},
                }
            }
            for index in range(provider_count)
        }
        parsed_models_dev = pricing.parse_models_dev_catalog(models_dev)
        self.assertEqual(len(parsed_models_dev), pricing.MAX_PROVIDERS)

        model_count = pricing.MAX_MODELS_PER_PROVIDER + 1
        direct = {
            f"model-{index}": {
                "litellm_provider": "openai",
                "mode": "chat",
                "input_cost_per_token": 0.000001,
                "output_cost_per_token": 0.000001,
            }
            for index in range(model_count)
        }
        parsed_direct = pricing.parse_catalog(direct)
        self.assertEqual(len(parsed_direct["openai"]), pricing.MAX_MODELS_PER_PROVIDER)

        long_model = "x" * (pricing.MAX_MODEL_ID_LENGTH + 1)
        openrouter = {
            "data": [{"id": f"openai/model-{index}", "pricing": {"prompt": 1, "completion": 1}} for index in range(model_count)]
            + [{"id": f"openai/{long_model}", "pricing": {"prompt": 1, "completion": 1}}]
        }
        parsed_openrouter = pricing.parse_openrouter_catalog(openrouter)
        self.assertEqual(len(parsed_openrouter["openai"]), pricing.MAX_MODELS_PER_PROVIDER)
        self.assertNotIn(f"openai/{long_model}", parsed_openrouter["openai"])
        self.assertEqual(
            pricing.parse_openrouter_catalog({"data": [{"id": f"openai/{long_model}", "pricing": {"prompt": 1, "completion": 1}}]}),
            {},
        )

        oversized = {f"provider-{index}": {"model": {"input": 1, "output": 1}} for index in range(provider_count)}
        self.assertFalse(pricing._valid_tables(oversized))
        self.assertFalse(pricing._valid_tables({"openai": {f"model-{index}": {"input": 1, "output": 1} for index in range(model_count)}}))
        self.assertFalse(pricing._valid_tables({"openai": {long_model: {"input": 1, "output": 1}}}))

    def test_dynamic_namespaces_are_requested_and_cache_validated(self):
        requested = pricing._requested_models({"ollama-cloud": ["deepseek-v4.1-flash"], "google": ["gemini-2.5-pro"]})
        self.assertEqual(
            requested,
            [("ollama-cloud", "deepseek-v4.1-flash"), ("google", "gemini-2.5-pro")],
        )
        self.assertTrue(pricing._valid_tables({"ollama-cloud": {"deepseek-v4.1-flash": {"input": 0.15, "output": 0.6}}}))

    def test_exact_match_and_free_model_zero_rate_are_estimable(self):
        bucket = billing.UsageBucket("opencode", "big-pickle", "session", 100, 50)
        result = billing.aggregate_session_usage([bucket], {"opencode": {"big-pickle": {"input": 0, "output": 0}}})

        self.assertEqual(result["costStatus"], "exact")
        self.assertEqual(result["costProvenance"], "estimated")
        self.assertEqual(result["costUSD"], 0)

    def test_model_lookup_is_exact_without_near_match_aliases(self):
        rates = pricing.parse_models_dev_catalog(models_dev_catalog())
        result = billing.aggregate_session_usage(
            [
                billing.UsageBucket("ollama-cloud", "deepseek-v4.1-flash", "session", 100, 50),
                billing.UsageBucket("ollama-cloud", "deepseek-v4.1", "session", 100, 50),
            ],
            rates,
        )

        self.assertEqual(result["costStatus"], "partial")
        self.assertGreater(result["costUSD"], 0)

    def test_cache_rejects_wrong_version_and_malformed_dynamic_tables(self):
        path = self.home / "cache" / pricing.CACHE_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "version": pricing.CACHE_VERSION - 1,
                    "source": pricing.MODELS_DEV_SOURCE_URL,
                    "fetchedAt": 1,
                    "checkedAt": 1,
                    "providers": {"google": {"gemini": {"input": 1, "output": 2}}},
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(pricing._read_cache(str(path)), {})
        path.write_text(
            json.dumps(
                {
                    "version": pricing.CACHE_VERSION,
                    "source": pricing.MODELS_DEV_SOURCE_URL,
                    "fetchedAt": 1,
                    "checkedAt": 1,
                    "providers": {"bad/provider": {"gemini": {"input": 1, "output": 2}}},
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(pricing._read_cache(str(path)), {})

    def test_cached_catalog_reuses_one_validated_disk_snapshot(self):
        snapshot = {
            "version": pricing.CACHE_VERSION,
            "source": pricing.MODELS_DEV_SOURCE_URL,
            "fetchedAt": 1,
            "checkedAt": 1,
            "providers": {"openai": {"gpt-test": {"input": 1, "output": 2}}},
        }
        with mock.patch.object(pricing, "_read_cache", return_value=snapshot) as read_cache:
            first = pricing.cached_catalog()
            second = pricing.cached_catalog()

        self.assertEqual(first, second)
        self.assertEqual(read_cache.call_count, 1)

    def test_snapshot_revalidates_after_cache_file_identity_changes(self):
        path = self.write(
            f"cache/{pricing.CACHE_FILENAME}",
            {
                "version": pricing.CACHE_VERSION,
                "source": pricing.MODELS_DEV_SOURCE_URL,
                "fetchedAt": 1,
                "checkedAt": 1,
                "providers": {"openai": {"first": {"input": 1, "output": 2}}},
            },
        )
        with mock.patch.object(pricing, "_read_cache", wraps=pricing._read_cache) as read_cache:
            self.assertIn("first", pricing.cached_catalog()["openai"])
            self.assertIn("first", pricing.cached_catalog()["openai"])
            self.assertEqual(read_cache.call_count, 1)
            path.write_text(
                json.dumps(
                    {
                        "version": pricing.CACHE_VERSION,
                        "source": pricing.MODELS_DEV_SOURCE_URL,
                        "fetchedAt": 2,
                        "checkedAt": 2,
                        "providers": {"openai": {"replacement-model": {"input": 3, "output": 4}}},
                    }
                ),
                encoding="utf-8",
            )
            os.utime(path, ns=(2_000_000_000, 2_000_000_000))
            self.assertIn("replacement-model", pricing.cached_catalog()["openai"])

        self.assertEqual(read_cache.call_count, 2)

    def test_load_catalog_refresh_invalidates_cached_catalog(self):
        path = self.write(
            f"cache/{pricing.CACHE_FILENAME}",
            {
                "version": pricing.CACHE_VERSION,
                "source": pricing.MODELS_DEV_SOURCE_URL,
                "fetchedAt": 1,
                "checkedAt": 1,
                "providers": {"openai": {"old-model": {"input": 1, "output": 2}}},
                "error": "",
            },
        )
        self.assertEqual(pricing.cached_catalog(), {"openai": {"old-model": {"input": 1, "output": 2}}})
        with (
            mock.patch.object(pricing.time, "time", return_value=2),
            mock.patch.object(pricing, "_fetch_models_dev", return_value={"openai": {"new-model": {"input": 3, "output": 4}}}),
            mock.patch.object(pricing, "_fetch_litellm", return_value={}),
        ):
            pricing.load_catalog(force=True)

        self.assertEqual(
            pricing.cached_catalog(),
            {
                "openai": {
                    "new-model": {"input": 3, "output": 4},
                    "old-model": {"input": 1, "output": 2},
                }
            },
        )
        self.assertTrue(path.exists())

    def test_cached_catalog_fails_closed_after_cache_replacement(self):
        path = self.write(
            f"cache/{pricing.CACHE_FILENAME}",
            {
                "version": pricing.CACHE_VERSION,
                "source": pricing.MODELS_DEV_SOURCE_URL,
                "fetchedAt": 1,
                "checkedAt": 1,
                "providers": {"openai": {"gpt-test": {"input": 1, "output": 2}}},
            },
        )
        self.assertIn("openai", pricing.cached_catalog())
        path.write_text("{malformed", encoding="utf-8")
        self.assertEqual(pricing.cached_catalog(), {})

    def test_models_dev_precedes_litellm_and_litellm_fills_exact_misses(self):
        def fetch(url, timeout, headers=None):
            self.assertEqual(timeout, 10)
            if url == pricing.MODELS_DEV_SOURCE_URL:
                self.assertEqual(headers, {"Accept": "application/json", "User-Agent": "tidesi/1.0"})
                return HttpResult(200, json.dumps(models_dev_catalog()))
            if url == pricing.SOURCE_URL:
                fallback = catalog()
                fallback["litellm-only"] = fallback["gpt-test"]
                return HttpResult(200, json.dumps(fallback))
            raise AssertionError(url)

        self.fetch.side_effect = fetch
        result = pricing.load_catalog(force=True, models={"openai": ["gpt-test", "litellm-only"]})

        self.assertEqual(result["providers"]["openai"]["gpt-test"], {"input": 0.25, "output": 0.75})
        self.assertEqual(result["providers"]["openai"]["litellm-only"]["input"], 4)
        self.assertEqual(result["providers"]["google"]["gemini-2.5-pro"]["output"], 10)

    def test_units_and_provider_isolation(self):
        data = catalog()
        for key, provider, mode in (
            ("bedrock/claude-test", "bedrock", "chat"),
            ("openrouter/gpt-test", "openrouter", "chat"),
            ("region/claude-test", "anthropic", "chat"),
            ("gpt-image", "openai", "image_generation"),
            ("sample_spec", "example", "chat"),
        ):
            data[key] = {**data["gpt-test"], "litellm_provider": provider, "mode": mode}
        tables = pricing.parse_catalog(data)
        self.assertEqual(set(tables["anthropic"]), {"claude-test"})
        self.assertEqual(tables["anthropic"]["claude-test"]["input"], 2)
        self.assertEqual(tables["anthropic"]["claude-test"]["output"], 8)
        self.assertAlmostEqual(tables["anthropic"]["claude-test"]["cached"], 0.2)
        self.assertEqual(tables["openai"], {"gpt-test": {"input": 4, "output": 12}})

    def test_existing_billing_keeps_unknown_models_visible_and_unpriced(self):
        result = billing.price_models(
            [{"model": "known", "input_tokens": 1_000, "output_tokens": 200}, {"model": "unknown", "input_tokens": 50}],
            {"known": {"input": 1, "output": 2}},
        )

        self.assertEqual(set(result["models"]), {"known", "unknown"})
        self.assertTrue(result["models"]["known"]["priced"])
        self.assertFalse(result["models"]["unknown"]["priced"])
        self.assertGreater(result["totalCostUSD"], 0)

    def test_incomplete_and_invalid_rates_do_not_become_free_models(self):
        for value in (None, True, "0.001", -1, float("nan"), float("inf"), 10**400):
            with self.subTest(value=str(value)):
                data = catalog()
                data["bad"] = {**data["gpt-test"], "input_cost_per_token": value}
                data["bad-cache"] = {**data["gpt-test"], "cache_read_input_token_cost": value}
                tables = pricing.parse_catalog(data)
                self.assertNotIn("bad", tables["openai"])
                if value is not None:
                    self.assertNotIn("bad-cache", tables["openai"])
        data = catalog()
        data["free"] = {**data["gpt-test"], "input_cost_per_token": 0, "output_cost_per_token": 0}
        self.assertEqual(pricing.parse_catalog(data)["openai"]["free"], {"input": 0, "output": 0})
        for data in (None, [], {}, {"model": {"litellm_provider": []}}):
            with self.assertRaises(ValueError):
                pricing.parse_catalog(data)
        self.assertEqual(set(pricing.parse_catalog({"gpt-test": catalog()["gpt-test"]})), {"openai"})

    def test_shared_daily_cache_updates_new_models_and_prices(self):
        self.assertEqual(pricing.get_pricing("anthropic")["claude-test"]["input"], 2)
        self.assertEqual(pricing.get_pricing("openai")["gpt-test"]["output"], 12)
        self.assertEqual(
            self.fetch.call_args_list,
            [
                mock.call(
                    pricing.MODELS_DEV_SOURCE_URL,
                    headers={"Accept": "application/json", "User-Agent": "tidesi/1.0"},
                    timeout=10,
                ),
                mock.call(pricing.SOURCE_URL, timeout=10),
            ],
        )
        self.clock.return_value += pricing.REFRESH_SECONDS
        data = catalog()
        data["gpt-test"]["input_cost_per_token"] = 0.000006
        data["new-model"] = data["gpt-test"]
        self.fetch.return_value = HttpResult(200, json.dumps(data))
        self.assertEqual(pricing.get_pricing("openai")["new-model"]["input"], 6)
        self.assertEqual(self.fetch.call_count, 4)

    def test_normal_refresh_boundary_is_exactly_seven_days(self):
        pricing.load_catalog()
        self.clock.return_value += pricing.REFRESH_SECONDS - 1
        pricing.load_catalog()
        self.assertEqual(self.fetch.call_count, 2)
        self.clock.return_value += 1
        pricing.load_catalog()
        self.assertEqual(self.fetch.call_count, 4)

    def test_force_refresh_bypasses_a_fresh_cache(self):
        pricing.load_catalog()
        pricing.load_catalog(force=True)
        self.assertEqual(self.fetch.call_count, 4)

    def test_concurrent_forced_refreshes_share_one_upstream_fetch(self):
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        def fetch(_url, timeout, headers=None):
            nonlocal calls
            with calls_lock:
                calls += 1
            entered.set()
            release.wait()
            return HttpResult(200, json.dumps(catalog()))

        self.fetch.side_effect = fetch
        threads = [threading.Thread(target=lambda: pricing.load_catalog(force=True)) for _ in range(2)]
        for thread in threads:
            thread.start()
        try:
            self.assertTrue(entered.wait(2))
        finally:
            release.set()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(calls, 2)

    def test_litellm_hit_does_not_consult_openrouter(self):
        pricing.load_catalog(models={"anthropic": ["claude-test"]})
        self.assertEqual(self.fetch.call_count, 2)

    def test_fresh_cache_requested_miss_consults_openrouter_without_litellm_refetch(self):
        pricing.load_catalog()
        self.fetch.reset_mock()
        self.fetch.return_value = HttpResult(200, json.dumps(raw_fixture("pricing-openrouter-response")))
        result = pricing.load_catalog(models={"anthropic": ["claude-missing"]})
        self.assertEqual(result["providers"]["anthropic"]["claude-test"]["input"], 2)
        self.assertEqual(result["providers"]["anthropic"]["claude-missing"]["output"], 9.0)
        self.fetch.assert_called_once_with(pricing.OPENROUTER_SOURCE_URL, timeout=10)

    def test_openrouter_prices_an_exact_litellm_miss(self):
        self.fetch.side_effect = [
            HttpResult(200, json.dumps(models_dev_catalog())),
            HttpResult(200, json.dumps(catalog())),
            HttpResult(200, json.dumps(raw_fixture("pricing-openrouter-response"))),
        ]
        result = pricing.load_catalog(models={"anthropic": ["claude-missing"]})
        self.assertEqual(result["providers"]["anthropic"]["claude-missing"], {"input": 3.0, "output": 9.0, "cached": 0.3})
        self.assertEqual(self.fetch.call_count, 3)

    def test_openrouter_fallback_creates_provider_bucket_after_litellm_failure(self):
        self.fetch.side_effect = [
            HttpResult(503, ""),
            HttpResult(503, ""),
            HttpResult(200, json.dumps(raw_fixture("pricing-openrouter-response"))),
        ]
        result = pricing.load_catalog(models={"anthropic": ["claude-missing"]})
        self.assertEqual(result["providers"]["anthropic"]["claude-missing"]["input"], 3.0)
        self.assertEqual(result["providers"]["anthropic"]["claude-missing"]["output"], 9.0)
        self.assertEqual(self.fetch.call_count, 3)

    def test_both_pricing_sources_fail_without_cache(self):
        self.fetch.side_effect = [HttpResult(503, ""), HttpResult(503, ""), HttpResult(503, "")]
        result = pricing.load_catalog(models={"anthropic": ["missing"]})
        self.assertEqual(result["providers"], {})
        self.assertTrue(result["error"])
        self.assertEqual(self.fetch.call_count, 3)

    def test_failed_requested_refresh_retains_last_good_rates(self):
        pricing.load_catalog()
        self.clock.return_value += pricing.REFRESH_SECONDS
        self.fetch.side_effect = [HttpResult(503, ""), HttpResult(503, ""), HttpResult(503, "")]
        result = pricing.load_catalog(models={"anthropic": "missing"})
        self.assertEqual(result["providers"]["openai"]["gpt-test"]["input"], 4)
        self.assertTrue(result["error"])

    def test_cached_token_math_and_unknown_model_status(self):
        result = billing.aggregate_session_usage(
            [
                billing.UsageBucket("anthropic", "known", "session", 1_000, 200, 400),
                billing.UsageBucket("anthropic", "unknown", "session", 100, 10),
            ],
            {"anthropic": {"known": {"input": 2, "output": 8, "cached": 0.2}}},
        )
        self.assertAlmostEqual(result["costUSD"], 0.00288)
        self.assertEqual(result["costStatus"], "partial")

    def test_missing_usage_is_unavailable(self):
        result = billing.aggregate_session_usage([], {"anthropic": {}})
        self.assertEqual(result, {"costStatus": "unavailable"})

    def test_session_rows_keep_cost_out_of_redacted_detail(self):
        with mock.patch.object(pricing, "cached_catalog", return_value={"anthropic": {"claude-test": {"input": 2, "output": 8}}}):
            entry = sessions._entry("claude", "Title", 2_000, session_id="id", detail="$secret")
            entry = sessions._with_usage_cost(entry, "anthropic", [billing.UsageBucket("anthropic", "claude-test", "id", 100, 50)])
        self.assertEqual(entry["costStatus"], "exact")
        self.assertAlmostEqual(entry["costUSD"], 0.0006)
        self.assertEqual(entry["detail"], "$secret")

    def test_claude_session_sums_multiple_models_and_marks_partial(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "projects", "-tmp-widget")
            os.makedirs(project)
            path = os.path.join(project, "session.jsonl")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(json.dumps({"message": {"model": "known-a", "usage": {"input_tokens": 100, "output_tokens": 50}}}) + "\n")
                stream.write(json.dumps({"message": {"model": "known-b", "usage": {"input_tokens": 200, "output_tokens": 25}}}) + "\n")
                stream.write(json.dumps({"message": {"model": "unknown", "usage": {"input_tokens": 1, "output_tokens": 1}}}) + "\n")
            with (
                mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": root}, clear=True),
                mock.patch.object(
                    pricing,
                    "cached_catalog",
                    return_value={"anthropic": {"known-a": {"input": 1, "output": 2}, "known-b": {"input": 3, "output": 4}}},
                ),
            ):
                entry = sessions._claude_entries()[0]
        self.assertEqual(entry["costStatus"], "partial")
        self.assertAlmostEqual(entry["costUSD"], 0.0009)

    def test_cache_write_failure_keeps_current_process_rates(self):
        with mock.patch("tidesi.pricing.os.replace", side_effect=PermissionError):
            pricing.load_catalog(force=True)
        self.assertEqual(pricing.cached_catalog()["openai"]["gpt-test"]["input"], 4)

    def test_failed_refresh_retains_rates_and_backs_off(self):
        original = pricing.load_catalog()
        for response in (HttpResult(0, ""), HttpResult(200, "<html>broken</html>"), HttpResult(200, "{}")):
            self.clock.return_value += pricing.REFRESH_SECONDS
            self.fetch.return_value = response
            failed = pricing.load_catalog()
            self.assertTrue(failed["error"])
            self.assertEqual(failed["providers"], original["providers"])
            self.assertEqual(failed["fetchedAt"], original["fetchedAt"])
            calls = self.fetch.call_count
            self.assertEqual(pricing.load_catalog(), failed)
            self.assertEqual(self.fetch.call_count, calls)
        self.clock.return_value += pricing.RETRY_SECONDS
        self.fetch.return_value = HttpResult(200, json.dumps(catalog()))
        self.assertEqual(pricing.load_catalog()["error"], "")

    def test_first_offline_fetch_and_corrupt_cache_remain_unpriced(self):
        self.write(f"cache/{pricing.CACHE_FILENAME}", "broken")
        self.fetch.return_value = HttpResult(503, "")
        self.assertEqual(pricing.get_pricing("anthropic"), {})
        self.assertEqual(pricing.get_pricing("openai"), {})
        self.assertEqual(self.fetch.call_count, 2)

    def test_unwritable_cache_still_uses_download(self):
        with mock.patch("tidesi.pricing.os.replace", side_effect=PermissionError):
            self.assertEqual(pricing.get_pricing("openai")["gpt-test"]["input"], 4)
        self.assertEqual(list((self.home / "cache").iterdir()), [])

    def test_normalizers_use_supplied_rates_without_io(self):
        for name, normalize, model in (
            ("claude-success", normalize_claude, "claude-sonnet-4"),
            ("openai-api-key-only", normalize_openai, "gpt-4o-mini"),
        ):
            raw = copy.deepcopy(raw_fixture(name))
            raw["inputs"]["pricing"] = {model: {"input": 7, "output": 11}}
            org = normalize(raw)["details"]["organizationUsage"]
            row = org["models"][model]
            self.assertTrue(row["priced"])
            self.assertAlmostEqual(row["cost_usd"], row["input_tokens"] * 7e-6 + row["output_tokens"] * 11e-6)
            del raw["inputs"]["pricing"]
            self.assertFalse(normalize(raw)["details"]["organizationUsage"]["models"][model]["priced"])
        self.fetch.assert_not_called()

    def test_collectors_only_load_prices_when_org_usage_exists(self):
        with (
            mock.patch.object(collect, "get_claude_credentials", return_value={"claudeAdminApiKey": "test"}),
            mock.patch.object(collect, "get_openai_credentials", return_value={"openaiApiKey": "test"}),
            mock.patch.object(collect, "get_codex_rate_limits", return_value={}),
            mock.patch.object(collect, "get_codex_stats", return_value={}),
            mock.patch.object(collect, "provider_status", return_value=None),
            mock.patch.object(collect, "fetch_json") as usage,
        ):
            usage.return_value = HttpResult(200, '{"data": []}')
            for collector in (collect.collect_claude, collect.collect_openai):
                self.assertEqual(collector(1_800_000_000)["inputs"]["pricing"], {})
            self.fetch.assert_not_called()
            usage.return_value = HttpResult(200, '{"data": [{"results": [{"model": "test"}]}]}')
            self.assertIn("claude-test", collect.collect_claude(1_800_000_000)["inputs"]["pricing"])
            self.assertIn("gpt-test", collect.collect_openai(1_800_000_000)["inputs"]["pricing"])
            self.assertEqual(self.fetch.call_count, 2)


class CatalogRowsTest(unittest.TestCase):
    """The searchable rate table the Spend tab renders."""

    def _cache(self, directory, providers):
        path = os.path.join(directory, pricing.CACHE_FILENAME)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(
                {
                    "version": pricing.CACHE_VERSION,
                    "source": pricing.MODELS_DEV_SOURCE_URL,
                    "fetchedAt": 10,
                    "checkedAt": 10,
                    "providers": providers,
                    "error": "",
                },
                stream,
            )
        return path

    def test_reads_the_cache_without_ever_fetching(self):
        with tempfile.TemporaryDirectory() as directory:
            self._cache(directory, {"anthropic": {"claude-x": {"input": 3, "output": 15, "cached": 0.3}}})
            with (
                mock.patch("tidesi.config.cache_dir", return_value=directory),
                mock.patch.object(pricing, "fetch_json", side_effect=AssertionError("rate table fetched")),
            ):
                pricing._CATALOG_CACHE.clear()
                pricing._CURRENT_SNAPSHOTS.clear()
                result = pricing.catalog_rows()

        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0], {"provider": "anthropic", "model": "claude-x", "input": 3.0, "output": 15.0, "cached": 0.3})
        self.assertEqual(result["unit"], "USD per 1M tokens")

    def test_repeated_table_queries_validate_once_without_mutating_catalog_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            self._cache(directory, {"anthropic": {"claude-x": {"input": 3, "output": 15}}})
            with (
                mock.patch("tidesi.config.cache_dir", return_value=directory),
                mock.patch.object(pricing, "_read_cache", wraps=pricing._read_cache) as read_cache,
                mock.patch.object(pricing, "fetch_json", side_effect=AssertionError("table query fetched rates")),
            ):
                pricing._CATALOG_CACHE.clear()
                pricing._CURRENT_SNAPSHOTS.clear()
                first = pricing.catalog_rows("claude")
                second = pricing.catalog_rows("anthropic")

        self.assertEqual(first["rows"], second["rows"])
        self.assertEqual(read_cache.call_count, 1)

    def test_filters_on_provider_or_model_and_pages_stably(self):
        providers = {
            "anthropic": {"claude-a": {"input": 1, "output": 2}, "claude-b": {"input": 1, "output": 2}},
            "openai": {"gpt-a": {"input": 1, "output": 2}},
        }
        with tempfile.TemporaryDirectory() as directory:
            self._cache(directory, providers)
            with mock.patch("tidesi.config.cache_dir", return_value=directory):
                pricing._CATALOG_CACHE.clear()
                pricing._CURRENT_SNAPSHOTS.clear()
                by_model = pricing.catalog_rows("claude")
                by_provider = pricing.catalog_rows("openai")
                first = pricing.catalog_rows("", limit=2, offset=0)
                second = pricing.catalog_rows("", limit=2, offset=2)

        self.assertEqual([row["model"] for row in by_model["rows"]], ["claude-a", "claude-b"])
        self.assertEqual([row["model"] for row in by_provider["rows"]], ["gpt-a"])
        self.assertEqual([row["model"] for row in first["rows"]], ["claude-a", "claude-b"])
        self.assertTrue(first["hasMore"])
        self.assertEqual([row["model"] for row in second["rows"]], ["gpt-a"])
        self.assertFalse(second["hasMore"])

    def test_omits_a_rate_that_is_not_a_finite_number(self):
        """The in-memory snapshot is not re-validated on read, so the row
        builder has to drop a non-finite rate rather than render it."""
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, pricing.CACHE_FILENAME)
            with mock.patch("tidesi.config.cache_dir", return_value=directory):
                pricing._CATALOG_CACHE.clear()
                pricing._CURRENT_SNAPSHOTS.clear()
                pricing._CURRENT_SNAPSHOTS[path] = {
                    "version": pricing.CACHE_VERSION,
                    "source": pricing.MODELS_DEV_SOURCE_URL,
                    "fetchedAt": 10,
                    "checkedAt": 10,
                    "providers": {"x": {"bad": {"input": float("inf"), "output": -1}, "good": {"input": 1, "output": 2}}},
                    "error": "",
                }
                try:
                    rows = {row["model"]: row for row in pricing.catalog_rows()["rows"]}
                finally:
                    pricing._CURRENT_SNAPSHOTS.clear()

        self.assertEqual(rows["good"]["input"], 1.0)
        self.assertNotIn("input", rows["bad"])
        self.assertNotIn("output", rows["bad"])
