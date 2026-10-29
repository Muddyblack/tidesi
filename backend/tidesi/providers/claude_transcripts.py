"""Bring ~/.claude/stats-cache.json up to date from Claude Code's transcripts.

Claude Code stopped refreshing stats-cache.json (its ``lastComputedDate`` stays
frozen), but every session is still written to ~/.claude/projects/**/*.jsonl.
Days after the cache's last computed date are rebuilt from those transcripts
and merged into the cache blob, so the usage charts keep moving.

Each transcript is parsed once: a small per-file summary is kept in a cache
keyed by path, size and mtime, so only new or growing files are read again.
The first run over a large history is spread across several refreshes by a time
budget; whatever is parsed by then is shown.
"""

import datetime
import json
import os
import tempfile
import time

from .. import paths
from .claude_credentials import claude_config_dir

_CACHE_VERSION = 1
_BUDGET_SECONDS = 8.0


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0


def _local(ts):
    """(YYYY-MM-DD, hour, epoch) in local time for an ISO-8601 UTC stamp."""
    try:
        moment = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    local = moment.astimezone()
    return local.strftime("%Y-%m-%d"), local.hour, moment.timestamp()


def parse_transcript(path):
    """Summarise one transcript: per-day messages, tool calls, tokens by model,
    hours, plus when the session started and how long it ran."""
    assistant = {}  # message id -> (date, hour, model, usage, tools); the last line wins
    days = {}
    first = last = None
    messages = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                is_assistant = '"type":"assistant"' in line
                is_user = '"type":"user"' in line
                if not (is_assistant or is_user) or '"timestamp"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                stamp = _local(entry.get("timestamp"))
                if not stamp:
                    continue
                first = stamp[2] if first is None else min(first, stamp[2])
                last = stamp[2] if last is None else max(last, stamp[2])
                message = entry.get("message") or {}
                if entry.get("type") == "user":
                    content = message.get("content")
                    tool_result = (
                        isinstance(content, list) and content and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
                    )
                    if entry.get("isMeta") or tool_result:
                        continue
                    day = days.setdefault(stamp[0], {"messages": 0, "tools": 0, "hours": {}, "models": {}})
                    day["messages"] += 1
                    day["hours"][str(stamp[1])] = day["hours"].get(str(stamp[1]), 0) + 1
                    messages += 1
                elif entry.get("type") == "assistant":
                    usage = message.get("usage")
                    if not isinstance(usage, dict):
                        continue
                    content = message.get("content")
                    tools = sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use") if isinstance(content, list) else 0
                    key = message.get("id") or entry.get("uuid") or f"{stamp[2]}"
                    assistant[key] = (stamp[0], stamp[1], str(message.get("model") or ""), usage, tools)
    except OSError:
        return None
    for date, hour, model, usage, tools in assistant.values():
        if not model or model.startswith("<"):
            continue
        day = days.setdefault(date, {"messages": 0, "tools": 0, "hours": {}, "models": {}})
        day["messages"] += 1
        messages += 1
        day["tools"] += tools
        day["hours"][str(hour)] = day["hours"].get(str(hour), 0) + 1
        m = day["models"].setdefault(model, {"in": 0, "out": 0, "cr": 0, "cc": 0})
        m["in"] += _num(usage.get("input_tokens"))
        m["out"] += _num(usage.get("output_tokens"))
        m["cr"] += _num(usage.get("cache_read_input_tokens"))
        m["cc"] += _num(usage.get("cache_creation_input_tokens"))
    if first is None:
        return {"days": {}, "start": "", "duration": 0, "messages": 0}
    start = datetime.datetime.fromtimestamp(first).strftime("%Y-%m-%d")
    return {"days": days, "start": start, "duration": int((last - first) * 1000), "messages": messages}


def _cache_path():
    return os.path.join(paths.cache_dir(), "claude-transcripts.json")


def _load_cache():
    try:
        with open(_cache_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) and data.get("version") == _CACHE_VERSION else None
    return files if isinstance(files, dict) else {}


def _save_cache(files):
    target = _cache_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".claude-transcripts-", dir=os.path.dirname(target))
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"version": _CACHE_VERSION, "files": files}, handle, separators=(",", ":"))
        os.replace(temporary, target)
    except OSError:
        pass


def _transcripts(root, since_epoch):
    found = []
    for directory, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            full = os.path.join(directory, name)
            try:
                info = os.stat(full)
            except OSError:
                continue
            if info.st_mtime >= since_epoch:
                depth = os.path.relpath(directory, root).count(os.sep)
                found.append((full, info.st_size, info.st_mtime, depth == 0))
    found.sort(key=lambda f: f[2], reverse=True)
    return found


def supplement(stats, now=None, home=None, budget=_BUDGET_SECONDS):
    """``stats`` (a stats-cache.json blob, possibly empty) plus every day after
    its lastComputedDate rebuilt from the transcripts."""
    return _build(stats, now, home, budget, "")[0]


