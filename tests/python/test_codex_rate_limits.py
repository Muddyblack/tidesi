"""The codex app-server client against a fake `codex` on PATH — a shell script
on POSIX, a .cmd shim on Windows, the way npm installs the real one there."""

import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

import _support  # noqa: F401  (sys.path)
from _support import IsolatedHomeTest
from tidesi import collect
from tidesi.http import HttpResult
from tidesi.providers import codex_rate_limits
from tidesi.providers.codex_rate_limits import TTL_SECONDS, get_codex_rate_limits, identity_key

FAKE_SERVER = r"""
import json, os, sys, queue, threading
pid_file = os.environ.get("FAKE_CODEX_PID")
if pid_file:
    with open(pid_file, "w") as fh:
        fh.write(str(os.getpid()))
launch_file = os.environ.get("FAKE_CODEX_LAUNCHES")
if launch_file:
    with open(launch_file, "a") as fh:
        fh.write("1\n")
def read_line():
    line = b""
    while not line.endswith(b"\n"):
        chunk = os.read(sys.stdin.fileno(), 1)
        if not chunk:
            break
        line += chunk
    return line

read_line()
# The client must await initialize's response before sending the next RPC.
# A thread works with anonymous pipes on Windows, where select() does not.
pending = queue.Queue()
reader = threading.Thread(target=lambda: pending.put(read_line()), daemon=True)
reader.start()
try:
    pending.get(timeout=0.05)
except queue.Empty:
    pass
else:
    print(json.dumps({"id": 1, "result": {}}), flush=True)
    sys.exit(0)
print(json.dumps({"id": 1, "result": {"userAgent": "test"}}), flush=True)
pending.get(timeout=10)
reader.join()
if os.environ.get("FAKE_CODEX_MODE") == "closed":
    sys.exit(0)
if os.environ.get("FAKE_CODEX_MODE") == "error":
    print(json.dumps({"id": 2, "error": {"code": -32601, "message": "method not found"}}), flush=True)
    sys.exit(0)
if os.environ.get("FAKE_CODEX_MODE") == "expired":
    error = {"code": -32603, "message": "Provided authentication token is expired: token_expired fixture-secret"}
    print(json.dumps({"id": 2, "error": error}), flush=True)
    sys.exit(0)
if os.environ.get("FAKE_CODEX_MODE") == "empty":
    print(json.dumps({"id": 2, "result": {}}), flush=True)
    sys.exit(0)
if os.environ.get("FAKE_CODEX_MODE") == "ok":
    limits = {"primary": {"usedPercent": 42, "windowDurationMins": 10080, "resetsAt": 200}}
    print(json.dumps({"id": 2, "result": {"rateLimits": limits}}), flush=True)
# Stay up like the real server, until stdin closes or it is killed.
sys.stdin.read()
"""


def _pid_alive(pid):
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


