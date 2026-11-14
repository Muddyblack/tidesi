"""Extra logins for one provider ("work" and "personal" Claude, two Codex homes).

Every provider that keeps its login under a home directory chosen by an
environment variable (``CLAUDE_CONFIG_DIR``, ``CODEX_HOME``…) can be shown more
than once. The settings file lists the extra homes::

    "accounts": {
      "claude": [{"id": "work", "label": "Work", "home": "~/.claude-work"}]
    }

Each entry becomes a provider of its own with the id ``<provider>@<id>``
(``claude@work``). It is collected and normalized exactly like the provider it
is based on, with that provider's home variable pointed at the entry's
directory for the duration of the fetch, so no provider needs to know it is
being asked about a second account. Only the directory is stored: tokens stay
where the tool put them and are never copied or refreshed here.

The override is process-wide, so every fetch of a provider that has a home
variable runs under one lock. Providers fetched in parallel threads never see
each other's home.
"""

import contextlib
import os
import re
import threading

from . import config

ACCOUNT_SEPARATOR = "@"
MAX_ACCOUNTS_PER_PROVIDER = 8

# Provider id -> the variable naming the directory its login lives under.
HOME_ENV = {
    "claude": "CLAUDE_CONFIG_DIR",
    "openai": "CODEX_HOME",
    "grok": "GROK_HOME",
    "kimi": "KIMI_CODE_HOME",
    "mistral": "VIBE_HOME",
    "kiro": "KIRO_HOME",
    "antigravity": "ANTIGRAVITY_HOME",
}

# Provider id -> (the session collector that reads its transcripts, the
# provider id those rows carry). Only providers with a session collector can
# have their sessions and spend read per account.
SESSION_SOURCES = {
    "claude": ("claude", "claude"),
    "openai": ("codex", "openai"),
    "grok": ("grok", "grok"),
    "antigravity": ("antigravity", "antigravity"),
    "mistral": ("mistral", "mistral"),
}

_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_LOCK = threading.RLock()


def base_id(provider_id):
    """``claude`` for both ``claude`` and ``claude@work``."""
    return str(provider_id).split(ACCOUNT_SEPARATOR, 1)[0]


def is_account_id(provider_id):
    return ACCOUNT_SEPARATOR in str(provider_id)


def configured(cfg):
    """The valid extra accounts in ``cfg``, ``{provider: [account, …]}``.

    An account is ``{"id", "key", "label", "home", "base"}``. Anything that is
    not usable — unknown provider, a provider with no home variable, a bad or
    repeated key, an empty home — is dropped rather than reported: a typo in a
    hand-edited settings file must cost that entry, not the whole backend.
    """
    raw = (cfg or {}).get("accounts")
    if not isinstance(raw, dict):
        return {}
    result = {}
    for provider, entries in raw.items():
        if provider not in HOME_ENV or not isinstance(entries, list):
            continue
        seen = set()
        accepted = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("id") or "").strip().lower()
            home = str(entry.get("home") or "").strip()
            if not _KEY.match(key) or key in seen or not home:
                continue
            seen.add(key)
            accepted.append(
                {
                    "id": f"{provider}{ACCOUNT_SEPARATOR}{key}",
                    "key": key,
                    "label": str(entry.get("label") or "").strip() or key,
                    "home": home,
                    "base": provider,
                }
            )
            if len(accepted) == MAX_ACCOUNTS_PER_PROVIDER:
                break
        if accepted:
            result[provider] = accepted
    return result


def find(cfg, provider_id):
    """The configured account behind ``claude@work``, else None."""
    for account in configured(cfg).get(base_id(provider_id), ()):
        if account["id"] == provider_id:
            return account
    return None


def session_accounts(cfg=None):
    """The configured accounts whose sessions can be read, in settings order.

    Their rows carry the account's id (``claude@work``) as their provider, so
    sessions, spend and the source filter treat each account as its own."""
    found = configured(config.load_settings() if cfg is None else cfg)
    return [account for provider, entries in found.items() if provider in SESSION_SOURCES for account in entries]


def launch_env(account):
    """``{VARIABLE: folder}`` for a command run on behalf of ``account``."""
    return {HOME_ENV[account["base"]]: os.path.expanduser(account["home"])}


def expand(cfg, provider_ids):
    """``provider_ids`` with each provider's extra accounts right after it."""
    extra = configured(cfg)
    result = []
    for id_ in provider_ids:
        result.append(id_)
        result.extend(account["id"] for account in extra.get(id_, ()))
    return result


@contextlib.contextmanager
def home_for(provider, home=None):
    """Run a fetch of ``provider`` with its home variable set to ``home``.

    With no ``home`` the primary account is being fetched: the environment is
    left as it is, but the lock is still taken so an extra account's override
    cannot be in effect at the same time.
    """
    name = HOME_ENV.get(provider)
    if not name:
        yield
        return
    with _LOCK:
        if home is None:
            yield
            return
        previous = os.environ.get(name)
        # Expanded, as a shell hands it to the tool. Claude Code keys its macOS
        # Keychain item to the variable as it sees it, so "~/.claude-work" and
        # the path a shell makes of it are two different logins.
        os.environ[name] = os.path.expanduser(home)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


def relabel(provider, account):
    """Turn the normalized base provider into the account's own provider."""
    result = dict(provider)
    base_label = str(result.get("label") or account["base"])
    result["id"] = account["id"]
    result["label"] = f"{base_label} · {account['label']}"
    result["profile"] = {"base": account["base"], "label": account["label"], "home": account["home"]}
    return result
