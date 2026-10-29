"""Read Antigravity CLI transcripts and editor artifacts without writing them."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from ..billing import UsageBucket
from ..contract import epoch_of
from . import antigravity_usage

_MAX_SESSIONS = 60
_MAX_TRANSCRIPT_LINES = 20_000
_MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024
_MAX_LINE_BYTES = 256 * 1024
_MAX_TITLE_CHARS = 2_000
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class AntigravitySession:
    session_id: str
    title: str
    last_activity: int
    source_path: str
    usage: tuple[UsageBucket, ...] = ()
    usage_complete: bool = True


def _home() -> Path:
    return Path(os.environ.get("ANTIGRAVITY_HOME") or os.environ.get("GEMINI_HOME") or os.path.expanduser("~/.gemini"))


def _valid_id(value: str) -> bool:
    return bool(_UUID.fullmatch(value))


def _mtime(path: Path) -> int:
    try:
        return int(path.stat().st_mtime)
    except OSError:
        return 0


def _session_dirs(root: Path) -> list[tuple[str, Path]]:
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    out = []
    for entry in entries:
        try:
            is_directory = entry.is_dir()
        except OSError:
            continue
        if is_directory and _valid_id(entry.name):
            out.append((entry.name, entry))
    return out


def _user_title(content: str) -> str:
    match = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.DOTALL)
    title = match.group(1) if match else re.split(r"<(?:ADDITIONAL_METADATA|USER_SETTINGS_CHANGE)>", content, maxsplit=1)[0]
    return " ".join(title.split())[:_MAX_TITLE_CHARS].strip()


def _read_cli(session_id: str, transcript: Path) -> AntigravitySession | None:
    latest = _mtime(transcript)
    title = ""
    recognized = False
    try:
        with transcript.open("rb") as stream:
            remaining = _MAX_TRANSCRIPT_BYTES
            for _ in range(_MAX_TRANSCRIPT_LINES):
                if remaining <= 0:
                    break
                limit = min(_MAX_LINE_BYTES, remaining)
                line = stream.readline(limit + 1)
                # Stop at an oversized record rather than parsing a partial
                # record or spending unbounded time draining its remainder.
                if not line or len(line) > limit:
                    break
                remaining -= len(line)
                try:
                    row = json.loads(line.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if not isinstance(row, dict) or not isinstance(row.get("step_index"), int):
                    continue
                row_type = row.get("type")
                if not isinstance(row_type, str):
                    continue
                recognized = True
                latest = max(latest, epoch_of(row.get("created_at")))
                if not title and row_type == "USER_INPUT" and isinstance(row.get("content"), str):
                    title = _user_title(row["content"])
    except OSError:
        return None
    if not recognized or latest <= 0:
        return None
    return AntigravitySession(session_id, title or "Antigravity", latest, str(transcript))


def _markdown_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title[:_MAX_TITLE_CHARS]
    return fallback.replace("_", " ").replace("-", " ").strip().title() or "Antigravity"


def _read_editor(session_id: str, conversation: Path) -> AntigravitySession | None:
    try:
        artifacts = sorted(conversation.glob("*.md"), key=_mtime, reverse=True)
    except OSError:
        return None
    if not artifacts:
        return None
    artifact = artifacts[0]
    try:
        with artifact.open(encoding="utf-8", errors="replace") as stream:
            text = stream.read(_MAX_TITLE_CHARS)
    except OSError:
        return None
    activity = _mtime(artifact)
    if activity <= 0:
        return None
    return AntigravitySession(session_id, _markdown_title(text, artifact.stem), activity, str(artifact))


def _discover(*, include_all: bool = False) -> list[AntigravitySession]:
    root = _home()
    selected: dict[str, AntigravitySession] = {}
    # The IDE's newer store (antigravity-ide) keeps the same transcript layout as the CLI.
    for client in ("antigravity-cli", "antigravity-ide"):
        for session_id, conversation in _session_dirs(root / client / "brain"):
            record = _read_cli(session_id, conversation / ".system_generated" / "logs" / "transcript.jsonl")
            if record is not None:
                selected[session_id] = record
    editor_root = root / "antigravity" / "brain"
    for session_id, conversation in _session_dirs(editor_root):
        record = _read_editor(session_id, conversation)
        previous = selected.get(session_id)
        if record is not None and (previous is None or record.last_activity > previous.last_activity):
            selected[session_id] = record
    for native in antigravity_usage.read_usage(root):
        previous = selected.get(native.session_id)
        if previous is None:
            selected[native.session_id] = AntigravitySession(
                native.session_id, "Antigravity", native.last_activity, native.source_path, native.usage, native.usage_complete
            )
        else:
            selected[native.session_id] = replace(
                previous, last_activity=max(previous.last_activity, native.last_activity), usage=native.usage, usage_complete=native.usage_complete
            )
    records = sorted(selected.values(), key=lambda record: (-record.last_activity, record.session_id))
    return records if include_all else records[:_MAX_SESSIONS]


def read_recent_sessions(*, include_all: bool = False) -> list[AntigravitySession]:
    """Return the bounded, newest-first Antigravity session metadata."""
    return _discover(include_all=include_all)


def read_session_targets() -> list[AntigravitySession]:
    """Return the same records used to resolve an opaque resume key."""
    return _discover(include_all=True)
