import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tidesi import config, pricing, session_manifest, sessions
from tidesi.providers.zed_sessions import ZedSession
from tidesi.session_index import SessionIndex


class ZedProviderRoutingTests(unittest.TestCase):
    def test_unknown_hosted_model_does_not_guess_a_vendor_from_its_name(self):
        # Given: the saved hosted model has no exact catalog identity.
        with mock.patch.object(sessions.pricing, "cached_catalog", return_value={}):
            # When: resolving its upstream provider.
            provider = sessions._resolve_zed_provider("zed.dev", "gpt-future-unknown")
        # Then: the usage stays with its known host rather than an invented vendor.
        self.assertEqual(provider, "zed.dev")

    def test_explicit_provider_is_preserved_for_unknown_models(self):
        with mock.patch.object(sessions.pricing, "cached_catalog", return_value={}):
            provider = sessions._resolve_zed_provider("future-provider", "unknown-model")
        self.assertEqual(provider, "future-provider")

    def test_future_catalog_model_updates_provider_identity_without_pricing_local_tokens(self):
        record = ZedSession("id", "title", "workspace", 1_800_000_000, "zed.dev", "future-model-v99", 1_000_000, 1_000_000)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(config, "cache_dir", return_value=directory):
            path = Path(directory) / pricing.CACHE_FILENAME
            snapshot = {
                "version": pricing.CACHE_VERSION,
                "source": pricing.MODELS_DEV_SOURCE_URL,
                "fetchedAt": 1,
                "checkedAt": 1,
                "providers": {},
            }
            path.write_text(json.dumps(snapshot), encoding="utf-8")
            index = SessionIndex(Path(directory) / "sessions.sqlite3")
            with mock.patch.object(sessions.zed_sessions, "read_sessions", return_value=[record]):
                initial = next(source for source in session_manifest.build_manifests() if source.source_id == "zed")
                index.reconcile([initial], lambda _: sessions._zed_entries(include_all=True))
                self.assertEqual(index.rows()[0]["costStatus"], "unavailable")
                snapshot["providers"] = {"future-vendor": {"future-model-v99": {"input": 7, "output": 11}}}
                path.write_text(json.dumps(snapshot), encoding="utf-8")

                updated = next(source for source in session_manifest.build_manifests() if source.source_id == "zed")
                index.reconcile([updated], lambda _: sessions._zed_entries(include_all=True))

            row = index.rows()[0]
            self.assertEqual(row["billingProvider"], "future-vendor")
            self.assertNotIn("costUSD", row)
            self.assertEqual(row["costStatus"], "unavailable")
