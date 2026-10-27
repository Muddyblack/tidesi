"""Which local agents bill per token, and which are covered by a plan.

A session's cost is derived from its token counts times the catalog rate. On a
pay-as-you-go API key that product is money owed. On a Claude Pro/Max or a
ChatGPT/Codex plan it is only what the same work *would* have cost on the API —
the subscription already paid for it.

Mixing the two into one "local spend" total overstates what the user actually
pays, sometimes by orders of magnitude, so every session row carries the mode
it was priced under and the envelope totals them separately.

Only providers that expose a plan signal in credentials we already read are
classified; everything else is bring-your-own-key and therefore ``API``.
"""

import json
import os

from .providers.claude_credentials import get_claude_credentials
from .providers.openai_credentials import get_openai_credentials

SUBSCRIPTION = "subscription"
API = "api"

# A subscriptionType of "api" would mean console billing rather than a plan.
_NON_PLAN_VALUES = frozenset({"", "api", "none", "free_api"})

_CACHE = {}


def _text(value):
    return value.strip().lower() if isinstance(value, str) else ""


def _claude_mode():
    credentials = get_claude_credentials()
    oauth = credentials.get("claudeAiOauth")
    plan = _text(oauth.get("subscriptionType")) if isinstance(oauth, dict) else ""
    return SUBSCRIPTION if plan not in _NON_PLAN_VALUES else API


def _openai_mode():
    credentials = get_openai_credentials()
    # An OAuth ChatGPT login is a plan even when the JWT carries no plan name;
    # an API key alone is metered.
    if _text(credentials.get("authMode")) == "chatgpt":
        return SUBSCRIPTION
    return SUBSCRIPTION if _text(credentials.get("planType")) not in _NON_PLAN_VALUES else API


# Session provider id -> how to classify it. Absent means API.
_RESOLVERS = {
    "claude": _claude_mode,
    "openai": _openai_mode,
}


def reset_cache():
    """Drop the per-process memo. Tests and long-lived callers use this."""
    _CACHE.clear()


def _opencode_mode(provider: str) -> str:
    if provider not in ("openai", "anthropic"):
        return API
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    try:
        with open(os.path.join(data_home, "opencode", "auth.json"), encoding="utf-8") as stream:
            auth = json.load(stream)
    except (OSError, ValueError):
        return API
    entry = auth.get(provider) if isinstance(auth, dict) else None
    return SUBSCRIPTION if isinstance(entry, dict) and _text(entry.get("type")) == "oauth" else API


def mode_for(provider, source=""):
    """``"subscription"`` or ``"api"`` for one session provider id.

    Memoized: credential lookup reads files and decodes a JWT, which must not
    happen once per session row.
    """
    key = (provider, source)
    if key in _CACHE:
        return _CACHE[key]
    resolver = _RESOLVERS.get(provider)
    try:
        mode = _opencode_mode(provider) if source == "opencode" else resolver() if resolver is not None else API
    except Exception:
        # An unreadable credential store must not stop a listing; metered is
        # the honest default, since it never hides a cost the user does owe.
        mode = API
    _CACHE[key] = mode
    return mode
