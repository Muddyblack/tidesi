import datetime

from ..contract import epoch_of, flat_window, jround, monthly_window, num, pct_clamp, provider_base, provider_error
from ..messages import tr
from ..stats import copilot_stats


def next_month_utc(now):
    """Premium requests reset on the first of the following month, UTC."""
    dt = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    y, m = dt.year, dt.month
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return epoch_of(f"{y:04d}-{m:02d}-01T00:00:00Z")


def _reset_at(res, now):
    """/copilot_internal reports the plan's own reset day (a bare date); the
    billing endpoint reports nothing, and the cycle is the calendar month."""
    date = res.get("resetDate")
    if isinstance(date, str) and date != "":
        at = epoch_of(f"{date[0:10]}T00:00:00Z")
        if at > 0:
            return at
    return next_month_utc(now)


def normalize_copilot(raw):
    now = raw["now"]
    inp = raw["inputs"]
    res = inp.get("usage") or {}
    stats = copilot_stats(inp.get("stats"), now)

    if not isinstance(res, dict) or not res:
        return provider_error(
            "copilot", "Copilot", "#8b5cf6", now, tr("Copilot: no token configured"), {"hasKey": False, "keyValid": False, "stats": stats}
        )
    if res.get("error") is not None:
        return provider_error(
            "copilot",
            "Copilot",
            "#8b5cf6",
            now,
            tr("Copilot: %1", res["error"]),
            {"hasKey": res.get("hasKey") is True, "keyValid": res.get("keyValid") is True, "stats": stats},
        )

    pct = pct_clamp(num(res.get("pct")))
    used = num(res.get("used"))
    quota = num(res.get("quota")) if res.get("quota") is not None else 300
    unlimited = res.get("unlimited") is True
    username = res.get("username") or ""
    plan = res.get("plan") or ""
    reset_at = _reset_at(res, now)
    detail = tr("%1 requests · unlimited", used) if unlimited else tr("%1 / %2 requests", used, quota)

    r = provider_base("copilot", "Copilot", "#8b5cf6", now)
    r["summary"] = {
        "pct": pct,
        "text": "∞" if unlimited else f"{jround(pct)}%",
        "detail": f"@{username}" if username != "" else tr("Personal billing"),
        "hasChart": True,
    }
    r["quotaWindows"] = [flat_window("copilot", tr("Premium requests"), pct, reset_at, detail, not unlimited, history="gh", period="month")]
    r["slots"] = [{"pct": pct, "color": "#8b5cf6", "text": None, "tooltip": tr("Copilot premium requests: %1", detail)}]
    r["chartWindows"] = monthly_window("copilot", "gh", False)
    r["historyValues"] = {"gh": pct}
    r["details"] = {
        "hasKey": res.get("hasKey") is True,
        "keyValid": res.get("keyValid") is True,
        "username": username,
        "used": used,
        "quota": quota,
        "unlimited": unlimited,
        "plan": plan,
        "pct": pct,
        "resetAt": reset_at,
        "stats": stats,
    }
    return r
