from ..contract import (
    account,
    bar_row,
    bars_section,
    compact_tokens,
    epoch_of,
    flat_window,
    jround,
    monthly_window,
    note_section,
    num,
    pct_clamp,
    plan_chip,
    provider_base,
    provider_error,
    remote_info,
    sections,
)
from ..messages import lines, tr
from ..stats import antigravity_stats

REMOTE_URL = "https://antigravity.google.com/"


def _family(m):
    name = (m.get("label") or m.get("modelId") or "").lower()
    return "gemini" if ("gemini" in name or "google" in name) else "external"


def _reset_stamp(epoch):
    """ "Oct 7, 20:31" in local time, for a tooltip."""
    if not epoch:
        return ""
    import datetime

    dt = datetime.datetime.fromtimestamp(epoch)
    return f"{dt:%b} {dt.day}, {dt:%H:%M}"


def _model_tooltip(m):
    return lines(
        m["displayName"],
        tr("%1% used", jround(m["usedPct"])),
        tr("⚠ Quota exhausted") if m["isExhausted"] else "",
        tr("Resets: %1", _reset_stamp(m["resetAt"])) if m["resetAt"] else "",
    )


def _avg(values):
    return (sum(values) / len(values)) if values else 0


def _remote(remote):
    if not remote:
        return None
    if isinstance(remote.get("instances"), list):
        instances = []
        labels = {"online": tr("Online"), "offline": tr("Offline"), "idle": tr("Idle"), "unknown": tr("Unknown")}
        for device in remote["instances"]:
            state = device.get("state", "unknown")
            detail = labels.get(state, labels["unknown"])
            updated = epoch_of(device.get("updatedAt"))
            if updated:
                detail += " · " + tr("Updated %1", _reset_stamp(updated))
            instances.append({"name": device["name"], "state": state, "detail": detail, "url": device.get("url") or REMOTE_URL})
        online = sum(row["state"] == "online" for row in instances)
        return remote_info(
            "online" if online else "offline",
            tr("Remote devices"),
            tr("%1 online · %2 devices", online, len(instances)) if instances else tr("No remote devices registered for this account."),
            REMOTE_URL,
            tr("Open antigravity.google.com ↗"),
            tr("Select a device to open it in Antigravity."),
            instances=instances,
        )
    if remote.get("connected"):
        state, title, detail = "online", tr("Remote Control is online"), tr("This device can be reached from your other devices.")
    elif remote.get("enabled"):
        state, title, detail = "offline", tr("Remote Control is offline"), tr("Remote Control is on, but this device is not connected right now.")
    else:
        state = "off"
        title = tr("Remote Control is off")
        detail = tr("Turn it on in Antigravity's settings to work with local agents from another device.")
    return remote_info(
        state,
        title,
        detail,
        REMOTE_URL,
        tr("Open antigravity.google.com ↗"),
        tr("Device list unavailable. Showing this desktop app's local status; open the website to see all devices."),
    )


