"""Which login a provider row belongs to, and the history series it records.

A tab reads a login folder (``~/.claude``, ``~/.claude-work``), and a folder
is not an account: log out and back in as someone else and the same folder
reads another quota. Recorded into one series, two accounts make a chart of
neither — the new login's 2 % drawn as a fall from the old one's 80 %, its
week compared with the other account's week, an ETA read off the jump back.

Where a normalizer can tell who is signed in, it says so as
``identity: {"id": <stable account id>, "label": <email or name>}`` — an
account id or address, never a token or key, which change when renewed or
rotated on the same account. This module turns that into the row's history
series:

- the first login ever seen in a tab keeps the series the tab had (``s`` and
  ``w``; ``s@work`` for an extra account), so the history recorded before
  carries on;
- every other login gets series of its own, ``s#<key>``, where ``key`` is a
  short digest of its id;
- a login seen before gets its series back, whichever tab it shows up in.

A row without an identity (an older CLI, a file that could not be read)
records into the series of the login last seen in its tab, or the tab's own
series when there has never been one: nothing changes for a provider that
cannot say who is signed in.

Each login's last reading is kept too, so a tab can list the other logins seen
in it and how much of their quota they had left: what someone who rotates
between accounts wants to know. A window whose reset has passed since reads 0 %.

The registry is a small JSON file next to the usage history (0600, only the
digest of an id and the label the provider showed are stored)::

    {"version": 1,
     "providers": {"claude": {
        "tabs": {"claude": {"owner": "3f9a1c2b", "last": "8d0e4f11"}},
        "logins": {"3f9a1c2b": {"label": "me@example.com", "suffix": "", "tab": "claude",
                                "firstSeen": 1.7e9, "lastSeen": 1.7e9,
                                "windows": [{"label": "5-hour session", "pct": 23, "resetAt": 1.7e9}]}}}}}
"""

import contextlib
import hashlib
import json
import os
import threading
import time

from . import config, paths
from .contract import bar_row, bars_section, finalize
from .messages import tr

VERSION = 1
FILE_NAME = "accounts-seen.json"
LOCK_NAME = ".accounts-seen.lock"
# A login unseen for this long is forgotten (its history series stay in the
# history file until they age out of it like any other).
FORGET_AFTER_S = 180 * 86400
# More logins than this per provider and the longest unseen go first; a tab's
# own first login is kept for as long as it is not too old.
MAX_LOGINS = 64
# The last reading of a login is refreshed at most this often unless something
# else changed: the file is written for the logins *not* signed in, and a
# minute of difference in their last reading is no difference at all.
SNAPSHOT_EVERY_S = 60
# With nothing else changed, a login's "last seen" is written this often.
TOUCH_EVERY_S = 600
# A tab that read a login within this long is still reading it.
SAME_LOGIN_S = 3 * TOUCH_EVERY_S
# How long to wait for another process holding the registry. Past that the row
# is resolved from what is on disk without writing: the answer is the same one
# the next poll writes.
LOCK_WAIT_S = 2.0
# How many other logins a tab lists.
MAX_SHOWN = 6

_THREAD_LOCK = threading.Lock()


def path():
    return config.env("ACCOUNTS_FILE", os.path.join(paths.history_dir(), FILE_NAME))


def digest(base, raw_id):
    """A short, stable key for one login of one provider. The id itself is never stored."""
    return hashlib.sha256(f"{base}\0{raw_id}".encode()).hexdigest()[:8]


def mark(provider, raw_id, label=""):
    """Say who ``provider`` (a normalized row) is signed in as."""
    raw_id = str(raw_id or "").strip()
    if raw_id:
        provider["identity"] = {"id": raw_id, "label": str(label or "").strip()}
    return provider


# ── The file ─────────────────────────────────────────────────────────────────


def _empty():
    return {"version": VERSION, "providers": {}}


