from ..contract import (
    account,
    flat_window,
    jround,
    monthly_window,
    note_section,
    num,
    pct_clamp,
    plan_chip,
    provider_base,
    provider_error,
    sections,
)
from ..messages import lines, tr

ACCENT = "#e6e6e6"


def normalize_zed(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage")
    res = res if isinstance(res, dict) else {}

    if res.get("deferred"):
        result = provider_base("zed", "Zed", ACCENT, now)
        result["stale"] = True
        result["summary"] = {"pct": 0, "text": "…", "detail": tr("Open Zed Usage to load account data"), "hasChart": False}
        result["slots"] = [{"pct": 0, "color": ACCENT, "text": "…", "tooltip": tr("Open Zed Usage to load account data")}]
        result["details"] = {
            "available": False,
            "deferred": True,
            "agentUsage": {"available": False, "source": "account-api", "usedUSD": None, "includedUSD": None},
        }
        return result

    if not res:
        return provider_error("zed", "Zed", ACCENT, now, tr("Zed: sign in to the Zed editor first"), {"available": False})
    if res.get("error") is not None:
        return provider_error("zed", "Zed", ACCENT, now, tr("Zed: %1", res["error"]), {"available": False})

    plan = res.get("plan") or ""
    used, limit = res.get("used"), res.get("limit")
    reset_at = num(res.get("resetAt"))
    unlimited = res.get("unlimited") is True
    r = provider_base("zed", "Zed", ACCENT, now)
    r["account"] = account("Zed", [plan_chip(plan)])
    rows = []
    if not unlimited and isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        pct = pct_clamp(used / limit * 100)
        rows.append(flat_window("zed_predictions", tr("Edit predictions"), pct, reset_at, tr("%1 / %2", jround(used), jround(limit)), True))
    elif unlimited:
        rows.append(flat_window("zed_predictions", tr("Edit predictions"), 0, reset_at, tr("Unlimited"), False))
    if not rows:
        return provider_error("zed", "Zed", ACCENT, now, tr("Zed: no usage figures in the account response"), {"available": False, "plan": plan})
    pct = rows[0]["pct"]
    r["summary"] = {
        "pct": pct,
        "text": f"{jround(pct)}%" if rows[0]["showMeter"] else "∞",
        "detail": plan,
        "hasChart": rows[0]["showMeter"],
    }
    r["quotaWindows"] = rows
    r["slots"] = [
        {
            "pct": pct,
            "color": ACCENT,
            "text": None,
            "tooltip": lines("Zed", tr("Plan: %1", plan) if plan else "", tr("%1: %2", rows[0]["label"], rows[0]["detail"])),
        }
    ]
    r["sections"] = sections(
        note_section(tr("Overdue invoices on this account — check your Zed billing page.")) if res.get("overdue") else None,
        note_section(tr("Uses the Zed editor's own login on macOS, Linux and Windows.")),
    )
    if rows[0]["showMeter"]:
        r["chartWindows"] = monthly_window("zed", "zd", False)
        r["historyValues"] = {"zd": pct}
    r["details"] = {"available": True, "plan": plan, "used": used, "limit": limit, "unlimited": unlimited, "resetAt": reset_at, "untested": False}
    if isinstance(res.get("agentUsage"), dict):
        r["details"]["agentUsage"] = res["agentUsage"]
    return r
