"""Resolve Grok CLI OAuth (and optional xAI API key), then fetch credit/billing
data from the same cli-chat-proxy endpoint the Grok Build CLI uses for
`/usage`. Also aggregates local session stats from ~/.grok/sessions.

Ported from tools/sh/get-grok-usage — the biggest and most stateful helper.
Only the free-tier path has been exercised against a real account; treat the
billing/credits path as unverified until checked against one.
"""

import os
import re
import time
import urllib.parse
from collections import deque
from dataclasses import dataclass

from .. import accounts
from ..contract import epoch_of
from ..http import as_json, fetch_json, resolve_key
from ..messages import tr

CLIENT_VER = "0.2.106"

_FREE_USAGE_RE = re.compile(r"tokens \(actual/limit\): (?P<used>\d+)/(?P<limit>\d+)")


def _gnum(v):
    """Coerce values to numbers; objects report through a ``val`` field."""
    if v is None or isinstance(v, bool):
        return 0
    if isinstance(v, dict):
        return v.get("val") or 0
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v) if ("." in v or "e" in v.lower()) else int(v)
        except ValueError:
            return 0
    return 0


def _first_present(*vals):
    """Return the first value that is neither None nor False."""
    for v in vals:
        if v is not None and v is not False:
            return v
    return None


def grok_home():
    """``$GROK_HOME`` when set, else ``~/.grok`` — where the Grok CLI keeps its
    login, settings, session logs and unified log, on every platform."""
    return os.path.expanduser(os.environ.get("GROK_HOME") or "~/.grok")


@dataclass(frozen=True)
class GrokSession:
    """One discovered session directory and the markers it carries.

    ``mtime`` is the marker the resume/target view keys on (summary wins when
    both layouts are present); ``summary_mtime``/``signals_mtime`` keep each
    layout's own timestamp for the readers that use them.
    """

    session_id: str
    directory: str
    cwd: str
    mtime: float
    summary_path: str
    summary_mtime: float
    signals_path: str
    signals_mtime: float

    @property
    def marker(self) -> str:
        return "summary.json" if self.summary_path else "signals.json"


_DISCOVERY_TTL_SECONDS = 30.0
_DISCOVERY_CACHE: dict[str, tuple[float, list[GrokSession]]] = {}


def _discovery_ttl():
    raw = os.environ.get("GROK_DISCOVERY_TTL_SECONDS")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return _DISCOVERY_TTL_SECONDS


def _decode_workspace(name):
    if name.startswith("%2F"):
        try:
            return urllib.parse.unquote(name)
        except Exception:
            return ""
    return ""


def _walk_sessions(sessions_dir):
    """One recursive pass over the store, recording every session directory.

    The four local Grok views (signals, summaries, legacy entries, resume
    targets) each used to walk this tree independently. This is the single
    pass they now share.
    """
    records = []
    for root, _dirs, files in os.walk(sessions_dir):
        if root == sessions_dir:
            continue
        names = set(files)
        summary = os.path.join(root, "summary.json") if "summary.json" in names else ""
        signals = os.path.join(root, "signals.json") if "signals.json" in names else ""
        if not summary and not signals:
            continue
        session_id = os.path.basename(root)
        if not session_id or session_id == "sessions":
            continue
        try:
            summary_mtime = os.path.getmtime(summary) if summary else 0.0
            signals_mtime = os.path.getmtime(signals) if signals else 0.0
        except OSError:
            continue
        parent = os.path.basename(os.path.dirname(root.rstrip("/\\")))
        records.append(
            GrokSession(
                session_id=session_id,
                directory=root,
                cwd=_decode_workspace(parent),
                mtime=summary_mtime or signals_mtime,
                summary_path=summary,
                summary_mtime=summary_mtime,
                signals_path=signals,
                signals_mtime=signals_mtime,
            )
        )
    return records


def discover_sessions(sessions_dir=None, *, now=None):
    """Return the store's sessions from one walk, memoized per directory.

    The memo lives only as long as this process and expires after
    ``GROK_DISCOVERY_TTL_SECONDS`` (default 30), so the four local views of one
    collection share a single walk without ever serving a stale store later.
    """
    sessions_dir = sessions_dir or os.path.join(grok_home(), "sessions")
    if not os.path.isdir(sessions_dir):
        return []
    now = time.time() if now is None else now
    key = os.path.normcase(os.path.realpath(os.path.abspath(sessions_dir)))
    cached = _DISCOVERY_CACHE.get(key)
    if cached is not None and 0 <= (now - cached[0]) < _discovery_ttl():
        return cached[1]
    records = _walk_sessions(sessions_dir)
    _DISCOVERY_CACHE[key] = (now, records)
    return records


