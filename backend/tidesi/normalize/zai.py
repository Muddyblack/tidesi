import datetime
import math

from ..contract import compact_tokens, flat_window, jround, monthly_window, num, pct_clamp, provider_base, provider_error
from ..messages import tr

# Anything at or above this, read as a duration, would be more than 31 years —
# so it is an absolute epoch instead. Live z.ai responses put an absolute
# millisecond timestamp in `nextResetTime`, despite the field name; treating it
# as a duration and adding it to `now` produced reset dates in the 2080s, which
# went unnoticed because the formatted string carries no year.
_ABSOLUTE_MS_FLOOR = 1000000000000


# Two decimals and an uppercase "K" to match how the vendor's dashboard prints
# the same figure — this number exists to be checked against that page, and a
# differently rounded one invites the reader to wonder which is wrong.
_ZAI_UNITS = (("B", 1000000000), ("M", 1000000), ("K", 1000))


def _compact(n):
    return compact_tokens(n, decimals=2, units=_ZAI_UNITS, trim_zeros=False)


def _today_label(today):
    """ "Today (Aug 12)" — the reset column already carries the date this rolls
    over to, and a bare "Today" next to tomorrow's date reads as a mismatch.
    Naming the day being summed removes the question."""
    date = str(today.get("date") or "")
    try:
        d = datetime.datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return tr("Today")
    return tr("Today (%1 %2)", d.strftime("%b"), d.day)


def _today_detail(today):
    """The value itself: "41.18M tokens", or "" if it did not come back."""
    return tr("%1 tokens", _compact(today["tokens"])) if today.get("tokens") is not None else ""


def _today_note(today):
    """The aside: "370 calls". Tokens are what the quota is spent in, so they
    are the value; the call count is context for it."""
    return tr("%1 calls", num(today["calls"])) if today.get("calls") is not None else ""


def _reset_at(value, now):
    """Reset epoch in seconds from a value that may be absolute or relative."""
    ms = num(value)
    if ms <= 0:
        return 0
    if ms >= _ABSOLUTE_MS_FLOOR:
        return math.floor(ms / 1000)
    return math.floor(now + ms / 1000)


def normalize_zai(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage") or {}

    if not isinstance(res, dict) or not res:
        return provider_error("zai", "Z.AI", "#126ef4", now, tr("Z.AI: no token configured"), {"hasKey": False, "keyValid": False})
    if res.get("error") is not None:
        return provider_error(
            "zai",
            "Z.AI",
            "#126ef4",
            now,
            tr("Z.AI: %1", res["error"]),
            {"hasKey": res.get("hasKey") is True, "keyValid": res.get("keyValid") is True},
        )

    token_pct = pct_clamp(num(res.get("tokenPct")))
    token2_pct = pct_clamp(num(res.get("token2Pct")))
    tools_pct = pct_clamp(num(res.get("toolsPct")))
    token_reset = _reset_at(res.get("tokenResetMs"), now)
    token2_reset = _reset_at(res.get("token2ResetMs"), now)
    tools_reset = _reset_at(res.get("toolsResetMs"), now)
    token_detail = (
        tr("%1 / %2 tokens", num(res.get("tokenUsed")), num(res.get("tokenLimit")))
        if res.get("tokenUsed") is not None and res.get("tokenLimit") is not None
        else ""
    )
    tools_detail = tr("%1 remaining", num(res.get("toolsRemaining"))) if res.get("toolsRemaining") is not None else ""

    r = provider_base("zai", "Z.AI", "#126ef4", now)
    r["summary"] = {"pct": token_pct, "text": f"{jround(token_pct)}%", "detail": res.get("level") or "", "hasChart": True}
    today = res.get("today") if isinstance(res.get("today"), dict) else None
    today_detail = _today_detail(today) if today else ""

    r["quotaWindows"] = [
        flat_window("zai_tokens", tr("5-hour tokens"), token_pct, token_reset, token_detail, True, history="za", period=18000000),
        flat_window("zai_tokens_long", tr("7-day tokens"), token2_pct, token2_reset, "", True, history="za7", period=604800000),
        flat_window("zai_tools", tr("Monthly tools"), tools_pct, tools_reset, tools_detail, True, history="zat", period="month"),
    ]
    if today_detail:
        # Consumption so far, not a share of anything, so it carries a value
        # instead of a meter. The reset column holds the date change, so a
        # total that has stopped growing (see _report_day) has a visible
        # horizon instead of looking stuck.
        r["quotaWindows"].append(
            flat_window("zai_today", _today_label(today), 0, num(today.get("rollsOverAt")), today_detail, False, note=_today_note(today))
        )
    r["slots"] = [
        {"pct": token_pct, "color": "#126ef4", "text": None, "tooltip": tr("Z.AI tokens (5h): %1%", jround(token_pct))},
        {"pct": token2_pct, "color": "#3b82f6", "text": None, "tooltip": tr("Z.AI tokens (7d): %1%", jround(token2_pct))},
        {"pct": tools_pct, "color": "#60a5fa", "text": None, "tooltip": tr("Z.AI tools: %1%", jround(tools_pct))},
    ]
    r["chartWindows"] = monthly_window("zai", "za", False)
    r["historyValues"] = {"za": token_pct, "za7": token2_pct, "zat": tools_pct}
    r["details"] = {
        "hasKey": res.get("hasKey") is True,
        "keyValid": res.get("keyValid") is True,
        "level": res.get("level") or "",
        "token": {
            "pct": token_pct,
            "used": num(res.get("tokenUsed")) if res.get("tokenUsed") is not None else None,
            "limit": num(res.get("tokenLimit")) if res.get("tokenLimit") is not None else None,
            "resetAt": token_reset,
        },
        "tokenLong": {
            "pct": token2_pct,
            "resetAt": token2_reset,
        },
        "tools": {
            "pct": tools_pct,
            "remaining": num(res.get("toolsRemaining")) if res.get("toolsRemaining") is not None else None,
            "resetAt": tools_reset,
        },
        "models": res.get("models") or [],
        "today": {
            "available": today is not None,
            "date": (today or {}).get("date") or "",
            "rollsOverAt": num((today or {}).get("rollsOverAt")),
            "tokens": num((today or {}).get("tokens")) if (today or {}).get("tokens") is not None else None,
            "calls": num((today or {}).get("calls")) if (today or {}).get("calls") is not None else None,
            "models": [
                {"name": m.get("name") or "", "tokens": num(m.get("tokens"))} for m in ((today or {}).get("models") or []) if isinstance(m, dict)
            ],
            "tools": (today or {}).get("tools") or {},
        },
    }
    return r
