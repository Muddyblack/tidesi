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
    sections,
)
from ..messages import lines, tr

ACCENT = "#f4e04d"


def _pct(used, total):
    # An empty prepaid balance is an exhausted one, not an unknown one.
    return pct_clamp(used / total * 100) if total > 0 else 100


def normalize_kilo(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage")
    res = res if isinstance(res, dict) else {}

    if not res:
        return provider_error("kilo", "Kilo", ACCENT, now, tr("Kilo: no API key or CLI login found"), {"hasKey": False})
    if res.get("error") is not None:
        return provider_error("kilo", "Kilo", ACCENT, now, tr("Kilo: %1", res["error"]), {"hasKey": True})

    credits = res.get("credits") if isinstance(res.get("credits"), dict) else None
    sub = res.get("pass") if isinstance(res.get("pass"), dict) else None
    rows = []
    if credits:
        used, total = num(credits.get("used")), num(credits.get("total"))
        rows.append(("kilo_credits", tr("Credits"), _pct(used, total), 0, tr("%1 / %2", money(used, "USD"), money(total, "USD")), True))
    if sub:
        used, total = num(sub.get("used")), num(sub.get("total"))
        bonus = num(sub.get("bonus"))
        detail = tr("%1 / %2", money(used, "USD"), money(total, "USD"))
        if bonus > 0:
            detail = tr("%1 (incl. %2 bonus)", detail, money(bonus, "USD"))
        rows.append(("kilo_pass", tr("Kilo Pass"), _pct(used, total), num(sub.get("resetAt")), detail, True))

    top = max(rows, key=lambda row: row[2])
    r = provider_base("kilo", "Kilo", ACCENT, now)
    r["account"] = account("Kilo", [plan_chip(sub["tier"])] if sub else [])
    r["summary"] = {"pct": top[2], "text": f"{jround(top[2])}%", "detail": top[1], "hasChart": True}
    r["quotaWindows"] = [flat_window(key, label, pct, reset, detail, meter) for key, label, pct, reset, detail, meter in rows]
    r["slots"] = [{"pct": top[2], "color": ACCENT, "text": None, "tooltip": lines("Kilo", *(tr("%1: %2", row[1], row[4]) for row in rows))}]
    remaining = num(credits.get("remaining")) if credits else None
    r["sections"] = sections(
        facts_section([fact(tr("Credit balance"), money(remaining, "USD"), "danger" if rows[0][2] >= 90 and credits else "")], tinted=True)
        if credits
        else None,
        note_section(tr("Read with your Kilo CLI login.") if res.get("source") == "cli" else tr("Read with your Kilo API key.")),
    )
    r["chartWindows"] = monthly_window("kilo", "kl", False)
    r["historyValues"] = {"kl": top[2]}
    r["details"] = {"hasKey": True, "source": res.get("source") or "", "credits": credits, "pass": sub}
    return r
