"""Shared by the unittest suites here: puts the backend package on sys.path.

These are plain unittest suites so the backend contract runs on Windows as
well as Linux.  Shell tests are reserved for the thin shell launchers.
"""

import json
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOLS = os.path.join(REPO, "backend")

if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

FIXTURES = Path(REPO) / "tests" / "fixtures"


@dataclass(frozen=True, slots=True)
class _SeedSource:
    source_id: str
    mtime_ns: int
    size: int


def seed_session_index(directory, rows):
    """Reconcile a fresh session index under ``directory`` with ``rows``.

    Returns the cache path. The rows are redacted exactly as a real collector
    run would redact them, so the materialized contribution table is built
    from the same data the envelope reads.
    """
    from tidesi.session_index import SessionIndex

    cache = Path(directory) / "sessions.sqlite3"
    SessionIndex(cache).reconcile([_SeedSource("test", 1, 1)], lambda _source: rows)
    return cache


def raw_fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


def provider_fixtures():
    # HTTP response fixtures belong to collector tests, not normalization.
    return [p.stem for p in sorted(FIXTURES.glob("*.json")) if p.stem != "muse-quota" and not p.stem.endswith("-response")]


def fixture(name):
    from tidesi.contract import finalize
    from tidesi.normalize import normalize

    raw = raw_fixture(name)
    if not raw.get("id"):
        raise ValueError(f"{name} is not a provider envelope")
    return finalize(normalize(raw))


class IsolatedHomeTest(unittest.TestCase):
    """No developer credentials, native app directories, cache, or network."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATH") if key in os.environ}
        env.update(
            {
                "HOME": str(self.home),
                "USERPROFILE": str(self.home),
                "XDG_CONFIG_HOME": str(self.home / ".config"),
                "XDG_DATA_HOME": str(self.home / ".local/share"),
                "XDG_CACHE_HOME": str(self.home / ".cache"),
                "APPDATA": str(self.home / "AppData/Roaming"),
                "LOCALAPPDATA": str(self.home / "AppData/Local"),
                "TIDESI_CONFIG": str(self.home / "config.json"),
                "TIDESI_CACHE_DIR": str(self.home / "cache"),
                "LANGUAGE": "",
                "LC_ALL": "C.UTF-8",
            }
        )
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch("tidesi.config.cache_dir", return_value=str(self.home / "cache"))
        patch.start()
        self.addCleanup(patch.stop)
        patch = mock.patch("socket.create_connection", side_effect=AssertionError("unexpected network request"))
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, relative, value):
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
        return path


def env_without_xdg(**extra):
    """A copy of the environment with every XDG_* variable dropped, plus `extra`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(extra)
    return env
