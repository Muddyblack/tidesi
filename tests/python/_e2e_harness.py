"""Hermetic fixtures for backend E2E tests, not an OS security sandbox.

Python audit hooks catch accidental network/credential-store access, including
access caught by application code. Native fake executables are trusted fixtures;
Qt/native extensions and deliberately escaping processes need separate isolation.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from _e2e_guard import _python_flags_safe
from _e2e_process import run_process

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict = field(repr=False)
    body: bytes = field(repr=False)

    def header(self, name):
        return self.headers.get(name.lower(), "")


@dataclass
class LoopbackResponse:
    status: int = 200
    body: object = "{}"
    headers: dict = field(default_factory=lambda: {"Content-Type": "application/json"})
    close_connection: bool = False


class LoopbackHTTPServer:
    """Record requests on IPv4 loopback; callbacks must finish in bounded time."""

    def __init__(self, response_for_request=None):
        self._requests = []
        self._connections = set()
        self._workers = []
        self._errors = []
        self._lock = threading.Lock()
        self._closed = False
        self._response_for_request = response_for_request or (lambda _request: LoopbackResponse())
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def handle(self):
                try:
                    super().handle()
                except OSError:
                    # A stalled or disconnected client must not retain a worker.
                    pass

            def _dispatch(self):
                try:
                    length = int(self.headers.get("Content-Length", "0") or "0")
                except ValueError:
                    length = -1
                if not 0 <= length <= 1024 * 1024:
                    self.send_error(400)
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    return
                request = RecordedRequest(
                    self.command,
                    self.path,
                    {key.lower(): value for key, value in self.headers.items()},
                    body,
                )
                with owner._lock:
                    owner._requests.append(request)
                response = owner._response_for_request(request)
                if response.close_connection:
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    return
                body = response.body.encode("utf-8") if isinstance(response.body, str) else bytes(response.body)
                self.send_response(response.status)
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            do_GET = _dispatch
            do_POST = _dispatch
            do_PUT = _dispatch
            do_PATCH = _dispatch
            do_DELETE = _dispatch

            def log_message(self, _format, *_args):
                pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            block_on_close = False
            allow_reuse_address = True

            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(2)
                with owner._lock:
                    owner._connections.add(connection)
                return connection, address

            def shutdown_request(self, request):
                try:
                    super().shutdown_request(request)
                finally:
                    with owner._lock:
                        owner._connections.discard(request)

            def process_request(self, request, client_address):
                worker = threading.Thread(
                    target=self.process_request_thread,
                    args=(request, client_address),
                    name="e2e-http-request",
                    daemon=True,
                )
                with owner._lock:
                    owner._workers.append(worker)
                worker.start()

            def handle_error(self, _request, _client_address):
                # Never dump a handler traceback containing credential fixtures.
                with owner._lock:
                    owner._errors.append("E2E HTTP handler failed")

        self._server = Server(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="e2e-loopback-http",
            daemon=True,
        )
        self._thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    @property
    def requests(self):
        with self._lock:
            return tuple(self._requests)

    @property
    def active_workers(self):
        with self._lock:
            return sum(worker.is_alive() for worker in self._workers)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._server.shutdown()
        with self._lock:
            connections = tuple(self._connections)
            workers = tuple(self._workers)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        self._server.server_close()
        deadline = time.monotonic() + 3
        for worker in (self._thread, *workers):
            worker.join(timeout=max(0, deadline - time.monotonic()))
        if self._thread.is_alive() or self.active_workers:
            raise RuntimeError("E2E HTTP server workers did not stop")
        if self._errors:
            raise RuntimeError(self._errors[0])

    def __enter__(self):
        return self

    def __exit__(self, _type, _value, _traceback):
        self.close()


class ChildProcess:
    """Run Python and trusted fixture scripts with isolated state and I/O guards."""

    def __init__(self, home, http_base=None, bin_dir=None, extra_env=None):
        self.home = Path(home).resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.bin_dir = Path(bin_dir).resolve() if bin_dir is not None else None
        guard_dir = self.home / ".e2e-python"
        guard_dir.mkdir(parents=True, exist_ok=True)
        self._violation_path = guard_dir / "violations.log"
        self._ready_path = guard_dir / "ready"
        shutil.copyfile(Path(__file__).with_name("_e2e_guard.py"), guard_dir / "_e2e_guard.py")
        bootstrap = f"""import os
try:
    from _e2e_guard import install
    install({str(self._violation_path)!r}, {str(self.bin_dir) if self.bin_dir is not None else None!r})
    with open({str(self._ready_path)!r}, "a", encoding="ascii") as ready:
        ready.write("ready")
