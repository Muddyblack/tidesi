"""The one thing about Muse that cannot be read for free: the plan windows.

Meta publishes the Current/Weekly windows of the TUI's `/usage` screen only as
a `response.subscription_usage` frame riding a live model call. They are in
neither the MSP wire schema, the view fold, `session-index.db` nor the
feature-config cache, the CLI has no `usage`/`status`/`quota` subcommand, and
the frame is the *last* event on the stream — after `response.incomplete` — so
the call cannot be cut short to avoid paying for the generation.

Reading them therefore costs tokens, which docs/provider-contract.md says a
statistic must not do. So this lives in its own module, off by default, and
providers/muse.py never imports it: everything the widget shows out of the box
comes from local files, and a test asserts that module's import graph can reach
no network at all. Switching this on (settings → "Muse live quota", the
Hyprland toggle, or WIDGET_MUSE_QUOTA=1) is a deliberate, informed choice, and
the frontends state the per-refresh cost using the model's own catalog price
before the user makes it.

One refresh is a minimal streaming call (`store: false`, max_output_tokens 16 —
about 12 input and ~120 output tokens, most of them reasoning). Results are
cached for MUSE_QUOTA_TTL_SECONDS (default 1800) so a 5-minute poll interval
cannot turn into a per-poll model call. Every failure degrades to the free
local statistics and says whether the credential was refused or the endpoint
was unreachable — a flaky network must never be reported as a bad key.
"""

import datetime
import json
import os

from .. import config as _config
from .. import keychain
from ..contract import num
from ..http import as_json, clean_credential, resolve_key, route_e2e_url
from .muse import auth_meta, configured_model, model_catalog

_QUOTA_URL = "https://api.meta.ai/v1/responses"
_QUOTA_TIMEOUT = 15
# Measured against a live probe: what one refresh actually costs, used to price
# the warning the settings panel shows. Tokens, not dollars — the rate comes
# from the user's own model catalog.
QUOTA_CALL_TOKENS = {"input": 12, "output": 120}


def _ttl():
    try:
        return max(0, int(os.environ.get("MUSE_QUOTA_TTL_SECONDS", "1800")))
    except ValueError:
        return 1800


# Where the Muse CLI puts the key on macOS, which is not in auth.json: that
# file keeps the identity and the Keychain keeps the secret.
KEYCHAIN_SERVICE = "ai.meta.dev.credentials"
KEYCHAIN_ACCOUNT = "meta"


def _keychain_key():
    """The stored key from the login Keychain on macOS, "" anywhere else.

    The item holds either the key itself or the same JSON object auth.json
    holds; both are accepted, because which one it is is not documented and a
    guess that is wrong should cost nothing."""
    secret = keychain.password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    if not secret:
        return ""
    parsed = as_json(secret)
    if isinstance(parsed, dict):
        return clean_credential(parsed.get("api_key") or parsed.get("apiKey"))
    return clean_credential(secret)


def _api_key():
    """An explicit META_API_KEY (widget field or environment) wins over the
    login store's own api_key, mirroring the CLI's precedence. The OIDC
    access_token in that store is not valid on the Model API and is never used.

    On macOS the store usually has no api_key at all — the CLI keeps it in the
    login Keychain — so that is read last rather than leaving Mac users with
    nothing but the environment variable."""
    key = resolve_key("WIDGET_MUSE_API_KEY", "META_API_KEY")
    if key:
        return key
    stored = clean_credential((auth_meta() or {}).get("api_key"))
    return stored or _keychain_key()


def quota_model():
    """A chat model id for the call. The snapshot is account-level, so any chat
    model returns the same windows — which means we can always use whatever the
    CLI itself is set to, and never a name pinned in this source."""
    model = configured_model()
    if model:
        return model
    catalog = model_catalog()
    for name, row in catalog.items():
        if row.get("current") or row.get("default"):
            return name
    return next(iter(catalog), "")


def refresh_cost(model=""):
    """{tokens, usd, currency} for one refresh, priced from the local catalog —
    or usd None when the catalog does not price that model. The frontends show
    this before the user turns the switch on, so the number has to come from
    the account's real rates rather than a figure baked in here."""
    row = model_catalog().get(model or quota_model()) or {}
    tokens = QUOTA_CALL_TOKENS["input"] + QUOTA_CALL_TOKENS["output"]
    if not row.get("input$") and not row.get("output$"):
        return {"tokens": tokens, "usd": None, "currency": row.get("currency") or "USD"}
    usd = QUOTA_CALL_TOKENS["input"] / 1000000 * num(row.get("input$")) + QUOTA_CALL_TOKENS["output"] / 1000000 * num(row.get("output$"))
    return {"tokens": tokens, "usd": usd, "currency": row.get("currency") or "USD"}


