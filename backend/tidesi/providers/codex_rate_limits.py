"""Drives `codex app-server --stdio` over JSON-RPC to read plan rate limits.

Popen with line-buffered stdin/stdout; 15s for startup and 5s for reading limits, and the
child is always reaped.

Replies are read on a helper thread rather than with `selectors`: Windows can
only select() on sockets, never on a pipe, and a thread works the same on both.

Successful replies are cached for a short TTL, keyed by SHA-256 of the access
token plus the normalized CODEX_HOME, so a refresh inside the TTL (another
widget instance, a pricing-triggered refresh) does not launch the app-server
again. The collector stores a successful HTTP-fallback reply here too. Only the
limit fields are written, never the token or the path. Replies carry
`_codexSource`/`_codexAge` metadata that the collector pops before normalizing.
A failed refresh is not papered over with older data here: the frontends
already keep their last good values on screen and mark them stale.
"""

import hashlib
import json
import math
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time

from .. import config, paths

TTL_SECONDS = 120
STARTUP_TIMEOUT_SECONDS = 15
READ_TIMEOUT_SECONDS = 5
_FIELDS = ("rateLimits", "rate_limit", "rateLimitsByLimitId", "additional_rate_limits", "_codexNoLimits")


def _pump(stream, lines):
    """Reader thread: hand every line on, then None for end of stream."""
    try:
        for line in stream:
            lines.put(line)
    except (OSError, ValueError):
        # ValueError: the stream was closed under us during cleanup.
        pass
    finally:
        lines.put(None)


def _read_until_id(lines, want_id, timeout):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError from None
        if line is None:
            # Leave the end marker for the next phase, which would otherwise
            # sit out its whole deadline on a stream that has already ended.
            lines.put(None)
            return None
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("id") == want_id:
            return obj


def _stop(proc):
    """Terminate the child and everything it started, then reap it.

    On Windows an npm install is codex.cmd, so the child is cmd.exe with node
    and the native server below it. Terminating cmd.exe alone would orphan the
    server on every poll, so the whole tree goes, by pid, while the parent is
    still there to name it."""
    if paths.IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                **paths.no_window(),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass


def _live_codex_rate_limits():
    """One live `codex app-server` round trip; the raw reply dict."""
    # The resolved path, not the bare name: Popen does not apply PATHEXT, so on
    # Windows "codex" would never find codex.cmd or codex.exe.
    codex = shutil.which("codex")
    if codex is None:
        return {}

    try:
        proc = subprocess.Popen(
            [codex, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **paths.no_window(),
        )
    except OSError:
        return {}

    # Popen fills both pipes because stdin/stdout are PIPE above, but they are
    # Optional on the type level — bind them once so the child is still reaped
    # if that ever fails rather than raising past the cleanup below.
    stdin, stdout = proc.stdin, proc.stdout

    result = {}
    reader = None
    try:
        if stdin is None or stdout is None:
            return {}

        lines = queue.Queue()
        reader = threading.Thread(target=_pump, args=(stdout, lines), daemon=True)
        reader.start()

        initialize = json.dumps(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "kde-ai-usage", "version": "1"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        try:
            stdin.write(initialize + "\n")
            stdin.flush()
        except (BrokenPipeError, OSError):
            return {}

        try:
            initialized = _read_until_id(lines, 1, STARTUP_TIMEOUT_SECONDS)
        except TimeoutError:
            return {"_codexError": "Codex app-server startup timed out"}
        if initialized is not None:
            read_limits = json.dumps({"id": 2, "method": "account/rateLimits/read", "params": None})
            try:
                stdin.write(read_limits + "\n")
                stdin.flush()
            except (BrokenPipeError, OSError):
                read_limits_reply = None
            else:
                try:
                    read_limits_reply = _read_until_id(lines, 2, READ_TIMEOUT_SECONDS)
                except TimeoutError:
                    result = {"_codexError": "Codex app-server rate-limit request timed out"}
                    read_limits_reply = None
            if read_limits_reply is not None and "result" in read_limits_reply:
                result = read_limits_reply.get("result") or {}
                if isinstance(result, dict) and not result:
                    result = {"_codexNoLimits": True}
            elif read_limits_reply is not None:
                error = read_limits_reply.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                if isinstance(message, str) and ("token_expired" in message.lower() or "authentication token is expired" in message.lower()):
                    result = {"_codexError": "token expired"}
    finally:
        try:
            if stdin is not None:
                stdin.close()
        except Exception:
            pass
        _stop(proc)
        # Closing stdout while the reader is blocked in it can deadlock on the
        # stream's lock, so only close it once the reader has seen the end. A
        # grandchild still holding the pipe open leaves the daemon thread
        # parked there, which costs nothing: this process exits right after.
        if reader is not None:
            reader.join(timeout=1)
        if stdout is not None and (reader is None or not reader.is_alive()):
            try:
                stdout.close()
            except Exception:
                pass

    return result


def identity_key(token, home):
    """Opaque cache key for the access token and normalized Codex home."""
    normalized_home = os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(home))))
    return hashlib.sha256((token + "\0" + normalized_home).encode("utf-8")).hexdigest()


