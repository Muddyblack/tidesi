"""Support for the E2E suites: run the real `tidesi` CLI in a throwaway HOME.

Isolation is by environment, not sandboxing. The child gets a scrubbed
environment pointing every config/data/cache directory into a temp dir, and
`TIDESI_E2E_HTTP_BASE` sends all upstream HTTP(S) traffic to a loopback server
that the test controls, so nothing reaches a real vendor service.
"""

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    headers: dict = field(repr=False)
    body: bytes = field(repr=False)

    def header(self, name):
        return self.headers.get(name.lower(), "")


@dataclass
class Response:
    body: object = ""
    status: int = 200
    content_type: str = "application/json"
    drop: bool = False  # close the connection without answering


class Server:
    """Loopback HTTP server. `routes` maps a path prefix to a Response or a JSON-able body.

    Upstream URLs arrive as /<scheme>/<host>/<path>, e.g. /https/api.deepseek.com/user/balance.
    Unrouted paths answer 404; every request is recorded in `requests`.
    """

    def __init__(self):
        self.routes = {}
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                owner.requests.append(Request(self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
                reply = owner.reply(self.path)
                if reply.drop:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                payload = reply.body if isinstance(reply.body, str) else json.dumps(reply.body)
                data = payload.encode("utf-8")
                self.send_response(reply.status)
                self.send_header("Content-Type", reply.content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _serve

            def log_message(self, *_args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def reply(self, path):
        for prefix, route in self.routes.items():
            if path.startswith(prefix):
                return route if isinstance(route, Response) else Response(route)
        return Response(status=404)

    def close(self):
        self._server.shutdown()
        self._server.server_close()


class E2ETest(unittest.TestCase):
    """A throwaway HOME, a loopback server and a PATH holding only fake CLIs and Python."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.server = Server()
        self.addCleanup(self.server.close)
        self.bin = self.home / "bin"
        self.bin.mkdir()

        home = str(self.home)
        system = ["/usr/bin", "/bin"] if os.name != "nt" else [os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")]
        self.env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT") if key in os.environ}
        self.env.update(
            HOME=home,
            USERPROFILE=home,
            APPDATA=home + "/AppData/Roaming",
            LOCALAPPDATA=home + "/AppData/Local",
            XDG_CONFIG_HOME=home + "/xdg-config",
            XDG_DATA_HOME=home + "/xdg-data",
            XDG_CACHE_HOME=home + "/xdg-cache",
            TIDESI_CONFIG=home + "/settings.json",
            TIDESI_CACHE_DIR=home + "/tidesi-cache",
            TIDESI_E2E_HTTP_BASE=self.server.url,
            PATH=os.pathsep.join([str(self.bin), str(Path(sys.executable).parent), *system]),
            PYTHON=sys.executable,
            PYTHONPATH=str(BACKEND),
            PYTHONUTF8="1",
            PYTHONDONTWRITEBYTECODE="1",
            LC_ALL="C.UTF-8",
            TEMP=home,
            TMP=home,
            TMPDIR=home,
        )
        # Without a fresh pricing table the backend would try to download one.
        self.write(
            "tidesi-cache/pricing-models-v2.json",
            {"version": 2, "source": "https://models.dev/api.json", "fetchedAt": time.time(), "checkedAt": time.time(), "providers": {}, "error": ""},
        )

    def write(self, relative, content):
        """Create a file under HOME; non-str content is written as JSON."""
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return path

    def database(self, relative, *statements):
        """Create a SQLite file under HOME; each statement is SQL or a (SQL, params) pair."""
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection, connection:
            for statement in statements:
                connection.execute(*([statement] if isinstance(statement, str) else statement))
        return path

    def fake_cli(self, name, python_body):
        """Put an executable called `name` on the child's PATH, implemented in Python."""
        if os.name == "nt":
            self.write(f"bin/{name}.py", python_body)
            self.write(f"bin/{name}.cmd", f'@echo off\n"%PYTHON%" "%~dp0{name}.py" %*\n')
        else:
            path = self.write(f"bin/{name}", "#!/usr/bin/env python3\n" + python_body)
            path.chmod(0o755)

    def cli(self, *args, **env):
        return subprocess.run(
            [sys.executable, "-m", "tidesi", *args],
            cwd=REPO,
            env={**self.env, **env},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    def provider(self, name, *, secrets=(), ok=True, **env):
        """Run one provider through the CLI and return its envelope row."""
        self.write("settings.json", {"providers": {name: True}, **env.pop("config", {})})
        result = self.cli("--provider", name, **env)
        for secret in secrets:
            self.assertNotIn(secret, result.stdout + result.stderr, "credential leaked into CLI output")
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["schemaVersion"], 1)
        self.assertEqual([row["id"] for row in envelope["providers"]], [name])
        row = envelope["providers"][0]
        self.assertEqual(row["ok"], ok, row["error"])
        return row

    def assert_not_persisted(self, secret):
        """The credential must not end up in any cache or data file the CLI wrote."""
        for name in ("xdg-cache", "xdg-data", "tidesi-cache"):
            for path in (self.home / name).rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes(), f"credential leaked into {path.name}")