def normalize_antigravity(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage") or {}
    # Local history from the transcripts, shown even when the IDE is not running.
    stats = antigravity_stats(raw["inputs"].get("stats"), now)

    if not isinstance(res, dict) or not res:
        return provider_error("antigravity", "Antigravity", "#4285f4", now, tr("Antigravity not configured"), {"stats": stats})

    if res.get("error") is not None:
        first_line = (res["error"] or "").split("\n")[0]
        if "Antigravity is not running" in first_line:
            first_line = "Antigravity is not running in IDE"
        return provider_error("antigravity", "Antigravity", "#4285f4", now, first_line, {"stats": stats})

    models = []
    for m in res.get("models") or []:
        rem = m.get("remainingPercentage")
        rem = rem if isinstance(rem, (int, float)) and not isinstance(rem, bool) else None
        models.append(
            {
                "modelId": m.get("modelId") or "unknown",
                "displayName": m.get("label") or m.get("modelId") or "unknown",
                "hasQuota": rem is not None,
                "usedPct": pct_clamp((1 - rem) * 100) if rem is not None else 0,
                "resetTime": m.get("resetTime") or "",
                "resetAt": epoch_of(m.get("resetTime") or ""),
                "isExhausted": m.get("isExhausted") is True,
                "family": _family(m),
            }
        )

    quoted = [m for m in models if m["hasQuota"]]
    pct = _avg([m["usedPct"] for m in quoted])
    g = [m for m in quoted if m["family"] == "gemini"]
    e = [m for m in quoted if m["family"] == "external"]
    gpct = _avg([m["usedPct"] for m in g])
    epct = _avg([m["usedPct"] for m in e])
    reset_ats = [m["resetAt"] for m in models if m["resetAt"] > 0]
    earliest = min(reset_ats) if reset_ats else 0

    groups = []
    for key in ("gemini", "external"):
        group = [m for m in models if m["family"] == key]
        if not group:
            continue
        group_quoted = [m for m in group if m["hasQuota"]]
        group_resets = [m["resetAt"] for m in group if m["resetAt"] > 0]
        groups.append(
            {
                "key": key,
                "label": tr("Gemini Models") if key == "gemini" else tr("Claude & GPT Models"),
                "usedPct": _avg([m["usedPct"] for m in group_quoted]),
                "resetAt": min(group_resets) if group_resets else 0,
                "isExhausted": any(m["isExhausted"] for m in group),
                "models": sorted(m["modelId"] for m in group),
            }
        )

    credits = res.get("promptCredits") or {}
    plan = res.get("planType") or ("LOCAL" if res.get("method") == "local" else "CLOUD")

    r = provider_base("antigravity", "Antigravity", "#4285f4", now)
    r["summary"] = {"pct": pct, "text": f"{jround(pct)}%", "detail": plan, "hasChart": True}
    remote = res.get("remote") if isinstance(res.get("remote"), dict) else None
    r["account"] = account(
        res.get("email") or tr("Gemini Code Assist"),
        [plan_chip(plan)],
    )
    # A family holding several models gets a header over per-model bars (the
    # IDE's "Gemini Models" / "Claude & GPT Models" grouping); a family of one
    # (all the agy CLI reports) is simply a row, as a header over a sub-row
    # with the same name and number would print everything twice.
    per_model = any(len(g["models"]) > 1 for g in groups)
    monthly = num(credits.get("monthly"))
    available = num(credits.get("available"))
    quota_windows = []
    if monthly > 0:
        # Prompt credits are their own pool, the headline of the tab.
        qw = flat_window(
            "credits",
            tr("Prompt Credits"),
            (1 - available / monthly) * 100,
            earliest,
            tr("%1 / %2 left", available, compact_tokens(monthly)),
            True,
        )
        qw["color"] = "#4285f4"
        quota_windows.append(qw)
    elif models and (not groups or per_model):
        # The average says something the family rows do not.
        qw = flat_window("overall", tr("Overall Quota"), pct, earliest, "", True)
        qw["color"] = "#4285f4"
        quota_windows.append(qw)
    if not per_model:
        for grp in groups:
            qw = flat_window(grp["key"], grp["label"], grp["usedPct"], grp["resetAt"], "", True)
            qw["color"] = "#4285f4" if grp["key"] == "gemini" else "#34a853"
            quota_windows.append(qw)
    r["quotaWindows"] = quota_windows
    by_id = {m["modelId"]: m for m in models}
    r["sections"] = sections(
        bars_section(
            [
                {
                    "label": grp["label"],
                    "color": "#4285f4" if grp["key"] == "gemini" else "#34a853",
                    "pct": grp["usedPct"],
                    "resetAt": grp["resetAt"],
                    "rows": [
                        bar_row(by_id[mid]["displayName"], by_id[mid]["usedPct"], by_id[mid]["isExhausted"], _model_tooltip(by_id[mid]))
                        for mid in grp["models"]
                        if mid in by_id
                    ],
                }
                for grp in groups
            ],
            title=tr("Model Quotas"),
        )
        if per_model
        else None,
        note_section(tr("Average quota usage across Gemini models")) if False else None,
    )
    r["slots"] = [
        {
            "pct": gpct,
            "color": "#4285f4",
            "text": None,
            "tooltip": lines(tr("Gemini (Google) quota: %1%", jround(gpct)), tr("Plan: %1", plan) if plan != "" else ""),
        },
        {
            "pct": epct,
            "color": "#34a853",
            "text": None,
            "tooltip": lines(tr("External models quota: %1%", jround(epct)), tr("Plan: %1", plan) if plan != "" else ""),
        },
    ]
    r["chartWindows"] = monthly_window("antigravity", "ag", False)
    history_values = {"ag": pct}
    if g:
        history_values["agg"] = gpct
    if e:
        history_values["age"] = epct
    r["historyValues"] = history_values
    r["details"] = {
        # Present only where Antigravity 2 keeps a Remote Control log; the tab
        # for it shows up with it.
        "remote": _remote(remote),
        "email": res.get("email") or "",
        "planType": plan,
        "promptCreditsMonthly": num(credits.get("monthly")),
        "promptCreditsAvailable": num(credits.get("available")),
        "pct": pct,
        "googlePct": gpct,
        "externalPct": epct,
        "resetAt": earliest,
        "models": {
            m["modelId"]: {
                "displayName": m["displayName"],
                "usedPct": m["usedPct"],
                "resetTime": m["resetTime"],
                "resetAt": m["resetAt"],
                "isExhausted": m["isExhausted"],
                "hasQuota": m["hasQuota"],
            }
            for m in models
        },
        "groups": groups,
        "stats": stats,
    }
    return r
