"""A provider whose fetch raises costs that provider its tab, not the whole
envelope — in the Windows tray app one exception there used to blank every
provider's data at once."""

import unittest
from unittest import mock

import _support  # noqa: F401 — puts the backend package on sys.path
from tidesi import envelope


def _collect(id_, now):
    if id_ == "claude":
        # What writing a status page with "→" in it through a cp1252 file raised.
        raise UnicodeEncodeError("charmap", "→", 0, 1, "character maps to <undefined>")
    return {"id": id_, "now": now, "inputs": {}}


class ProviderCrashTest(unittest.TestCase):
    def build(self, selected):
        with mock.patch.object(envelope, "collect", _collect):
            return envelope.build(selected, now=1_700_000_000)

    def test_crash_becomes_that_providers_error(self):
        env = self.build(["claude", "elsewhere"])
        claude, other = env["providers"]
        self.assertEqual(claude["id"], "claude")
        self.assertEqual(claude["label"], "Claude")
        self.assertIs(claude["ok"], False)
        self.assertIn("UnicodeEncodeError", claude["error"])
        self.assertIsInstance(claude["details"]["status"], dict)
        # The other provider still came through, crash or not.
        self.assertEqual(other["id"], "elsewhere")

    def test_every_default_provider_has_a_crash_label(self):
        from tidesi import config

        self.assertEqual(sorted(envelope._CRASH_LABELS), sorted(config.ALL_PROVIDERS))

    def test_deferred_zed_refresh_uses_cached_provider_without_collecting(self):
        cached = {"providers": [{"id": "zed", "ok": True, "summary": {"text": "cached"}}]}
        with (
            mock.patch("tidesi.widget_state.last_snapshot", return_value=cached),
            mock.patch.object(envelope, "collect", side_effect=AssertionError("deferred provider was collected")),
        ):
            result = envelope.build(["zed"], now=1_700_000_000, deferred_provider_ids={"zed"})
        self.assertEqual(result["providers"][0]["summary"]["text"], "cached")

    def test_explicit_zed_refresh_forces_account_collection(self):
        def collect(provider, now, *, refresh_zed=False):
            return {"id": provider, "now": now, "inputs": {"usage": {"deferred": not refresh_zed}}}

        with (
            mock.patch.object(envelope, "collect", side_effect=collect) as collect_mock,
            mock.patch("tidesi.widget_state.last_snapshot", return_value={}),
        ):
            envelope.build(["zed"], now=1_700_000_000)
        collect_mock.assert_called_once_with("zed", 1_700_000_000, refresh_zed=True)

    def test_first_deferred_zed_snapshot_is_a_non_error_placeholder(self):
        with (
            mock.patch.object(envelope, "collect", return_value={"id": "zed", "now": 1_700_000_000, "inputs": {"usage": {"deferred": True}}}),
            mock.patch("tidesi.widget_state.last_snapshot", return_value={}),
        ):
            result = envelope.build(["zed"], now=1_700_000_000, deferred_provider_ids={"zed"})
        provider = result["providers"][0]
        self.assertEqual(provider["id"], "zed")
        self.assertTrue(provider["ok"])
        self.assertTrue(provider["details"]["deferred"])


if __name__ == "__main__":
    unittest.main()