def _build(stats, now, home, budget, through):
    """supplement(), limited to days up to ``through`` (YYYY-MM-DD) when given,
    and whether every transcript was read: the budget may cut it short."""
    stats = dict(stats) if isinstance(stats, dict) else {}
    root = os.path.join(home or claude_config_dir(), "projects")
    if not os.path.isdir(root):
        return stats, True
    now = now if now is not None else time.time()
    cutoff = str(stats.get("lastComputedDate") or "")[:10]
    try:
        since = datetime.datetime.strptime(cutoff, "%Y-%m-%d").timestamp() if cutoff else 0
    except ValueError:
        cutoff, since = "", 0

    cache = _load_cache()
    deadline = time.monotonic() + budget
    fresh = {}
    complete = True
    dirty = False
    for path, size, mtime, top_level in _transcripts(root, since):
        entry = cache.get(path)
        if not (entry and entry.get("size") == size and entry.get("mtime") == mtime):
            if time.monotonic() > deadline:
                complete = False
                if entry:
                    fresh[path] = entry
                continue
            summary = parse_transcript(path)
            if summary is None:
                continue
            entry = dict(summary, size=size, mtime=mtime, top=top_level)
            dirty = True
        fresh[path] = entry
    if dirty or len(fresh) != len(cache):
        _save_cache(fresh)

    activity, tokens, usage, hours = {}, {}, {}, {}
    sessions = 0
    longest = None
    for entry in fresh.values():
        started = entry.get("start") or ""
        if entry.get("top") and cutoff < started and (not through or started <= through):
            sessions += 1
            activity.setdefault(entry["start"], {"sessions": 0})["sessions"] += 1
        if (
            entry.get("top")
            and cutoff < started
            and (not through or started <= through)
            and (longest is None or entry["duration"] > longest["duration"])
        ):
            longest = {"duration": entry["duration"], "messageCount": entry["messages"], "date": entry["start"]}
        for date, day in entry.get("days", {}).items():
            if date <= cutoff or (through and date > through):
                continue
            row = activity.setdefault(date, {"sessions": 0})
            row["messages"] = row.get("messages", 0) + day["messages"]
            row["tools"] = row.get("tools", 0) + day["tools"]
            for hour, count in day["hours"].items():
                hours[hour] = hours.get(hour, 0) + count
            for model, m in day["models"].items():
                tokens.setdefault(date, {})
                tokens[date][model] = tokens[date].get(model, 0) + m["in"] + m["out"] + m["cr"] + m["cc"]
                u = usage.setdefault(model, {"in": 0, "out": 0, "cr": 0, "cc": 0})
                for k in u:
                    u[k] += m[k]
    if not activity:
        return stats, complete

    stats["dailyActivity"] = list(stats.get("dailyActivity") or []) + [
        {"date": d, "messageCount": r.get("messages", 0), "sessionCount": r["sessions"], "toolCallCount": r.get("tools", 0)}
        for d, r in sorted(activity.items())
    ]
    stats["dailyModelTokens"] = list(stats.get("dailyModelTokens") or []) + [{"date": d, "tokensByModel": t} for d, t in sorted(tokens.items())]
    merged = {k: dict(v) for k, v in (stats.get("modelUsage") or {}).items() if isinstance(v, dict)}
    for model, u in usage.items():
        row = merged.setdefault(model, {})
        row["inputTokens"] = _num(row.get("inputTokens")) + u["in"]
        row["outputTokens"] = _num(row.get("outputTokens")) + u["out"]
        row["cacheReadInputTokens"] = _num(row.get("cacheReadInputTokens")) + u["cr"]
        row["cacheCreationInputTokens"] = _num(row.get("cacheCreationInputTokens")) + u["cc"]
    stats["modelUsage"] = merged
    counts = dict(stats.get("hourCounts") or {})
    for hour, n in hours.items():
        counts[hour] = _num(counts.get(hour)) + n
    stats["hourCounts"] = counts
    stats["totalSessions"] = _num(stats.get("totalSessions")) + sessions
    stats["totalMessages"] = _num(stats.get("totalMessages")) + sum(r.get("messages", 0) for r in activity.values())
    if longest and longest["duration"] > _num((stats.get("longestSession") or {}).get("duration")):
        stats["longestSession"] = longest
    stats.setdefault("firstSessionDate", min(activity) + "T00:00:00.000Z")
    stats["lastComputedDate"] = through or datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    return stats, complete


# ── The widget's own copy ────────────────────────────────────────────────────
# Claude prunes old transcripts, so days rebuilt from them are folded into a
# file of ours once finished (everything before today); Claude's own
# stats-cache.json is only read to seed it.


def archive_path():
    return os.path.join(paths.data_dir(), "claude-stats.json")


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".claude-stats-", dir=os.path.dirname(path))
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
        os.replace(temporary, path)
    except OSError:
        pass


def load(legacy, now=None, home=None, budget=_BUDGET_SECONDS):
    """The Claude stats blob: our archive (seeded from Claude's old
    stats-cache.json, ``legacy``, while it exists) plus the transcripts."""
    now = now if now is not None else time.time()
    archive = _read(archive_path())
    legacy = legacy if isinstance(legacy, dict) else {}
    if str(legacy.get("lastComputedDate") or "") > str(archive.get("lastComputedDate") or ""):
        archive = legacy
    yesterday = (datetime.datetime.fromtimestamp(now) - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    if str(archive.get("lastComputedDate") or "") < yesterday:
        frozen, complete = _build(archive, now, home, budget, yesterday)
        if complete and frozen is not archive and frozen.get("lastComputedDate") == yesterday:
            archive = frozen
    if archive and archive != _read(archive_path()):
        _write(archive_path(), archive)
    return _build(archive, now, home, budget, "")[0]
