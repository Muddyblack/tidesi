import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from _support import REPO  # noqa: F401
from tidesi import collect
from tidesi.http import HttpResult
from tidesi.normalize.zed import normalize_zed
from tidesi.providers import zed


class ZedLinuxCredentialsTest(unittest.TestCase):
    def test_deferred_account_read_uses_no_credentials_or_network(self):
        with (
            mock.patch.object(zed, "_settings", side_effect=AssertionError("deferred refresh read settings")),
            mock.patch.object(zed, "fetch_json", side_effect=AssertionError("deferred refresh made a request")),
        ):
            self.assertEqual(zed.get_zed_usage(refresh=False), {"deferred": True})

    def test_normal_provider_refresh_defers_zed_but_targeted_refresh_fetches_it(self):
        with mock.patch.object(collect, "get_zed_usage", return_value={"deferred": True}) as get_usage:
            deferred = collect.collect("zed", 1_800_000_000)
        self.assertEqual(deferred["inputs"]["usage"], {"deferred": True})
        get_usage.assert_called_once_with(refresh=False)
        with mock.patch.object(collect, "get_zed_usage", return_value={"plan": "Student"}) as get_usage:
            targeted = collect.collect("zed", 1_800_000_000, refresh_zed=True)
        self.assertEqual(targeted["inputs"]["usage"], {"plan": "Student"})
        get_usage.assert_called_once_with(refresh=True)

    def test_secret_tool_attributes_on_stderr_are_read_with_the_matching_secret(self):
        output = "\n".join(
            (
                "[/org/freedesktop/secrets/item/zed]",
                "secret = fixture-token",
                "attribute.url = https://zed.dev",
                "attribute.username = 12345",
            )
        )
        with (
            mock.patch.object(zed.shutil, "which", return_value="secret-tool"),
            mock.patch.object(
                zed.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout=output),
            ) as run,
        ):
            login = zed._secret_service_login("https://zed.dev")

        self.assertEqual(login, ("12345", "fixture-token"))
        self.assertIs(run.call_args.kwargs["stderr"], zed.subprocess.STDOUT)

    def test_linux_secret_service_login_fetches_account_quota(self):
        secret_output = """[/org/freedesktop/secrets/item/wrong]
label = zed-github-account
secret = ignored-token
attribute.url = https://other.example
attribute.username = wrong-user

[/org/freedesktop/secrets/item/zed]
label = zed-github-account
secret = {"version":2,"id":"token-id","token":"opaque-token"}
attribute.url = https://zed.dev
attribute.username = 12345
"""
        account_response = {
            "plan": {
                "plan_v3": "zed_pro",
                "usage": {"edit_predictions": {"used": 7, "limit": 100}},
                "subscription_period": {"ended_at": 1_800_000_000},
            }
        }
        with (
            mock.patch.object(zed, "_settings", return_value={}),
            mock.patch.object(zed.paths, "IS_MACOS", False),
            mock.patch.object(zed.paths, "IS_WINDOWS", False),
            mock.patch.object(zed.shutil, "which", return_value="/nix/store/libsecret/bin/secret-tool"),
            mock.patch.object(zed.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=secret_output)) as run,
            mock.patch.object(zed, "fetch_json", return_value=HttpResult(200, json.dumps(account_response))) as fetch,
        ):
            result = zed.get_zed_usage()

        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            ["/nix/store/libsecret/bin/secret-tool", "search", "--all", "--unlock", "url", "https://zed.dev"],
        )
        self.assertEqual(fetch.call_args.kwargs["headers"]["Authorization"], '12345 {"version":2,"id":"token-id","token":"opaque-token"}')
        self.assertEqual(result["plan"], "Pro")
        self.assertEqual(result["used"], 7)
        self.assertNotIn("opaque-token", json.dumps(result))

    def test_account_response_without_billed_usage_does_not_substitute_local_estimates(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "threads.db"
            database.touch()
            response = {
                "plan": {
                    "plan_v3": "zed_student",
                    "subscription_period": {"started_at": "2026-10-18T00:00:00Z", "ended_at": "2026-11-18T00:00:00Z"},
                    "usage": {
                        "model_requests": {"used": 9, "limit": {"limited": 100}},
                        "edit_predictions": {"used": 0, "limit": "unlimited"},
                    },
                }
            }
            from tidesi.providers import zed_sessions

            with (
                mock.patch.object(zed, "_settings", return_value={}),
                mock.patch.object(zed, "_credential_store_login", return_value=("account-id", "access-token")),
                mock.patch.object(zed, "fetch_json", return_value=HttpResult(200, json.dumps(response))),
                mock.patch.object(zed_sessions, "discover_database_paths", return_value=[str(database)]),
                mock.patch.object(zed_sessions, "read_sessions") as read_sessions,
            ):
                result = zed.get_zed_usage()

        self.assertEqual(result["includedUSD"], 10)
        self.assertEqual(
            result["agentUsage"],
            {
                "available": False,
                "source": "account-api",
                "usedUSD": None,
                "costStatus": "unavailable",
                "dailyUSD": [],
                "periodStart": result["periodStart"],
                "periodEnd": result["resetAt"],
                "includedUSD": 10.0,
                "usageBasedBilling": None,
            },
        )
        read_sessions.assert_not_called()

    def test_missing_linux_secret_tool_does_not_make_a_network_request(self):
        with (
            mock.patch.object(zed, "_settings", return_value={}),
            mock.patch.object(zed.paths, "IS_MACOS", False),
            mock.patch.object(zed.paths, "IS_WINDOWS", False),
            mock.patch.object(zed.shutil, "which", return_value=None),
            mock.patch.object(zed, "fetch_json") as fetch,
        ):
            result = zed.get_zed_usage()

        self.assertEqual(result, {})
        fetch.assert_not_called()

    def test_windows_remains_explicitly_unsupported_without_credentials(self):
        with (
            mock.patch.object(zed, "_settings", return_value={}),
            mock.patch.object(zed.paths, "IS_MACOS", False),
            mock.patch.object(zed.paths, "IS_WINDOWS", True),
            mock.patch.object(zed, "fetch_json") as fetch,
        ):
            result = zed.get_zed_usage()

        self.assertEqual(result, {"unsupported": True})
        fetch.assert_not_called()
        normalized = normalize_zed({"now": 1_800_000_000, "inputs": {"usage": result}})
        self.assertIn("Windows", normalized["error"])

    def test_verified_quota_response_is_not_marked_untested(self):
        result = normalize_zed(
            {"now": 1_800_000_000, "inputs": {"usage": {"plan": "Student", "used": 0, "unlimited": True, "resetAt": 1_800_000_000}}}
        )
        self.assertTrue(result["ok"])
        self.assertFalse(result["details"]["untested"])


if __name__ == "__main__":
    unittest.main()
