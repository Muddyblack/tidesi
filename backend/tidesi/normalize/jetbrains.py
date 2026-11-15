from ..contract import (
    account,
    chip,
    fact,
    facts_section,
    flat_window,
    jround,
    monthly_window,
    note_section,
    num,
    pct_clamp,
    provider_base,
    provider_error,
    sections,
)
from ..messages import lines, tr

ACCENT = "#e6e6e6"


def _amount(value):
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def normalize_jetbrains(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage")
    res = res if isinstance(res, dict) else {}
    hint = tr("Open a JetBrains IDE signed in to JetBrains AI once so it records your quota.")

    if not res:
        r = provider_error("jetbrains", "JetBrains AI", ACCENT, now, tr("JetBrains AI: no IDE quota file found"), {"available": False})
        r["sections"] = sections(note_section(hint))
        return r
    if res.get("error") is not None:
        r = provider_error("jetbrains", "JetBrains AI", ACCENT, now, tr("JetBrains AI: %1", res["error"]), {"available": False})
        r["sections"] = sections(note_section(hint))
        return r

    used, maximum, remaining = num(res.get("used")), num(res.get("maximum")), num(res.get("available"))
    top_up = num(res.get("topUpAvailable"))
    reset_at = num(res.get("resetAt"))
    pct = pct_clamp(used / maximum * 100) if maximum > 0 else 0
    detail = tr("%1 / %2 credits", _amount(used), _amount(maximum))

    r = provider_base("jetbrains", "JetBrains AI", ACCENT, now)
    r["account"] = account(res.get("ide") or "", [chip(res.get("state"), "muted")] if res.get("state") not in ("", "Available", None) else [])
    r["summary"] = {"pct": pct, "text": f"{jround(pct)}%", "detail": detail, "hasChart": True}
    r["quotaWindows"] = [flat_window("jetbrains", tr("Monthly credits"), pct, reset_at, detail, True, history="jb")]
    r["slots"] = [
        {
            "pct": pct,
            "color": ACCENT,
            "text": None,
            "tooltip": lines("JetBrains AI", tr("Credits: %1", detail), tr("Top-up credits: %1", _amount(top_up)) if top_up > 0 else ""),
        }
    ]
    r["sections"] = sections(
        facts_section(
            [
                fact(tr("Remaining"), _amount(remaining), "danger" if pct >= 90 else "warn" if pct >= 70 else ""),
                fact(tr("Top-up credits"), _amount(top_up), "good") if top_up > 0 else None,
            ],
            tinted=True,
        ),
        note_section(tr("Read from the IDE's own quota file. No key or network request needed; it updates while an IDE is running.")),
    )
    r["chartWindows"] = monthly_window("jetbrains", "jb", False)
    r["historyValues"] = {"jb": pct}
    r["details"] = {
        "available": True,
        "ide": res.get("ide") or "",
        "used": used,
        "maximum": maximum,
        "remaining": remaining,
        "topUpAvailable": top_up,
        "pct": pct,
        "resetAt": reset_at,
        "recordedAt": num(res.get("recordedAt")),
    }
    return r
