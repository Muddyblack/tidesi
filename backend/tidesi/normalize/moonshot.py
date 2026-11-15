from ..contract import flat_window, jround, money, monthly_window, pct_clamp, provider_base, provider_error, rolling_windows
from ..messages import tr

ACCENT = "#1e3a8a"


def _window_label(w):
    if w.get("name"):
        return w["name"]
    seconds = w.get("seconds") or 0
    if seconds == 604800:
        return tr("Weekly limit")
    if seconds == 86400:
        return tr("Daily limit")
    if seconds and seconds % 86400 == 0:
        return tr("%1-day limit", seconds // 86400)
    if seconds and seconds % 3600 == 0:
        return tr("%1-hour limit", seconds // 3600)
    if seconds:
        return tr("%1-minute limit", seconds // 60)
    return tr("Plan usage")


def _plan_windows(plan):
    """Kimi Code plan rows, each with its percentage worked out."""
    out = []
    for w in plan.get("windows") or []:
        limit = w.get("limit") or 0
        used = w.get("used") or 0
        pct = pct_clamp(used / limit * 100) if limit > 0 else 0
        out.append({**w, "label": _window_label(w), "pct": pct})
    return out


def normalize_moonshot(raw):
    """Two independent Kimi sources: the Moonshot API balance (an API key) and
    the Kimi Code plan quota (the `kimi` CLI login). Either one is enough."""
    now = raw["now"]
    res = raw["inputs"].get("usage") or {}
    plan = raw["inputs"].get("codePlan") or {}
    res = res if isinstance(res, dict) else {}
    plan = plan if isinstance(plan, dict) else {}

    has_balance = bool(res) and res.get("error") is None
    has_plan = bool(plan) and plan.get("error") is None
    windows = _plan_windows(plan) if has_plan else []
    exhausted = has_plan and plan.get("exhausted") is True
    booster = plan.get("booster") if has_plan and isinstance(plan.get("booster"), dict) else None

    code_plan = {
        "loggedIn": plan.get("loggedIn") is True,
        "available": has_plan,
        "exhausted": exhausted,
        "message": plan.get("message") or "",
        "error": plan.get("error") or "",
        "windows": [
            {
                "label": w["label"],
                "name": w.get("name") or "",
                "seconds": w.get("seconds") or 0,
                "pct": w["pct"],
                "used": w["used"],
                "limit": w["limit"],
                "resetAt": w["resetAt"],
            }
            for w in windows
        ],
        "booster": (
            {
                "balance": booster["balanceCents"] / 100,
                "total": booster["totalCents"] / 100,
                "monthlyLimit": booster["monthlyChargeLimitCents"] / 100 if booster["monthlyChargeLimitEnabled"] else 0,
                "monthlyUsed": booster["monthlyUsedCents"] / 100,
                "currency": booster["currency"],
            }
            if booster
            else None
        ),
    }
    details = {
        "hasKey": res.get("hasKey") is True,
        "keyValid": has_balance and res.get("keyValid") is True,
        "balanceError": res.get("error") or "",
        "codePlan": code_plan,
    }

    if not has_balance and not has_plan:
        errors = [e for e in (plan.get("error"), res.get("error")) if e]
        if errors:
            return provider_error("kimi", "Kimi", ACCENT, now, tr("Kimi: %1", errors[0]), details)
        return provider_error("kimi", "Kimi", ACCENT, now, tr("Kimi: no Moonshot API key or Kimi Code login"), details)

    available = res.get("availableBalance") or 0
    voucher = res.get("voucherBalance") or 0
    cash = res.get("cashBalance") or 0
    details.update({"availableBalance": available, "voucherBalance": voucher, "cashBalance": cash, "currency": "USD"})

    rows = []
    # The first 5-hour and the first weekly window are the ones charted as
    # kc / kcw below; their rows say so.
    first_of = {}
    for w in windows:
        first_of.setdefault(w.get("seconds"), w)
    for i, w in enumerate(windows):
        series = {18000: "kc", 604800: "kcw"}.get(w.get("seconds"), "") if first_of.get(w.get("seconds")) is w else ""
        rows.append(flat_window(f"kimi_code_{i}", w["label"], w["pct"], w["resetAt"], f"{w['used']} / {w['limit']}", True, history=series))
    if exhausted and not windows:
        rows.append(flat_window("kimi_code_plan", tr("Plan usage"), 100, 0, code_plan["message"] or tr("Plan quota used up"), True))
    if code_plan["booster"]:
        b = code_plan["booster"]
        rows.append(
            flat_window(
                "kimi_booster",
                tr("Extra usage"),
                0,
                0,
                tr("%1 left", money(b["balance"], b["currency"])),
                False,
                tr("of %1", money(b["total"], b["currency"])),
            )
        )
    if has_balance:
        rows.append(flat_window("kimi_balance", tr("Available balance"), 0, 0, money(available, "USD"), False))
        rows.append(flat_window("kimi_split", tr("Voucher / cash"), 0, 0, f"{money(voucher, 'USD')} / {money(cash, 'USD')}", False))

    r = provider_base("kimi", "Kimi", ACCENT, now)
    r["quotaWindows"] = rows

    if has_plan:
        pct = 100 if exhausted and not windows else max((w["pct"] for w in windows), default=0)
        r["summary"] = {"pct": pct, "text": f"{jround(pct)}%", "detail": tr("Kimi Code"), "hasChart": True}
        tip = "Kimi Code"
        for w in windows:
            tip += f"\n{w['label']}: {jround(w['pct'])}%"
        if exhausted:
            tip += f"\n{code_plan['message'] or 'Plan quota used up'}"
        if has_balance:
            tip += f"\nMoonshot balance: {money(available, 'USD')}"
        r["slots"] = [{"pct": pct, "color": ACCENT, "text": None, "tooltip": tip}]

        # Kimi Code runs the same 5-hour + weekly pair as Claude and Codex; a
        # window of any other length is shown but not charted.
        session = next((w for w in windows if w["seconds"] == 18000), None)
        weekly = next((w for w in windows if w["seconds"] == 604800), None)
        history = {}
        if session:
            history["kc"] = session["pct"]
        if weekly:
            history["kcw"] = weekly["pct"]
        if history:
            as_window = lambda w: {"available": True, "pct": w["pct"], "resetAt": w["resetAt"]} if w else None  # noqa: E731
            r["chartWindows"] = rolling_windows(
                "kimi_code_5h", "kimi_code_24h", "kimi_code_7d", "kc", "kcw", as_window(session), as_window(weekly), monthly_id="kimi_code_30d"
            )
            r["historyValues"] = history
        elif has_balance:
            r["chartWindows"] = monthly_window("kimi", "km", True)
            r["historyValues"] = {"km": available}
        else:
            r["summary"]["hasChart"] = False
    else:
        r["summary"] = {"pct": 0, "text": money(available, "USD"), "detail": tr("Moonshot API"), "hasChart": True}
        r["slots"] = [{"pct": 0, "color": ACCENT, "text": money(available, "USD"), "tooltip": tr("Kimi balance: %1", money(available, "USD"))}]
        r["chartWindows"] = monthly_window("kimi", "km", True)
        r["historyValues"] = {"km": available}

    r["details"] = details
    return r
