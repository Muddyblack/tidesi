from ..contract import fact, facts_section, flat_window, money, monthly_window, num, provider_base, provider_error, sections
from ..messages import tr


def normalize_openrouter(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage") or {}

    if not isinstance(res, dict) or not res:
        return provider_error(
            "openrouter",
            "OpenRouter",
            "#9333ea",
            now,
            tr("OpenRouter: no API key configured"),
            {"hasKey": False, "keyValid": False},
        )
    if res.get("error") is not None:
        return provider_error(
            "openrouter",
            "OpenRouter",
            "#9333ea",
            now,
            res["error"],
            {"hasKey": res.get("hasKey") is True, "keyValid": False},
        )

    def amount(key):
        value = res.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    total_credits, used_credits = amount("creditsTotalUSD"), amount("creditsUsedUSD")
    balance = max(total_credits - used_credits, 0) if total_credits is not None and used_credits is not None else None
    key_unavailable = res.get("keyUnavailable") is True

    usage = num(res.get("usageUSD"))
    limit = res.get("limitUSD")
    limit = limit if isinstance(limit, (int, float)) and not isinstance(limit, bool) else None
    pct = min(usage / limit * 100, 100) if (limit is not None and limit > 0) else 0
    account = res.get("label") or ""

    r = provider_base("openrouter", "OpenRouter", "#9333ea", now)
    r["summary"] = {"pct": pct, "text": money(usage, "USD"), "detail": account, "hasChart": True}
    detail = money(usage, "USD") + (f" / {money(limit, 'USD')}" if limit is not None else " / unlimited")
    r["quotaWindows"] = [flat_window("openrouter", tr("Credit usage"), pct, 0, detail, True, history="or")] if not key_unavailable else []
    if balance is not None:
        r["quotaWindows"].append(flat_window("openrouter_balance", tr("Balance"), 0, 0, money(balance, "USD"), False))
    spend = [
        fact(label, money(value, "USD"))
        for label, value in (
            (tr("Today"), amount("usageDailyUSD")),
            (tr("This week"), amount("usageWeeklyUSD")),
            (tr("This month"), amount("usageMonthlyUSD")),
        )
        if value is not None
    ]
    r["sections"] = sections(facts_section(spend, title=tr("Key spend")))
    tooltip = (
        "OpenRouter"
        + (f"\n{account}" if account != "" else "")
        + f"\nUsed: {money(usage, 'USD')}"
        + (f"\nLimit: {money(limit, 'USD')}" if limit is not None else "")
    )
    r["slots"] = [{"pct": pct, "color": "#9333ea", "text": money(usage, "USD") if usage > 0 else tr("✓ key"), "tooltip": tooltip}]
    if key_unavailable:
        # Only the account balance answered: lead with it, not with a $0 spend.
        r["summary"] = {"pct": 0, "text": money(balance, "USD"), "detail": tr("Balance"), "hasChart": True}
        r["slots"] = [
            {"pct": 0, "color": "#9333ea", "text": money(balance, "USD"), "tooltip": f"OpenRouter\n{tr('Balance')}: {money(balance, 'USD')}"}
        ]
    r["chartWindows"] = monthly_window("openrouter", "or", False)
    r["historyValues"] = {"or": pct} if pct > 0 else {}
    limit_remaining = res.get("limitRemainingUSD")
    limit_remaining = limit_remaining if isinstance(limit_remaining, (int, float)) and not isinstance(limit_remaining, bool) else None
    r["details"] = {
        "hasKey": res.get("hasKey") is True,
        "keyValid": res.get("keyValid") is True,
        "label": account,
        "usageUSD": usage,
        "limitUSD": limit,
        "limitRemainingUSD": limit_remaining,
        "isFreeTier": res.get("isFreeTier") is True,
        "rateLimit": res.get("rateLimit") or {},
        "creditsTotalUSD": total_credits,
        "creditsUsedUSD": used_credits,
        "balanceUSD": balance,
        "usageDailyUSD": amount("usageDailyUSD"),
        "usageWeeklyUSD": amount("usageWeeklyUSD"),
        "usageMonthlyUSD": amount("usageMonthlyUSD"),
        "keyUnavailable": key_unavailable,
    }
    return r
