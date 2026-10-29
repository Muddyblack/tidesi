from ..billing import empty_org_usage, price_models
from ..contract import (
    account,
    chip,
    effort_chip,
    flat_window,
    jround,
    money,
    plan_chip,
    provider_base,
    provider_error,
    quota_window,
    rolling_windows,
    short_model_name,
    unavailable_window,
    window_value,
)
from ..messages import tr
from ..stats import codex_stats


def _codex_window(w):
    if not isinstance(w, dict):
        return {"kind": "", "value": unavailable_window()}
    mins = w.get("windowDurationMins")
    mins = mins if isinstance(mins, (int, float)) and not isinstance(mins, bool) else None
    secs = w.get("limit_window_seconds")
    secs = secs if isinstance(secs, (int, float)) and not isinstance(secs, bool) else None
    if mins == 300 or secs == 18000:
        kind = "session"
    elif mins == 10080 or secs == 604800:
        kind = "weekly"
    else:
        kind = ""
    pct = w["usedPercent"] if w.get("usedPercent") is not None else w.get("used_percent")
    reset = w["resetsAt"] if w.get("resetsAt") is not None else w.get("reset_at")
    value = unavailable_window() if kind == "" else window_value(pct, reset, True)
    # A duration alone is not evidence of an active session allowance. Treat
    # zero usage without a reset as a placeholder; keep zero with a reset
    # (including Spark), and keep nonzero usage even if its reset is missing.
    if kind == "session" and value["pct"] == 0 and value["resetAt"] <= 0:
        value = unavailable_window()
    return {"kind": kind, "value": value}


def _assign_codex_window(base, w):
    c = _codex_window(w)
    if c["kind"] != "":
        base[c["kind"]] = c["value"]


def codex_normalize(p):
    main = p.get("rateLimits") or p.get("rate_limit")
    base = {"session": unavailable_window(), "weekly": unavailable_window()}
    if main is not None:
        _assign_codex_window(base, main.get("primary") or main.get("primary_window"))
        _assign_codex_window(base, main.get("secondary") or main.get("secondary_window"))

    by_id = p.get("rateLimitsByLimitId")
    additional = []
    if by_id is not None:
        for key, s in by_id.items():
            if key == "codex":
                continue
            entry = {
                "name": s.get("limitName") or s.get("limitId") or key,
                "session": unavailable_window(),
                "weekly": unavailable_window(),
                "limitReached": s.get("rateLimitReachedType") is not None,
            }
            _assign_codex_window(entry, s.get("primary"))
            _assign_codex_window(entry, s.get("secondary"))
            additional.append(entry)
    else:
        for i, lim in enumerate(p.get("additional_rate_limits") or []):
            r = lim.get("rate_limit") or {}
            entry = {
                "name": lim.get("limit_name") or f"Model {i + 1}",
                "session": unavailable_window(),
                "weekly": unavailable_window(),
                "limitReached": r.get("limit_reached") is True,
            }
            _assign_codex_window(entry, r.get("primary_window"))
            _assign_codex_window(entry, r.get("secondary_window"))
            additional.append(entry)

    m = main or {}
    return {
        **base,
        "limitReached": (m.get("limit_reached") is True) or (m.get("rateLimitReachedType") is not None),
        "planType": m.get("planType") or p.get("plan_type") or "",
        "additional": additional,
    }


def openai_account(creds, plan, stats):
    """Codex / ChatGPT identity: the account, the live model and reasoning
    effort of the newest rollout, the plan."""
    model = (stats or {}).get("model") or ""
    return account(
        creds.get("email") or creds.get("accountId") or tr("Codex / ChatGPT User"),
        [
            chip(short_model_name(model), "accent") if model else None,
            effort_chip((stats or {}).get("effortLevel")),
            plan_chip(plan),
        ],
    )


