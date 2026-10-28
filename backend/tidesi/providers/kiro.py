"""Kiro monthly credits, from kiro-cli's login or the Kiro IDE's snapshot.

Two sources, one record shape:

- kiro-cli keeps no usage snapshot on disk, but it does keep its login in
  ~/.local/share/kiro-cli/data.sqlite3. With that token the widget asks the
  same getUsageLimits endpoint the CLI's own /usage screen reads. Live, so it
  wins whenever it answers.
- The Kiro IDE caches the last usage payload in state.vscdb — no network, but
  only as fresh as the IDE's last refresh. It is the fallback, and the only
  source on a machine without kiro-cli.

The kiro-cli store is opened read-only. The CLI refreshes its short-lived
token itself; a second writer here could race it and sign the user out, so an
expired token is reported rather than renewed.

state.vscdb is a real SQLite database — an ItemTable(key, value) key/value
store, same shape VS Code forks use — so it is queried properly first. The
marker/brace scan (ported from the old Perl helper) stays as a fallback in
case a future Kiro version changes the table shape.
"""

import json
import os
import re
import sqlite3
import time

from .. import paths, sources
from ..contract import epoch_of, num
from ..http import as_json, clean_credential, fetch_json, http_error_text
from ..messages import tr

_MARKER_KEY = "kiro.resourceNotifications.usageState"
_AGENT_KEY = "kiro.kiroAgent"
_PLAN_BY_LIMIT = {50: "free", 1000: "pro", 2000: "pro+", 10000: "power"}


