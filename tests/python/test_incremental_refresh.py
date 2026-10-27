import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from _support import IsolatedHomeTest
from tidesi import __main__ as backend
from tidesi import envelope


class IncrementalRefreshTest(IsolatedHomeTest):
    def test_completed_provider_is_delivered_before_the_first_provider_finishes(self):
        release = threading.Event()
        delivered = threading.Event()
        updates = []

        def collect(provider, now):
            if provider == "openai":
                if not release.wait(5):
                    raise TimeoutError("test did not release slow provider")
            return {"id": provider, "ok": True}

        def receive(provider):
            updates.append(provider["id"])
            delivered.set()

        with (
            mock.patch.object(envelope, "collect", side_effect=collect),
            mock.patch.object(envelope, "normalize", side_effect=lambda row: row),
            mock.patch.object(envelope, "_local_spend", return_value={}),
            ThreadPoolExecutor(max_workers=1) as worker,
        ):
            future = worker.submit(envelope.build, ["openai", "cursor"], now=100, on_provider=receive)
            try:
                self.assertTrue(delivered.wait(2), "completed provider was held behind the slow provider")
                self.assertEqual(updates, ["cursor"])
                self.assertFalse(future.done())
            finally:
                release.set()
            result = future.result(timeout=5)
        self.assertEqual([row["id"] for row in result["providers"]], ["openai", "cursor"])
        self.assertEqual(updates, ["cursor", "openai"])

    def test_provider_failure_is_delivered_without_losing_other_results(self):
        def collect(provider, now):
            if provider == "openai":
                raise RuntimeError("fixture provider failed")
            return {"id": provider, "ok": True}

        updates = []
        with (
            mock.patch.object(envelope, "collect", side_effect=collect),
            mock.patch.object(envelope, "normalize", side_effect=lambda row: row),
            mock.patch.object(envelope, "_local_spend", return_value={}),
        ):
            result = envelope.build(["openai", "cursor"], now=100, on_provider=updates.append)
        self.assertEqual({row["id"]: row["ok"] for row in updates}, {"openai": False, "cursor": True})
        self.assertEqual(len(result["providers"]), 2)

    def test_stream_flushes_provider_updates_and_only_persists_the_final_snapshot(self):
        class Output(io.StringIO):
            flushes = 0

            def flush(self):
                self.flushes += 1

        output = Output()
        final = {"schemaVersion": 1, "updatedAt": 100, "active": "cursor", "providers": [{"id": "cursor", "ok": True}], "localSpend": {}}

        def build(selected, *, on_provider):
            self.assertEqual(selected, ["cursor"])
            on_provider(final["providers"][0])
            self.assertEqual(output.flushes, 1)
            update = json.loads(output.getvalue())
            self.assertTrue(update["partial"])
            self.assertEqual(update["providers"], final["providers"])
            return final

        with (
            mock.patch.object(envelope, "build", side_effect=build),
            mock.patch("sys.stdout", output),
            mock.patch.object(backend.widget_state, "save_snapshot") as save,
        ):
            self.assertEqual(backend.main(["--provider", "cursor", "--stream", "--save-snapshot"]), 0)
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1], final)
        save.assert_called_once_with(final)


if __name__ == "__main__":
    unittest.main()
