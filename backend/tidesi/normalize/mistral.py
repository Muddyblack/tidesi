from ..contract import account, chip, flat_window, money, monthly_window, num, provider_base, provider_error
from ..messages import tr


def normalize_mistral(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage") or {}

    if not isinstance(res, dict) or not res:
        return provider_error(
            "mistral",
            "Mistral",
            "#ff7000",
            now,
            tr("Mistral: no API key configured"),
            {"hasKey": False, "keyValid": False},
        )

    cost = num(res.get("vibeTotalCost"))
    valid = res.get("keyValid") is True
    pct = min(cost / 50 * 100, 100)
    models = res.get("availableModels") or []
    details = {
        "hasKey": res.get("hasKey") is True,
        "keyValid": valid,
        "availableModels": models,
        "vibe": {
            "sessionCount": num(res.get("vibeSessionCount")),
            "totalCost": cost,
            "totalTokens": num(res.get("vibeTotalTokens")),
            "promptTokens": num(res.get("vibePromptTokens")),
            "completionTokens": num(res.get("vibeCompletionTokens")),
            "totalSteps": num(res.get("vibeTotalSteps")),
            "toolOk": num(res.get("vibeToolOk")),
            "toolFail": num(res.get("vibeToolFail")),
            "activeModel": res.get("vibeActiveModel") or "",
            "recent": res.get("vibeRecent") or [],
        },
    }

    if res.get("error") is not None:
        return provider_error("mistral", "Mistral", "#ff7000", now, res["error"], details)

    r = provider_base("mistral", "Mistral", "#ff7000", now)
    r["ok"] = valid
    r["account"] = account(
        details["vibe"]["activeModel"] or "Mistral Vibe",
        [chip(tr("CONNECTED"), "good")] if valid else [],
    )
    r["summary"] = {
        "pct": pct,
        "text": money(cost, "USD"),
        "detail": tr("%1 models available", len(models)),
        "hasChart": True,
    }
    qw = flat_window("mistral", tr("vibe CLI spend"), pct, 0, money(cost, "USD") + " total", True)
    qw["resetText"] = "$50 soft cap"
    r["quotaWindows"] = [qw]
    if cost > 0:
        text = money(cost, "USD")
    elif valid:
        text = tr("✓ key")
    else:
        text = "—"
    r["slots"] = [
        {
            "pct": 0,
            "color": "#ff7000",
            "text": text,
            "tooltip": (
                "Mistral AI" + ("\nAPI key configured" if valid else "\nNo key set") + (f"\nSpend (vibe): {money(cost, 'USD')}" if cost > 0 else "")
            ),
        }
    ]
    r["chartWindows"] = monthly_window("mistral", "mv", True)
    r["historyValues"] = {"mv": cost} if cost > 0 else {}
    r["details"] = details
    return r
