"""Claude Code sessions that have Remote Control (``/remote-control``, ``/rc``) on.

Each running Claude Code writes ``<config dir>/sessions/<pid>.json`` (``~/.claude`` unless ``CLAUDE_CONFIG_DIR`` is set). One that has
been opened to other devices carries a ``bridgeSessionId``, which is also the
last part of its claude.ai/code address. Only the sessions' own state is read;
no conversation text is.
"""

import json
import os

from ..pricing_lock import _is_process_alive
from .claude_credentials import claude_config_dir

URL = "https://claude.ai/code"


def _sessions_dir():
    return os.path.join(claude_config_dir(), "sessions")


def remote_sessions(directory=None):
    """The live Remote Control sessions, newest first: ``{name, folder, status, url}``."""
    found = []
    try:
        entries = list(os.scandir(directory or _sessions_dir()))
    except OSError:
        return found
    for entry in entries:
        if not entry.name.endswith(".json"):
            continue
        try:
            if not entry.is_file():
                continue
            with open(entry.path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        bridge = data.get("bridgeSessionId") if isinstance(data, dict) else None
        pid = data.get("pid") if isinstance(data, dict) else None
        if not isinstance(bridge, str) or not bridge.startswith("session_") or not isinstance(pid, int) or isinstance(pid, bool):
            continue
        if not _is_process_alive(pid):
            continue
        cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else ""
        found.append(
            {
                "name": str(data.get("name") or ""),
                "folder": os.path.basename(cwd.rstrip("/\\")),
                "status": str(data.get("status") or ""),
                "url": f"{URL}/{bridge}",
                "updatedAt": data.get("updatedAt") if isinstance(data.get("updatedAt"), (int, float)) else 0,
            }
        )
    found.sort(key=lambda row: row["updatedAt"], reverse=True)
    return found
