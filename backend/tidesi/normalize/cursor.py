from ..contract import flat_window, jround, money, monthly_window, num, pct_clamp, provider_base, provider_error
from ..messages import lines, tr
from ..stats import cursor_stats

ACCENT = "#e6e6e6"


def _ms_epoch(v):
    """Connect-JSON encodes int64 as a string of milliseconds."""
    n = num(v)
    return int(n // 1000) if n > 0 else 0


def normalize_cursor(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage") or {}

    if not isinstance(res, dict) or not res:
        return provider_error(
            "cursor",
            "Cursor",
            ACCENT,
            now,
            tr("Cursor: not signed in — run cursor-agent login"),
            {"loggedIn": False, "source": "", "stats": {"available": False}},
        )
    base_details = {"loggedIn": res.get("loggedIn") is True, "source": res.get("source") or "", "stats": cursor_stats(res.get("stats"), now)}
    if res.get("error") is not None:
        return provider_error("cursor", "Cursor", ACCENT, now, tr("Cursor: %1", res["error"]), base_details)

    usage = res.get("usage") if isinstance(res.get("usage"), dict) else {}
    plan_body = res.get("plan") if isinstance(res.get("plan"), dict) else {}
    plan_info = plan_body.get("planInfo") if isinstance(plan_body.get("planInfo"), dict) else {}
    plan_name = plan_info.get("planName") or ""

    pu = usage.get("planUsage")
    if not isinstance(pu, dict):
        # Enterprise seats get no plan block; cursor-agent says the same.
        return provider_error(
            "cursor", "Cursor", ACCENT, now, tr("Cursor: usage details are not available for this plan"), {**base_details, "planName": plan_name}
        )

    reset_at = _ms_epoch(usage.get("billingCycleEnd")) or _ms_epoch(plan_info.get("billingCycleEnd"))
    # Spend figures are integer cents.
    included = num(pu.get("includedSpend")) / 100
    limit = num(pu.get("limit")) / 100
    total = pct_clamp(num(pu.get("totalPercentUsed")))
    if total == 0 and limit > 0 and included > 0:
        total = pct_clamp(included / limit * 100)
    auto = pct_clamp(num(pu.get("autoPercentUsed")))
    api = pct_clamp(num(pu.get("apiPercentUsed")))
    has_split = "autoPercentUsed" in pu or "apiPercentUsed" in pu

    slu = usage.get("spendLimitUsage") if isinstance(usage.get("spendLimitUsage"), dict) else {}
    od_used = num(slu.get("individualUsed")) / 100
    od_limit = num(slu.get("individualLimit")) / 100

    total_detail = f"{money(included, 'USD')} / {money(limit, 'USD')}" if limit > 0 else tr("%1% of included usage", jround(total))
    windows = [flat_window("cursor_total", tr("Included usage"), total, reset_at, total_detail, True, history="cu")]
    if has_split:
        windows.append(flat_window("cursor_auto", tr("Auto + Composer"), auto, reset_at, tr("%1% used", jround(auto)), True))
        windows.append(flat_window("cursor_api", tr("API models"), api, reset_at, tr("%1% used", jround(api)), True))
    if od_limit > 0 or od_used > 0:
        od_pct = od_used / od_limit * 100 if od_limit > 0 else 0
        od_detail = f"{money(od_used, 'USD')} / {money(od_limit, 'USD')}" if od_limit > 0 else money(od_used, "USD")
        windows.append(flat_window("cursor_on_demand", tr("On-demand"), od_pct, reset_at, od_detail, od_limit > 0))

    r = provider_base("cursor", "Cursor", ACCENT, now)
    r["summary"] = {"pct": total, "text": f"{jround(total)}%", "detail": plan_name, "hasChart": True}
    r["quotaWindows"] = windows
    r["slots"] = [
        {
            "pct": total,
            "color": ACCENT,
            "text": None,
            "tooltip": lines("Cursor", tr("Plan: %1", plan_name) if plan_name else "", tr("Included usage: %1%", jround(total))),
        }
    ]
    r["chartWindows"] = monthly_window("cursor", "cu", False)
    r["historyValues"] = {"cu": total}
    upgrade = plan_body.get("nextUpgrade") if isinstance(plan_body.get("nextUpgrade"), dict) else {}
    r["details"] = {
        **base_details,
        "planName": plan_name,
        "price": plan_info.get("price") or "",
        "totalPct": total,
        "autoPct": auto,
        "apiPct": api,
        "hasSplit": has_split,
        "includedSpend": included,
        "limit": limit,
        "bonusSpend": num(pu.get("bonusSpend")) / 100,
        "remaining": num(pu.get("remaining")) / 100,
        "resetAt": reset_at,
        "cycleStartAt": _ms_epoch(usage.get("billingCycleStart")),
        "onDemandUsed": od_used,
        "onDemandLimit": od_limit,
        "displayMessage": usage.get("displayMessage") or "",
        "nextUpgrade": {"name": upgrade.get("name") or "", "price": upgrade.get("price") or ""},
    }
    # Free/Hobby responses can contain zero billing percentages without any
    # included allowance. Those are not measurements of the agent limit.
    # Likewise, an absent percentage must not become a measured zero.
    free_without_allowance = plan_name.strip().lower() in ("free", "hobby") and limit <= 0
    missing_meter = pu.get("totalPercentUsed") is None and limit <= 0
    if free_without_allowance or missing_meter:
        message = (
            "Cursor: agent usage limit unavailable. The billing response does not provide "
            "a usable quota for this plan; 0% does not mean agent usage is available. "
            "Check Cursor's limit message for availability and reset timing."
        )
        r = provider_error("cursor", "Cursor", ACCENT, now, message, r["details"])
        r["summary"]["detail"] = plan_name
        r["summary"]["hasChart"] = False
    return r