except BaseException:
    os._exit(97)
"""
        (guard_dir / "sitecustomize.py").write_text(bootstrap, encoding="utf-8")

        # Keep a venv's bin directory, rather than resolving its Python symlink.
        path_entries = [str(self.bin_dir)] if self.bin_dir is not None else []
        path_entries.append(str(Path(sys.executable).parent))
        if os.name == "nt":
            system_root = os.environ.get("SystemRoot", r"C:\Windows")
            path_entries.extend((str(Path(system_root) / "System32"), system_root))
        else:
            path_entries.extend(("/usr/bin", "/bin", "/usr/sbin", "/sbin"))

        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT") if key in os.environ}
        env.update(
            {
                "HOME": str(self.home),
                "USERPROFILE": str(self.home),
                "APPDATA": str(self.home / "AppData" / "Roaming"),
                "LOCALAPPDATA": str(self.home / "AppData" / "Local"),
                "XDG_CONFIG_HOME": str(self.home / "xdg-config"),
                "XDG_DATA_HOME": str(self.home / "xdg-data"),
                "XDG_CACHE_HOME": str(self.home / "xdg-cache"),
                "TIDESI_CONFIG": str(self.home / "settings.json"),
                "TIDESI_CACHE_DIR": str(self.home / "tidesi-cache"),
                "LANGUAGE": "",
                "LC_ALL": "C.UTF-8",
                "PYTHONPATH": os.pathsep.join((str(guard_dir), str(BACKEND))),
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUTF8": "1",
                "PATH": os.pathsep.join(dict.fromkeys(path_entries)),
                "TEMP": str(self.home),
                "TMP": str(self.home),
                "TMPDIR": str(self.home),
                "PYTHON": sys.executable,
            }
        )
        if http_base is not None:
            env["TIDESI_E2E_HTTP_BASE"] = http_base
        if extra_env:
            if any(key.startswith(("PYTHON", "TIDESI_E2E_", "LD_", "DYLD_")) for key in extra_env):
                raise ValueError("extra_env cannot override the E2E bootstrap")
            env.update(extra_env)
        self.env = env
        self._pythonpath = env["PYTHONPATH"]

    def _verify_guard(self):
        if self._violation_path.exists():
            raise AssertionError("E2E isolation violation: " + self._violation_path.read_text(encoding="ascii").strip())
        if not self._ready_path.exists():
            raise AssertionError("E2E child guard did not initialize")

    def run(self, args, timeout=30, cwd=REPO):
        args = [os.fspath(arg) for arg in args]
        if self.env.get("PYTHONPATH") != self._pythonpath or self.env.get("PYTHONNOUSERSITE") != "1":
            raise AssertionError("E2E bootstrap environment changed")
        command = args[0]
        if os.path.dirname(command):
            executable = Path(cwd, command).absolute()
        else:
            resolved = shutil.which(command, path=self.env["PATH"])
            if resolved is None:
                raise FileNotFoundError(command)
            executable = Path(resolved).absolute()
        is_python = executable.resolve() == Path(sys.executable).resolve()
        is_fake = self.bin_dir is not None and self.bin_dir in executable.resolve().parents
        if not is_python and not is_fake:
            raise AssertionError("E2E isolation violation: untrusted initial executable")
        if is_python and not _python_flags_safe(args):
            raise AssertionError("E2E isolation violation: unsafe Python startup options")
        args[0] = str(executable)
        self._violation_path.unlink(missing_ok=True)
        self._ready_path.unlink(missing_ok=True)
        try:
            result = run_process(args, cwd=str(cwd), env=self.env, timeout=timeout)
        except subprocess.TimeoutExpired:
            self._verify_guard()
            raise
        self._verify_guard()
        return result


def install_fake_cli(bin_dir, name, python_body):
    """Install a trusted Python fixture, without changing the parent's PATH."""
    if name in (".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("fake CLI name must be a simple executable name")
    directory = Path(bin_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        script = directory / (name + ".py")
        script.write_text(python_body, encoding="utf-8")
        wrapper = directory / (name + ".cmd")
        wrapper.write_text(f'@echo off\n"%PYTHON%" "%~dp0{name}.py" %*\n', encoding="utf-8")
        return wrapper

    executable = directory / name
    executable.write_text("#!/usr/bin/env python3\n" + python_body, encoding="utf-8")
    executable.chmod(0o755)
    return executable


def scrub(text, *secrets):
    """Redact seeded values before including child output in a test failure."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text
