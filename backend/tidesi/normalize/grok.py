from ..contract import epoch_of, flat_window, jround, monthly_window, num, pct_clamp, provider_base, provider_error, reset_text
from ..messages import tr


def normalize_grok(raw):
    now = raw["now"]
    res = raw["inputs"].get("usage") or {}

    if not isinstance(res, dict) or not res:
        return provider_error(
            "grok",
            "Grok",
            "#e6e6e6",
            now,
            tr("Grok: run grok --oauth or configure an xAI key"),
            {"hasKey": False, "loggedIn": False},
        )

    pct = pct_clamp(num(res.get("creditUsagePercent")))
    used = num(res.get("used")) if res.get("used") is not None else num(res.get("onDemandUsed"))
    limit = num(res.get("monthlyLimit")) if res.get("monthlyLimit") is not None else num(res.get("onDemandCap"))
    has_billing = res.get("hasBilling") is True
    quota_kind = res.get("quotaKind") or ""
    has_chart = quota_kind != "free-tier"
    sessions = num(res.get("sessionCount"))
    if quota_kind == "free-tier":
        reset = {"text": res.get("quotaWindow") or tr("rolling 24h"), "at": 0}
    else:
        at = epoch_of(res.get("billingPeriodEnd") or "")
        reset = {"text": reset_text(at), "at": at}
    detail = res.get("teamName") or res.get("email") or res.get("tierId") or tr("Grok CLI")

    r = provider_base("grok", "Grok", "#e6e6e6", now)
    r["error"] = res.get("billingError") or ""
    r["summary"] = {
        "pct": pct,
        "text": f"{jround(pct)}%" if has_billing else tr("CLI"),
        "detail": detail,
        "hasChart": has_chart,
    }
    if has_billing:
        qw0 = flat_window(
            "grok", tr("Credit usage"), pct, reset["at"], f"{used} / {limit}" if limit > 0 else "", True, history="gr" if has_chart else ""
        )
        qw0["resetText"] = reset["text"]
        quota_windows = [qw0]
    else:
        quota_windows = [flat_window("grok", tr("Billing quota"), 0, 0, tr("Not exposed for this Grok account"), False)]
    quota_windows.append(
        flat_window(
            "grok_local",
            tr("Local CLI activity"),
            0,
            0,
            tr(
                "%1%2%3 tokens · %4 tool calls",
                sessions,
                " session · " if sessions == 1 else " sessions · ",
                num(res.get("totalTokens")),
                num(res.get("totalToolCalls")),
            ),
            False,
        )
    )
    r["quotaWindows"] = quota_windows
    r["slots"] = [
        {
            "pct": pct,
            "color": "#e6e6e6",
            "text": None if has_billing else tr("CLI"),
            "tooltip": (tr("Grok credits: %1% used", jround(pct)) if has_billing else tr("Grok CLI connected; billing quota is not exposed")),
        }
    ]
    r["chartWindows"] = monthly_window("grok", "gr", False) if has_chart else []
    r["historyValues"] = {"gr": pct} if has_chart else {}
    r["details"] = {
        "hasKey": (res.get("xaiApiKey") or "") != "",
        "loggedIn": res.get("loggedIn") is True,
        "pct": pct,
        "used": used,
        "monthlyLimit": limit,
        "email": res.get("email") or "",
        "teamName": res.get("teamName") or "",
        "tierId": res.get("tierId") or "",
        "billingPeriodEnd": res.get("billingPeriodEnd") or "",
        "sessionCount": sessions,
        "totalTokens": num(res.get("totalTokens")),
        "totalToolCalls": num(res.get("totalToolCalls")),
        "hasBilling": has_billing,
        "quotaKind": quota_kind,
        "quotaWindow": res.get("quotaWindow") or "",
        "quotaExhausted": res.get("quotaExhausted") is True,
        "billingError": res.get("billingError") or "",
    }
    return r