class CodexRateLimitsTest(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.bin, True)
        server = os.path.join(self.bin, "fake_codex.py")
        with open(server, "w", encoding="utf-8") as fh:
            fh.write(FAKE_SERVER)
        if os.name == "nt":
            with open(os.path.join(self.bin, "codex.cmd"), "w", encoding="utf-8") as fh:
                fh.write(f'@echo off\r\n"{sys.executable}" "{server}" %*\r\n')
        else:
            shim = os.path.join(self.bin, "codex")
            with open(shim, "w", encoding="utf-8") as fh:
                fh.write(f'#!/bin/sh\nexec "{sys.executable}" "{server}" "$@"\n')
            os.chmod(shim, os.stat(shim).st_mode | stat.S_IEXEC)
        self.pid_file = os.path.join(self.bin, "pid")

    def call(self, mode):
        env = {"PATH": self.bin + os.pathsep + os.environ.get("PATH", ""), "FAKE_CODEX_MODE": mode, "FAKE_CODEX_PID": self.pid_file}
        with mock.patch.dict(os.environ, env):
            return get_codex_rate_limits()

    def test_reads_the_rate_limits(self):
        result = self.call("ok")
        self.assertEqual(result["rateLimits"]["primary"]["usedPercent"], 42)
        self.assertEqual(result["rateLimits"]["primary"]["windowDurationMins"], 10080)

    def test_a_server_that_never_answers_gives_up(self):
        started = time.monotonic()
        result = self.call("silent")
        self.assertEqual(result.pop("_codexSource"), "unavailable")
        self.assertIsNone(result.pop("_codexAge"))
        self.assertIn("rate-limit", result["_codexError"])
        self.assertLess(time.monotonic() - started, 15)

    def test_no_codex_on_path(self):
        with mock.patch.dict(os.environ, {"PATH": tempfile.gettempdir()}):
            result = get_codex_rate_limits()
        self.assertEqual(result.pop("_codexSource"), "unavailable")
        self.assertIsNone(result.pop("_codexAge"))
        self.assertEqual(result, {})

    def test_expired_token_error_is_safe_and_not_discarded(self):
        result = self.call("expired")
        self.assertEqual(result["_codexError"], "token expired")
        self.assertNotIn("fixture-secret", json.dumps(result))

    def test_a_server_that_exits_without_limits_returns_empty(self):
        result = self.call("closed")
        self.assertEqual(result.pop("_codexSource"), "unavailable")
        self.assertIsNone(result.pop("_codexAge"))
        self.assertEqual(result, {})

    @unittest.skipUnless(shutil.which("true") or os.name == "nt", "needs a shell")
    def test_the_server_does_not_outlive_the_call(self):
        # On Windows the child is cmd.exe and the server its grandchild; only a
        # tree kill takes both.
        try:
            import psutil  # noqa: F401
        except ImportError:
            self.skipTest("psutil not installed")
        self.call("ok")
        with open(self.pid_file, encoding="utf-8") as fh:
            pid = int(fh.read())
        deadline = time.monotonic() + 5
        while _pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(_pid_alive(pid), "the fake server was left running")


