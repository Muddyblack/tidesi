"""Pieces shared by the private numeric usage caches (OpenCode, Antigravity).

Each cache keeps its own file format; these helpers decide when a cached
summary is still valid and write it so that only the owner can read it.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def source_fingerprint(*files) -> str:
    """Hash of the parser sources, so a code change invalidates old summaries."""
    digest = hashlib.sha256()
    for path in files:
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def database_state(database) -> list | None:
    """Identity, size and times of a SQLite database and its WAL, or None when
    either cannot be read (a missing WAL is fine)."""
    states = []
    for suffix in ("", "-wal"):
        try:
            stat = os.stat(str(database) + suffix)
        except FileNotFoundError:
            states.append(None)
            continue
        except OSError:
            return None
        states.append((stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
    return states


def write_private_json(path: Path, payload) -> None:
    """Atomically replace `path` with `payload`, readable by the owner only.
    A cache that cannot be written is skipped, never an error."""
    temporary = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix="." + path.stem + "-", dir=str(path.parent))
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
        os.replace(temporary, path)
        temporary = ""
    except (OSError, TypeError, ValueError):
        return
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass
