"""Read-only discovery of local Zed Agent threads for the Sessions view."""

from __future__ import annotations

import io
import json
import math
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .. import paths
from ..session_index import SkipSource
from ..sqlite_readonly import connect_readonly as _connect_readonly
from ..sqlite_readonly import table_columns

_REQUIRED_COLUMNS = {"id", "summary", "updated_at", "data_type", "data"}
_MAX_THREAD_DATA_BYTES = 64 * 1024 * 1024
_MAX_SCAN_SECONDS = 30.0
_MAX_ROWS = 60
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
_MAX_TOKEN_COUNT = (1 << 63) - 1


@dataclass(frozen=True)
class ZedSession:
    session_id: str
    title: str
    workspace: str
    last_activity: int
    model_provider: str = ""
    model_id: str = ""
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def discover_database_paths() -> list[str]:
    """Return candidate thread databases, honoring an explicit DB-file override."""
    override = os.environ.get("TIDESI_ZED_THREADS_DB", "").strip()
    if override:
        return [str(Path(override).expanduser().absolute())]

    if paths.IS_MACOS:
        root = os.path.expanduser(paths.APPLE_APP_SUPPORT)
        app_directory = "Zed"
    elif paths.IS_WINDOWS:
        root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        app_directory = "Zed"
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        app_directory = "zed"
    return [os.path.join(root, app_directory, "threads", "threads.db")]


def _text(value: object, *, limit: int = 4096) -> str:
    return value[:limit] if type(value) is str else ""


def _timestamp(value: object) -> int:
    if type(value) is int:
        if value <= 0:
            return 0
        seconds = value // 1000 if value >= 100_000_000_000 else value
        return min(seconds, _MAX_TOKEN_COUNT)
    if type(value) is float:
        if not math.isfinite(value) or value <= 0:
            return 0
        seconds = value / 1000 if value >= 100_000_000_000 else value
        return min(int(seconds), _MAX_TOKEN_COUNT)
    if not isinstance(value, str) or not value.strip():
        return 0
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00" if raw.endswith("Z") else raw)
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        seconds = parsed.timestamp()
    except (OverflowError, OSError, ValueError):
        return 0
    return max(0, int(seconds)) if math.isfinite(seconds) else 0


def _provider_id(value: object) -> str:
    if not isinstance(value, str):
        return ""
    provider = value.strip().lower()
    if len(provider) > 64 or not re.fullmatch(r"[a-z0-9._-]+", provider):
        return ""
    return provider


def _model_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return ""
    return value


def _token_count(value: object) -> int:
    if type(value) is int:
        return min(max(0, value), _MAX_TOKEN_COUNT)
    if type(value) is not float or not math.isfinite(value):
        return 0
    return min(max(0, int(value)), _MAX_TOKEN_COUNT)


def _usage_counts(value: object) -> tuple[int, int, int, int]:
    if not isinstance(value, dict):
        return (0, 0, 0, 0)
    return (
        _token_count(value.get("input_tokens")),
        _token_count(value.get("output_tokens")),
        _token_count(value.get("cache_read_input_tokens")),
        _token_count(value.get("cache_creation_input_tokens")),
    )


def _usage_totals(thread: dict) -> tuple[int, int, int, int]:
    cumulative = _usage_counts(thread.get("cumulative_token_usage"))
    if any(cumulative):
        return cumulative

    requests = thread.get("request_token_usage")
    values = requests.values() if isinstance(requests, dict) else requests if isinstance(requests, list) else ()
    totals = [0, 0, 0, 0]
    for request in values:
        for index, count in enumerate(_usage_counts(request)):
            totals[index] = min(totals[index] + count, _MAX_TOKEN_COUNT)
    return tuple(totals)


def _total_tokens(thread: dict) -> int:
    return min(sum(_usage_totals(thread)), _MAX_TOKEN_COUNT)


def _decode_thread(data_type: object, data: object) -> dict | None:
    if isinstance(data, memoryview):
        data = data.tobytes()
    if isinstance(data, str):
        raw = data.encode("utf-8")
    elif isinstance(data, bytes):
        raw = data
    else:
        return None

    kind = _text(data_type, limit=16)
    if kind == "zstd":
        try:
            import zstandard
        except ImportError:
            return None
        try:
            decompressor = zstandard.ZstdDecompressor(max_window_size=_MAX_THREAD_DATA_BYTES)
            with decompressor.stream_reader(io.BytesIO(raw)) as stream:
                raw = stream.read(_MAX_THREAD_DATA_BYTES + 1)
        except zstandard.ZstdError:
            return None
        if len(raw) > _MAX_THREAD_DATA_BYTES:
            return None
    elif kind != "json":
        return None

    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _workspace_basename(paths_value: object, order_value: object) -> str:
    serialized_paths = _text(paths_value, limit=32_768)
    if not serialized_paths:
        return ""
    entries = serialized_paths.split("\n")
    order = []
    if isinstance(order_value, str):
        for item in order_value.split(","):
            try:
                order.append(int(item))
            except ValueError:
                order.append(-1)
    if len(order) != len(entries) or any(index < 0 for index in order) or len(set(order)) != len(entries):
        order = list(range(len(entries)))
    ordered = [path for _, path in sorted(zip(order, entries), key=lambda item: item[0])]
    path = ordered[0].strip().replace("\\", "/").rstrip("/")
    basename = path.rsplit("/", 1)[-1]
    if basename in ("", ".", ".."):
        return ""
    return "".join(char for char in basename if char.isprintable() and char not in "/\\")[:100]


def _read_database(database_path: str, *, include_all: bool = False) -> list[ZedSession]:
    if not os.path.isfile(database_path):
        return []

    try:
        with _connect_readonly(database_path) as connection:
            columns = table_columns(connection, "threads")
            if not _REQUIRED_COLUMNS <= columns:
                raise SkipSource("Zed thread database has an unsupported schema")

            selected = ["id", "summary", "updated_at", "data_type", "data"]
            for optional in ("folder_paths", "folder_paths_order"):
                if optional in columns:
                    selected.append(optional)
            query = f"SELECT {', '.join(selected)} FROM threads"
            if "parent_id" in columns:
                query += " WHERE parent_id IS NULL OR parent_id = ''"
            query += " ORDER BY updated_at DESC"
            if not include_all:
                query += f" LIMIT {_MAX_ROWS}"

            deadline = time.monotonic() + _MAX_SCAN_SECONDS
            rows = []
            for row in connection.execute(query):
                if time.monotonic() >= deadline:
                    raise SkipSource("Zed thread scan exceeded its time limit")
                values = dict(zip(selected, row))
                session_id = _text(values.get("id"), limit=512)
                last_activity = _timestamp(values.get("updated_at"))
                if not session_id or not last_activity:
                    continue

                thread = _decode_thread(values.get("data_type"), values.get("data"))
                if thread is not None and thread.get("subagent_context") is not None:
                    continue
                model = thread.get("model") if isinstance(thread, dict) else None
                model = model if isinstance(model, dict) else {}
                usage = _usage_totals(thread) if thread is not None else (0, 0, 0, 0)
                rows.append(
                    ZedSession(
                        session_id=session_id,
                        title=_text(values.get("summary")),
                        workspace=_workspace_basename(values.get("folder_paths"), values.get("folder_paths_order")),
                        last_activity=last_activity,
                        model_provider=_provider_id(model.get("provider")),
                        model_id=_model_id(model.get("model")),
                        tokens=min(sum(usage), _MAX_TOKEN_COUNT),
                        input_tokens=usage[0],
                        output_tokens=usage[1],
                        cache_read_tokens=usage[2],
                        cache_write_tokens=usage[3],
                    )
                )
    except SkipSource:
        raise
    except (OSError, sqlite3.Error, ValueError) as error:
        raise SkipSource("Zed thread database could not be read") from error

    return rows


def read_sessions(*, include_all: bool = False) -> list[ZedSession]:
    """Read local root threads without modifying Zed's database."""
    selected: dict[str, ZedSession] = {}
    for database_path in discover_database_paths():
        for record in _read_database(database_path, include_all=include_all):
            previous = selected.get(record.session_id)
            if previous is None or record.last_activity > previous.last_activity:
                selected[record.session_id] = record
    records = sorted(selected.values(), key=lambda record: (-record.last_activity, record.session_id))
    return records if include_all else records[:_MAX_ROWS]
