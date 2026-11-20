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

The override is process-wide, so the fetches that touch one variable run under
that variable's lock: ``claude`` and ``claude@work`` take turns, while Codex or
Grok, which read other variables, go on in parallel. A provider with no extra
account takes no lock at all. Providers fetched in parallel threads never see
each other's home.

An extra account reads only what lives in its folder. Keys that come from the
environment or the settings file (an admin API key, a Moonshot key) belong to
whoever set them up, which is the main login: read again under the account they
would show the same bill twice and add it to the total twice.
``extra_account()`` tells a collector which of the two it is fetching for.
"""

import contextlib
import os
import re
import threading

from . import config
from .messages import lines

ACCOUNT_SEPARATOR = "@"
MAX_ACCOUNTS_PER_PROVIDER = 8

# Provider id -> the variable naming the directory its login lives under.
#
# Only providers whose quota is read from that directory belong here. Kiro reads
# its quota from the IDE's database and kiro-cli's store, and Antigravity from
# the running IDE: neither looks at KIRO_HOME / ANTIGRAVITY_HOME, so a second
# "account" of either showed the main login's quota under another name.
HOME_ENV = {
    "claude": "CLAUDE_CONFIG_DIR",
    "openai": "CODEX_HOME",
    "grok": "GROK_HOME",
    "kimi": "KIMI_CODE_HOME",
    "mistral": "VIBE_HOME",
}

# Where each tool keeps its login when the variable is not set: the main
# login's folder, which an extra account must not point at again.
DEFAULT_HOME = {
    "claude": "~/.claude",
    "openai": "~/.codex",
    "grok": "~/.grok",
    "kimi": "~/.kimi-code",
    "mistral": "~/.vibe",
}

# Provider id -> (the session collector that reads its transcripts, the
# provider id those rows carry). Only providers with a session collector can
# have their sessions and spend read per account.
SESSION_SOURCES = {
    "claude": ("claude", "claude"),
    "openai": ("codex", "openai"),
    "grok": ("grok", "grok"),
    "mistral": ("mistral", "mistral"),
}

_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
# One lock per variable: the override of CLAUDE_CONFIG_DIR has nothing to do
# with a Codex fetch, which may run at the same time.
_LOCKS = {name: threading.RLock() for name in set(HOME_ENV.values())}
# The extra account the current thread is fetching for, if any.
_FETCHING = threading.local()


def base_id(provider_id):
    """``claude`` for both ``claude`` and ``claude@work``."""
    return str(provider_id).split(ACCOUNT_SEPARATOR, 1)[0]


def is_account_id(provider_id):
    return ACCOUNT_SEPARATOR in str(provider_id)


def folder_key(home):
    """``home`` as a comparable folder: expanded, absolute, without a trailing
    separator, and in the platform's case. Two spellings of one folder are one
    login."""
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(home))))


def main_home(provider):
    """The folder the provider's main login is read from in this process."""
    name = HOME_ENV.get(provider)
    return (os.environ.get(name) if name else "") or DEFAULT_HOME.get(provider, "")


def configured(cfg):
    """The valid extra accounts in ``cfg``, ``{provider: [account, …]}``.

    An account is ``{"id", "key", "label", "home", "base"}``. Anything that is
    not usable — unknown provider, a provider with no home variable, a bad or
    repeated key, an empty home, a folder the main login or an earlier account
    already reads — is dropped rather than reported: a typo in a hand-edited
    settings file must cost that entry, not the whole backend. (Settings refuses
    a repeated folder as it is typed; this is the net under a hand edit.) A
    second reading of one folder would count its sessions and spend twice.
    """
    raw = (cfg or {}).get("accounts")
    if not isinstance(raw, dict):
        return {}
    result = {}
    for provider, entries in raw.items():
        if provider not in HOME_ENV or not isinstance(entries, list):
            continue
        seen = set()
        folders = {folder_key(main_home(provider))}
        accepted = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("id") or "").strip().lower()
            home = str(entry.get("home") or "").strip()
            if not _KEY.match(key) or key in seen or not home or folder_key(home) in folders:
                continue
            seen.add(key)
            folders.add(folder_key(home))
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


def extra_account():
    """The extra account this thread is fetching for, or None for a main login.

    A collector asks before it reads a key from the environment or the settings
    file: those belong to the main login, and an extra account reads only its
    own folder."""
    return getattr(_FETCHING, "account", None)


@contextlib.contextmanager
def home_for(provider, home=None, *, account=None, exclusive=True):
    """Run a fetch of ``provider`` with its home variable set to ``home``.

    With no ``home`` the primary account is being fetched: the environment is
    left as it is. The variable's lock is still taken, so an extra account's
    override cannot be in effect at the same time, unless ``exclusive`` is
    false — the caller knows there is no extra account to wait for.
    ``account`` is the extra account being fetched, for ``extra_account()``.
    """
    name = HOME_ENV.get(provider)
    if not name or (home is None and not exclusive):
        yield
        return
    with _LOCKS[name]:
        if home is None:
            yield
            return
        previous = os.environ.get(name)
        # Expanded, as a shell hands it to the tool. Claude Code keys its macOS
        # Keychain item to the variable as it sees it, so "~/.claude-work" and
        # the path a shell makes of it are two different logins.
        os.environ[name] = os.path.expanduser(home)
        before = getattr(_FETCHING, "account", None)
        _FETCHING.account = account or {"base": provider, "home": home}
        try:
            yield
        finally:
            _FETCHING.account = before
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous


def series(provider, suffix):
    """``provider`` with every history series it names renamed ``<key><suffix>``.

    Two logins are two quotas, and one series fed by both is a chart and an ETA
    of neither: an extra account records ``s@work``, and a login told apart from
    the one before it in the same folder (tidesi.identity) ``s#<key>``."""
    if not suffix:
        return provider
    result = dict(provider)
    rename = lambda key: f"{key}{suffix}"  # noqa: E731
    if isinstance(result.get("historyValues"), dict):
        result["historyValues"] = {rename(key): value for key, value in result["historyValues"].items()}
    if isinstance(result.get("chartWindows"), list):
        result["chartWindows"] = [{**w, "key": rename(w["key"])} if isinstance(w, dict) and w.get("key") else w for w in result["chartWindows"]]
    if isinstance(result.get("quotaWindows"), list):
        result["quotaWindows"] = [
            {**w, "historyKey": rename(w["historyKey"])} if isinstance(w, dict) and w.get("historyKey") else w for w in result["quotaWindows"]
        ]
    return result


def series_suffix(account):
    """The suffix an extra account's series had before logins were told apart."""
    return f"{ACCOUNT_SEPARATOR}{account['key']}" if account else ""


def relabel(provider, account, suffix=None):
    """Turn the normalized base provider into the account's own provider.

    Its series get ``suffix``, by default the account's own (``s@work``)."""
    result = dict(provider)
    base_label = str(result.get("label") or account["base"])
    result["id"] = account["id"]
    result["label"] = f"{base_label} · {account['label']}"
    result["profile"] = {"base": account["base"], "label": account["label"], "home": account["home"]}
    if isinstance(result.get("slots"), list):
        # A tray or panel tooltip says "Claude 5-hour: 23%" for every Claude
        # login; the account's name on top says which one.
        result["slots"] = [
            {**slot, "tooltip": lines(result["label"], slot["tooltip"])} if isinstance(slot, dict) and slot.get("tooltip") else slot
            for slot in result["slots"]
        ]
    return series(result, series_suffix(account) if suffix is None else suffix)
