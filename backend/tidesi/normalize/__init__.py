from .. import sources
from ..contract import provider_error, status_summary
from ..identity import mark
from .antigravity import normalize_antigravity
from .claude import normalize_claude
from .cline import normalize_cline
from .coderabbit import normalize_coderabbit
from .copilot import normalize_copilot
from .cursor import normalize_cursor
from .deepseek import normalize_deepseek
from .grok import normalize_grok
from .jetbrains import normalize_jetbrains
from .junie import normalize_junie
from .kilo import normalize_kilo
from .kiro import normalize_kiro
from .mimo import normalize_mimo
from .mistral import normalize_mistral
from .moonshot import normalize_moonshot
from .muse import normalize_muse
from .ollama import normalize_ollama
from .openai import normalize_openai
from .opencode import normalize_opencode
from .openrouter import normalize_openrouter
from .pi import normalize_pi
from .selfhosted import normalize_selfhosted
from .windsurf import normalize_windsurf
from .zai import normalize_zai
from .zed import normalize_zed

_DISPATCH = {
    "claude": normalize_claude,
    "openai": normalize_openai,
    "antigravity": normalize_antigravity,
    "kiro": normalize_kiro,
    "mistral": normalize_mistral,
    "muse": normalize_muse,
    "openrouter": normalize_openrouter,
    "ollama": normalize_ollama,
    "selfhosted": normalize_selfhosted,
    "grok": normalize_grok,
    "zai": normalize_zai,
    "copilot": normalize_copilot,
    "deepseek": normalize_deepseek,
    "kimi": normalize_moonshot,
    "cursor": normalize_cursor,
    "cline": normalize_cline,
    "opencode": normalize_opencode,
    "mimo": normalize_mimo,
    "junie": normalize_junie,
    "jetbrains": normalize_jetbrains,
    "windsurf": normalize_windsurf,
    "pi": normalize_pi,
    "kilo": normalize_kilo,
    "coderabbit": normalize_coderabbit,
    "zed": normalize_zed,
}


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _login(id_, inputs):
    """(account id, label) of the login a provider read, for the providers that
    name one in what they already collect; ("", "") otherwise. Claude's own
    normalizer says it (from .claude.json).

    Only a real account id or address counts. A token or API key changes
    when it is renewed or rotated on the same account, and would split one
    account's history in two."""
    inputs = inputs if isinstance(inputs, dict) else {}
    usage = inputs.get("usage") if isinstance(inputs.get("usage"), dict) else {}
    if id_ == "openai":
        creds = inputs.get("credentials") if isinstance(inputs.get("credentials"), dict) else {}
        if creds.get("codexLoggedIn") is True or _text(creds.get("codexAccessToken")):
            # A ChatGPT workspace is the plan whose limits these are.
            return _text(creds.get("accountId")) or _text(creds.get("email")).lower(), _text(creds.get("email"))
    elif id_ == "grok":
        user = _text(usage.get("userId"))
        team = _text(usage.get("teamId"))
        email = _text(usage.get("email"))
        return (f"{user}:{team}" if user else email.lower()), email
    elif id_ in ("antigravity", "muse") and usage.get("error") is None:
        email = _text(usage.get("email"))
        return email.lower(), email
    return "", ""


def normalize(raw):
    id_ = raw.get("id")
    fn = _DISPATCH.get(id_)
    if fn is None:
        r = provider_error(id_, id_, "#888888", raw.get("now") or 0, f"unknown provider: {id_}", {})
    else:
        r = fn(raw)
    # Attached here rather than in each normalizer so every provider carries
    # the same block — error states included — and both frontends can draw one
    # status chip from it without a provider table of their own.
    if isinstance(r.get("details"), dict):
        r["details"]["status"] = status_summary((raw.get("inputs") or {}).get("status"), id_)
    # Same reasoning: every provider that can be read more than one way says how,
    # error states included, so the settings page can show what is set up.
    sources.attach(id_, raw.get("inputs"), r)
    if "identity" not in r:
        account_id, label = _login(id_, raw.get("inputs"))
        mark(r, account_id, label)
    return r