def _query_sqlite(db_path):
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM ItemTable WHERE key = ?", (_MARKER_KEY,)).fetchone()
        nested = not row or row[0] is None
        if nested:
            row = conn.execute("SELECT value FROM ItemTable WHERE key = ?", (_AGENT_KEY,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    try:
        state = json.loads(value)
        if nested:
            return state.get(_MARKER_KEY) if isinstance(state, dict) else None
        return state
    except ValueError:
        return None


def _brace_match_scan(db_path):
    try:
        with open(db_path, "rb") as f:
            blob = f.read()
    except OSError:
        return None
    marker = b'"' + _MARKER_KEY.encode() + b'":'
    start = blob.find(marker)
    if start < 0:
        return None
    start += len(marker)
    json_start = blob.find(b"{", start)
    if json_start < 0:
        return None

    depth = 0
    in_string = False
    escaped = False
    json_end = -1
    for i in range(json_start, len(blob)):
        ch = blob[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == 0x5C:  # backslash
                escaped = True
            elif ch == 0x22:  # "
                in_string = False
            continue
        if ch == 0x22:
            in_string = True
        elif ch == 0x7B:  # {
            depth += 1
        elif ch == 0x7D:  # }
            depth -= 1
            if depth == 0:
                json_end = i
                break
    if json_end < 0:
        return None
    try:
        return json.loads(blob[json_start : json_end + 1].decode("utf-8", "replace"))
    except ValueError:
        return None


def _plan_type(limit, title=""):
    """The subscription title ("KIRO FREE") when the source carries one, the
    well-known credit allowances otherwise."""
    if isinstance(title, str) and title.strip():
        t = title.strip().lower()
        return t[5:] if t.startswith("kiro ") else t
    return _PLAN_BY_LIMIT.get(limit, "custom")


def _usage_record(breakdown, current, limit, percentage, reset, timestamp, source, title=""):
    if percentage == 0 and limit > 0 and current > 0:
        percentage = (current / limit) * 100.0
    remaining = max(limit - current, 0) if limit > 0 else 0

    # The IDE snapshot carries {code, symbol}; the API a bare currency code.
    currency = breakdown.get("currency")
    if isinstance(currency, dict):
        code = currency.get("code") or "USD"
        symbol = currency.get("symbol") or "$"
    else:
        code = currency if isinstance(currency, str) and currency else "USD"
        symbol = "$" if code == "USD" else code + " "

    return {
        "planType": _plan_type(limit, title),
        "usageType": breakdown.get("type") or breakdown.get("resourceType") or "",
        "usageUnit": breakdown.get("unit") or "",
        "displayName": breakdown.get("displayName") or "Credit",
        "displayNamePlural": breakdown.get("displayNamePlural") or "Credits",
        "currentUsage": current,
        "usageLimit": limit,
        "percentageUsed": percentage,
        "remaining": remaining,
        "currentOverages": num(breakdown.get("currentOveragesWithPrecision", breakdown.get("currentOverages"))),
        "overageCap": num(breakdown.get("overageCap")),
        "overageCharges": num(breakdown.get("overageCharges")),
        "overageRate": num(breakdown.get("overageRate")),
        "resetDate": reset,
        "currencyCode": code,
        "currencySymbol": symbol,
        "timestamp": timestamp,
        "source": source,
    }


# ── Kiro IDE snapshot ──────────────────────────────────────────────────────


def _ide_db():
    return os.environ.get("KIRO_IDE_DB") or paths.first_file(
        [os.path.join(base, "Kiro", "User", "globalStorage", "state.vscdb") for base in paths.electron_app_data_dirs()]
    )


def _ide_usage():
    """None when the IDE was never installed; a record or {"error"} otherwise."""
    db_path = _ide_db()
    if not os.path.isfile(db_path):
        return None

    usage_state = _query_sqlite(db_path)
    if usage_state is None:
        usage_state = _brace_match_scan(db_path)
    if usage_state is None:
        return {"error": tr("No Kiro usage snapshot — open Kiro and let it refresh")}
    if not isinstance(usage_state, dict):
        return {"error": tr("Kiro usage payload could not be parsed")}

    breakdowns = usage_state.get("usageBreakdowns")
    breakdown = breakdowns[0] if isinstance(breakdowns, list) and breakdowns else {}
    if not isinstance(breakdown, dict):
        breakdown = {}

    return _usage_record(
        breakdown,
        current=num(breakdown.get("currentUsage")),
        limit=num(breakdown.get("usageLimit")),
        percentage=num(breakdown.get("percentageUsed")),
        reset=breakdown.get("resetDate") or "",
        timestamp=num(usage_state.get("timestamp")),
        source="ide",
    )


# ── kiro-cli login + live API ──────────────────────────────────────────────


def _cli_db():
    """kiro-cli's store.

    kiro-cli is a fork of Amazon Q CLI and keeps its data through Rust's
    `dirs`, whose local-data directory is ~/Library/Application Support on
    macOS rather than the XDG path. Both are looked at, best first; off macOS
    they are the same place."""
    if os.environ.get("KIRO_CLI_DB"):
        return os.environ["KIRO_CLI_DB"]
    return paths.first_file([os.path.join(base, "kiro-cli", "data.sqlite3") for base in paths.data_home_dirs()])


def _cli_login(db_path):
    """(access token, profile ARN, expiry epoch) from kiro-cli's store, or None.

    The token row is keyed by login flavour (kirocli:social:token for a
    Google/GitHub login, others for Builder ID / Identity Center), so any
    *:token row carrying an access token is accepted. The profile ARN sits on
    the social token, and in the state table for every login."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute("SELECT key, value FROM auth_kv WHERE key LIKE '%:token'").fetchall()
        try:
            profile_row = conn.execute("SELECT value FROM state WHERE key = 'api.codewhisperer.profile'").fetchone()
        except sqlite3.Error:
            profile_row = None
    except sqlite3.Error:
        return None
    finally:
        conn.close()

    profile = None
    if profile_row and profile_row[0] is not None:
        value = profile_row[0]
        profile = as_json(value.decode("utf-8", "replace") if isinstance(value, bytes) else value)
    profile_arn = profile.get("arn") if isinstance(profile, dict) else ""

    for _key, value in sorted(rows, key=lambda r: ":social:" not in r[0]):
        token = as_json(value.decode("utf-8", "replace") if isinstance(value, bytes) else value)
        if not isinstance(token, dict):
            continue
        access = clean_credential(token.get("access_token"))
        if access:
            arn = token.get("profile_arn") or profile_arn or ""
            return access, str(arn), epoch_of(token.get("expires_at") or "")
    return None


def _region_of(arn):
    """arn:aws:codewhisperer:<region>:<account>:profile/<id> → region, guarded
    because it becomes part of a hostname."""
    parts = arn.split(":")
    region = parts[3] if len(parts) > 3 else ""
    return region if re.fullmatch(r"[a-z]{2}(-[a-z]+)+-\d", region) else "us-east-1"


def parse_cli_usage(body):
    """The getUsageLimits response (as parsed JSON) → the shared record."""
    if not isinstance(body, dict):
        return {"error": tr("Kiro usage response could not be parsed")}
    items = body.get("usageBreakdownList")
    if not isinstance(items, list) or not items:
        single = body.get("usageBreakdown")
        items = [single] if isinstance(single, dict) else []
    items = [b for b in items if isinstance(b, dict)]
    if not items:
        return {"error": tr("Kiro usage response has no credit breakdown")}
    breakdown = next((b for b in items if b.get("resourceType") == "CREDIT"), items[0])

    def precise(key):
        value = breakdown.get(key + "WithPrecision")
        n = num(value if value is not None else breakdown.get(key))
        # 50.0 would otherwise read "0.13 / 50.0 credits" in the detail line.
        return int(n) if isinstance(n, float) and n.is_integer() else n

    subscription = body.get("subscriptionInfo")
    title = subscription.get("subscriptionTitle") if isinstance(subscription, dict) else ""
    return _usage_record(
        breakdown,
        current=precise("currentUsage"),
        limit=precise("usageLimit"),
        percentage=0,
        reset=breakdown.get("nextDateReset") or body.get("nextDateReset") or "",
        timestamp=0,
        source="cli",
        title=title,
    )


def _cli_usage():
    """None when kiro-cli was never signed in here; a record or {"error"}."""
    db_path = _cli_db()
    if not os.path.isfile(db_path):
        return None
    login = _cli_login(db_path)
    if login is None:
        return {"error": tr("kiro-cli is not signed in — run kiro-cli login")}
    token, arn, expires = login
    if expires and expires <= time.time():
        return {"error": tr("kiro-cli login expired — run kiro-cli once to refresh it")}

    import urllib.parse

    query = urllib.parse.urlencode({"origin": "AI_EDITOR", "profileArn": arn, "resourceType": "AGENTIC_REQUEST"})
    result = fetch_json(
        f"https://q.{_region_of(arn)}.amazonaws.com/getUsageLimits?{query}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "kde-ai-usage/kiro"},
        timeout=12,
        fixture_path=os.environ.get("KIRO_CLI_USAGE_RESPONSE_FILE"),
    )
    if result.status in (401, 403):
        return {"error": tr("kiro-cli login rejected — run kiro-cli once to refresh it")}
    if result.status != 200:
        return {"error": http_error_text(result.status)}
    return parse_cli_usage(as_json(result.body))


SOURCES = (
    sources.Source("cli", "cli", tr("kiro-cli"), tr("The kiro-cli login, read live"), lambda: os.path.isfile(_cli_db())),
    sources.Source("ide", "ide", tr("Kiro IDE"), tr("The usage snapshot the IDE caches"), lambda: os.path.isfile(_ide_db())),
)

_READERS = {"cli": lambda: _cli_usage(), "ide": lambda: _ide_usage()}


def get_kiro_usage():
    answers = {}
    order = sources.candidates("kiro", tuple(_READERS))
    for source in order:
        answers[source] = _READERS[source]()
        if answers[source] is not None and answers[source].get("error") is None:
            return answers[source]
    # None answered: the first that has something to say is the actionable one
    # (an expired kiro-cli login, say), in the order they were tried.
    for source in order:
        if answers[source] is not None:
            return answers[source]
    return {"error": tr("No Kiro state found — sign in to Kiro IDE or kiro-cli once")}
