import io
import queue
import unittest
from unittest import mock

import _support  # noqa: F401 (backend import path)
from tidesi import collect
from tidesi.http import HttpResult
from tidesi.providers import codex_rate_limits as rpc


class CodexDeadlineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.proc = mock.Mock(stdin=io.StringIO(), stdout=io.StringIO())
        for patch in (
            mock.patch.object(rpc.shutil, "which", return_value="/fixture/codex"),
            mock.patch.object(rpc.subprocess, "Popen", return_value=self.proc),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(rpc, "_stop")
        self.stop = patch.start()
        self.addCleanup(patch.stop)

    def test_startup_timeout_is_reported_and_process_cleanup_runs(self) -> None:
        with mock.patch.object(rpc, "_read_until_id", side_effect=TimeoutError):
            result = rpc._live_codex_rate_limits()
        self.assertIn("startup", result["_codexError"])
        self.stop.assert_called_once_with(self.proc)

    def test_rate_limit_timeout_is_distinct_and_keeps_a_short_read_deadline(self) -> None:
        with mock.patch.object(rpc, "_read_until_id", side_effect=[{"id": 1, "result": {}}, TimeoutError]) as read:
            result = rpc._live_codex_rate_limits()
        self.assertIn("rate-limit", result["_codexError"])
        self.assertGreater(read.call_args_list[0].args[2], 5)
        self.assertEqual(read.call_args_list[1].args[2], 5)
        self.stop.assert_called_once_with(self.proc)

    def test_cold_start_can_answer_after_the_original_five_second_budget(self) -> None:
        def reply(lines: queue.Queue[str | None], want_id: int, timeout: float):
            if want_id == 1:
                if timeout < 10:
                    raise TimeoutError
                return {"id": 1, "result": {}}
            return {"id": 2, "result": {"rateLimits": {"primary": {"usedPercent": 42}}}}

        with mock.patch.object(rpc, "_read_until_id", side_effect=reply):
            result = rpc._live_codex_rate_limits()
        self.assertEqual(result["rateLimits"]["primary"]["usedPercent"], 42)

    def test_deadline_expiry_is_not_the_same_as_end_of_stream(self) -> None:
        with self.assertRaises(TimeoutError):
            rpc._read_until_id(queue.Queue(), 1, 0)
        lines = queue.Queue()
        lines.put(None)
        self.assertIsNone(rpc._read_until_id(lines, 1, 5))

    def test_http_success_can_recover_a_rpc_timeout(self) -> None:
        with (
            mock.patch.object(collect, "get_openai_credentials", return_value={"codexAccessToken": "fixture-token"}),
            mock.patch.object(collect, "get_codex_rate_limits", return_value={"_codexError": "Codex app-server startup timed out"}),
            mock.patch.object(collect, "get_codex_stats", return_value={}),
            mock.patch.object(collect, "provider_status", return_value=None),
            mock.patch.object(collect, "remember_codex_rate_limits"),
            mock.patch.object(collect, "fetch_json", return_value=HttpResult(200, '{"rateLimits":{"primary":{"usedPercent":7}}}')) as fetch,
        ):
            raw = collect.collect_openai(1_800_000_000)
        fetch.assert_called_once()
        self.assertEqual(raw["inputs"]["codexError"], "")
        self.assertEqual(raw["inputs"]["codex"]["rateLimits"]["primary"]["usedPercent"], 7)
