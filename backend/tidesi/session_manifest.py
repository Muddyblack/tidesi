"""Metadata-only discovery for the local session collectors."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from . import paths
from .providers import cursor_sessions, junie, mimo, mistral_sessions, opencode  # noqa: F401
from .providers.grok import grok_home
from .providers.muse import sessions_root as muse_sessions_root
from .providers.openai_credentials import codex_home


@dataclass(frozen=True)
class SessionManifest:
    source_id: str
    mtime_ns: int
    size: int


def _stat_record(path: str, kind: str) -> tuple[str, str, int, int, int]:
    normalized = os.path.abspath(os.path.normpath(path))
    try:
        stat = os.stat(normalized)
    except OSError:
        return kind, normalized, 0, 0, 0
    return kind, normalized, 1, stat.st_mtime_ns, stat.st_size


def _directory_records(root: str, excluded: set[str] | None = None) -> list[tuple[str, str, int, int, int]]:
    records = [_stat_record(root, "directory")]
    for dirpath, dirnames, _filenames in os.walk(root):
        if excluded:
            dirnames[:] = [name for name in dirnames if name not in excluded]
        dirnames.sort()
        records.extend(_stat_record(os.path.join(dirpath, name), "directory") for name in dirnames)
    return records


def _matching_files(root: str, predicate, *, excluded: set[str] | None = None) -> list[tuple[str, str, int, int, int]]:
    directories_by_parent: dict[str, list[tuple[str, str, int, int, int]]] = {}
    files: list[tuple[str, str, int, int, int]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if excluded:
            dirnames[:] = [name for name in dirnames if name not in excluded]
        directories_by_parent[dirpath] = [_stat_record(os.path.join(dirpath, name), "directory") for name in sorted(dirnames)]
        files.extend(_stat_record(os.path.join(dirpath, name), "file") for name in sorted(filenames) if predicate(name))
    directories = [_stat_record(root, "directory")]
    pending = [root]
    while pending:
        parent = pending.pop()
        children = directories_by_parent.get(parent, [])
        directories.extend(children)
        pending.extend(record[1] for record in reversed(children))
    return directories + files


def _cline_records(root: str) -> list[tuple[str, str, int, int, int]]:
    records = [_stat_record(root, "directory")]
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return records
    for name in names:
        directory = os.path.join(root, name)
        if not os.path.isdir(directory):
            continue
        records.append(_stat_record(directory, "directory"))
        records.append(_stat_record(os.path.join(directory, name + ".json"), "file"))
    return records


def _claude_records(root: str) -> list[tuple[str, str, int, int, int]]:
    projects = os.path.join(root, "projects")
    records = [_stat_record(root, "directory"), _stat_record(os.path.join(root, "history.jsonl"), "file")]
    records.append(_stat_record(projects, "directory"))
    try:
        project_names = sorted(os.listdir(projects))
    except OSError:
        return records
    for name in project_names:
        project = os.path.join(projects, name)
        if not os.path.isdir(project):
            continue
        records.append(_stat_record(project, "directory"))
        try:
            entries = sorted(os.listdir(project))
        except OSError:
            continue
        records.extend(
            _stat_record(os.path.join(project, entry), "file")
            for entry in entries
            if entry.endswith(".jsonl") and os.path.isfile(os.path.join(project, entry))
        )
    return records


def _opencode_records() -> list[tuple[str, str, int, int, int]]:
    records: list[tuple[str, str, int, int, int]] = []
    explicit = os.environ.get("OPENCODE_DB", "").strip()
    if explicit:
        resolved = opencode.explicit_database_path(explicit)
        if resolved:
            records.extend(_stat_record(resolved + suffix, "file") for suffix in ("", "-wal"))
    for base in paths.data_home_dirs():
        for directory in (base, os.path.join(base, "opencode")):
            records.append(_stat_record(directory, "directory"))
            try:
                entries = sorted(os.listdir(directory))
            except OSError:
                continue
            records.extend(
                _stat_record(os.path.join(directory, name) + suffix, "file")
                for name in entries
                if name.startswith("opencode") and name.endswith(".db")
                for suffix in ("", "-wal")
            )
    return records


def _mimo_records():
    # Parser revision invalidates totals previously attributed to imported history.
    records = [("parser", "mimo-native-messages-v2", 1, 0, 0)]
    records += [_stat_record(os.path.join(base, "mimocode"), "directory") for base in paths.data_home_dirs()]
    for path in mimo.discover_database_paths():
        records.extend(_stat_record(path + suffix, "file") for suffix in ("", "-wal"))
    return records


def _antigravity_records(root: str) -> list[tuple[str, str, int, int, int]]:
    records: list[tuple[str, str, int, int, int]] = []
    for client in ("antigravity-cli", "antigravity"):
        directory = os.path.join(root, client, "conversations")
        if not os.path.isdir(directory):
            continue
        records.append(_stat_record(directory, "directory"))
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        records.extend(
            _stat_record(os.path.join(directory, name) + suffix, "file") for name in names if name.endswith(".db") for suffix in ("", "-wal")
        )
    for tree, predicate in (
        (os.path.join(root, "antigravity-cli", "brain"), lambda path: path.endswith(os.path.join(".system_generated", "logs", "transcript.jsonl"))),
        (os.path.join(root, "antigravity", "brain"), lambda path: path.endswith(".md")),
    ):
        directories_by_parent: dict[str, list[tuple[str, str, int, int, int]]] = {}
        files: list[tuple[str, str, int, int, int]] = []
        for dirpath, dirnames, filenames in os.walk(tree):
            directories_by_parent[dirpath] = [_stat_record(os.path.join(dirpath, name), "directory") for name in sorted(dirnames)]
            files.extend(_stat_record(os.path.join(dirpath, name), "file") for name in sorted(filenames) if predicate(os.path.join(dirpath, name)))
        directories = [_stat_record(tree, "directory")]
        pending = [tree]
        while pending:
            parent = pending.pop()
            children = directories_by_parent.get(parent, [])
            directories.extend(children)
            pending.extend(record[1] for record in reversed(children))
        records.extend(directories)
        records.extend(files)
    return records


def _fingerprint(source_id: str, records: list[tuple[str, str, int, int, int]]) -> SessionManifest:
    encoded = json.dumps(records, separators=(",", ":"), sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return SessionManifest(source_id, int(digest[:16], 16) & ((1 << 63) - 1), len(encoded))


def build_manifests() -> list[SessionManifest]:
    """Fingerprint each session collector's metadata inputs separately.

    One digest per collector, not one for all seven: the stores are written
    independently and constantly — a Claude transcript grows while you read it
    — so a combined digest changed on nearly every poll and re-ran every
    collector. Keyed per source, a Claude write re-parses only Claude and the
    other six keep their cached rows.

    The order here is the order rows are merged in, and must stay in step with
    ``sessions.SESSION_COLLECTORS``.
    """
    claude_root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    antigravity_root = os.environ.get("ANTIGRAVITY_HOME") or os.environ.get("GEMINI_HOME") or os.path.expanduser("~/.gemini")
    groups = (
        ("cline", _cline_records(os.environ.get("CLINE_SESSIONS_DIR") or os.path.expanduser("~/.cline/data/sessions"))),
        ("muse", _matching_files(muse_sessions_root(), lambda name: name == "session.jsonl", excluded={".msp-view-v1"})),
        (
            "codex",
            _matching_files(
                os.environ.get("CODEX_SESSIONS_DIR") or os.path.join(codex_home(), "sessions"),
                lambda name: name.endswith(".jsonl"),
            ),
        ),
        (
            "grok",
            _matching_files(
                os.path.join(grok_home(), "sessions"),
                # summary.json is the current per-session layout, updates.jsonl
                # carries the usage totals, signals.json is the older format.
                lambda name: name in ("summary.json", "updates.jsonl", "signals.json"),
            ),
        ),
        ("claude", _claude_records(claude_root)),
        ("opencode", _opencode_records()),
        ("mimo", _mimo_records()),
        ("antigravity", _antigravity_records(antigravity_root)),
        (
            "mistral",
            [(path, "file", mtime_ns, size, 0) for path, mtime_ns, size in mistral_sessions.session_records()],
        ),
        (
            "cursor",
            [(path, "file", mtime_ns, size, 0) for path, mtime_ns, size in cursor_sessions.session_records()],
        ),
    )
    groups += (("junie", _matching_files(str(junie.sessions_root()), lambda name: name in ("index.jsonl", "summary.json", "events.jsonl"))),)
    return [_fingerprint(source_id, records) for source_id, records in groups]
