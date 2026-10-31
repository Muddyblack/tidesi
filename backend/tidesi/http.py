"""Shared credential + HTTP plumbing for the provider helpers.

`urllib.request` is imported lazily inside fetch_json, never at module scope:
it pulls in ssl/http.client/email and costs ~60ms of interpreter startup that
--normalize, cache-hit status lookups, and unconfigured providers should never
pay. See plan.md constraint #3.
"""

import os

from .messages import tr


class HttpResult:
    __slots__ = ("status", "body")

    def __init__(self, status, body):
        self.status = status
        self.body = body


def clean_credential(value):
    """Strip whitespace that cannot be part of an HTTP credential."""
    return str(value or "").translate(str.maketrans("", "", "\n\r ")).strip()


def resolve_key(widget_var, env_var, *files):
    """Credential precedence, identical for every provider: the value the
    widget passed in, then a conventional environment variable, then the
    first readable config file.

    Every source is cleaned the same way: strip the surrounding whitespace,
    then drop any \n, \r or space left embedded in the middle. A credential
    pasted with a trailing newline would otherwise reach urllib, which
    rejects the header with a ValueError — and that exception carries the
    credential into the traceback, defeating the guarantee that no secret
    leaves this package. This only covers whitespace/CR/LF; a credential
    containing other control characters would still reach urllib as-is.

    `widget_var` and `env_var` each accept either a single name or a sequence
    of them, tried in order. Several vendors ship two spellings of the same
    variable — Moonshot is read as both MOONSHOT_API_KEY and KIMI_API_KEY,
    Z.AI documents Z_AI_API_KEY while this package reads ZAI_TOKEN — and a
    provider that knows only one of them reports "no token configured" at a
    user who did set the key, which is the least debuggable failure we can
    produce.
    """

    value = ""
    for source in (widget_var, env_var):
        names = (source,) if isinstance(source, str) else source
        for name in names or ():
            if not name:
                continue
            value = clean_credential(os.environ.get(name, ""))
            if value:
                break
        if value:
            break
    if not value:
        for path in files:
            if not path or not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    value = clean_credential(f.read())
            except OSError:
                value = ""
            if value:
                break
    return value


def http_error_text(status):
    """The frontend-facing error vocabulary — deliberately shorter than the
    per-provider wording (see docs/provider-contract.md)."""
    if status in (0, None, ""):
        return "offline"
    if status == 401:
        return "token expired"
    if status == 403:
        return "access denied"
    if status == 429:
        return "rate limited"
    return f"err {status}"


def error_json(message):
    return {"hasKey": False, "keyValid": False, "error": message}


def http_error_json(label, status, auth_message=None):
    """Maps a status code onto the usual wording. Providers with a more
    specific authentication message than "Invalid <label> credential" pass it
    as auth_message."""
    if status in (401, 403):
        return error_json(auth_message or tr("Invalid %1 credential", label))
    if status in (0, None, ""):
        return error_json(tr("%1 network error", label))
    return error_json(tr("%1 HTTP %2", label, status))


def route_e2e_url(url):
    """Route an upstream URL to the explicitly configured loopback test server.

    The hook is inert unless TIDESI_E2E_HTTP_BASE is set. Restricting that base
    to an IP-literal loopback HTTP address makes accidental production use fail
    closed rather than forwarding credentials to an arbitrary remote host.
    """
    base = os.environ.get("TIDESI_E2E_HTTP_BASE")
    if not base:
        return url

    import ipaddress
    from urllib.parse import urlsplit, urlunsplit

    target = urlsplit(url)
    destination = urlsplit(base)
    try:
        destination_ip = ipaddress.ip_address(destination.hostname or "")
        destination_port = destination.port
    except ValueError as exc:
        raise ValueError("TIDESI_E2E_HTTP_BASE must use an IP-literal loopback address") from exc

    if (
        destination.scheme != "http"
        or not destination_ip.is_loopback
        or not destination_port
        or destination.username is not None
        or destination.password is not None
        or destination.path not in ("", "/")
        or destination.query
        or destination.fragment
    ):
        raise ValueError("TIDESI_E2E_HTTP_BASE must be a plain loopback HTTP origin")
    if target.scheme not in ("http", "https") or not target.hostname or target.username is not None or target.password is not None:
        raise ValueError("E2E HTTP routing requires an absolute HTTP(S) URL without user info")

    try:
        target_is_loopback = ipaddress.ip_address(target.hostname).is_loopback
    except ValueError:
        target_is_loopback = target.hostname.casefold() == "localhost"
    if target_is_loopback:
        return url

    routed_path = f"/{target.scheme}/{target.netloc}{target.path or '/'}"
    return urlunsplit((destination.scheme, destination.netloc, routed_path, target.query, ""))


def fetch_json(url, headers=None, timeout=10, fixture_path=None, data=None):
    """Returns an HttpResult. `fixture_path` replays a recorded response as a
    200 so tests never touch the network — the *_RESPONSE_FILE hooks. `data`
    (bytes) turns the request into a POST, for RPC-style endpoints."""
    if fixture_path and os.path.isfile(fixture_path):
        try:
            with open(fixture_path, encoding="utf-8") as f:
                return HttpResult(200, f.read())
        except OSError:
            return HttpResult(0, "")

    import urllib.error
    import urllib.request

    url = route_e2e_url(url)
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResult(resp.status, resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return HttpResult(e.code, body)
    except (urllib.error.URLError, TimeoutError, OSError):
        return HttpResult(0, "")


def as_json(text):
    """Parse JSON, returning None on any failure — mirrors the bash
    as_json/null-on-invalid convention."""
    import json

    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None
