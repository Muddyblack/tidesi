"""Small state files the Plasma widget keeps outside its KConfig.

Plasma rewrites a panel's whole applet config file on every change and keeps
one config group per widget instance. Two things fit that badly, so they live
here instead:

* the last successful usage envelope, replayed (marked stale) while a freshly
  started widget waits for its first fetch. It carries account labels such as
  e-mail addresses, so it is a private cache file, not a line in
  ``plasma-org.kde.plasma.desktop-appletsrc``;
* the settings every widget instance shares (enabled providers, keys,
  appearance). Each instance merges its own changes in and adopts everyone
  else's, so a second panel or screen never needs to be configured again.
"""

import json
import os
import re
import tempfile
from contextlib import contextmanager

from . import accounts, config, paths
from .contract import PROVIDER_ICONS
from .historyio import _acquire, _unlock

SNAPSHOT_FILENAME = "last-snapshot.json"
SHARED_SETTINGS_FILENAME = "plasma-shared-settings.json"


def snapshot_path():
    return os.path.join(config.cache_dir(), SNAPSHOT_FILENAME)


def shared_settings_path():
    """One file per installed widget id, so the renamed test copy that
    `make install` adds never rewrites the real widget's settings."""
    override = config.env("PLASMA_SHARED_SETTINGS")
    if override:
        return override
    widget_id = re.sub(r"[^A-Za-z0-9._-]", "", config.env("WIDGET_ID", ""))
    name = f"plasma-shared-settings-{widget_id}.json" if widget_id else SHARED_SETTINGS_FILENAME
    return os.path.join(paths.config_dir(), name)


def _write_private(path, payload):
    """Atomically replace ``path`` with ``payload`` as JSON, readable by the user only."""
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    except OSError:
        return False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, separators=(",", ":"), ensure_ascii=False)
        os.replace(temporary, path)
        return True
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        return False


@contextmanager
def _locked(path):
    """Hold ``path``.lock around a read-merge-write: two widgets saving at the
    same moment would otherwise each drop the other's change."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    except OSError:
        pass
    lock = _acquire(path + ".lock", 2.0)
    try:
        yield
    finally:
        if lock is not None:
            _unlock(lock)
            lock.close()


def _read_object(path):
    try:
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _current_providers(providers):
    """An older install's snapshot can contain removed providers or icons."""
    if not isinstance(providers, list):
        return []
    result = []
    for provider in providers:
        if not isinstance(provider, dict) or accounts.base_id(provider.get("id")) not in config.ALL_PROVIDERS:
            continue
        provider = dict(provider)
        if "icon" in provider:
            provider["icon"] = PROVIDER_ICONS.get(accounts.base_id(provider["id"]), "")
        result.append(provider)
    return result


def save_snapshot(envelope):
    """Fold the providers that answered in ``envelope`` into the last good one.

    Each widget fetches only the providers it shows, so the file is merged per
    provider id rather than replaced: one instance's Claude-only refresh must
    not drop the Codex values another instance saved. Errored providers never
    replace a good entry.
    """
    providers = envelope.get("providers") if isinstance(envelope, dict) else None
    if not isinstance(providers, list):
        return False
    good = [p for p in _current_providers(providers) if not p.get("error")]
    if not good:
        return False
    path = snapshot_path()
    with _locked(path):
        stored = _read_object(path) or {}
        by_id = {p["id"]: p for p in _current_providers(stored.get("providers"))}
        for provider in good:
            by_id[provider["id"]] = provider
        merged = dict(envelope)
        merged["providers"] = list(by_id.values())
        return _write_private(path, merged)


def last_snapshot():
    """The last good envelope, or an empty object when there is none."""
    stored = _read_object(snapshot_path()) or {}
    if "providers" in stored:
        stored["providers"] = _current_providers(stored["providers"])
        if stored.get("active") and accounts.base_id(stored["active"]) not in config.ALL_PROVIDERS:
            stored["active"] = next((p["id"] for p in stored["providers"]), "")
    return stored


def merge_shared_settings(patch):
    """Merge ``patch`` into the shared settings file and return the result.

    Only keys present in ``patch`` change, so two instances editing different
    settings at the same time never undo each other. A ``None`` value removes
    a key. An empty patch is a plain read.
    """
    path = shared_settings_path()
    if not isinstance(patch, dict) or not patch:
        return _read_object(path) or {}
    with _locked(path):
        current = _read_object(path) or {}
        merged = dict(current)
        for key, value in patch.items():
            if not isinstance(key, str) or not key:
                continue
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        if merged != current and not os.path.islink(path):
            _write_private(path, merged)
        return merged
