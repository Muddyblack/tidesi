from ..contract import (
    account,
    fact,
    facts_section,
    flat_window,
    jround,
    money,
    monthly_window,
    note_section,
    num,
    pct_clamp,
    plan_chip,
    provider_base,
    provider_error,
    rolling_windows,
    sections,
)
from ..messages import lines, tr

LABEL = "Command Code"
ACCENT = "#e6e6e6"


def _pct(used, total):
    return pct_clamp(used / total * 100) if total > 0 else 0


def normalize_commandcode(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage")
    res = res if isinstance(res, dict) else {}

    if not res:
        return provider_error("commandcode", LABEL, ACCENT, now, tr("Command Code: no API key or CLI login found"), {"hasKey": False})
    if res.get("error") is not None:
        return provider_error("commandcode", LABEL, ACCENT, now, tr("Command Code: %1", res["error"]), {"hasKey": True})

    def window(name):
        value = res.get(name)
        return value if isinstance(value, dict) else None

    five, weekly, monthly = window("fiveHour"), window("weekly"), window("monthly")
    rows = []  # key, label, pct, resetAt, detail, history key, period
    for key, label, value, total, history, period in (
        ("commandcode_5h", tr("5-hour"), five, "cap", "cmd5", 18000000),
        ("commandcode_weekly", tr("Weekly"), weekly, "cap", "cmd7", 604800000),
        ("commandcode_monthly", tr("Monthly credits"), monthly, "total", "cmdm", "month"),
    ):
        if value:
            used, limit = num(value.get("used")), num(value.get(total))
            detail = tr("%1 / %2", money(used, "USD"), money(limit, "USD"))
            rows.append((key, label, _pct(used, limit), num(value.get("resetAt")), detail, history, period))

    plan = res.get("plan") if isinstance(res.get("plan"), str) else ""
    extra = num(res.get("extra"))
    r = provider_base("commandcode", LABEL, ACCENT, now)
    r["account"] = account(LABEL, [plan_chip(plan)])
    if rows:
        top = max(rows, key=lambda row: row[2])
        r["summary"] = {"pct": top[2], "text": f"{jround(top[2])}%", "detail": top[1], "hasChart": True}
        r["quotaWindows"] = [flat_window(key, label, pct, reset, detail, True, history=h, period=p) for key, label, pct, reset, detail, h, p in rows]
        r["slots"] = [{"pct": top[2], "color": ACCENT, "text": None, "tooltip": lines(LABEL, *(tr("%1: %2", row[1], row[4]) for row in rows))}]
        r["historyValues"] = {row[5]: row[2] for row in rows}
    else:
        # Pay as you go: no windows and no monthly allowance, only a balance.
        r["summary"] = {"pct": 0, "text": money(num(res.get("remaining")) + extra, "USD"), "detail": tr("Credit balance"), "hasChart": False}
        r["slots"] = [{"pct": 0, "color": ACCENT, "text": None, "tooltip": LABEL}]

    def chart(value):
        return (
            {"available": True, "pct": _pct(num(value.get("used")), num(value.get("cap"))), "resetAt": num(value.get("resetAt"))} if value else None
        )

    if five or weekly:
        r["chartWindows"] = rolling_windows("commandcode_5h", "commandcode_24h", "commandcode_7d", "cmd5", "cmd7", chart(five), chart(weekly))
    elif monthly:
        r["chartWindows"] = monthly_window("commandcode", "cmdm", False)
    r["sections"] = sections(
        facts_section(
            [fact(tr("Monthly credits left"), money(num(res.get("remaining")), "USD"))]
            + ([fact(tr("Extra credits"), money(extra, "USD"))] if extra > 0 else []),
            tinted=True,
        ),
        note_section(tr("Read with your Command Code CLI login.") if res.get("source") == "cli" else tr("Read with your Command Code API key.")),
    )
    r["details"] = {
        "hasKey": True,
        "source": res.get("source") or "",
        "plan": plan,
        "fiveHour": five,
        "weekly": weekly,
        "monthly": monthly,
        "extra": extra,
    }
    return r
