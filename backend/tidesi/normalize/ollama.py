"""Normalize the limit buckets returned by Ollama Cloud's usage API."""

import math

from ..contract import chart_window, epoch_of, flat_window, jround, provider_base, provider_error
from ..messages import lines, tr

ACCENT = "#f0f0f0"
BUCKETS = (("session", "Session (5h)"), ("weekly", "Weekly (7d)"), ("monthly", "Monthly"))


def normalize_ollama(raw):
    now = raw["now"]
    body = raw["inputs"].get("usage") or {}
    if not body:
        return provider_error("ollama", "Ollama Cloud", ACCENT, now, tr("Ollama Cloud: no API key found in settings, environment, or OpenCode"), {})
    if not isinstance(body, dict):
        return provider_error("ollama", "Ollama Cloud", ACCENT, now, tr("Ollama Cloud: invalid usage response"), {})
    if body.get("error"):
        return provider_error("ollama", "Ollama Cloud", ACCENT, now, str(body["error"]), {})
    limits = body.get("limits")
    included = body.get("included")
    if isinstance(included, dict):
        limits = {}
        for key, _ in BUCKETS[:2]:
            bucket = included.get(key)
            if not isinstance(bucket, dict):
                continue
            remaining = bucket.get("remaining_percent")
            if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not math.isfinite(remaining) or not 0 <= remaining <= 100:
                continue
            limits[key] = {"usage": (100 - remaining) / 100, "resets_at": bucket.get("resets_at")}
        balance, allowance = included.get("balance_usd"), included.get("allowance_usd")
        if (
            all(not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) for value in (balance, allowance))
            and allowance > 0
        ):
            period = included.get("period")
            limits["monthly"] = {
                "usage": max(0, (allowance - balance) / allowance),
                "resets_at": period.get("until") if isinstance(period, dict) else None,
                "balance_usd": balance,
                "allowance_usd": allowance,
            }
    if not isinstance(limits, dict):
        return provider_error("ollama", "Ollama Cloud", ACCENT, now, tr("Ollama Cloud: usage response has no limit data"), {})

    windows = []
    models = {}
    for key, label in BUCKETS:
        bucket = limits.get(key)
        if not isinstance(bucket, dict):
            continue
        fraction = bucket.get("usage")
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not math.isfinite(fraction) or fraction < 0:
            continue
        pct = max(0, min(100, fraction * 100))
        detail = tr("%1% used", jround(pct))
        if "allowance_usd" in bucket:
            label = tr("Included credits")
            detail = tr("%1 of %2 USD remaining", f"{bucket['balance_usd']:.2f}", f"{bucket['allowance_usd']:.2f}")
        windows.append(flat_window(f"ollama_{key}", label, pct, epoch_of(bucket.get("resets_at")), detail, True))
        rows = bucket.get("models")
        if isinstance(rows, list):
            models[key] = [
                {"name": row["name"], "requestCount": row["request_count"]}
                for row in rows
                if isinstance(row, dict)
                and isinstance(row.get("name"), str)
                and isinstance(row.get("request_count"), int)
                and not isinstance(row["request_count"], bool)
                and row["request_count"] >= 0
            ]
    if not windows:
        return provider_error("ollama", "Ollama Cloud", ACCENT, now, tr("Ollama Cloud: no recognized usage limits in response"), {})

    activity = body.get("activity") if isinstance(body.get("activity"), dict) else {}
    cost = activity.get("cost")
    try:
        cost_value = float(cost) if not isinstance(cost, bool) else -1
    except (TypeError, ValueError, OverflowError):
        cost_value = -1
    cost = str(cost) if math.isfinite(cost_value) and cost_value >= 0 else ""
    primary = windows[0]
    # Legacy session/weekly plans can report zero activity cost despite
    # consumed quota. Treat that zero as unavailable rather than a bill.
    if cost_value == 0 and primary["key"] in ("ollama_session", "ollama_weekly") and any(w["pct"] > 0 for w in windows):
        cost = ""
    r = provider_base("ollama", "Ollama Cloud", ACCENT, now)
    r["summary"] = {"pct": primary["pct"], "text": f"{jround(primary['pct'])}%", "detail": primary["label"], "hasChart": True}
    r["quotaWindows"] = windows
    r["slots"] = [
        {
            "pct": primary["pct"],
            "color": ACCENT,
            "text": None,
            "tooltip": lines("Ollama Cloud", *(tr("%1: %2%", w["label"], jround(w["pct"])) for w in windows)),
        }
    ]
    # Never join different limit types in one history series. The endpoint can
    # change between session/weekly and monthly even for the same account.
    by_key = {w["key"]: w for w in windows}
    long_window = by_key.get("ollama_monthly") or by_key.get("ollama_weekly") or primary
    r["chartWindows"] = [
        chart_window("ollama_5h", primary["key"], "5H", 18000000, "5h"),
        chart_window("ollama_24h", primary["key"], "24H", 86400000, "24h"),
        chart_window("ollama_7d", long_window["key"], "7D", 604800000, "7d"),
        chart_window("ollama_30d", long_window["key"], "30D", 2592000000, "30d"),
    ]
    r["historyValues"] = {w["key"]: w["pct"] for w in windows}
    r["details"] = {
        "models": models,
        "activityCost": cost,
        "limitType": primary["key"],
        "resetUnavailable": not any(w["resetAt"] > 0 for w in windows),
    }
    purchased = body.get("purchased")
    remaining = purchased.get("balance_usd") if isinstance(purchased, dict) else None
    if not isinstance(remaining, bool) and isinstance(remaining, (int, float)) and math.isfinite(remaining) and remaining >= 0:
        r["quotaWindows"].append(flat_window("ollama_purchased", tr("Purchased credits"), 0, 0, tr("%1 USD remaining", f"{remaining:.2f}"), False))
    return r