def _has_limits(data):
    """A well-formed limit payload, including an explicit empty result."""
    if not isinstance(data, dict):
        return False
    if data.get("_codexNoLimits") is True:
        return True
    if "rateLimits" in data:
        return isinstance(data["rateLimits"], dict)
    if "rate_limit" in data:
        return isinstance(data["rate_limit"], dict)
    if "rateLimitsByLimitId" in data:
        return isinstance(data["rateLimitsByLimitId"], dict)
    if "additional_rate_limits" in data:
        return isinstance(data["additional_rate_limits"], list)
    return False


def _cache_path(key):
    return os.path.join(config.cache_dir(), f"codex-rate-limits-{key}.json")


def _read_cache(key):
    """A validated same-identity cache payload, or None."""
    try:
        with open(_cache_path(key), encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("identity") != key:
        return None
    saved_at = payload.get("savedAt")
    data = payload.get("data")
    if isinstance(saved_at, bool) or not isinstance(saved_at, (int, float)) or not math.isfinite(saved_at):
        return None
    if not _has_limits(data):
        return None
    return {"savedAt": float(saved_at), "data": {field: data[field] for field in _FIELDS if field in data}}


def _write_cache(key, saved_at, data):
    path = _cache_path(key)
    payload = {
        "version": 1,
        "identity": key,
        "savedAt": saved_at,
        "data": {field: data[field] for field in _FIELDS if field in data},
    }
    directory = os.path.dirname(path)
    try:
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".codex-rate-limits-", dir=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, separators=(",", ":"))
            os.replace(temporary, path)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass
    except OSError:
        pass


def remember(token, home, data, now=None):
    """Cache a successful reply obtained another way (the HTTP fallback)."""
    if token and _has_limits(data):
        _write_cache(identity_key(token, home), time.time() if now is None else now, data)


def get_codex_rate_limits(token="", home="", now=None):
    """Rate limits with a short positive TTL cache.

    A fresh cache hit returns the cached data without launching the app-server
    and is tagged `_codexSource: "cached"` with its `_codexAge`. A live reply is
    "live" with age 0; a failed attempt is "unavailable" with no age. Without a
    token there is no identity to scope the cache to, so every call is live.
    """
    checked_at = time.time() if now is None else now
    key = identity_key(token, home) if token else None
    if key is not None:
        cached = _read_cache(key)
        if cached is not None:
            age = checked_at - cached["savedAt"]
            if 0 <= age <= TTL_SECONDS:
                return {**cached["data"], "_codexSource": "cached", "_codexAge": int(age)}

    result = _live_codex_rate_limits()
    if not _has_limits(result):
        return {**result, "_codexSource": "unavailable", "_codexAge": None}
    if key is not None:
        _write_cache(key, checked_at, result)
    return {**result, "_codexSource": "live", "_codexAge": 0}