def reset_discovery_cache():
    _DISCOVERY_CACHE.clear()


def _resolve_api_key():
    # An extra account (another GROK_HOME) is its own folder's login only; the
    # environment's xAI key belongs to the main one.
    if accounts.extra_account():
        return ""
    return resolve_key(
        ("WIDGET_XAI_API_KEY", "WIDGET_GROK_API_KEY"),
        ("XAI_API_KEY", "GROK_API_KEY"),
        os.path.expanduser("~/.config/xai/api-key"),
        os.path.expanduser("~/.xai/api-key"),
        os.path.expanduser("~/.config/grok/api-key"),
    )


def _read_grok_auth(auth_file):
    if not os.path.isfile(auth_file):
        return {}
    try:
        with open(auth_file, encoding="utf-8") as f:
            data = as_json(f.read())
    except OSError:
        data = None
    if not isinstance(data, dict):
        return {}
    candidates = [{**v, "_ct": v.get("create_time") or ""} for v in data.values() if isinstance(v, dict) and (v.get("key") or "") != ""]
    if not candidates:
        return {}
    candidates.sort(key=lambda c: c["_ct"])
    candidates.reverse()
    chosen = candidates[0]
    return {
        "access_token": chosen.get("key") or "",
        "email": chosen.get("email") or "",
        "user_id": chosen.get("user_id") or chosen.get("principal_id") or "",
        "team_id": chosen.get("team_id") or "",
        "auth_mode": chosen.get("auth_mode") or "",
        "expires_at": chosen.get("expires_at") or "",
        "first_name": chosen.get("first_name") or "",
    }


def _grok_local_stats():
    default = {"sessionCount": 0, "totalToolCalls": 0, "totalTokens": 0, "models": [], "totalSessionSeconds": 0}
    sessions_dir = os.path.join(grok_home(), "sessions")
    if not os.path.isdir(sessions_dir):
        return default
    records = discover_sessions(sessions_dir)
    paths = [record.signals_path for record in records if record.signals_path][:200]

    docs = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                d = as_json(f.read())
        except OSError:
            d = None
        if isinstance(d, dict):
            docs.append(d)
    if not docs:
        return default

    models = set()
    for d in docs:
        for m in d.get("modelsUsed") or []:
            models.add(m)

    return {
        "sessionCount": len(docs),
        "totalToolCalls": sum((d.get("toolCallCount") or 0) for d in docs),
        "totalTokens": sum((d.get("contextTokensUsed") or 0) for d in docs),
        "models": sorted(models),
        "totalSessionSeconds": sum((d.get("sessionDurationSeconds") or 0) for d in docs),
    }


def _grok_account_meta(access_token, team_id_hint, user_id_hint, client_ver):
    user_id, team_id = user_id_hint, team_id_hint
    team_blocked = False
    team_name = ""
    tier_id = ""
    blocked_reasons = []

    me = fetch_json(
        "https://api.x.ai/v1/me",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json", "User-Agent": f"xai-grok-cli/{client_ver}"},
        timeout=8,
    )
    me_json = as_json(me.body)
    if isinstance(me_json, dict):
        if not user_id:
            user_id = me_json.get("user_id") or ""
        if not team_id:
            team_id = me_json.get("team_id") or ""
        if me_json.get("team_blocked") is True:
            team_blocked = True

    team_resp = fetch_json(
        "https://management-api.x.ai/auth/teams",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        timeout=8,
    )
    team_json = as_json(team_resp.body)
    if isinstance(team_json, dict) and team_json.get("teams"):
        teams = team_json.get("teams") or []
        chosen = next((t for t in teams if t.get("teamId") == team_id), teams[0] if teams else {})
        team_name = chosen.get("name") or ""
        tier_raw = _first_present(chosen.get("tierId"), chosen.get("tier"), "")
        tier_id = "" if tier_raw is None else str(tier_raw)
        blocked_reasons = chosen.get("blockedReasons") or []
        if len(blocked_reasons) > 0:
            team_blocked = True

    return user_id, team_id, team_name, tier_id, team_blocked, blocked_reasons


