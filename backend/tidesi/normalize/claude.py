from ..billing import empty_org_usage, price_models
from ..contract import (
    account,
    chip,
    effort_chip,
    jround,
    num,
    plan_chip,
    provider_base,
    provider_error,
    quota_window,
    remote_info,
    rolling_windows,
    unavailable_window,
    window_value,
)
from ..messages import lines, tr
from ..stats import claude_stats


def scope_name(scope):
    # A scoped limit names what it is narrowed to, either a model or a surface.
    scope = scope if isinstance(scope, dict) else {}
    model = scope.get("model")
    model = model if isinstance(model, dict) else {}
    name = str(model.get("display_name") or "").strip()
    if name == "":
        name = str(scope.get("surface") or "").strip()
    return name


def scoped_key(name):
    slug = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    return "weekly_" + slug if slug else "weekly_scoped"


def scoped_label(name):
    # An unnamed scope still gets a row; "scoped" is vague but it is not a lie,
    # and dropping the row would hide a budget that is being spent.
    return tr("7-day %1", name) if name else tr("7-day scoped")


def tier_label(raw):
    """ "default_claude_max_5x" → "default max 5x": the rate-limit tier as words."""
    if not raw:
        return ""
    words = []
    for part in str(raw).replace("_claude_ai", "").split("_"):
        known = {"default": tr("default"), "pro": tr("pro"), "max": tr("max"), "business": tr("business"), "free": tr("free")}
        words.append(known.get(part.lower()) or (part[:1].upper() + part[1:]))
    return " ".join(str(w) for w in words)


def claude_account(base_details, plan):
    """The line under the tabs: tier (or organisation), effort, dream, plan."""
    if plan == "":
        return account()
    name = tier_label(base_details.get("rateLimitTier")) or (
        tr("%1…", base_details["organizationUuid"][:8]) if base_details.get("organizationUuid") else tr("Claude Code User")
    )
    dream = base_details.get("autoDream") is True
    return account(
        name,
        [
            effort_chip(base_details.get("effortLevel")),
            chip(
                tr("dream: on") if dream else tr("dream: off"),
                "accent" if dream else "off",
                tr("Extended thinking (dream mode): ON\nClaude will reason longer on complex tasks")
                if dream
                else tr("Extended thinking (dream mode): OFF"),
            ),
            plan_chip(plan),
        ],
    )


def claude_windows(u):
    session = unavailable_window()
    weekly = unavailable_window()
    scoped = []
    for i in u.get("limits") or []:
        group, kind = i.get("group"), i.get("kind")
        if group == "session" or kind == "session":
            session = window_value(i.get("percent"), i.get("resets_at"), i.get("is_active"))
        elif kind == "weekly_scoped":
            # A scoped week is a second, narrower budget running alongside the
            # all-models week, not a reading of it — Claude Code lists it as its
            # own "Current week (<model>)" row. Folding it into `weekly` would
            # report the model's percentage as the account's.
            #
            # It is read as active for the same reason the legacy windows below
            # are: `is_active` marks which limit is currently binding, not
            # whether the number is real, and a percentage that came back is a
            # percentage worth showing. An inapplicable limit has no percent and
            # still falls out as unavailable.
            scoped.append((scope_name(i.get("scope")), window_value(i.get("percent"), i.get("resets_at"), True)))
        elif group == "weekly" or kind in ("weekly", "weekly_all"):
            weekly = window_value(i.get("percent"), i.get("resets_at"), i.get("is_active"))
    if not session["available"] and u.get("five_hour") is not None:
        fh = u["five_hour"]
        session = window_value(fh.get("utilization"), fh.get("resets_at"), True)
    if not weekly["available"] and u.get("seven_day") is not None:
        sd = u["seven_day"]
        weekly = window_value(sd.get("utilization"), sd.get("resets_at"), True)
    return {"session": session, "weekly": weekly, "scoped": scoped}


def _remote(sessions):
    """The Remote tab: the Claude Code sessions open to other devices, or None
    when there are none (the tab is then not shown)."""
    rows = [s for s in sessions or [] if isinstance(s, dict) and s.get("url")]
    if not rows:
        return None
    return remote_info(
        "online",
        tr("%1 session open to other devices", len(rows)) if len(rows) == 1 else tr("%1 sessions open to other devices", len(rows)),
        tr("Started with /remote-control in Claude Code. Continue them from your phone or the browser."),
        "https://claude.ai/code",
        tr("Open claude.ai/code ↗"),
        "",
        [
            {
                "name": s.get("name") or s.get("folder") or tr("Claude Code session"),
                "detail": s.get("folder") or "",
                "state": "busy" if s.get("status") == "busy" else "idle",
                "url": s["url"],
            }
            for s in rows
        ],
    )


