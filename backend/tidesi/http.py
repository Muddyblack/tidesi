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
    """Send an upstream URL to the loopback test server named by TIDESI_E2E_HTTP_BASE.

    Inert when unset. The base must be a plain http://127.0.0.1:<port> origin, so
    a stray value cannot forward credentials to a remote host. The original
    scheme and host travel in the path: /<scheme>/<host>/<path>.
    """
    base = os.environ.get("TIDESI_E2E_HTTP_BASE")
    if not base:
        return url

    from urllib.parse import urlsplit, urlunsplit

    origin = urlsplit(base)
    try:
        plain = (
            origin.scheme == "http"
            and origin.hostname == "127.0.0.1"
            and origin.port
            and not (origin.path.strip("/") or origin.query or origin.username)
        )
    except ValueError:
        plain = False
    if not plain:
        raise ValueError("TIDESI_E2E_HTTP_BASE must be a plain http://127.0.0.1:<port> loopback origin")

    target = urlsplit(url)
    if target.hostname in ("127.0.0.1", "localhost", "::1"):
        return url
    return urlunsplit((origin.scheme, origin.netloc, f"/{target.scheme}/{target.netloc}{target.path or '/'}", target.query, ""))


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
