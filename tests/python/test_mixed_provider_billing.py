import json
import tempfile
from unittest import mock

from _support import IsolatedHomeTest
from test_opencode_costs import _assistant_message, _create_database
from tidesi import billing_mode, sessions
from tidesi.session_index import _row_cost_groups


class MixedProviderBillingTest(IsolatedHomeTest):
    def setUp(self) -> None:
        super().setUp()
        billing_mode.reset_cache()
        self.addCleanup(billing_mode.reset_cache)

    def test_opencode_uses_its_own_oauth_instead_of_the_codex_cli_login(self) -> None:
        self.write(".local/share/opencode/auth.json", {"openai": {"type": "oauth", "access": "fixture-secret"}})
        with mock.patch.object(billing_mode, "get_openai_credentials", return_value={"authMode": "apikey"}):
            self.assertEqual(billing_mode.mode_for("openai", "opencode"), "subscription")
            self.assertEqual(billing_mode.mode_for("openai"), "api")

    def test_opencode_api_key_does_not_inherit_an_unrelated_codex_subscription(self) -> None:
        self.write(".local/share/opencode/auth.json", {"openai": {"type": "api", "key": "fixture-secret"}})
        with mock.patch.object(billing_mode, "get_openai_credentials", return_value={"authMode": "chatgpt"}):
            self.assertEqual(billing_mode.mode_for("openai", "opencode"), "api")
            self.assertEqual(billing_mode.mode_for("openai"), "subscription")

    def test_mixed_session_splits_billing_per_provider_without_changing_its_total(self) -> None:
        self.write(".local/share/opencode/auth.json", {"openai": {"type": "oauth", "access": "fixture-secret"}})
        messages = [
            _assistant_message("message-1", provider_id="ollama-cloud", tokens={"input": 0, "output": 0}, cost=0.42),
            _assistant_message("message-2", provider_id="openai", tokens={"input": 800, "output": 100}),
        ]
        with tempfile.TemporaryDirectory() as root:
            database = _create_database(root, messages=messages)
            with (
                mock.patch.dict("os.environ", {"OPENCODE_DB": database}),
                mock.patch.object(sessions.pricing, "cached_catalog", return_value={"openai": {"gpt-test": {"input": 2, "output": 8}}}),
                mock.patch.object(billing_mode, "get_openai_credentials", return_value={"authMode": "apikey"}),
            ):
                entry = sessions._opencode_entries()[0]
        self.assertEqual(entry["providerCosts"]["openai"]["costBilling"], "subscription")
        self.assertEqual(entry["providerCosts"]["ollama-cloud"]["costBilling"], "api")
        groups = _row_cost_groups(entry)
        self.assertAlmostEqual(sum(group[3] for group in groups), 0.4224)
        self.assertAlmostEqual(sum(group[3] for group in groups if group[0] == "api"), 0.42)
        self.assertAlmostEqual(sum(group[3] for group in groups if group[0] == "subscription"), 0.0024)
        self.assertEqual(sum(group[6] for group in groups), 900)
        self.assertEqual(sum(group[6] for group in groups if group[2].startswith("ollama-cloud")), 0)
        self.assertNotIn("fixture-secret", json.dumps(entry))

    def test_legacy_child_without_mode_inherits_parent_instead_of_reclassifying_history(self) -> None:
        row = {
            "provider": "opencode",
            "source": "opencode",
            "costBilling": "subscription",
            "providerCosts": {"openai": {"costUSD": 2, "costStatus": "exact", "costProvenance": "estimated"}},
        }
        self.assertEqual(_row_cost_groups(row)[0][0], "subscription")

    def test_mixed_provenance_does_not_double_count_session_tokens(self) -> None:
        row = {
            "provider": "openai",
            "costBilling": "api",
            "tokens": 100,
            "lastActivityAt": 1_800_000_000,
            "costUSD": 1,
            "costStatus": "exact",
            "costProvenance": "mixed",
            "costBreakdown": {"actualUSD": 0.4, "estimatedUSD": 0.6},
        }
        self.assertEqual(sum(group[6] for group in _row_cost_groups(row)), 100)
        separate = _row_cost_groups(row, "actual") + _row_cost_groups(row, "estimated")
        self.assertEqual(sum(group[6] for group in separate), 100)
