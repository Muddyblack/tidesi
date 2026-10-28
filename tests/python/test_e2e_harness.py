"""Regression tests for E2E isolation, portability, and resource lifetime."""

import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _e2e_harness import ChildProcess, LoopbackHTTPServer, LoopbackResponse, install_fake_cli


class E2EHarnessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)

    def test_child_can_reach_loopback_server(self):
        with LoopbackHTTPServer(lambda _request: LoopbackResponse(body='{"ok":true}')) as server:
            child = ChildProcess(self.home, http_base=server.base_url)
            result = child.run(
                [
                    sys.executable,
                    "-c",
                    "import sys, urllib.request; print(urllib.request.urlopen(sys.argv[1]).read().decode())",
                    server.base_url + "/probe",
                ]
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '{"ok":true}')
        self.assertEqual(len(server.requests), 1)
        self.assertEqual((server.requests[0].method, server.requests[0].path), ("GET", "/probe"))
        self.assertEqual(server.active_workers, 0)

    def test_swallowed_network_denials_still_fail_the_parent_test(self):
        # These targets are numeric/local even if the guard regresses: no public
        # DNS lookup or external packet is needed to exercise the audit events.
        operations = [
            "socket.socket().connect(('0.0.0.0', 1))",
            "socket.socket().connect_ex(('0.0.0.0', 1))",
            "socket.getaddrinfo('192.0.2.1', 80)",
            "socket.gethostbyname('192.0.2.1')",
            "socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'test', ('0.0.0.0', 9))",
        ]
        if hasattr(socket.socket, "sendmsg"):
            operations.append("socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendmsg([b'test'], [], 0, ('0.0.0.0', 9))")
        child = ChildProcess(self.home)
        for operation in operations:
            with self.subTest(operation=operation):
                code = f"""import socket
try:
    {operation}
except Exception:
    pass
"""
                with self.assertRaisesRegex(AssertionError, "E2E isolation violation"):
                    child.run([sys.executable, "-c", code])

    def test_real_credential_store_subprocess_is_denied_even_when_caught(self):
        child = ChildProcess(self.home)
        code = """import subprocess
try:
    subprocess.run(['/usr/bin/security', 'find-generic-password', '-w'], capture_output=True)
except Exception:
    pass
"""
        with self.assertRaisesRegex(AssertionError, "E2E isolation violation"):
            child.run([sys.executable, "-c", code])

    def test_initial_native_command_is_rejected_before_launch(self):
        child = ChildProcess(self.home)
        with self.assertRaisesRegex(AssertionError, "untrusted initial executable"):
            child.run([str(self.home / "not-an-approved-native-program")])

    def test_python_startup_cannot_bypass_the_guard(self):
        child = ChildProcess(self.home)
        for flag in ("-S", "-I", "-E", "-IS", "-Wignore::probe.Warning", "-bWignore::probe.Warning", "-Xdev", "-uXdev"):
            with self.subTest(flag=flag):
                with self.assertRaisesRegex(AssertionError, "unsafe Python startup"):
                    child.run([sys.executable, flag, "-c", "pass"])

    def test_nested_python_startup_cannot_bypass_the_guard(self):
        child = ChildProcess(self.home)
        for flag in ("-S", "-Wignore::probe.Warning", "-bWignore::probe.Warning", "-Xdev", "-uXdev"):
            with self.subTest(flag=flag):
                code = f"""import subprocess, sys
try:
    subprocess.run([sys.executable, {flag!r}, '-c', 'pass'], capture_output=True)
except Exception:
    pass
"""
                with self.assertRaisesRegex(AssertionError, "unsafe Python startup"):
                    child.run([sys.executable, "-c", code])

    def test_native_environment_mutations_are_denied_even_when_caught(self):
        child = ChildProcess(self.home)
        for operation in ("os.putenv('PYTHONPATH', '')", "os.unsetenv('PYTHONPATH')"):
            with self.subTest(operation=operation):
                code = f"""import os
try:
    {operation}
except Exception:
    pass
"""
                with self.assertRaisesRegex(AssertionError, "guard environment mutation"):
                    child.run([sys.executable, "-c", code])

    def test_http_module_keeps_network_imports_lazy(self):
        child = ChildProcess(self.home)
        code = """import sys
assert 'urllib.request' not in sys.modules
from tidesi.http import route_e2e_url
url = 'https://example.invalid/probe'
assert route_e2e_url(url) == url
assert 'urllib.request' not in sys.modules
"""
        result = child.run([sys.executable, "-c", code])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bootstrap_failure_is_not_silently_ignored_by_python(self):
        child = ChildProcess(self.home)
        (self.home / ".e2e-python" / "_e2e_guard.py").unlink()
        with self.assertRaisesRegex(AssertionError, "guard did not initialize"):
            child.run([sys.executable, "-c", "print('unguarded')"])

    def test_bootstrap_environment_cannot_be_removed(self):
        child = ChildProcess(self.home)
        child.env["PYTHONPATH"] = ""
        with self.assertRaisesRegex(AssertionError, "bootstrap environment changed"):
            child.run([sys.executable, "-c", "pass"])

    def test_environment_does_not_inherit_credentials_or_proxies(self):
        with mock.patch.dict("os.environ", {"META_API_KEY": "not-a-real-secret", "HTTPS_PROXY": "http://example.invalid:1"}):
            child = ChildProcess(self.home)
        self.assertNotIn("META_API_KEY", child.env)
        self.assertNotIn("HTTPS_PROXY", child.env)

    def test_output_decoding_is_utf8_independent_of_parent_locale(self):
        child = ChildProcess(self.home)
        with mock.patch("locale.getpreferredencoding", return_value="ascii"):
            result = child.run([sys.executable, "-c", "print(chr(233))"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "é")

    def test_server_can_drop_a_connection(self):
        with LoopbackHTTPServer(lambda _request: LoopbackResponse(close_connection=True)) as server:
            child = ChildProcess(self.home)
            code = f"""import urllib.request
try:
    urllib.request.urlopen("{server.base_url}/offline", timeout=3)
except OSError:
    print("offline")
else:
    raise SystemExit(1)
"""
            result = child.run([sys.executable, "-c", code])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "offline")
        self.assertEqual(len(server.requests), 1)

    def test_server_closes_incomplete_request_workers(self):
        server = LoopbackHTTPServer()
        self.addCleanup(server.close)
        address = server._server.server_address
        with socket.create_connection(address, timeout=2) as connection:
            connection.sendall(b"POST /stalled HTTP/1.0\r\nContent-Length: 10000\r\n\r\npartial")
            deadline = time.monotonic() + 2
            while not server.active_workers and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreater(server.active_workers, 0)
            server.close()
            self.assertEqual(server.active_workers, 0)
            self.assertEqual(connection.recv(1), b"")

    def test_fake_cli_is_found_on_the_child_path(self):
        bin_dir = self.home / "bin"
        install_fake_cli(
            bin_dir,
            "fixture-codex",
            """import json, sys
print(json.dumps(sys.argv[1:]))
""",
        )
        child = ChildProcess(self.home, bin_dir=bin_dir)
        result = child.run(["fixture-codex", "app-server", "--json"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '["app-server", "--json"]')

    def test_fake_cli_name_cannot_be_a_directory(self):
        for name in (".", "..", "../escape"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                install_fake_cli(self.home / "bin", name, "pass")

    def test_server_records_headers_and_request_body_without_repr_leaks(self):
        body = b'{"hello":"world"}'
        with LoopbackHTTPServer() as server:
            child = ChildProcess(self.home)
            code = """import sys, urllib.request
request = urllib.request.Request(sys.argv[1], data=b'{"hello":"world"}', headers={'X-Test': 'yes'})
print(urllib.request.urlopen(request).status)
"""
            result = child.run([sys.executable, "-c", code, server.base_url + "/rpc"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "200")
        self.assertEqual(len(server.requests), 1)
        request = server.requests[0]
        self.assertEqual((request.method, request.path), ("POST", "/rpc"))
        self.assertEqual(request.header("x-test"), "yes")
        self.assertEqual(request.body, body)
        self.assertNotIn("hello", repr(request))

    def _background_child(self, wait_for_timeout):
        port_path = self.home / "background-port"
        grandchild = f"""import socket, time
from pathlib import Path
listener = socket.socket()
listener.bind(('127.0.0.1', 0))
listener.listen()
Path({str(port_path)!r}).write_text(str(listener.getsockname()[1]), encoding='ascii')
time.sleep(15)
"""
        code = f"""import subprocess, sys, time
from pathlib import Path
subprocess.Popen([sys.executable, '-c', {grandchild!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = time.monotonic() + 5
while not Path({str(port_path)!r}).exists():
    if time.monotonic() >= deadline:
        raise RuntimeError('background child did not start')
    time.sleep(0.01)
if {wait_for_timeout!r}:
    time.sleep(15)
"""
        child = ChildProcess(self.home)
        if wait_for_timeout:
            with self.assertRaises(subprocess.TimeoutExpired):
                child.run([sys.executable, "-c", code], timeout=3)
        else:
            result = child.run([sys.executable, "-c", code], timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(port_path.exists(), "background fixture must start before cleanup is tested")
        port = int(port_path.read_text(encoding="ascii"))
        # Termination requests are asynchronous; allow the descendant a bounded
        # interval to release its listener, and close every successful probe.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    pass
            except OSError:
                return
            time.sleep(0.01)
        self.fail("background descendant listener survived process cleanup")

    def test_timeout_terminates_descendant_processes(self):
        self._background_child(wait_for_timeout=True)

    def test_normal_exit_terminates_background_processes(self):
        self._background_child(wait_for_timeout=False)


if __name__ == "__main__":
    unittest.main()