def _tail_lines(path, n):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return list(deque(f, maxlen=n))
    except OSError:
        return []


def _extract_free_usage(lines):
    matches = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        obj = as_json(line)
        if not isinstance(obj, dict):
            continue
        ctx = obj.get("ctx") or {}
        text = ctx.get("message") or ctx.get("reason") or ctx.get("error") or ""
        if "free-usage-exhausted" not in text:
            continue
        entry = {"ts": obj.get("ts") or "", "text": text}
        m = _FREE_USAGE_RE.search(text)
        if m:
            entry["used"] = m.group("used")
            entry["limit"] = m.group("limit")
        matches.append(entry)
    return matches[-1] if matches else {}


def _resolve_free_usage(free_usage, now):
    ts = free_usage.get("ts") or ""
    if not ts:
        return free_usage
    epoch = epoch_of(ts)
    if epoch <= 0 or (now - epoch) >= 86400:
        return {}
    return free_usage


def _extract_local_billing(lines):
    matches = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        obj = as_json(line)
        if not isinstance(obj, dict) or obj.get("msg") != "billing: fetched credits config":
            continue
        ctx = obj.get("ctx") or {}
        matches.append({"config": ctx.get("config") or {}, "subscriptionTier": ctx.get("subscriptionTier") or ""})
    return matches[-1] if matches else {}


def _grok_billing_calls(access_token, client_ver):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "x-grok-client-version": client_ver,
        "User-Agent": f"xai-grok-cli/{client_ver}",
    }
    credits_json = {}
    billing_json = {}
    billing_error = ""

    cr = fetch_json("https://cli-chat-proxy.grok.com/v1/billing?format=credits", headers=headers, timeout=12)
    if cr.status == 200:
        parsed = as_json(cr.body)
        if parsed is not None:
            credits_json = parsed
    elif cr.status in (401, 403):
        billing_error = tr("Grok auth expired — run grok --oauth")
    elif cr.status not in (0, None, ""):
        billing_error = tr("Billing HTTP %1", cr.status)

    br = fetch_json("https://cli-chat-proxy.grok.com/v1/billing", headers=headers, timeout=12)
    if br.status == 200:
        parsed = as_json(br.body)
        if parsed is not None:
            billing_json = parsed

    return credits_json, billing_json, billing_error


