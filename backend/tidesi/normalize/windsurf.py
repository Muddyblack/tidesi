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
    reset_text,
    sections,
)
from ..messages import lines, tr

ACCENT = "#34e8bb"


def _pool_pct(pool):
    return pct_clamp(num(pool["used"]) / num(pool["total"]) * 100) if isinstance(pool, dict) and num(pool.get("total")) > 0 else None


def normalize_windsurf(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage")
    res = res if isinstance(res, dict) else {}
    hint = tr("Open Windsurf and sign in once so it caches your plan.")

    if not res:
        r = provider_error("windsurf", "Windsurf", ACCENT, now, tr("Windsurf: no local data found"), {"available": False})
        r["sections"] = sections(note_section(hint))
        return r
    if res.get("error") is not None:
        r = provider_error("windsurf", "Windsurf", ACCENT, now, tr("Windsurf: %1", res["error"]), {"available": False})
        r["sections"] = sections(note_section(hint))
        return r

    rows = []  # (key, label, pct, reset_at, detail)
    daily, weekly = res.get("dailyUsedPct"), res.get("weeklyUsedPct")
    if daily is not None:
        rows.append(
            ("windsurf_daily", tr("Daily quota"), pct_clamp(num(daily)), num(res.get("dailyResetAt")), tr("%1% used", jround(pct_clamp(num(daily)))))
        )
    if weekly is not None:
        rows.append(
            (
                "windsurf_weekly",
                tr("Weekly quota"),
                pct_clamp(num(weekly)),
                num(res.get("weeklyResetAt")),
                tr("%1% used", jround(pct_clamp(num(weekly)))),
            )
        )
    for key, label, pool in (
        ("windsurf_messages", tr("Messages"), res.get("messages")),
        ("windsurf_flow", tr("Flow actions"), res.get("flowActions")),
    ):
        pct = _pool_pct(pool)
        # Counters only stand in for a percentage the cache did not carry.
        if pct is not None and (daily is None or weekly is None):
            rows.append((key, label, pct, num(res.get("expiresAt")), tr("%1 / %2", jround(num(pool["used"])), jround(num(pool["total"])))))

    plan = res.get("plan") or ""
    if not rows:
        r = provider_error(
            "windsurf", "Windsurf", ACCENT, now, tr("Windsurf: the cached plan has no quota figures"), {"available": False, "plan": plan}
        )
        r["sections"] = sections(note_section(hint))
        return r

    top = max(rows, key=lambda row: row[2])
    r = provider_base("windsurf", "Windsurf", ACCENT, now)
    r["account"] = account("Windsurf", [plan_chip(plan)])
    r["summary"] = {"pct": top[2], "text": f"{jround(top[2])}%", "detail": plan or top[1], "hasChart": True}
    r["quotaWindows"] = [flat_window(key, label, pct, reset, detail, True) for key, label, pct, reset, detail in rows]
    r["slots"] = [
        {
            "pct": top[2],
            "color": ACCENT,
            "text": None,
            "tooltip": lines("Windsurf", tr("Plan: %1", plan) if plan else "", *(tr("%1: %2", row[1], row[4]) for row in rows)),
        }
    ]
    recorded = num(res.get("recordedAt"))
    r["sections"] = sections(
        note_section(
            tr("Read from Windsurf's local cache, last written %1. It only updates while Windsurf is running.", reset_text(recorded))
            if recorded > 0
            else tr("Read from Windsurf's local cache. It only updates while Windsurf is running.")
        )
    )
    r["chartWindows"] = monthly_window("windsurf", "ws", False)
    r["historyValues"] = {"ws": top[2]}
    r["details"] = {
        "available": True,
        "plan": plan,
        "dailyUsedPct": daily,
        "weeklyUsedPct": weekly,
        "dailyResetAt": num(res.get("dailyResetAt")),
        "weeklyResetAt": num(res.get("weeklyResetAt")),
        "expiresAt": num(res.get("expiresAt")),
        "recordedAt": recorded,
    }
    return r