def normalize_openai(raw):
    now = raw["now"]
    inp = raw["inputs"]
    creds = inp.get("credentials") or {}
    has_key = (creds.get("openaiApiKey") or "") != ""
    logged_in = (creds.get("codexLoggedIn") is True) or ((creds.get("codexAccessToken") or "") != "")
    codex = codex_normalize(inp.get("codex") or {})
    codex_available = codex["session"]["available"] or codex["weekly"]["available"]
    stats = codex_stats(inp.get("stats"), now)
    org_error = inp.get("orgUsageError") or ""
    org_available = isinstance(inp.get("orgUsage"), dict) and isinstance(inp["orgUsage"].get("data"), list)
    if inp.get("orgUsage") is not None:
        entries = [item for r in (inp["orgUsage"].get("data") or []) for item in (r.get("results") or [])]
        org = price_models(entries, inp.get("pricing") or {})
    else:
        org = empty_org_usage()
    plan = codex.get("planType") or creds.get("planType") or ""

    details = {
        "hasApiKey": has_key,
        "codexLoggedIn": logged_in,
        "email": creds.get("email") or "",
        "planType": plan,
        "orgId": creds.get("orgId") or "",
        "accountId": creds.get("accountId") or "",
        "authMode": creds.get("authMode") or "",
        "codex": {
            "available": codex_available,
            "limitReached": codex["limitReached"],
            "session": codex["session"],
            "weekly": codex["weekly"],
            "additional": codex["additional"],
        },
        "organizationUsage": org,
        "organizationUsageAvailable": org_available,
        "organizationUsageError": org_error,
        "stats": stats,
    }

    if not has_key and not logged_in:
        return provider_error("openai", "OpenAI", "#10a37f", now, tr("OpenAI: no API key or Codex login"), details)

    if codex_available:
        # Only the windows the plan actually reports. Plans without a 5-hour
        # window exist (OpenAI dropped it from some), and listing it anyway put
        # an empty "0%" row in the popups and a fake 0% on the pill.
        session_on, weekly_on = codex["session"]["available"], codex["weekly"]["available"]
        headline = codex["session"] if session_on else codex["weekly"]
        r = provider_base("openai", "OpenAI", "#10a37f", now)
        r["account"] = openai_account(creds, plan, stats)
        r["summary"] = {
            "pct": headline["pct"],
            "text": f"{jround(headline['pct'])}%",
            "detail": plan + (f" · {creds.get('email')}" if (creds.get("email") or "") != "" else ""),
            "hasChart": True,
        }
        quota_windows = []
        if session_on:
            quota_windows.append(quota_window("codex_session", tr("Codex 5-hour"), codex["session"], tr("ChatGPT/Codex plan window")))
        if weekly_on:
            quota_windows.append(quota_window("codex_weekly", tr("Codex weekly"), codex["weekly"], tr("Secondary plan window")))
        for a in codex["additional"]:
            if a["session"]["available"]:
                quota_windows.append(
                    quota_window(
                        "additional",
                        tr("%1 · 5-hour", a["name"]),
                        a["session"],
                        tr("Limit reached") if a["limitReached"] else "",
                    )
                )
            if a["weekly"]["available"]:
                quota_windows.append(quota_window("additional", tr("%1 · weekly", a["name"]), a["weekly"], ""))
        r["quotaWindows"] = quota_windows
        if org_error:
            r["quotaWindows"].append(flat_window("openai_org_usage", tr("Organization usage unavailable"), 0, 0, org_error, False))
        r["slots"] = []
        if session_on:
            r["slots"].append(
                {
                    "pct": codex["session"]["pct"],
                    "color": "#10a37f",
                    "text": None,
                    "tooltip": tr("Codex 5h: %1% left", jround(100 - codex["session"]["pct"])),
                }
            )
        if weekly_on:
            r["slots"].append(
                {
                    "pct": codex["weekly"]["pct"],
                    "color": "#10a37f",
                    "text": None,
                    "tooltip": tr("Codex weekly: %1% left", jround(100 - codex["weekly"]["pct"])),
                }
            )
        r["chartWindows"] = rolling_windows(
            "codex_primary",
            "codex_day",
            "codex_weekly",
            "cp",
            "cw",
            codex["session"],
            codex["weekly"],
            monthly_id="codex_monthly",
        )
        r["historyValues"] = {
            **({"cp": codex["session"]["pct"]} if codex["session"]["available"] else {}),
            **({"cw": codex["weekly"]["pct"]} if codex["weekly"]["available"] else {}),
        }
        r["details"] = details
        return r

    # Signed in or keyed, but the plan windows are not exposed. Account status
    # and org billing still render, so this is not an error state.
    r = provider_base("openai", "OpenAI", "#10a37f", now)
    if logged_in:
        r["account"] = openai_account(creds, plan, stats)
    r["stale"] = bool(inp.get("codexError") or org_error)
    if inp.get("codexError"):
        r["error"] = (
            tr("Codex login expired. Run codex login to sign in again.")
            if inp.get("codexError") == "token expired"
            else tr("OpenAI usage refresh failed: %1", inp["codexError"])
        )
    elif org_error:
        r["error"] = org_error
    email = creds.get("email") or ""
    total_cost_text = money(org["totalCostUSD"], "USD") if org["totalCostUSD"] > 0 else tr("API")
    r["summary"] = {
        "pct": 0,
        "text": total_cost_text,
        "detail": email if email != "" else tr("API key configured"),
        "hasChart": False,
    }
    org_detail = tr("Codex signed in; no organization API key")
    if has_key:
        org_detail = tr("Organization usage available") if org_available else tr("Organization usage unavailable")
    if org_error:
        org_detail = org_error
    r["quotaWindows"] = [
        {
            "key": "account",
            "label": tr("API credentials"),
            "pct": 0,
            "available": True,
            "resetAt": 0,
            "resetText": "",
            "detail": org_detail,
            "showMeter": False,
        }
    ]
    r["slots"] = [{"pct": 0, "color": "#10a37f", "text": total_cost_text, "tooltip": email if email != "" else tr("API key configured")}]
    r["details"] = details
    return r
