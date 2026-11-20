"""Resolve Mistral API key and verify it, plus read vibe CLI session stats.

Ported from tools/sh/get-mistral-usage. Mistral has no public billing API, so:
1. resolve the key (widget config > env var > vibe .env > known config files)
2. call GET /v1/models to verify validity and get the model list
3. read ~/.vibe session logs for local cost / token / session stats
"""

import hashlib
import hmac
import json
import os
import re
import tempfile

from .. import accounts, paths
from ..http import as_json, fetch_json, resolve_key
from ..messages import tr
from . import mistral_sessions

_CACHE_VERSION = 1


def _vibe_home():
    """Vibe's folder: ``$VIBE_HOME`` — the folder itself, as Vibe and the
    session reader take it — else ``.vibe`` in the home folder."""
    return os.environ.get("VIBE_HOME") or os.path.join(os.environ.get("HOME") or os.path.expanduser("~"), ".vibe")


def _vibe_key():
    path = os.path.join(_vibe_home(), ".env")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("MISTRAL_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return ""


def _vibe_session_dir():
    return os.path.join(_vibe_home(), "logs", "session")


def _cache_key(session_dir):
    """Opaque identity for one normalized vibe session store.

    The canonical session path is hashed so only the digest ever reaches disk —
    never the path itself. Two different ``HOME`` values resolve to different
    session paths and therefore different cache files.
    """
    normalized = os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(session_dir))))
    return hashlib.sha256(os.fsencode(normalized)).hexdigest()


def _shared_fingerprint(files):
    """One opaque digest over every ``meta.json`` file.

    Each record is (path, mtime_ns, size) — only the digest reaches disk — so
    the shared digest changes when a log is added, removed,
    renamed, or rewritten — and stays stable when nothing changed. Raises
    ``OSError`` if a listed log disappears mid-walk; the caller then treats the
    store as changed and rescans.
    """
    records = []
    for path in files:
        stat = os.stat(path)
        records.append((path, stat.st_mtime_ns, stat.st_size))
    records.sort()
    encoded = json.dumps(records, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_cache(path, key, fingerprint):
    """Return cached vibe stats only when store identity and fingerprint match."""
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != _CACHE_VERSION:
        return None
    stored_key = payload.get("key")
    if not isinstance(stored_key, str) or not hmac.compare_digest(stored_key, key):
        return None
    stored_fingerprint = payload.get("fingerprint")
    if not isinstance(stored_fingerprint, str) or not hmac.compare_digest(stored_fingerprint, fingerprint):
        return None
    stats = payload.get("stats")
    return stats if isinstance(stats, dict) else None


def _write_cache(path, key, fingerprint, stats):
    payload = {"version": _CACHE_VERSION, "key": key, "fingerprint": fingerprint, "stats": stats}
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".vibe-stats-", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"))
            os.replace(temporary, path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except OSError:
        return


def _aggregate(docs):
    def s(d, key):
        return (d.get("stats") or {}).get(key) or 0

    by_newest = sorted(docs, key=lambda d: d.get("start_time") or "", reverse=True)
    recent = []
    for d in by_newest[:12]:
        wd = (d.get("environment") or {}).get("working_directory") or ""
        recent.append(
            {
                "title": d.get("title") or tr("untitled"),
                # vibe records the directory in its own OS's spelling.
                "project": wd.replace("\\", "/").rstrip("/").split("/")[-1] if wd else "",
                "branch": d.get("git_branch") or "",
                "cost": s(d, "session_cost"),
                "tokens": s(d, "session_total_llm_tokens"),
                "start": d.get("start_time") or "",
            }
        )
    return {
        "vibeSessionCount": len(docs),
        "vibeTotalCost": sum(s(d, "session_cost") for d in docs),
        "vibeTotalTokens": sum(s(d, "session_total_llm_tokens") for d in docs),
        "vibePromptTokens": sum(s(d, "session_prompt_tokens") for d in docs),
        "vibeCompletionTokens": sum(s(d, "session_completion_tokens") for d in docs),
        "vibeTotalSteps": sum(s(d, "steps") for d in docs),
        "vibeToolOk": sum(s(d, "tool_calls_succeeded") for d in docs),
        "vibeToolFail": sum(s(d, "tool_calls_failed") for d in docs),
        "vibeRecent": recent,
    }


def _vibe_stats():
    session_dir = _vibe_session_dir()
    if not os.path.isdir(session_dir):
        return {}
    cache_dir = paths.cache_dir()
    key = _cache_key(session_dir)
    cache_path = os.path.join(cache_dir, f"vibe-stats-{key}.json")

    paths_list = []
    for root, _dirs, files in os.walk(session_dir):
        for name in files:
            if name == "meta.json":
                paths_list.append(os.path.join(root, name))
    paths_list.sort()
    # Vibe 2.x keeps token totals in each unified session's projection state.
    state_files = sorted(p for p, _m, _s in mistral_sessions.session_records() if p.endswith("projection-state.json"))

    try:
        fingerprint = _shared_fingerprint(paths_list + state_files)
    except OSError:
        fingerprint = None

    cached = None if fingerprint is None else _read_cache(cache_path, key, fingerprint)
    if cached is not None:
        return cached

    docs = []
    for p in paths_list:
        try:
            with open(p, encoding="utf-8") as f:
                d = as_json(f.read())
        except OSError:
            continue
        if isinstance(d, dict) and d.get("stats") is not None:
            docs.append(d)
    docs.extend(mistral_sessions.unified_documents())

    result = _aggregate(docs) if docs else {}
    if fingerprint is not None:
        _write_cache(cache_path, key, fingerprint, result)
    return result


def _vibe_model():
    path = os.path.join(_vibe_home(), "config.toml")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("active_model"):
                    m = re.search(r'=\s*"?([^"]*?)"?\s*$', line.rstrip("\n"))
                    return m.group(1) if m else ""
    except OSError:
        pass
    return ""


def get_mistral_usage():
    # An extra account (another VIBE_HOME) uses only the key in its own .env.
    api_key = (
        ""
        if accounts.extra_account()
        else resolve_key(
            "WIDGET_MISTRAL_API_KEY",
            "MISTRAL_API_KEY",
            os.path.expanduser("~/.config/mistral/api-key"),
            os.path.expanduser("~/.mistral/api-key"),
            os.path.expanduser("~/.config/mistral.key"),
        )
    )
    if not api_key:
        api_key = _vibe_key()

    vibe_stats = _vibe_stats()
    vibe_model = _vibe_model()
    base = {**vibe_stats, "vibeActiveModel": vibe_model}

    if not api_key:
        return {**base, "hasKey": False, "keyValid": False}

    result = fetch_json(
        "https://api.mistral.ai/v1/models",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=8,
        fixture_path=os.environ.get("MISTRAL_RESPONSE_FILE"),
    )

    if result.status == 200:
        body = as_json(result.body)
        if body is None:
            return {**base, "hasKey": True, "keyValid": False, "error": tr("Mistral invalid JSON")}
        return {
            **base,
            "hasKey": True,
            "keyValid": True,
            "availableModels": [m.get("id") for m in (body.get("data") or []) if m.get("id")],
        }
    if result.status in (401, 403):
        return {**base, "hasKey": True, "keyValid": False, "error": tr("Invalid API key (401)")}
    if result.status == 429:
        return {**base, "hasKey": True, "keyValid": True, "error": tr("Rate limited (429)")}
    if result.status in (0, None, ""):
        return {**base, "hasKey": True, "keyValid": False, "error": tr("Mistral network error")}
    return {**base, "hasKey": True, "keyValid": False, "error": tr("HTTP %1", result.status)}