def _assemble(
    token,
    api_key,
    email,
    user_id,
    team_id,
    team_name,
    auth_mode,
    expires_at,
    first_name,
    default_model,
    tier_id,
    team_blocked,
    blocked_reasons,
    local,
    credits,
    billing,
    local_billing,
    free_usage,
    billing_error,
):
    credits = credits if isinstance(credits, dict) else {}
    billing = billing if isinstance(billing, dict) else {}
    local_billing = local_billing if isinstance(local_billing, dict) else {}
    free_usage = free_usage if isinstance(free_usage, dict) else {}

    credits_config = credits.get("config") or {}
    cc = credits_config if credits_config else (local_billing.get("config") or {})
    bc = billing.get("config") or {}
    cc_period = cc.get("currentPeriod") or {}

    credit_pct_raw = _gnum(_first_present(cc.get("creditUsagePercent"), cc.get("credit_usage_percent"), None))
    used = _gnum(_first_present(free_usage.get("used"), bc.get("used"), bc.get("includedUsed"), None))
    monthly_limit = _gnum(_first_present(free_usage.get("limit"), bc.get("monthlyLimit"), cc.get("monthlyLimit"), None))
    on_demand_used = _gnum(_first_present(cc.get("onDemandUsed"), bc.get("onDemandUsed"), None))
    on_demand_cap = _gnum(_first_present(cc.get("onDemandCap"), bc.get("onDemandCap"), None))
    prepaid = _gnum(_first_present(cc.get("prepaidBalance"), bc.get("prepaidBalance"), None))
    unified = _first_present(cc.get("isUnifiedBillingUser"), bc.get("isUnifiedBillingUser"), False)

    period_start = _first_present(cc.get("billingPeriodStart"), cc_period.get("start"), bc.get("billingPeriodStart"), "")
    period_end = _first_present(cc.get("billingPeriodEnd"), cc_period.get("end"), bc.get("billingPeriodEnd"), "")
    period_type = _first_present(cc_period.get("type"), "")

    free_limit = _gnum(free_usage.get("limit"))
    free_used = _gnum(free_usage.get("used"))

    if free_limit > 0:
        pct = min(free_used / free_limit * 100, 100)
    elif credit_pct_raw > 0:
        pct = credit_pct_raw
    elif monthly_limit > 0:
        pct = min(used / monthly_limit * 100, 100)
    elif on_demand_cap > 0:
        pct = min(on_demand_used / on_demand_cap * 100, 100)
    else:
        pct = 0

    has_billing = free_limit > 0 or len(credits) > 0 or len(billing) > 0

    return {
        "loggedIn": token != "",
        "grokAccessToken": token,
        "xaiApiKey": api_key,
        "email": email,
        "firstName": first_name,
        "userId": user_id,
        "teamId": team_id,
        "teamName": team_name,
        "authMode": auth_mode,
        "expiresAt": expires_at,
        "defaultModel": default_model,
        "tierId": tier_id if tier_id != "" else (local_billing.get("subscriptionTier") or ""),
        "teamBlocked": team_blocked,
        "blockedReasons": blocked_reasons,
        "creditUsagePercent": pct,
        "used": used,
        "monthlyLimit": monthly_limit,
        "onDemandUsed": on_demand_used,
        "onDemandCap": on_demand_cap,
        "prepaidBalance": prepaid,
        "isUnifiedBilling": unified,
        "periodType": period_type,
        "billingPeriodStart": period_start,
        "billingPeriodEnd": period_end,
        "sessionCount": local.get("sessionCount") or 0,
        "totalToolCalls": local.get("totalToolCalls") or 0,
        "totalTokens": local.get("totalTokens") or 0,
        "models": local.get("models") or [],
        "totalSessionSeconds": local.get("totalSessionSeconds") or 0,
        "billingError": billing_error,
        "quotaKind": "free-tier" if free_limit > 0 else "billing",
        "quotaExhausted": free_limit > 0 and free_used >= free_limit,
        "quotaWindow": "rolling 24h" if free_limit > 0 else period_type,
        "hasBilling": has_billing,
    }


def get_grok_usage():
    now = time.time()
    api_key = _resolve_api_key()

    default_model = ""
    settings_path = os.path.join(grok_home(), "user-settings.json")
    if os.path.isfile(settings_path):
        try:
            with open(settings_path, encoding="utf-8") as f:
                s = as_json(f.read())
        except OSError:
            s = None
        if isinstance(s, dict):
            default_model = s.get("defaultModel") or ""

    auth = _read_grok_auth(os.path.join(grok_home(), "auth.json"))
    access_token = auth.get("access_token", "")
    email = auth.get("email", "")
    user_id = auth.get("user_id", "")
    team_id = auth.get("team_id", "")
    auth_mode = auth.get("auth_mode", "")
    expires_at = auth.get("expires_at", "")
    first_name = auth.get("first_name", "")

    if not access_token and not api_key:
        return {}

    local_stats = _grok_local_stats()

    team_name, tier_id, team_blocked, blocked_reasons = "", "", False, []
    if access_token:
        user_id, team_id, team_name, tier_id, team_blocked, blocked_reasons = _grok_account_meta(access_token, team_id, user_id, CLIENT_VER)

    free_usage, local_billing = {}, {}
    unified_log = os.path.join(grok_home(), "logs", "unified.jsonl")
    if os.path.isfile(unified_log):
        lines = _tail_lines(unified_log, 5000)
        free_usage = _resolve_free_usage(_extract_free_usage(lines), now)
        local_billing = _extract_local_billing(lines)

    credits_json, billing_json, billing_error = {}, {}, ""
    if access_token:
        credits_json, billing_json, billing_error = _grok_billing_calls(access_token, CLIENT_VER)

    return _assemble(
        access_token,
        api_key,
        email,
        user_id,
        team_id,
        team_name,
        auth_mode,
        expires_at,
        first_name,
        default_model,
        tier_id,
        team_blocked,
        blocked_reasons,
        local_stats,
        credits_json,
        billing_json,
        local_billing,
        free_usage,
        billing_error,
    )