class CodexRateLimitsCacheTest(unittest.TestCase):
    """The positive TTL cache: a repeated refresh inside the TTL must not
    launch `codex app-server` again, and the cache must never cross accounts."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache_patch = mock.patch("tidesi.providers.codex_rate_limits.config.cache_dir", return_value=self.directory.name)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.bin = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.bin, True)
        server = os.path.join(self.bin, "fake_codex.py")
        with open(server, "w", encoding="utf-8") as fh:
            fh.write(FAKE_SERVER)
        if os.name == "nt":
            with open(os.path.join(self.bin, "codex.cmd"), "w", encoding="utf-8") as fh:
                fh.write(f'@echo off\r\n"{sys.executable}" "{server}" %*\r\n')
        else:
            shim = os.path.join(self.bin, "codex")
            with open(shim, "w", encoding="utf-8") as fh:
                fh.write(f'#!/bin/sh\nexec "{sys.executable}" "{server}" "$@"\n')
            os.chmod(shim, os.stat(shim).st_mode | stat.S_IEXEC)
        self.launch_file = os.path.join(self.bin, "launches")
        self.env = {
            "PATH": self.bin + os.pathsep + os.environ.get("PATH", ""),
            "FAKE_CODEX_MODE": "ok",
            "FAKE_CODEX_LAUNCHES": self.launch_file,
        }

    def call(self, token, home, now):
        with mock.patch.dict(os.environ, self.env):
            return get_codex_rate_limits(token, home, now)

    def launches(self):
        try:
            with open(self.launch_file, encoding="utf-8") as fh:
                return len(fh.read().split())
        except OSError:
            return 0

    def test_cold_then_warm_launches_app_server_once(self):
        result = self.call("token-a", "/tmp/codex", 100)
        self.assertEqual(result.pop("_codexSource"), "live")
        self.assertEqual(result.pop("_codexAge"), 0)
        self.assertEqual(result["rateLimits"]["primary"]["usedPercent"], 42)
        self.assertEqual(self.launches(), 1)

        result = self.call("token-a", "/tmp/codex", 150)
        self.assertEqual(result.pop("_codexSource"), "cached")
        self.assertEqual(result.pop("_codexAge"), 50)
        self.assertEqual(result["rateLimits"]["primary"]["usedPercent"], 42)
        self.assertEqual(self.launches(), 1)

    def test_expired_cache_launches_again(self):
        self.call("token-a", "/tmp/codex", 100)
        self.assertEqual(self.launches(), 1)
        result = self.call("token-a", "/tmp/codex", 100 + TTL_SECONDS + 1)
        self.assertEqual(result.pop("_codexSource"), "live")
        self.assertEqual(self.launches(), 2)

    def test_clock_rollback_does_not_reuse_cache(self):
        self.call("token-a", "/tmp/codex", 100)
        result = self.call("token-a", "/tmp/codex", 99)
        self.assertEqual(result.pop("_codexSource"), "live")
        self.assertEqual(self.launches(), 2)

    def test_changed_identity_does_not_reuse_cache(self):
        self.call("token-a", "/tmp/codex", 100)
        self.assertEqual(self.launches(), 1)
        result = self.call("token-b", "/tmp/codex", 101)
        self.assertEqual(result.pop("_codexSource"), "live")
        self.assertEqual(self.launches(), 2)
        result = self.call("token-a", "/tmp/other", 102)
        self.assertEqual(result.pop("_codexSource"), "live")
        self.assertEqual(self.launches(), 3)

    def test_rpc_protocol_error_is_not_cached(self):
        with mock.patch.dict(os.environ, {**self.env, "FAKE_CODEX_MODE": "error"}):
            result = get_codex_rate_limits("token-a", "/tmp/codex", 100)
        self.assertEqual(result.pop("_codexSource"), "unavailable")
        self.assertEqual(self.launches(), 1)
        result = self.call("token-a", "/tmp/codex", 101)
        self.assertEqual(result.pop("_codexSource"), "live")
        self.assertEqual(self.launches(), 2)

    def test_startup_timeout_is_not_cached(self):
        with mock.patch.dict(os.environ, {**self.env, "FAKE_CODEX_MODE": "silent"}):
            result = get_codex_rate_limits("token-a", "/tmp/codex", 100)
        self.assertEqual(result.pop("_codexSource"), "unavailable")
        self.assertEqual(self.launches(), 1)
        result = self.call("token-a", "/tmp/codex", 101)
        self.assertEqual(result.pop("_codexSource"), "live")
        self.assertEqual(self.launches(), 2)

    def test_explicit_no_limits_is_cached_and_served(self):
        with mock.patch.dict(os.environ, {**self.env, "FAKE_CODEX_MODE": "empty"}):
            result = get_codex_rate_limits("token-a", "/tmp/codex", 100)
        self.assertEqual(result.pop("_codexSource"), "live")
        self.assertEqual(result.pop("_codexAge"), 0)
        self.assertEqual(result.pop("_codexNoLimits"), True)
        self.assertEqual(result, {})
        self.assertEqual(self.launches(), 1)
        result = self.call("token-a", "/tmp/codex", 150)
        self.assertEqual(result.pop("_codexSource"), "cached")
        self.assertEqual(result.pop("_codexAge"), 50)
        self.assertEqual(result.pop("_codexNoLimits"), True)
        self.assertEqual(result, {})
        self.assertEqual(self.launches(), 1)

    def test_cache_has_only_safe_metadata_and_hashed_identity(self):
        self.call("secret.jwt.value", "/tmp/codex", 100)
        names = os.listdir(self.directory.name)
        self.assertEqual(len(names), 1)
        with open(os.path.join(self.directory.name, names[0]), encoding="utf-8") as fh:
            content = fh.read()
        self.assertNotIn("secret", content)
        self.assertNotIn("/tmp/codex", content)
        payload = json.loads(content)
        self.assertEqual(set(payload["data"]), {"rateLimits"})
        self.assertEqual(payload["identity"], identity_key("secret.jwt.value", "/tmp/codex"))


class CodexCollectFallbackTest(IsolatedHomeTest):
    """collect_openai's HTTP fallback must still run when the app-server path
    produced nothing, and a failed fallback must keep its error provenance."""

    def setUp(self):
        super().setUp()
        self.creds = {"codexAccessToken": "token-a", "accountId": "acct-1"}
        self.patches = [
            mock.patch.object(collect, "get_openai_credentials", return_value=self.creds),
            mock.patch.object(collect, "get_codex_stats", return_value={}),
            mock.patch.object(collect, "provider_status", return_value=None),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_fallback_success_when_app_server_empty(self):
        with (
            mock.patch.object(collect, "get_codex_rate_limits", return_value={"_codexSource": "unavailable", "_codexAge": None}),
            mock.patch.object(collect, "fetch_json", return_value=HttpResult(200, '{"rateLimits": {"primary": {"usedPercent": 7}}}')) as fetch,
        ):
            raw = collect.collect_openai(1_800_000_000)
        self.assertEqual(raw["inputs"]["codex"]["rateLimits"]["primary"]["usedPercent"], 7)
        self.assertEqual(raw["inputs"]["codexError"], "")
        self.assertNotIn("codexSource", raw["inputs"])
        fetch.assert_called_once()

    def test_fallback_success_is_cached_for_the_next_refresh(self):
        with mock.patch.object(collect, "fetch_json", return_value=HttpResult(200, '{"rateLimits": {"primary": {"usedPercent": 7}}}')):
            with mock.patch.object(codex_rate_limits, "_live_codex_rate_limits", return_value={}):
                collect.collect_openai(1_800_000_000)
        with mock.patch.object(codex_rate_limits, "_live_codex_rate_limits") as live, mock.patch.object(collect, "fetch_json") as fetch:
            raw = collect.collect_openai(1_800_000_000)
        live.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(raw["inputs"]["codex"]["rateLimits"]["primary"]["usedPercent"], 7)

    def test_fallback_failure_keeps_error_and_serves_no_stale_limits(self):
        with (
            mock.patch.object(collect, "get_codex_rate_limits", return_value={"_codexSource": "unavailable", "_codexAge": None}),
            mock.patch.object(collect, "fetch_json", return_value=HttpResult(401, "unauthorized")),
        ):
            raw = collect.collect_openai(1_800_000_000)
        self.assertEqual(raw["inputs"]["codex"], {})
        self.assertNotEqual(raw["inputs"]["codexError"], "")

    def test_explicit_no_limits_skips_the_http_fallback(self):
        with (
            mock.patch.object(collect, "get_codex_rate_limits", return_value={"_codexSource": "live", "_codexAge": 0, "_codexNoLimits": True}),
            mock.patch.object(collect, "fetch_json") as fetch,
        ):
            raw = collect.collect_openai(1_800_000_000)
        fetch.assert_not_called()
        self.assertEqual(raw["inputs"]["codex"], {})

    def test_expired_rpc_auth_is_not_masked_by_a_second_http_failure(self):
        with (
            mock.patch.object(collect, "get_codex_rate_limits", return_value={"_codexError": "token expired"}),
            mock.patch.object(collect, "fetch_json") as fetch,
        ):
            raw = collect.collect_openai(1_800_000_000)
        fetch.assert_not_called()
        self.assertEqual(raw["inputs"]["codexError"], "token expired")
        self.assertEqual(raw["inputs"]["codex"], {})


if __name__ == "__main__":
    unittest.main()
