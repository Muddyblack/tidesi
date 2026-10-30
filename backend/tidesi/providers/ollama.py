"""Read Ollama Cloud account usage with an API key, including OpenCode's key.

The cloud usage endpoint is undocumented. Keep its response intact for the
normalizer, which accepts the limit buckets actually returned by the account.
This request never invokes a model or spends inference credits.
"""

import json
import os

from .. import sources
from ..http import as_json, clean_credential, fetch_json
from ..messages import tr


def _opencode_key():
    # OpenCode uses xdg-basedir on every OS, including Windows. Our native
    # paths.data_home() would point at AppData there and miss its auth file.
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    try:
        with open(os.path.join(data_home, "opencode", "auth.json"), encoding="utf-8") as f:
            auth = json.load(f)
    except (OSError, ValueError):
        return ""
    entry = auth.get("ollama-cloud") if isinstance(auth, dict) else None
    if isinstance(entry, dict) and entry.get("type") == "api" and isinstance(entry.get("key"), str):
        key = clean_credential(entry["key"])
        if key and not any(ord(ch) < 32 or ord(ch) == 127 for ch in key):
            return key
    return ""


def _own_key():
    return clean_credential(os.environ.get("WIDGET_OLLAMA_API_KEY")) or clean_credential(os.environ.get("OLLAMA_API_KEY"))


SOURCES = (
    sources.Source("key", "api", tr("API key"), tr("A key from settings or $OLLAMA_API_KEY"), lambda: bool(_own_key())),
    sources.Source("opencode", "file", tr("OpenCode login"), tr("The ollama-cloud key OpenCode stored"), lambda: bool(_opencode_key())),
)

_KEYS = {"key": lambda: _own_key(), "opencode": lambda: _opencode_key()}


def _resolve_key():
    for source in sources.candidates("ollama", tuple(_KEYS)):
        key = _KEYS[source]()
        if key:
            return key, source
    return "", ""


def get_ollama_usage():
    key, source = _resolve_key()
    if not key:
        return {}
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in key):
        return {"error": tr("Ollama Cloud: invalid API key characters")}
    result = fetch_json(
        "https://ollama.com/api/balance",
        headers={"Authorization": f"Bearer {key}"},
        timeout=10,
        fixture_path=os.environ.get("OLLAMA_RESPONSE_FILE"),
    )
    if result.status != 200:
        messages = {
            0: "offline",
            401: "Ollama Cloud: API key rejected",
            403: "Ollama Cloud: access denied",
            404: "Ollama Cloud: balance endpoint unavailable",
            429: "rate limited",
        }
        return {"error": messages.get(result.status, f"Ollama Cloud: HTTP {result.status}")}
    body = as_json(result.body)
    if not isinstance(body, dict) or not body or body.get("error"):
        return {"error": tr("Ollama Cloud: invalid usage response")}
    body["source"] = source
    return body
