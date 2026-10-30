"""Zed plan and edit-prediction usage (macOS/Linux/Windows; live-verified on Linux).

The editor stores its sign-in in the macOS login Keychain, Linux Secret
Service or Windows Credential Manager. The server is ``https://zed.dev`` (or ``server_url`` /
``credentials_url`` from ``settings.json``), the account is the Zed user id,
and the secret is the access token. This provider reads the credential from the
platform store and sends it to Zed's own cloud API, as the editor does. Linux
uses ``secret-tool`` and may need the Secret Service keyring unlocked. Windows
reads the Credential Manager entry named after the server URL. Response field names are read defensively;
the plan block is ``plan.plan_v3`` / ``plan.usage.edit_predictions`` /
``plan.subscription_period``. This API reports plan/edit-prediction quota, not
local model-token spend.
"""

import os
import re
import shutil
import subprocess
import urllib.parse

from .. import paths
from ..contract import epoch_of, finite_number
from ..http import as_json, clean_credential, fetch_json, http_error_text

_DEFAULT_SERVER = "https://zed.dev"
_API = "https://cloud.zed.dev/client/users/me"
SECRET_TOOL = "secret-tool"  # Pinned to libsecret's path by the Nix package.
_ACCOUNT_RE = re.compile(r'"acct"<blob>="([^"]*)"')


def _settings():
    explicit = os.environ.get("ZED_SETTINGS_PATH")
    path = explicit or os.path.join(paths.config_home(), "zed", "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = as_json(re.sub(r"^\s*//.*$", "", f.read(), flags=re.M))
    except OSError:
        return {}
    return data if isinstance(data, dict) else {}


def _server_url(settings):
    for key in ("credentials_url", "server_url"):
        value = settings.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().rstrip("/")
    return _DEFAULT_SERVER


def _api_url(server):
    host = urllib.parse.urlparse(server).hostname or ""
    if server in (_DEFAULT_SERVER, "https://staging.zed.dev") or host in ("zed.dev", "staging.zed.dev"):
        return _API
    # A custom server answers on its own origin; only HTTPS may carry a token.
    if urllib.parse.urlparse(server).scheme == "https":
        return f"{server}/client/users/me"
    return ""


def _secret_service_login(server):
    """(user_id, access_token) from Zed's Linux Secret Service entry."""
    executable = shutil.which(SECRET_TOOL)
    if not executable:
        return "", ""
    try:
        done = subprocess.run(
            [executable, "search", "--all", "--unlock", "url", server],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return "", ""
    if done.returncode != 0:
        return "", ""

    entries = []
    entry = None
    for line in done.stdout.splitlines():
        if line.startswith("[") and line.endswith("]"):
            if entry is not None:
                entries.append(entry)
            entry = {}
            continue
        if entry is None:
            continue
        key, separator, value = line.partition(" = ")
        if separator:
            entry[key] = value
    if entry is not None:
        entries.append(entry)

    for entry in entries:
        if entry.get("attribute.url") != server:
            continue
        user_id = clean_credential(entry.get("attribute.username"))
        token = clean_credential(entry.get("secret"))
        if user_id and token:
            return user_id, token
    return "", ""


def _windows_credential_login(server):
    """(user_id, access_token) from the Windows Credential Manager entry the
    editor writes under the server URL as its target name, or ("", "")."""
    try:
        import ctypes
        from ctypes import wintypes
    except (ImportError, ValueError):
        return "", ""
    if not hasattr(ctypes, "WinDLL"):
        return "", ""

    class Credential(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    read = advapi32.CredReadW
    read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
    read.restype = wintypes.BOOL
    free = advapi32.CredFree
    free.argtypes = [ctypes.c_void_p]
    free.restype = None

    pointer = ctypes.POINTER(Credential)()
    if not read(server, 1, 0, ctypes.byref(pointer)):  # 1 = CRED_TYPE_GENERIC
        return "", ""
    try:
        credential = pointer.contents
        blob = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize) if credential.CredentialBlobSize else b""
        user_id = credential.UserName or ""
    finally:
        free(pointer)
    return user_id, blob.decode("utf-8", "replace")


def _credential_store_login(server):
    """(user_id, access_token) from the platform credential store, or empty."""
    if paths.IS_WINDOWS:
        return _windows_credential_login(server)
    if not paths.IS_MACOS:
        return _secret_service_login(server)
    try:
        done = subprocess.run(
            ["/usr/bin/security", "find-internet-password", "-s", server, "-g"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "", ""
    if done.returncode != 0:
        return "", ""
    account = _ACCOUNT_RE.search(done.stdout)
    secret = re.search(r'^password: "(.*)"$', done.stderr, re.M)
    return (account.group(1) if account else ""), (secret.group(1) if secret else "")


def _plan_label(plan):
    value = plan.get("plan_v3") or plan.get("plan")
    if isinstance(value, dict):
        value = next(iter(value.values()), "") if len(value) == 1 else ""
    return str(value or "").replace("zed_", "").replace("_", " ").title()


def parse_user(body):
    plan = body.get("plan") if isinstance(body, dict) else None
    if not isinstance(plan, dict):
        return None
    predictions = (plan.get("usage") or {}).get("edit_predictions") or {}
    limit = predictions.get("limit")
    unlimited = limit == "unlimited" or (isinstance(limit, dict) and "unlimited" in limit)
    if isinstance(limit, dict):
        limit = limit.get("limited", limit.get("limit"))
    used = finite_number(predictions.get("used"), minimum=0)
    maximum = finite_number(limit, minimum=0)
    period_value = plan.get("subscription_period")
    period = period_value if isinstance(period_value, dict) else {}
    return {
        "plan": _plan_label(plan),
        "includedUSD": {"Free": 0.0, "Student": 10.0, "Pro": 5.0}.get(_plan_label(plan)),
        "periodStart": epoch_of(period.get("started_at")),
        "usageBasedBilling": plan.get("is_usage_based_billing_enabled") if type(plan.get("is_usage_based_billing_enabled")) is bool else None,
        "used": used,
        "limit": None if unlimited else maximum,
        "unlimited": unlimited,
        "resetAt": epoch_of(period.get("ended_at")),
        "overdue": plan.get("has_overdue_invoices") is True,
    }


def get_zed_usage(*, refresh=True):
    if not refresh:
        return {"deferred": True}
    server = _server_url(_settings())
    url = _api_url(server)
    if not url:
        return {"error": "custom server must use https"}
    user_id, token = _credential_store_login(server)
    fixture = os.environ.get("ZED_USAGE_RESPONSE_FILE")
    if not fixture and not (user_id and token):
        return {}
    result = fetch_json(
        url,
        headers={"Authorization": f"{user_id} {clean_credential(token)}", "Accept": "application/json", "User-Agent": "tidesi/zed"},
        timeout=12,
        fixture_path=fixture,
    )
    if result.status != 200:
        return {"error": http_error_text(result.status)}
    parsed = parse_user(as_json(result.body))
    if parsed is None:
        return {"error": "unexpected response"}
    parsed["agentUsage"] = {
        "available": False,
        "source": "account-api",
        "usedUSD": None,
        "costStatus": "unavailable",
        "dailyUSD": [],
        "periodStart": parsed["periodStart"],
        "periodEnd": parsed["resetAt"],
        "includedUSD": parsed["includedUSD"],
        "usageBasedBilling": parsed["usageBasedBilling"],
    }
    return parsed
