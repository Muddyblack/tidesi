"""Envelope assembly — fetch a set of providers, wrap them in the outer object.

Split out of __main__ so that every in-process consumer builds the identical
envelope: the JSON backend the QML frontends call, and the terminal frontend in
tidesi.cli. See docs/provider-contract.md for the shape produced here.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config
from .collect import collect
from .contract import SCHEMA_VERSION, provider_error, status_summary
from .normalize import normalize
from .session_index import _accumulate_group, _row_cost_groups

# Name and accent for a provider whose fetch raised — the ones its normalizer
# uses, which never runs in that case.
_CRASH_LABELS = {
    "claude": ("Claude", "#cc785c"),
    "antigravity": ("Antigravity", "#4285f4"),
    "openai": ("OpenAI", "#10a37f"),
    "kiro": ("Kiro", "#8b5cf6"),
    "mistral": ("Mistral", "#ff7000"),
    "openrouter": ("OpenRouter", "#9333ea"),
    "ollama": ("Ollama Cloud", "#f0f0f0"),
    "selfhosted": ("Local Models", "#38bdf8"),
    "grok": ("Grok", "#e6e6e6"),
    "zai": ("Z.AI", "#126ef4"),
    "copilot": ("Copilot", "#8b5cf6"),
    "deepseek": ("DeepSeek", "#4f8cff"),
    "kimi": ("Kimi", "#1e3a8a"),
    "muse": ("Muse", "#0064e0"),
    "cursor": ("Cursor", "#e6e6e6"),
    "cline": ("Cline", "#e6e6e6"),
    "opencode": ("OpenCode", "#38bdf8"),
    "mimo": ("MiMo Code", "#E8E8E8"),
    "junie": ("Junie", "#48e054"),
    "jetbrains": ("JetBrains AI", "#e6e6e6"),
    "windsurf": ("Windsurf", "#34e8bb"),
    "pi": ("Pi", "#d4d4d8"),
    "kilo": ("Kilo", "#f4e04d"),
    "coderabbit": ("CodeRabbit", "#ff7a3d"),
    "zed": ("Zed", "#e6e6e6"),
}


def enabled(cfg):
    """Provider ids switched on in the shared settings file, in contract order."""
    return [id_ for id_ in config.ALL_PROVIDERS if config.provider_enabled(cfg, id_)]


def _local_spend_group(sessions, provenance, billing="api"):
    contributions = (group[2:] for session in sessions for group in _row_cost_groups(session, provenance) if group[0] == billing)
    return _accumulate_group(contributions, provenance)


def _local_spend():
    try:
        from .session_cache import SessionCache

        groups = SessionCache().spend_groups()
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        groups = None
    if groups is not None:
        return groups
    from .sessions import all_session_rows

    try:
        # Every row, not ``collect_sessions()``'s first page: a paged total
        # would silently under-report spend on a machine with many sessions.
        sessions = all_session_rows()
    except Exception:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
        sessions = []
    if not isinstance(sessions, list):
        sessions = []
    return {
        # Money owed on metered keys.
        "actual": _local_spend_group(sessions, "actual"),
        "estimated": _local_spend_group(sessions, "estimated"),
        # What plan-covered work would have cost on the API. Never added to the
        # two above: a Pro/Max or ChatGPT plan already paid for it.
        "subscription": _local_spend_group(sessions, "estimated", billing="subscription"),
        "subscriptionActual": _local_spend_group(sessions, "actual", billing="subscription"),
    }


def crashed(id_, now, exc):
    """The error row for a provider whose collect() or normalize() raised.

    Providers are expected to turn every failure into an error of their own;
    this catches what one missed — a file in a shape nobody planned for, a
    platform difference — so that it costs that provider its tab rather than
    failing the whole envelope, and with it every other provider's."""
    label, accent = _CRASH_LABELS.get(id_, (id_, "#888888"))
    message = f"{label}: internal error ({type(exc).__name__}: {exc})"
    return provider_error(id_, label, accent, now, message, {"status": status_summary(None, id_)})


def provider_update(provider):
    return {
        "schemaVersion": SCHEMA_VERSION,
        "partial": True,
        "updatedAt": provider.get("updatedAt", 0),
        "active": provider["id"],
        "providers": [provider],
    }


def build(selected, now=None, *, on_provider=None):
    """Fetch every id in `selected` concurrently and return a full envelope."""
    if now is None:
        now = time.time()

    def fetch_one(id_):
        try:
            return normalize(collect(id_, now))
        except Exception as exc:  # noqa: BLE001  # noqa: BROAD_EXCEPT_OK
            return crashed(id_, now, exc)

    if len(selected) == 1:
        # Avoid starting a worker for the common single-provider case. Apart
        # from being cheaper, this keeps in-process callers on Windows from
        # blocking while ThreadPoolExecutor starts its first thread.
        providers = [fetch_one(selected[0])]
        if on_provider is not None:
            on_provider(providers[0])
    elif selected:
        with ThreadPoolExecutor(max_workers=len(selected)) as pool:
            pending = {pool.submit(fetch_one, id_): index for index, id_ in enumerate(selected)}
            completed = {}
            for future in as_completed(pending):
                provider = future.result()
                completed[pending[future]] = provider
                if on_provider is not None:
                    on_provider(provider)
            providers = [completed[index] for index in range(len(selected))]
    else:
        providers = []

    # `active` is the first healthy provider; frontends fall back to it when the
    # tab they remembered is gone.
    active = ""
    for p in providers:
        if p.get("ok") is True:
            active = p.get("id") or ""
            break
    else:
        if providers:
            active = providers[0].get("id") or ""

    return {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": int(now),
        "active": active,
        "providers": providers,
        "localSpend": _local_spend(),
    }
