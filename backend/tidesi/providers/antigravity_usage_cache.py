"""Private cache for numeric Antigravity usage summaries."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from .. import config


def parser_fingerprint(files: tuple[Path, ...]) -> str:
    """Hash parser sources once per root scan to invalidate stale summaries."""
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _cache_path(database: Path) -> Path:
    identity = os.fsencode(os.path.realpath(database))
    key = hashlib.sha256(identity).hexdigest()
    return Path(config.cache_dir()) / f"antigravity-usage-{key}.json"


def signature(database: Path, parser: str) -> str | None:
    """Return a signature of parser version and DB/WAL filesystem state."""
    states = []
    for suffix in ("", "-wal"):
        try:
            stat = os.stat(str(database) + suffix)
            states.append((stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            states.append(None)
        except OSError:
            return None
    return hashlib.sha256(json.dumps((parser, states)).encode()).hexdigest()


def load(database: Path, fingerprint: str, state: str) -> tuple[int, tuple[tuple[str, str, int, int, int, int], ...]] | None:
    """Load numeric summaries only when source and parser signatures match."""
    try:
        payload = json.loads(_cache_path(database).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if payload.get("fingerprint") != fingerprint or payload.get("state") != state:
            return None
        activity = payload["last_activity"]
        usage = payload["usage"]
        if type(activity) is not int or not isinstance(usage, list):
            return None
        rows = []
        for row in usage:
            if (
                not isinstance(row, list)
                or len(row) != 6
                or not isinstance(row[0], str)
                or not isinstance(row[1], str)
                or any(type(value) is not int or value < 0 for value in row[2:])
            ):
                return None
            rows.append((row[0], row[1], *row[2:]))
        return activity, tuple(rows)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save(database: Path, fingerprint: str, state: str, activity: int, usage: tuple[tuple[str, str, int, int, int, int], ...]) -> None:
    """Atomically save aggregate counters without source or session identity."""
    path = _cache_path(database)
    temporary = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".antigravity-usage-", dir=str(path.parent))
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                {"fingerprint": fingerprint, "state": state, "last_activity": activity, "usage": usage},
                stream,
                separators=(",", ":"),
                allow_nan=False,
            )
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        return
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def prune(databases: list[Path]) -> None:
    """Delete summaries whose conversation database is gone, so one file per
    database does not pile up in the cache directory."""
    keep = {_cache_path(database).name for database in databases}
    try:
        stale = [path for path in Path(config.cache_dir()).glob("antigravity-usage-*.json") if path.name not in keep]
    except OSError:
        return
    for path in stale:
        try:
            path.unlink()
        except OSError:
            pass