def _read(file_path):
    try:
        with open(file_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        return _empty()
    return data


def _write(file_path, data):
    from .history import replace

    directory = os.path.dirname(file_path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".accounts-seen.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"), ensure_ascii=False)
        replace(tmp, file_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


@contextlib.contextmanager
def _registry():
    """The registry as a dict, and whether changes to it can be saved.

    Yields ``(data, save)``: ``save()`` writes ``data`` back when the lock
    was taken, and does nothing otherwise — the same answer is written by the
    next poll that gets the lock. Never raises for a missing, unreadable or
    unwritable file: who is signed in is worth a series of its own, not the
    provider's tab."""
    from .historyio import _acquire, _release

    file_path = path()
    with _THREAD_LOCK:
        lock = None
        with contextlib.suppress(OSError):
            os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
            lock = _acquire(os.path.join(os.path.dirname(file_path) or ".", LOCK_NAME), LOCK_WAIT_S)
        try:
            data = _read(file_path)

            def save():
                if lock is not None:
                    with contextlib.suppress(OSError):
                        _write(file_path, data)

            yield data, save
        finally:
            if lock is not None:
                _release(lock)


# ── Resolving a row ──────────────────────────────────────────────────────────


def _windows(provider):
    """The row's quota windows as the last reading worth keeping: label, use
    and reset of each one the provider reported."""
    out = []
    for window in provider.get("quotaWindows") or []:
        if not isinstance(window, dict) or not window.get("available") or not window.get("showMeter", True):
            continue
        kept = finalize({"label": window.get("label") or "", "pct": window.get("pct") or 0, "resetAt": window.get("resetAt") or 0})
        out.append(kept)
    return out


def _num(value):
    """A stored number, 0 for anything that is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _forget_old(logins, tabs, now):
    owners = {state.get("owner") for state in tabs.values() if isinstance(state, dict)}

    def gone(login):
        return not isinstance(login, dict) or now - _num(login.get("lastSeen") or login.get("firstSeen")) > FORGET_AFTER_S

    for key in [k for k, login in logins.items() if gone(login)]:
        del logins[key]
    if len(logins) > MAX_LOGINS:
        spare = sorted((k for k in logins if k not in owners), key=lambda k: _num(logins[k].get("lastSeen")))
        for key in spare[: len(logins) - MAX_LOGINS]:
            del logins[key]


def resolve(provider, base, tab, legacy="", now=None):
    """Settle which login ``provider`` (normalized, not yet relabelled) is.

    ``base`` is the provider (``claude``), ``tab`` the row's id (``claude`` or
    ``claude@work``) and ``legacy`` the suffix the tab's series had before
    logins were told apart ("" or ``@work``).

    Returns ``(suffix, public, others)``: the suffix for the row's history
    series, what the row may show about its login (``{"key", "label",
    "sameAs"}``, or None when it cannot tell), and the other logins last seen
    in this tab, newest first. The registry is updated on the way.
    """
    now = time.time() if now is None else now
    ident = provider.pop("identity", None)
    raw = str(ident.get("id") or "").strip() if isinstance(ident, dict) else ""
    label = str(ident.get("label") or "").strip() if isinstance(ident, dict) else ""

    if not raw:
        # Nothing says who this is: keep recording for whoever was here last.
        # Read only — most providers never name a login, and cost nothing here.
        entry = _read(path())["providers"].get(base)
        entry = entry if isinstance(entry, dict) else {}
        logins = entry.get("logins") if isinstance(entry.get("logins"), dict) else {}
        state = (entry.get("tabs") or {}).get(tab) if isinstance(entry.get("tabs"), dict) else None
        last = state.get("last") if isinstance(state, dict) else None
        login = logins.get(last) if last else None
        suffix = login.get("suffix", legacy) if isinstance(login, dict) else legacy
        return suffix, None, _others(logins, tab, last) if last else []

    with _registry() as (data, save):
        # Hand-edited or half-understood, a part that is not the shape
        # expected is started over rather than trusted.
        entry = data["providers"].get(base)
        if not isinstance(entry, dict):
            entry = data["providers"][base] = {}
        for part in ("tabs", "logins"):
            if not isinstance(entry.get(part), dict):
                entry[part] = {}
        tabs, logins = entry["tabs"], entry["logins"]
        state = tabs.get(tab) if isinstance(tabs.get(tab), dict) else {}

        key = digest(base, raw)
        login = logins.get(key)
        changed = False
        if not isinstance(login, dict):
            # A tab's series belong to the first login seen in it (also when
            # it comes back after being forgotten); later ones start their own.
            if not state.get("owner") or state.get("owner") == key:
                state["owner"] = key
                suffix = legacy
            else:
                suffix = "#" + key
            login = {"suffix": suffix, "firstSeen": now, "tab": tab}
            logins[key] = login
            changed = True

        # Another tab reading this same login right now: one account signed in
        # in two folders. Both rows record into its one series.
        same_as = ""
        for other_tab, other in tabs.items():
            if other_tab != tab and isinstance(other, dict) and other.get("last") == key and now - _num(other.get("seen")) <= SAME_LOGIN_S:
                same_as = other_tab
                break

        if label and login.get("label") != label:
            login["label"] = label
            changed = True
        if state.get("last") != key:
            state["last"] = key
            changed = True
        if not same_as and login.get("tab") != tab:
            login["tab"] = tab
            changed = True

        if provider.get("ok") is True:
            age = now - _num(login.get("lastSeen"))
            windows = _windows(provider)
            resets = [w.get("resetAt") for w in windows] != [w.get("resetAt") for w in login.get("windows") or [] if isinstance(w, dict)]
            if windows and windows != login.get("windows") and (changed or resets or age >= SNAPSHOT_EVERY_S):
                login["windows"] = windows
                changed = True
            if changed or age >= TOUCH_EVERY_S:
                login["lastSeen"] = now
                state["seen"] = now
                changed = True
        tabs[tab] = state

        if changed:
            _forget_old(logins, tabs, now)
            save()
        public = {"key": key, "label": login.get("label") or "", "sameAs": same_as}
        return login.get("suffix", legacy), public, _others(logins, tab, key)


def _others(logins, tab, current):
    found = [dict(login) for key, login in logins.items() if key != current and isinstance(login, dict) and login.get("tab") == tab]
    found.sort(key=lambda login: _num(login.get("lastSeen")), reverse=True)
    return found[:MAX_SHOWN]


def others_section(others, now=None):
    """The "Other logins seen here" bars: each login's last reading, a window
    that has reset since at 0 %, and the soonest reset of what is still used."""
    now = time.time() if now is None else now
    groups = []
    for login in others:
        rows = []
        soonest = 0
        top = 0
        for window in login.get("windows") or []:
            if not isinstance(window, dict):
                continue
            reset = _num(window.get("resetAt"))
            pct = 0 if 0 < reset <= now else _num(window.get("pct"))
            row = bar_row(window.get("label") or "", pct, tooltip=tr("Its last reading here. A window that has reset since shows 0%."))
            if window.get("labelI18n"):
                row["labelI18n"] = window["labelI18n"]
            rows.append(row)
            top = max(top, pct)
            if pct > 0 and reset > now:
                soonest = reset if not soonest else min(soonest, reset)
        if rows:
            groups.append({"label": login.get("label") or tr("Another login"), "color": "#94a3b8", "pct": top, "resetAt": soonest, "rows": rows})
    return bars_section(groups, title=tr("Other logins seen here"))
