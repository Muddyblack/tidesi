from ..contract import (
    account,
    epoch_of,
    fact,
    facts_section,
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
from ..stats import kiro_stats


def _sign_in_note():
    return note_section(
        tr(
            "Sign in to kiro-cli (kiro-cli login), or open the Kiro IDE and sign in once so the widget can read its usage snapshot."
            " A kiro-cli login expires about an hour after the CLI last ran — start kiro-cli to renew it."
        )
    )


def _level(pct):
    return "danger" if pct >= 90 else "warn" if pct >= 70 else ""


def normalize_kiro(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage") or {}
    # Local history from kiro-cli and the IDE, shown even without a login.
    stats = kiro_stats(raw["inputs"].get("stats"), now)

    if not isinstance(res, dict) or not res:
        r = provider_error("kiro", "Kiro", "#8b5cf6", now, tr("Kiro: no local usage data found"), {"available": False, "stats": stats})
        r["sections"] = sections(_sign_in_note())
        return r
    if res.get("error") is not None:
        r = provider_error("kiro", "Kiro", "#8b5cf6", now, tr("Kiro: %1", res["error"]), {"available": False, "stats": stats})
        r["sections"] = sections(_sign_in_note())
        return r

    pct = pct_clamp(num(res.get("percentageUsed")))
    used = num(res.get("currentUsage"))
    limit = num(res.get("usageLimit"))
    reset_at = epoch_of(res.get("resetDate") or "")
    available = limit > 0 or used > 0
    detail = tr("%1 / %2 credits", used, limit)
    plan = res.get("planType") or ""

    r = provider_base("kiro", "Kiro", "#8b5cf6", now)
    r["ok"] = available
    r["error"] = "" if available else tr("Kiro: usage snapshot is empty")
    r["account"] = account("Kiro", [plan_chip(plan)])
    unit = str(res.get("displayNamePlural") or "Credits")
    symbol = res.get("currencySymbol") or "$"
    remaining, overages = num(res.get("remaining")), num(res.get("currentOverages"))
    charges, rate = num(res.get("overageCharges")), num(res.get("overageRate"))
    r["sections"] = sections(
        facts_section(
            [
                fact(tr("Current Usage"), tr("%1 %2", f"{used:.2f}", unit.lower()), "accent"),
                fact(tr("Remaining"), f"{remaining:.2f}", _level(pct)) if limit > 0 else None,
                fact(tr("Overage"), f"{symbol}{charges:.2f} ({overages:.2f})", "warn") if overages > 0 or charges > 0 else None,
                fact(tr("Overage Rate"), f"{symbol}{rate:.2f}/{str(res.get('displayName') or 'credit').lower()}") if rate > 0 else None,
            ],
            tinted=True,
        )
        if available
        else None,
        note_section(
            tr("Read live with the kiro-cli login. No API key required.")
            if res.get("source") == "cli"
            else tr("Read locally from Kiro app state. No API key or network request required.")
        ),
    )
    r["summary"] = {"pct": pct, "text": f"{jround(pct)}%", "detail": plan, "hasChart": True}
    r["quotaWindows"] = [flat_window("kiro", tr("Monthly credits"), pct, reset_at, detail, True)]
    r["slots"] = [
        {
            "pct": pct,
            "color": "#8b5cf6",
            "text": None,
            "tooltip": lines("Kiro", tr("Plan: %1", plan.upper()) if plan != "" else "", tr("Credits: %1", detail)),
        }
    ]
    r["chartWindows"] = monthly_window("kiro", "kr", False) if available else []
    r["historyValues"] = {"kr": pct} if available else {}
    r["details"] = {
        "available": available,
        "planType": plan,
        "displayName": res.get("displayName") or "Credit",
        "displayNamePlural": res.get("displayNamePlural") or "Credits",
        "currentUsage": used,
        "usageLimit": limit,
        "pct": pct,
        "remaining": num(res.get("remaining")),
        "currentOverages": num(res.get("currentOverages")),
        "overageCap": num(res.get("overageCap")),
        "overageCharges": num(res.get("overageCharges")),
        "overageRate": num(res.get("overageRate")),
        "currencyCode": res.get("currencyCode") or "USD",
        "currencySymbol": res.get("currencySymbol") or "$",
        "resetAt": reset_at,
        # "cli" = live from kiro-cli's login, "ide" = the Kiro IDE's snapshot.
        "source": res.get("source") if res.get("source") in ("cli", "ide") else "ide",
        "stats": stats,
    }
    return r
