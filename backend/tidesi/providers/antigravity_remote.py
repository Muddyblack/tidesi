"""Read the account's remote instances using Antigravity's existing sign-in.

Protocol and credential layout verified against agy 1.3.1: ApiService's
ListInstances REST binding and codeassistclient.StoredToken. This is a private
API; failure must never break quota reporting. No daemon is started and no
credentials, refreshed tokens, or device list are written to disk.
"""

import base64
import datetime
import json
import os
import shutil
import subprocess
import time
import urllib.parse

from .. import paths, windows_credentials
from ..http import as_json, fetch_json

INSTANCE_URL = "https://jetski-webchannel.googleapis.com/v1internal:listInstances"
REMOTE_URL = "https://antigravity.google.com/"
REFRESH_COOLDOWN = 600  # seconds between agy-driven token refreshes


def _stored_credentials():
    # agy uses service=gemini, username=antigravity in go-keyring. Its file
    # fallback lives in the application's data directory, without a suffix.
    command = None
    if paths.IS_WINDOWS:
        stored = as_json(windows_credentials.password("gemini:antigravity"))
        if isinstance(stored, dict) and isinstance(stored.get("token"), dict):
            return stored
    if paths.IS_MACOS:
        command = ["security", "find-generic-password", "-s", "gemini", "-a", "antigravity", "-w"]
    elif not paths.IS_WINDOWS and shutil.which(paths.SECRET_TOOL):
        command = [paths.SECRET_TOOL, "lookup", "service", "gemini", "username", "antigravity"]
    if command:
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2, **paths.no_window())
            stored = as_json(result.stdout) if result.returncode == 0 else None
            if isinstance(stored, dict) and isinstance(stored.get("token"), dict):
                return stored
        except (OSError, subprocess.TimeoutExpired):
            pass
    for directory in ("~/.gemini/antigravity", "~/.gemini/antigravity-cli"):
        try:
            with open(os.path.join(os.path.expanduser(directory), "antigravity-oauth-token"), encoding="utf-8") as stream:
                stored = json.load(stream)
        except (OSError, ValueError):
            continue
        if isinstance(stored, dict) and isinstance(stored.get("token"), dict):
            return stored
    return None


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _email(stored):
    # The ID token is used only to avoid mixing accounts, never to authenticate
    # a request. Google validates the access token on the actual API request.
    token = stored.get("token") or {}
    raw = stored.get("id_token") or token.get("id_token")
    try:
        payload = raw.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return _text(claims.get("email")).casefold()
    except (AttributeError, IndexError, TypeError, ValueError):
        return ""


def _usable_token(token):
    access = _text(token.get("access_token"))
    if not access or not access.isascii() or any(ord(c) <= 32 or ord(c) == 127 for c in access):
        return ""
    expiry = token.get("expiry")
    if expiry and expiry != "0001-01-01T00:00:00Z":
        try:
            expires = datetime.datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if expires.timestamp() <= datetime.datetime.now(datetime.timezone.utc).timestamp() + 30:
                return ""
        except (AttributeError, TypeError, ValueError, OverflowError):
            return ""
    return access


def _cooled_down():
    # Spawning agy costs seconds, so try it at most once per cooldown, whether
    # or not it helped. Refreshes run as separate processes, hence the file.
    stamp = os.path.join(paths.cache_dir(), "antigravity-remote-refresh")
    try:
        if time.time() - os.path.getmtime(stamp) < REFRESH_COOLDOWN:
            return False
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(stamp), exist_ok=True)
        with open(stamp, "w", encoding="utf-8"):
            pass
    except OSError:
        pass
    return True


def _refresh(stored):
    # Let agy refresh its own sign-in: any authenticated command renews the
    # stored token, so this repo never needs agy's OAuth client credentials.
    # Whoever signed in must still be the account we expected.
    agy = shutil.which("agy")
    if not agy or stored.get("wif_provider") or not _cooled_down():
        return ""
    try:
        subprocess.run([agy, "models"], capture_output=True, timeout=15, stdin=subprocess.DEVNULL, **paths.no_window())
    except (OSError, subprocess.TimeoutExpired):
        return ""
    fresh = _stored_credentials()
    if fresh is None or _email(fresh) != _email(stored):
        return ""
    stored["token"] = fresh["token"]
    return _usable_token(fresh["token"])


def _instances(data):
    if not isinstance(data, dict) or "error" in data:
        return None
    rows = data.get("instances", [])  # Protobuf JSON omits empty repeated fields.
    if not isinstance(rows, list):
        return None
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            continue
        name = _text(metadata.get("name"))
        if not name:
            continue
        status = row.get("status")
        state = (
            {
                "INSTANCE_STATUS_CONNECTED": "online",
                1: "online",
                "INSTANCE_STATUS_DISCONNECTED": "offline",
                2: "offline",
                "INSTANCE_STATUS_IDLE": "idle",
                3: "idle",
            }.get(status, "unknown")
            if isinstance(status, (str, int)) and not isinstance(status, bool)
            else "unknown"
        )
        instance_id = _text(row.get("uuid"))
        # agy's commands.remoteControlDeepLink uses /r/<escaped instance ID>.
        url = REMOTE_URL + "r/" + urllib.parse.quote(instance_id, safe="") if instance_id else REMOTE_URL
        result.append({"name": name, "state": state, "updatedAt": _text(row.get("statusLastUpdatedAt")), "url": url})
    return sorted(result, key=lambda row: (row["state"] != "online", row["name"].casefold()))


def get_instances(email=None):
    """Allowlisted device data, or None when auth/network/protocol is unavailable."""
    actual, rows = fetch_instances()
    expected = _text(email).casefold()
    if expected and actual and expected != actual:
        return None
    return rows


def fetch_instances():
    """(signed-in email, instances); lets the caller overlap this with other
    work and check the account afterwards."""
    stored = _stored_credentials()
    if stored is None:
        return "", None
    return _email(stored), _fetch(stored)


def _fetch(stored):
    access = _usable_token(stored["token"])
    refreshed = not access
    if not access:
        access = _refresh(stored)
    if not access:
        return None
    project = _text(stored.get("project_id"))
    url = INSTANCE_URL + ("?" + urllib.parse.urlencode({"project": project}) if project else "")
    for attempt in range(2):
        response = fetch_json(
            url,
            headers={"Authorization": "Bearer " + access, "Accept": "application/json"},
            timeout=3,
        )
        if response.status == 200:
            return _instances(as_json(response.body))
        if response.status != 401 or refreshed or attempt:
            return None
        access = _refresh(stored)
        refreshed = True
        if not access:
            return None
    return None