def _cache_path():
    return os.path.join(_config.cache_dir(), "muse-quota.json")


def _read_cache(ttl):
    try:
        with open(_cache_path(), encoding="utf-8", errors="replace") as f:
            cached = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict) or not isinstance(cached.get("quota"), dict):
        return None
    try:
        age = datetime.datetime.now(datetime.timezone.utc).timestamp() - float(cached.get("fetchedAt") or 0)
    except (TypeError, ValueError):
        return None
    return cached["quota"] if 0 <= age < ttl else None


def _write_cache(quota):
    try:
        os.makedirs(_config.cache_dir(), exist_ok=True)
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump({"fetchedAt": datetime.datetime.now(datetime.timezone.utc).timestamp(), "quota": quota}, f)
    except OSError:
        pass


def parse_subscription_event(body):
    """First response.subscription_usage object in an SSE stream, or {}.

    Line endings are normalised first: an SSE frame boundary is a blank line,
    which on the wire is frequently CRLF CRLF. Splitting a CRLF stream on
    "\\n\\n" finds no boundary at all and silently returns {} — the quota would
    just never appear, with no error to explain it.
    """
    buf = body.replace("\r\n", "\n") if isinstance(body, str) else ""
    start = 0
    while True:
        end = buf.find("\n\n", start)
        if end < 0:
            break
        for line in buf[start:end].splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                obj = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("type") == "response.subscription_usage":
                sub = obj.get("subscription")
                return sub if isinstance(sub, dict) else {}
        start = end + 2
    return {}


def subscription_to_quota(sub):
    if not isinstance(sub, dict):
        return {}
    window = sub.get("window") if isinstance(sub.get("window"), dict) else {}
    weekly = sub.get("weekly") if isinstance(sub.get("weekly"), dict) else {}
    quota = {"current": {}, "weekly": {}}
    cur_reset = int(num(window.get("resets_at")))
    wk_reset = int(num(weekly.get("resets_at")))
    if cur_reset > 0:
        quota["current"] = {"pct": num(window.get("used_percent")), "resetAt": cur_reset}
    if wk_reset > 0:
        quota["weekly"] = {"pct": num(weekly.get("used_percent")), "resetAt": wk_reset}
    if not quota["current"] and not quota["weekly"]:
        return {}
    return quota


def error_code(exc):
    """Stable reason code for a failed fetch. Separates "the server said no"
    from "we could not ask": without it a network timeout is indistinguishable
    from a rejected credential, and a flaky connection makes the tab accuse a
    working key of being invalid."""
    return "rejected" if getattr(exc, "code", None) in (401, 403) else "unreachable"


def _fetch(api_key, model, fixture_path=None):
    """(quota, error): error is "" on success, else an error_code value."""
    if fixture_path and os.path.isfile(fixture_path):
        try:
            # newline="" disables universal-newline translation: a replayed
            # capture has to carry the CRLF the wire actually sent, or it
            # silently tests a stream nobody ever receives.
            with open(fixture_path, encoding="utf-8", errors="replace", newline="") as f:
                raw = f.read()
        except OSError:
            return {}, "unreachable"
        try:
            doc = json.loads(raw)
        except ValueError:
            return subscription_to_quota(parse_subscription_event(raw)), ""
        if isinstance(doc, dict) and isinstance(doc.get("subscription"), dict):
            doc = doc["subscription"]
        return subscription_to_quota(doc), ""

    import urllib.error
    import urllib.request

    body = json.dumps({"model": model, "input": "ping", "stream": True, "max_output_tokens": 16, "store": False}).encode()
    req = urllib.request.Request(
        route_e2e_url(_QUOTA_URL),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "kde-ai-usage/muse",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_QUOTA_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {}, error_code(exc)
    # A 200 with no snapshot is not a failure: the account simply reports no
    # subscription windows (pay-as-you-go has none at all).
    return subscription_to_quota(parse_subscription_event(raw)), ""


def get_muse_quota():
    """(quota, error). `error` is "" when the snapshot came back or the account
    has no windows, else "disabled", "no-credential", "no-model", "rejected" or
    "unreachable"."""
    if not _config.muse_quota_enabled():
        return {}, "disabled"
    fixture_path = os.environ.get("MUSE_QUOTA_RESPONSE_FILE")
    if fixture_path and os.path.isfile(fixture_path):
        return _fetch("", "", fixture_path=fixture_path)
    key = _api_key()
    if not key:
        return {}, "no-credential"
    model = quota_model()
    if not model:
        return {}, "no-model"
    ttl = _ttl()
    if ttl > 0:
        cached = _read_cache(ttl)
        if cached is not None:
            return cached, ""
    quota, error = _fetch(key, model)
    if quota and ttl > 0:
        _write_cache(quota)
    return quota, error