def normalize_claude(raw):
    now = raw["now"]
    inp = raw["inputs"]
    creds = inp.get("credentials") or {}
    oauth = creds.get("claudeAiOauth") or {}
    token = oauth.get("accessToken") or ""
    has_admin = (creds.get("claudeAdminApiKey") or "") != ""
    usage = inp.get("usage")
    stats = claude_stats(inp.get("stats"), now)
    if inp.get("orgUsage") is not None:
        org = price_models((inp["orgUsage"].get("data") or []), inp.get("pricing") or {})
    else:
        org = empty_org_usage()

    base_details = {
        "hasOAuth": token != "",
        "hasAdminKey": has_admin,
        "subscriptionType": oauth.get("subscriptionType") or "",
        "rateLimitTier": oauth.get("rateLimitTier") or "",
        "organizationUuid": creds.get("organizationUuid") or "",
        "effortLevel": (inp.get("settings") or {}).get("effortLevel") or "",
        "autoDream": (inp.get("settings") or {}).get("autoDreamEnabled") is True,
        "organizationUsage": org,
        "stats": stats,
        "scopedWeekly": [],
    }

    if token == "":
        r = provider_error(
            "claude",
            "Claude",
            "#cc785c",
            now,
            tr("OAuth missing — API stats only") if has_admin else tr("Claude not logged in"),
            {
                **base_details,
                "session": unavailable_window(),
                "weekly": unavailable_window(),
                "extraTokens": 0,
                "extraUsage": {"enabled": False, "limit": 0, "used": 0, "pct": 0, "currency": "USD"},
            },
        )
        r["ok"] = has_admin
        return r

    if usage is None:
        err = inp.get("usageError") or ""
        return provider_error(
            "claude",
            "Claude",
            "#cc785c",
            now,
            err if err != "" else tr("Claude usage request failed"),
            {
                **base_details,
                "session": unavailable_window(),
                "weekly": unavailable_window(),
                "extraTokens": 0,
                "extraUsage": {"enabled": False, "limit": 0, "used": 0, "pct": 0, "currency": "USD"},
            },
        )

    w = claude_windows(usage)
    extra = usage.get("extra") or usage.get("extra_budget") or {}
    extra_usage = usage.get("extra_usage") or {}
    s_tokens = num((usage.get("five_hour") or {}).get("tokens_used"))
    s_limit = num((usage.get("five_hour") or {}).get("token_limit"))
    w_tokens = num((usage.get("seven_day") or {}).get("tokens_used"))
    w_limit = num((usage.get("seven_day") or {}).get("token_limit"))
    s_detail = tr("%1 / %2 tokens", s_tokens, s_limit) if s_limit > 0 else ""
    w_detail = tr("%1 / %2 tokens", w_tokens, w_limit) if w_limit > 0 else ""

    r = provider_base("claude", "Claude", "#cc785c", now)
    r["account"] = claude_account(base_details, base_details["subscriptionType"])
    r["summary"] = {
        "pct": w["session"]["pct"],
        "text": f"{jround(w['session']['pct'])}%",
        "detail": oauth.get("subscriptionType") or "",
        "hasChart": True,
    }
    r["quotaWindows"] = [
        quota_window("session", tr("5-hour session"), w["session"], s_detail),
        quota_window("weekly", tr("7-day window"), w["weekly"], w_detail),
    ] + [quota_window(scoped_key(name), scoped_label(name), win, "") for name, win in w["scoped"] if win["available"]]
    r["slots"] = [
        {
            "pct": w["session"]["pct"],
            "color": "#e05252",
            "text": None,
            "tooltip": lines(tr("Claude 5-hour: %1%", jround(w["session"]["pct"])), s_detail),
        },
        {
            "pct": w["weekly"]["pct"],
            "color": "#f5a623",
            "text": None,
            "tooltip": lines(tr("Claude 7-day: %1%", jround(w["weekly"]["pct"])), w_detail),
        },
    ]
    r["chartWindows"] = rolling_windows("session", "day", "weekly", "s", "w", w["session"], w["weekly"], monthly_id="monthly")
    r["historyValues"] = {
        **({"s": w["session"]["pct"]} if w["session"]["available"] else {}),
        **({"w": w["weekly"]["pct"]} if w["weekly"]["available"] else {}),
    }
    if extra.get("tokens_remaining") is not None:
        extra_tokens = num(extra.get("tokens_remaining"))
    else:
        extra_tokens = num(extra.get("token_limit"))
    r["details"] = {
        **base_details,
        "remote": _remote(inp.get("remote")),
        "session": {**w["session"], "tokensUsed": s_tokens, "tokenLimit": s_limit},
        "weekly": {**w["weekly"], "tokensUsed": w_tokens, "tokenLimit": w_limit},
        "scopedWeekly": [
            {"key": scoped_key(name), "label": scoped_label(name), "model": name, **win} for name, win in w["scoped"] if win["available"]
        ],
        "extraTokens": extra_tokens,
        "extraUsage": {
            "enabled": extra_usage.get("is_enabled") is True,
            "limit": num(extra_usage.get("monthly_limit")),
            "used": num(extra_usage.get("used_credits")),
            "pct": num(extra_usage.get("utilization")),
            "currency": extra_usage.get("currency") or "USD",
        },
    }
    return r
