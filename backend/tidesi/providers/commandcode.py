"""Command Code usage windows and monthly credits.

The same three GETs the CLI's own ``/usage`` screen makes (checked against
command-code 1.84.0; the endpoints are not in the public docs):

- ``/alpha/billing/credits``: remaining credits and the rolling 5-hour and
  weekly windows (``windowLimits``), in USD of model usage.
- ``/alpha/billing/subscriptions``: the plan and the billing period.
- ``/alpha/usage/summary?since=<period start>``: what the period has cost.

The credential is, in order: the widget setting / ``$COMMAND_CODE_API_KEY``,
then the key ``cmd login`` stored at ``~/.commandcode/auth.json`` (``apiKey``).
A key that is refused falls back to the CLI login.

ponytail: personal account only. An organisation's usage needs ``orgId`` (from
``/alpha/whoami``) on each call; add it when someone on a Teams plan asks.
"""

import os
import urllib.parse

from .. import sources
from ..contract import epoch_of, finite_number
from ..http import as_json, clean_credential, fetch_json, http_error_text, resolve_key
from ..messages import tr

_BASE = "https://api.commandcode.ai/alpha"
# Plan id prefix -> name, as the CLI prints it. Longest prefix first, so
# "individual-goat" is not read as "individual-go".
_PLANS = (
    ("individual-provider", "Provider"),
    ("individual-ultra", "Ultra"),
    ("individual-goat", "GOAT"),
    ("individual-max", "Max"),
    ("individual-pro", "Pro"),
    ("individual-go", "Go"),
    ("teams-pro", "Teams Pro"),
)


def _auth_file():
    return os.environ.get("COMMANDCODE_AUTH_PATH") or os.path.join(os.path.expanduser("~"), ".commandcode", "auth.json")


def _cli_token():
    try:
        with open(_auth_file(), encoding="utf-8") as f:
            data = as_json(f.read())
    except OSError:
        return ""
    return clean_credential(data.get("apiKey")) if isinstance(data, dict) else ""


def _own_key():
    return resolve_key("WIDGET_COMMANDCODE_API_KEY", "COMMAND_CODE_API_KEY")


def _get(token, path):
    return fetch_json(
        _BASE + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "tidesi/commandcode"},
        timeout=15,
    )


def _usd(value):
    number = finite_number(value)
    return max(0.0, number) if number is not None else 0.0


def parse_window(window):
    """One rolling window, or None when the plan does not have it."""
    if not isinstance(window, dict):
        return None
    cap = _usd(window.get("cap"))
    if cap <= 0:
        return None
    # resetAt is epoch milliseconds, and 0 until the first request opens the window.
    return {"used": _usd(window.get("used")), "cap": cap, "resetAt": int(_usd(window.get("resetAt")) // 1000)}


def plan_name(plan_id):
    plan_id = plan_id.lower().replace("_", "-") if isinstance(plan_id, str) else ""
    return next((name for prefix, name in _PLANS if plan_id.startswith(prefix)), plan_id)


def parse_usage(credits_body, subscription_body, summary_body):
    """The collector's result from the three response bodies, or None when the
    credits response is not one."""
    credits = credits_body.get("credits") if isinstance(credits_body, dict) else None
    if not isinstance(credits, dict):
        return None
    limits = credits_body.get("windowLimits")
    limits = limits if isinstance(limits, dict) else {}
    sub = subscription_body.get("data") if isinstance(subscription_body, dict) else None
    sub = sub if isinstance(sub, dict) else {}
    remaining = _usd(credits.get("monthlyCredits"))
    monthly = None
    if isinstance(summary_body, dict):
        used = summary_body.get("totalMonthlyCredits")
        used = _usd(summary_body.get("totalCost") if used is None else used)
        if used + remaining > 0:
            monthly = {"used": used, "total": used + remaining, "remaining": remaining, "resetAt": epoch_of(sub.get("currentPeriodEnd"))}
    return {
        "fiveHour": parse_window(limits.get("fiveHour")),
        "weekly": parse_window(limits.get("weekly")),
        "monthly": monthly,
        "remaining": remaining,
        "extra": _usd(credits.get("purchasedCredits")) + _usd(credits.get("freeCredits")),
        "plan": plan_name(sub.get("planId")) if sub.get("status") in ("active", "trialing", "past_due") else "",
    }


SOURCES = (
    sources.Source("key", "api", tr("API key"), tr("A key from settings or $COMMAND_CODE_API_KEY"), lambda: bool(_own_key())),
    sources.Source("cli", "cli", tr("Command Code CLI"), tr("The login `cmd login` stored"), lambda: os.path.isfile(_auth_file())),
)

_TOKENS = {"key": _own_key, "cli": _cli_token}


def get_commandcode_usage():
    # A refused key falls back to the CLI login (unless the user chose one).
    tokens = [(token, source) for source in sources.candidates("commandcode", tuple(_TOKENS)) if (token := _TOKENS[source]())]
    if not tokens:
        return {}
    token, source, result = "", "", None
    for candidate in tokens:
        token, source = candidate
        result = _get(token, "/billing/credits")
        if result.status not in (401, 403):
            break
    if result.status != 200:
        return {"error": http_error_text(result.status), "source": source}
    # The plan and the period's spend are extras: without them the windows still show.
    subscription = as_json(_get(token, "/billing/subscriptions").body)
    since = ((subscription or {}).get("data") or {}).get("currentPeriodStart") if isinstance(subscription, dict) else None
    summary = as_json(_get(token, "/usage/summary?" + urllib.parse.urlencode({"since": since})).body) if isinstance(since, str) and since else None
    usage = parse_usage(as_json(result.body), subscription, summary)
    if usage is None:
        return {"error": "unexpected response", "source": source}
    return {**usage, "source": source}
