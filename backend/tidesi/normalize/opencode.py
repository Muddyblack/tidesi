"""Mode-aware OpenCode normalization.

Zen mode renders the device-local session ledger (tokens, sessions, cost) and
labels it as local activity — it never invents an account quota. Go mode
renders the account-level quota windows (rolling/weekly/monthly) reported by
the Go usage endpoint, with three distinct persisted history series, while the
local ledger stays secondary in ``details``.
"""

import math

from ..contract import (
    chart_window,
    epoch_of,
    flat_window,
    jround,
    pct_clamp,
    provider_base,
    provider_error,
    resetting,
)
from ..messages import tr
from ..stats import opencode_stats
from .local_activity import normalize_local_activity

ACCENT = "#B7B1B1"

# Upstream Go usage endpoint (GET https://opencode.ai/zen/go/v1/usage) reports
# usage.rolling|weekly|monthly, each {status, percent, resetsAt (ISO 8601)}.
# The history keys are the explicit global persisted series names; each is
# paired with its chartWindow.key so the frontend plots the right series.
_GO_WINDOWS = (
    ("rolling", "opencode_go_rolling_pct", "Rolling (5h)", 18000000),
    ("weekly", "opencode_go_weekly_pct", "Weekly (7d)", 604800000),
    ("monthly", "opencode_go_monthly_pct", "Monthly", 2592000000),
)


def _parse_go_window(window):
    """Return (pct, reset_at) for a usable Go quota window, or None.

    Only upstream-confirmed fields are read: status, percent, resetsAt (ISO).
    Anything else — a missing window, a status other than "ok", a non-numeric
    or non-finite percent — is unavailable, never a fabricated 0/100 point.
    The percent is authoritative; resetsAt is best-effort (0 = no reset known).
    """
    if not isinstance(window, dict):
        return None
    if window.get("status") != "ok":
        return None
    percent = window.get("percent")
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        return None
    if not math.isfinite(percent):
        return None
    return pct_clamp(percent), epoch_of(window.get("resetsAt"))


def _go_chart_windows(parsed):
    """Chart range selectors over the available Go series.

    Each selector references the series that best matches its range: 5H/24H
    plot the rolling series when present, else weekly, else monthly; 7D plots
    weekly when present, else monthly, else rolling; 30D plots monthly when
    present, else weekly, else rolling. Only available series are referenced,
    so a missing window never produces a chart pointing at empty data.
    """
    rolling = parsed.get("rolling")
    weekly = parsed.get("weekly")
    monthly = parsed.get("monthly")
    out = []

    short = rolling or weekly or monthly
    if short:
        key, _label, period, (_pct, reset_at) = short
        out.append(resetting(chart_window("opencode_go_5h", key, "5H", 18000000, "5h"), period, {"resetAt": reset_at}))
        out.append(resetting(chart_window("opencode_go_24h", key, "24H", 86400000, "24h"), period, {"resetAt": reset_at}))

    mid = weekly or monthly or rolling
    if mid:
        key, _label, period, (_pct, reset_at) = mid
        out.append(resetting(chart_window("opencode_go_7d", key, "7D", 604800000, "7d"), period, {"resetAt": reset_at}))

    long = monthly or weekly or rolling
    if long:
        key, _label, period, (_pct, reset_at) = long
        out.append(resetting(chart_window("opencode_go_30d", key, "30D", 2592000000, "30d"), period, {"resetAt": reset_at}))

    return out


def _normalize_go(now, stats, account):
    go_error = account.get("goError") or ""
    go_usage = account.get("goUsage")
    if go_error:
        return provider_error(
            "opencode",
            "OpenCode",
            ACCENT,
            now,
            tr("OpenCode Go: %1", go_error),
            {"stats": stats, "source": "OpenCode Go API", "accountMode": "go", "goError": go_error},
        )
    if not isinstance(go_usage, dict):
        return provider_error(
            "opencode",
            "OpenCode",
            ACCENT,
            now,
            tr("OpenCode Go: no usage data"),
            {"stats": stats, "source": "OpenCode Go API", "accountMode": "go"},
        )
    usage = go_usage.get("usage")
    if not isinstance(usage, dict):
        return provider_error(
            "opencode",
            "OpenCode",
            ACCENT,
            now,
            tr("OpenCode Go: malformed usage response"),
            {"stats": stats, "source": "OpenCode Go API", "accountMode": "go"},
        )

    parsed = {}
    for name, key, label, period in _GO_WINDOWS:
        point = _parse_go_window(usage.get(name))
        if point is not None:
            parsed[name] = (key, label, period, point)

    if not parsed:
        return provider_error(
            "opencode",
            "OpenCode",
            ACCENT,
            now,
            tr("OpenCode Go: no usable quota windows"),
            {"stats": stats, "source": "OpenCode Go API", "accountMode": "go"},
        )

    primary = parsed.get("rolling") or parsed.get("weekly") or parsed.get("monthly")
    primary_label = primary[1]
    primary_pct = primary[3][0]

    r = provider_base("opencode", "OpenCode", ACCENT, now)
    r["summary"] = {"pct": primary_pct, "text": f"{jround(primary_pct)}%", "detail": primary_label, "hasChart": True}
    r["quotaWindows"] = [
        flat_window(key, label, pct, reset_at, tr("%1% used", jround(pct)), True, history=key)
        for _name, (key, label, _period, (pct, reset_at)) in parsed.items()
    ]
    r["slots"] = [
        {
            "pct": pct,
            "color": ACCENT,
            "text": f"{jround(pct)}%",
            "tooltip": tr("OpenCode Go %1\n%2% used", label, jround(pct)),
        }
        for _name, (key, label, _period, (pct, reset_at)) in parsed.items()
    ]
    r["chartWindows"] = _go_chart_windows(parsed)
    r["historyValues"] = {key: pct for _name, (key, _label, _period, (pct, _reset_at)) in parsed.items()}
    r["details"] = {
        "stats": stats,
        "periods": stats.get("periods") or [],
        "source": "OpenCode Go API",
        "accountMode": "go",
        "goError": "",
        "costStatus": stats.get("costStatus", "unavailable"),
        "costProvenance": "local ledger and exact model catalog where available",
    }
    return r


def normalize_opencode(raw):
    now = raw["now"]
    usage = raw["inputs"].get("usage") or {}
    account = raw["inputs"].get("account") or {}
    stats = opencode_stats(usage, now)
    mode = account.get("mode") or "zen"
    if mode == "go":
        return _normalize_go(now, stats, account)
    return normalize_local_activity(now, stats)
