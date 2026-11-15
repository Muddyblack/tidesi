from ..contract import (
    compact_tokens,
    epoch_of,
    flat_window,
    jround,
    money,
    monthly_window,
    num,
    provider_base,
    provider_error,
    quota_window,
    rolling_windows,
    unavailable_window,
    window_value,
)
from ..messages import tr
from ..stats import muse_stats

_MUSE_ACCENT = "#0064e0"

# The panel pills want the shortest thing that still reads as a count ("30k",
# not "30.00K"), and no Muse session reaches a billion tokens.
_MUSE_UNITS = (("M", 1000000), ("k", 1000))


def _compact(v):
    return compact_tokens(v, units=_MUSE_UNITS)


def _error(now, message, details):
    return provider_error("muse", "Muse", _MUSE_ACCENT, now, message, details)


def _window(section):
    """One plan window in the shared shape. The muse-specific rule is that a
    snapshot without a reset time is not a window at all (0% *with* a reset is
    simply a fresh one); every other shape rule belongs to window_value."""
    if not isinstance(section, dict) or epoch_of(section.get("resetAt")) <= 0:
        return unavailable_window()
    return window_value(section.get("pct"), section.get("resetAt"), True)


def normalize_muse(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage")
    # Guard before any res.get(): the raw envelope is replayed from disk in
    # --normalize, so a non-dict here has to be a rendered error, not a
    # traceback.
    if not isinstance(res, dict) or not res:
        return _error(now, "Muse: not installed", {"hasLogin": False, "stats": {"available": False}})

    stats = muse_stats(res.get("stats"), now)
    has_login = res.get("hasLogin") is True
    quota = res.get("quota") if isinstance(res.get("quota"), dict) else {}
    current, weekly = _window(quota.get("current")), _window(quota.get("weekly"))
    quota_error = res.get("quotaError") or ""

    # A paid-for quota must render on its own: someone with a live plan but no
    # local sessions yet would otherwise pay for the call and be told there is
    # nothing to show.
    if not stats.get("available") and not (current["available"] or weekly["available"]):
        return _error(
            now,
            "Muse: no sessions yet" if has_login else "Muse: not logged in",
            {"hasLogin": has_login, "current": current, "weekly": weekly, "quotaError": quota_error, "stats": stats},
        )

    total = num(stats.get("totalTokens"))
    output = num(stats.get("totalOutputTokens"))
    cost = num(stats.get("totalCostUSD"))
    currency = stats.get("currency") or "USD"
    model = stats.get("model") or ""
    calls = num(stats.get("totalModelCalls"))
    tokens = _compact(total)

    # Plan windows only exist here when the user opted into the billed call
    # (providers/muse_quota.py); everything else on this tab is free and local.
    windows = []
    for key, label, w, series in (("muse_current", "Current", current, "mc"), ("muse_weekly", "Weekly", weekly, "mw")):
        if w["available"]:
            windows.append(quota_window(key, label, w, tr("%1% used", jround(w["pct"])), history=series))

    if stats.get("available"):
        windows.append(
            flat_window(
                "muse_tokens",
                tr("Tokens"),
                0,
                0,
                tokens,
                False,
                note=tr("%1 out · %2 calls", _compact(output), int(calls)) if calls else _compact(output) + " out",
            )
        )
    if cost > 0:
        windows.append(flat_window("muse_spend", tr("Spend (est.)"), 0, 0, money(cost, currency), False, note=model))

    # The headline is the plan window when there is one to show, and the
    # lifetime total otherwise — Muse is the only provider that can be in
    # either state depending on a setting.
    headline = current if current["available"] else weekly
    pct = headline["pct"] if headline["available"] else 0

    tooltip = tr("Muse tokens: %1", tokens)
    if cost > 0:
        tooltip += f"\nSpend (est.): {money(cost, currency)}"
    if headline["available"]:
        tooltip = f"Muse {'Current' if current['available'] else 'Weekly'}: {jround(pct)}% used\n" + tooltip

    r = provider_base("muse", "Muse", _MUSE_ACCENT, now)
    r["summary"] = {
        "pct": pct,
        "text": f"{jround(pct)}%" if headline["available"] else tokens,
        "detail": model if model else tr("%1 sessions", int(num(stats.get("totalSessions")))),
        "hasChart": True,
    }
    r["quotaWindows"] = windows
    r["slots"] = [{"pct": pct, "color": _MUSE_ACCENT, "text": None if headline["available"] else tokens, "tooltip": tooltip}]
    r["chartWindows"] = monthly_window("muse", "mu", True)
    r["historyValues"] = {"mu": total}
    if current["available"] or weekly["available"]:
        r["chartWindows"] = rolling_windows("muse_session", "muse_day", "muse_weekly", "mc", "mw", current, weekly)
        if current["available"]:
            r["historyValues"]["mc"] = current["pct"]
        if weekly["available"]:
            r["historyValues"]["mw"] = weekly["pct"]
    # Only what is not already in `stats`: the totals, the model and the
    # currency are read from there by the frontends rather than restated here,
    # so one number never appears twice in an envelope.
    r["details"] = {
        "hasLogin": has_login,
        "email": res.get("email") or "",
        "fullName": res.get("fullName") or "",
        "current": current,
        "weekly": weekly,
        "quotaError": quota_error,
        "stats": stats,
    }
    return r
