from ..contract import account, fact, facts_section, flat_window, note_section, num, plan_chip, provider_base, provider_error, sections
from ..messages import lines, tr

ACCENT = "#ff7a3d"


def normalize_coderabbit(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage")
    res = res if isinstance(res, dict) else {}

    if not res:
        return provider_error("coderabbit", "CodeRabbit", ACCENT, now, tr("CodeRabbit: CLI not found"), {"available": False})
    if res.get("error") is not None:
        return provider_error("coderabbit", "CodeRabbit", ACCENT, now, tr("CodeRabbit: %1", res["error"]), {"available": False})

    reviews = res.get("reviews")
    reset_at = num(res.get("resetAt"))
    plan = res.get("plan") or ""
    billing = res.get("billing") or ""
    r = provider_base("coderabbit", "CodeRabbit", ACCENT, now)
    r["account"] = account(res.get("organization") or res.get("user") or "", [plan_chip(plan)])
    count_text = str(reviews) if isinstance(reviews, int) else "—"
    r["summary"] = {"pct": 0, "text": count_text, "detail": tr("reviews this period"), "hasChart": False}
    r["quotaWindows"] = [flat_window("coderabbit_reviews", tr("Your reviews"), 0, reset_at, count_text, False)]
    r["slots"] = [{"pct": 0, "color": ACCENT, "text": count_text, "tooltip": lines("CodeRabbit", tr("Your reviews: %1", count_text))}]
    r["sections"] = sections(
        facts_section([fact(tr("User"), res["user"]) if res.get("user") else None, fact(tr("Usage billing"), billing) if billing else None]),
        note_section(tr("Read by running the CodeRabbit CLI's own usage report. CodeRabbit publishes a count, not a quota.")),
    )
    r["details"] = {
        "available": True,
        "reviews": reviews,
        "organization": res.get("organization") or "",
        "plan": plan,
        "billing": billing,
        "resetAt": reset_at,
    }
    return r
