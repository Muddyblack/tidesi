"""Local agent sessions across supported CLIs.

Returns a redacted list for the Sessions tab. Titles and workspace folder
names are fine; absolute paths, raw session IDs, and transcript contents never
leave here. Claude Code writes no separate summary, so its row title is a
clipped preview of that session's own *opening* prompt (from `history.jsonl`,
one line per turn — the first one only), produced by `_clip_title()`. The raw
prompt stays in the backend. Antigravity titles can contain the first user
prompt or an editor artifact heading. Clipped title previews are shortened,
not scrubbed of secrets, and may contain sensitive prompt or artifact text in
the local UI; the full prompt is never exposed. Cline's own `prompt` field
looks similar but is not: providers/cline.py explains why it stays untouched.

Each resumable entry also carries an opaque ``openKey`` (a digest of
provider + session id) so the frontends can re-open exactly that session in
the user's terminal *without* ever learning where it lives: the key is handed
back to ``get-ai-usage --open-session <key>``, which re-scans
(``collect_open_targets()``), resolves the key to the private resume
coordinates (session id, cwd, CLI argv), and spawns the terminal itself.
Keys are validated against a fresh scan at open time, so a stale listing
simply fails closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from collections.abc import Sequence

from . import billing, billing_mode, pricing
from .contract import epoch_of, num
from .providers import antigravity_sessions, antigravity_usage, cursor_sessions, junie, mimo, mistral_sessions, opencode, zed_sessions
from .providers.cline import get_cline_session_records
from .providers.grok import discover_sessions, grok_home
from .providers.muse import sessions_root as muse_sessions_root
from .providers.openai_credentials import codex_home
from .session_cache import SessionCache
from .session_index import (
    SEARCH_FIELDS,
    SessionQueryResult,
    SessionRow,
    normalize_source_ids,
    source_descriptors,
)

# Cap so a machine with years of logs stays snappy on a tab open.
_MAX_SESSIONS = 60
# A session touched within this window is treated as active.
_ACTIVE_WITHIN_SEC = 15 * 60


def _basename(path):
    if not isinstance(path, str) or not path:
        return ""
    return os.path.basename(path.rstrip("/\\"))


# How much of a session's own title (Claude Code's first prompt, Cline's task
# text) shows inline. Neither CLI writes a separate short summary to disk, so
# the row shows a clipped preview.
_TITLE_PREVIEW_LEN = 60


def _format_tokens(n):
    """``1947411`` -> ``"1.95M"``; matches the K/M formatting every frontend's
    own ``formatTokens()`` already uses for the stats views, so a session
    row's count reads the same way rather than as a wall of raw digits."""
    n = float(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def _clip_title(text):
    """One line, collapsed whitespace, clipped to ``_TITLE_PREVIEW_LEN``."""
    if not isinstance(text, str):
        return ""
    flat = " ".join(text.split())
    if len(flat) <= _TITLE_PREVIEW_LEN:
        return flat
    return flat[:_TITLE_PREVIEW_LEN].rstrip() + "…"


def _state(last_activity, ended=False):
    if ended:
        return "idle"
    age = time.time() - last_activity
    if age < 0:
        return "idle"
    return "active" if age <= _ACTIVE_WITHIN_SEC else "idle"


def _entry(provider, title, last_activity, *, state="", session_name="", detail="", session_id="", full_title="", source=""):
    activity = int(last_activity or 0)
    if activity <= 0:
        return None
    entry = {
        "provider": provider,
        "title": title or provider,
        "sessionName": session_name or "",
        "state": state or _state(activity),
        "lastActivityAt": activity,
        "detail": detail or "",
        # Opaque resume handle — "" when this provider has no known resume
        # command (Muse) or the session id could not be established.
        "openKey": _open_key(source or provider, session_id) if session_id else "",
        "costStatus": "unavailable",
    }
    if source:
        entry["source"] = source
    # Only carried when the row's title was clipped, so a frontend can offer
    # to expand it; omitted rather than duplicating what's already the title.
    if full_title and full_title != entry["title"]:
        entry["fullTitle"] = full_title
    return entry


def _with_usage_cost(entry, provider, buckets):
    if entry is None:
        return None
    catalog = pricing.cached_catalog()
    providers = {bucket.provider for bucket in buckets if bucket.provider}
    rates = {rate_provider: catalog.get(rate_provider, {}) for rate_provider in providers or {provider}}
    if provider == "antigravity":
        rates = antigravity_usage.with_price_aliases(rates, buckets)
    costed = {**entry, **billing.aggregate_session_usage(buckets, rates)}
    # Structured, not just baked into the detail string: the Spend view's
    # per-day token series is built from these, the same way its cost series
    # is, so both halves of that chart cover the same days.
    costed["tokens"] = billing.total_tokens(buckets)
    # What the figure means: money owed on a metered key, or the API-equivalent
    # of work a plan already covers. See billing_mode for why they never mix.
    costed["costBilling"] = billing_mode.mode_for(entry.get("provider") or provider, entry.get("source") or "")
    return costed


def _cline_catalog_identity(provider, model):
    provider = pricing._safe_namespace(provider)
    if not provider or not isinstance(model, str) or not model:
        return None
    if provider != "cline":
        return (provider, model) if "/" not in model else None
    native_provider, separator, native_model = model.partition("/")
    native_provider = pricing._safe_namespace(native_provider)
    if not native_provider or not separator or not native_model or "/" in native_model:
        return None
    return native_provider, native_model


def _open_key(provider, session_id):
    """Opaque handle: ``sha256(provider:id)``.

    Content-addressed rather than positional, so concurrent refreshes that add
    or drop a session cannot shift the key onto a different conversation — a
    key either resolves to the same session or fails closed.
    """
    if not provider or not session_id:
        return ""
    digest = hashlib.sha256(f"{provider}:{session_id}".encode()).hexdigest()
    return digest


def _cline_entries(*, include_all=False):
    raw = get_cline_session_records()
    if not include_all:
        raw = raw[-_MAX_SESSIONS:]
    out = []
    for s in raw:
        ended = num(s.get("endedAt")) > 0
        last = num(s.get("endedAt")) or num(s.get("startedAt"))
        title = s.get("workspace") or s.get("model") or "Cline"
        bits = []
        tokens = num(s.get("input")) + num(s.get("output"))
        if tokens > 0:
            bits.append(f"{_format_tokens(tokens)} tok")
        model = s.get("model") or ""
        if model and model != title:
            bits.append(model)
        session_id = s.get("sessionId") or ""
        catalog_identity = _cline_catalog_identity(s.get("provider"), model)
        usage = []
        token_values = (num(s.get("input")), num(s.get("output")), num(s.get("cacheRead")), num(s.get("cacheWrite")))
        if sum(token_values) > 0 and catalog_identity is not None:
            rate_provider, rate_model = catalog_identity
            usage.append(
                billing.UsageBucket(
                    rate_provider,
                    rate_model,
                    session_id,
                    input_tokens=token_values[0],
                    output_tokens=token_values[1],
                    cache_read_tokens=token_values[2],
                    cache_write_tokens=token_values[3],
                )
            )
        out.append(
            _with_usage_cost(
                _entry(
                    "cline",
                    title,
                    last,
                    state=_state(last, ended=ended),
                    session_name=s.get("provider") or "",
                    detail=" · ".join(bits),
                    session_id=session_id,
                ),
                catalog_identity[0] if catalog_identity is not None else "",
                usage,
            )
        )
    return [e for e in out if e]


def _muse_entries(*, include_all=False):
    root = muse_sessions_root()
    if not os.path.isdir(root):
        return []
    out = []
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # Skip the folded view cache; the live session.jsonl is enough.
            dirnames[:] = [d for d in dirnames if d != ".msp-view-v1"]
            if "session.jsonl" not in filenames:
                continue
            path = os.path.join(dirpath, "session.jsonl")
            try:
                st = os.stat(path)
            except OSError:
                continue
            workspace = ""
            model = ""
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f):
                        if i > 40:
                            break
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(row, dict):
                            continue
                        cwd = row.get("cwd") or row.get("workspace") or ""
                        if isinstance(cwd, str) and cwd and not workspace:
                            workspace = _basename(cwd)
                        m = row.get("model") or ""
                        if isinstance(m, str) and m and not model:
                            model = m
                        if workspace and model:
                            break
            except OSError:
                pass
            title = workspace or _basename(dirpath) or "Muse"
            out.append(
                _entry(
                    "muse",
                    title,
                    st.st_mtime,
                    session_name=model,
                    detail=model if model and model != title else "",
                )
            )
            if not include_all and len(out) >= _MAX_SESSIONS:
                break
    except OSError:
        return []
    return out


# Codex replays a few synthetic "user" turns before the real prompt: the
# project's AGENTS.md, the sandbox permissions block, and the IDE's open-tab
# context. None of them describe the session, so they are skipped when picking
# a title.
_CODEX_SYNTHETIC_PREFIXES = (
    "# AGENTS.md instructions",
    "<permissions instructions>",
    "<environment_context>",
    "<user_instructions>",
)
# The IDE wrapper keeps the real prompt under this heading.
_CODEX_REQUEST_HEADING = "## My request for Codex:"
_CODEX_IMAGE_TAG = re.compile(r"^(?:<image\b[^>]*>\s*)+")


def _codex_prompt_text(payload):
    """The user-typed text of one transcript ``message`` record, or ""."""
    if payload.get("role") != "user":
        return ""
    content = payload.get("content")
    if not isinstance(content, list):
        return ""
    parts = [block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "input_text"]
    text = "\n".join(part for part in parts if isinstance(part, str)).strip()
    if not text:
        return ""
    heading = text.find(_CODEX_REQUEST_HEADING)
    if heading >= 0:
        text = text[heading + len(_CODEX_REQUEST_HEADING) :].strip()
    elif text.startswith(_CODEX_SYNTHETIC_PREFIXES):
        return ""
    # A pasted screenshot leads with an <image .../> tag that says nothing
    # about the session; the typed text after it is the real title.
    return _CODEX_IMAGE_TAG.sub("", text).strip()


def _codex_entries(*, include_all=False):
    sessions = os.environ.get("CODEX_SESSIONS_DIR") or os.path.join(codex_home(), "sessions")
    if not os.path.isdir(sessions):
        return []
    files = []
    for root, _dirs, names in os.walk(sessions):
        for name in names:
            if name.endswith(".jsonl"):
                path = os.path.join(root, name)
                try:
                    files.append((os.path.getmtime(path), path))
                except OSError:
                    continue
    files.sort(reverse=True)
    out = []
    for mtime, path in files if include_all else files[:_MAX_SESSIONS]:
        cwd = ""
        model = ""
        # The codex CLI's own cumulative counter — the last ``token_count``
        # event in the file, not a re-derivation from message content. Only
        # this numeric total is read; nothing else past the first 80 lines is.
        total_tokens = 0
        prompt = ""
        session_id = _codex_id_from_path(path)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i <= 80 and not prompt and '"input_text"' in line:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            row = None
                        if isinstance(row, dict):
                            payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
                            if isinstance(payload, dict) and payload.get("type") == "message":
                                prompt = _codex_prompt_text(payload)
                    if i <= 80 and ('"cwd"' in line or '"model"' in line or "turn_context" in line) and not (cwd and model):
                        try:
                            row = json.loads(line)
                        except ValueError:
                            row = None
                        if isinstance(row, dict):
                            payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
                            if not cwd:
                                raw = payload.get("cwd") or ""
                                if isinstance(raw, str):
                                    cwd = _basename(raw)
                            if not model:
                                raw = payload.get("model") or ""
                                if isinstance(raw, str):
                                    model = raw
                    if '"token_count"' in line:
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(row, dict):
                            payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
                            info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                            usage = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
                            tokens = num(usage.get("total_tokens"))
                            if tokens > 0:
                                total_tokens = tokens  # last one wins — it's cumulative
        except OSError:
            pass
        # Prefer what the user actually asked for; the working directory is
        # only a fallback, and moves to the detail line when it is not the title.
        folder_title = cwd or "Codex"
        title = _clip_title(prompt) or folder_title
        bits = []
        if folder_title != title:
            bits.append(folder_title)
        if total_tokens > 0:
            bits.append(f"{_format_tokens(total_tokens)} tok")
        if model:
            bits.append(model)
        out.append(
            _with_usage_cost(
                _entry(
                    "openai",
                    title,
                    mtime,
                    session_name="Codex",
                    detail=" · ".join(bits),
                    session_id=session_id,
                    full_title=prompt,
                ),
                "openai",
                _codex_session_usage(path, session_id, model),
            )
        )
    return out


def _codex_id_from_path(path):
    """Session id from a rollout filename: ``rollout-…-<uuid>.jsonl``.

    The uuid is the last five dash-separated fields of the stem, which keeps
    it distinct from the timestamp prefix (also dash-laden). Legacy rollouts
    without a uuid tail return "" — those sessions show in the tab but carry
    no resume button.
    """
    stem = os.path.basename(path)[: -len(".jsonl")]
    match = re.search(
        r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$",
        stem,
    )
    return match.group(1) if match else ""


def _claude_session_usage(path, session_id):
    buckets = []
    latest_cost_state = None
    seen_msg_ids = set()
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for index, line in enumerate(stream):
                if index > 50000:
                    break
                if '"cost-state"' in line:
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict) and row.get("type") == "cost-state":
                            latest_cost_state = row
                    except ValueError:
                        pass
                if '"usage"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                message = row.get("message") if isinstance(row.get("message"), dict) else {}
                msg_id = (message.get("id") if isinstance(message.get("id"), str) else "") or (
                    row.get("requestId") if isinstance(row.get("requestId"), str) else ""
                )
                if msg_id:
                    if msg_id in seen_msg_ids:
                        continue
                    seen_msg_ids.add(msg_id)
                usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
                model = (
                    message.get("model") if isinstance(message.get("model"), str) else row.get("model") if isinstance(row.get("model"), str) else ""
                )
                buckets.append(
                    billing.UsageBucket(
                        "anthropic",
                        model,
                        session_id,
                        num(usage.get("input_tokens")),
                        num(usage.get("output_tokens")),
                        num(usage.get("cache_read_input_tokens")),
                        num(usage.get("cache_creation_input_tokens")),
                        num(usage.get("reasoning_tokens")),
                    )
                )
    except OSError:
        return []

    if latest_cost_state is not None:
        model_usage = latest_cost_state.get("modelUsage")
        if isinstance(model_usage, dict):
            cost_buckets = []
            for model, usage in model_usage.items():
                if not isinstance(usage, dict):
                    continue
                cost_buckets.append(
                    billing.UsageBucket(
                        "anthropic",
                        model,
                        session_id,
                        num(usage.get("inputTokens")),
                        num(usage.get("outputTokens")),
                        num(usage.get("cacheReadInputTokens")),
                        num(usage.get("cacheCreationInputTokens")),
                        num(usage.get("thinkingTokens")),
                        provider_cost_usd=num(usage.get("costUSD")),
                    )
                )
            if cost_buckets or latest_cost_state.get("totalCostUSD", 0) == 0:
                return cost_buckets

    subagents_dir = os.path.join(os.path.splitext(path)[0], "subagents")
    if os.path.isdir(subagents_dir):
        try:
            for sub_name in sorted(os.listdir(subagents_dir)):
                if not sub_name.endswith(".jsonl"):
                    continue
                sub_path = os.path.join(subagents_dir, sub_name)
                try:
                    with open(sub_path, encoding="utf-8", errors="replace") as sub_stream:
                        for sub_index, sub_line in enumerate(sub_stream):
                            if sub_index > 10000:
                                break
                            if '"usage"' not in sub_line:
                                continue
                            try:
                                sub_row = json.loads(sub_line)
                            except ValueError:
                                continue
                            if not isinstance(sub_row, dict):
                                continue
                            sub_msg = sub_row.get("message") if isinstance(sub_row.get("message"), dict) else {}
                            sub_id = (sub_msg.get("id") if isinstance(sub_msg.get("id"), str) else "") or (
                                sub_row.get("requestId") if isinstance(sub_row.get("requestId"), str) else ""
                            )
                            if sub_id:
                                if sub_id in seen_msg_ids:
                                    continue
                                seen_msg_ids.add(sub_id)
                            sub_usage = sub_msg.get("usage") if isinstance(sub_msg.get("usage"), dict) else {}
                            sub_model = (
                                sub_msg.get("model")
                                if isinstance(sub_msg.get("model"), str)
                                else sub_row.get("model")
                                if isinstance(sub_row.get("model"), str)
                                else ""
                            )
                            buckets.append(
                                billing.UsageBucket(
                                    "anthropic",
                                    sub_model,
                                    session_id,
                                    num(sub_usage.get("input_tokens")),
                                    num(sub_usage.get("output_tokens")),
                                    num(sub_usage.get("cache_read_input_tokens")),
                                    num(sub_usage.get("cache_creation_input_tokens")),
                                    num(sub_usage.get("reasoning_tokens")),
                                )
                            )
                except OSError:
                    continue
        except OSError:
            pass

    return buckets


def _codex_usage_bucket(usage, model, session_id):
    if not isinstance(usage, dict):
        return None
    bucket = billing.UsageBucket(
        "openai",
        model,
        session_id,
        num(usage.get("input_tokens")),
        num(usage.get("output_tokens")),
        num(usage.get("cached_input_tokens") or usage.get("cache_read_input_tokens")),
        num(usage.get("cache_write_tokens")),
        num(usage.get("reasoning_output_tokens") or usage.get("reasoning_tokens")),
    )
    return (
        bucket
        if sum((bucket.input_tokens, bucket.output_tokens, bucket.cache_read_tokens, bucket.cache_write_tokens, bucket.reasoning_tokens)) > 0
        else None
    )


def _codex_session_usage(path, session_id, model_hint=""):
    turns = []
    cumulative = None
    model = model_hint
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for index, line in enumerate(stream):
                if index > 50000:
                    break
                if '"token_count"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
                raw_model = payload.get("model")
                if isinstance(raw_model, str) and raw_model:
                    model = raw_model
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                last = _codex_usage_bucket(info.get("last_token_usage"), model, session_id)
                if last is not None:
                    turns.append(last)
                cumulative = _codex_usage_bucket(info.get("total_token_usage"), model, session_id) or cumulative
    except OSError:
        return []
    return turns or ([cumulative] if cumulative is not None else [])


def _grok_usage(session_dir, session_id, model):
    """The CLI's own cumulative counters — the last ``usage`` record in
    ``updates.jsonl``, never the message text."""
    totals = None
    try:
        with open(os.path.join(session_dir, "updates.jsonl"), encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 20000:
                    break
                if '"usage"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                # JSON-RPC envelope: {"method": ..., "params": {"update": {"usage": {...}}}}
                params = row.get("params") if isinstance(row, dict) else None
                update = params.get("update") if isinstance(params, dict) else None
                usage = update.get("usage") if isinstance(update, dict) else None
                if isinstance(usage, dict):
                    totals = usage  # cumulative: the last one wins
    except OSError:
        return []
    if not isinstance(totals, dict):
        return []
    return [
        billing.UsageBucket(
            provider="xai",
            model=model or "",
            session_id=session_id,
            input_tokens=num(totals.get("inputTokens")),
            output_tokens=num(totals.get("outputTokens")),
            cache_read_tokens=num(totals.get("cachedReadTokens")),
            cache_write_tokens=num(totals.get("cacheCreationTokens")),
            reasoning_tokens=num(totals.get("reasoningTokens")),
        )
    ]


def _grok_summary_entries(sessions_dir, *, include_all=False):
    """Grok CLI >= the summary.json layout: one directory per session under a
    url-encoded workspace, carrying its own generated title and usage.

    The older builds wrote a single ``signals.json`` per session instead; that
    reader still runs for anyone on one, and simply finds nothing here.
    """
    found = []
    for record in discover_sessions(sessions_dir):
        if not record.summary_path:
            continue
        try:
            with open(record.summary_path, encoding="utf-8") as f:
                summary = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(summary, dict):
            continue
        found.append((record.summary_mtime, record.directory, record.session_id, summary))

    found.sort(reverse=True)
    out = []
    for mtime, session_dir, name, summary in found if include_all else found[:_MAX_SESSIONS]:
        info = summary.get("info") if isinstance(summary.get("info"), dict) else {}
        session_id = info.get("id") if isinstance(info.get("id"), str) else ""
        session_id = session_id or name
        model = summary.get("current_model_id") if isinstance(summary.get("current_model_id"), str) else ""
        title = summary.get("generated_title") or summary.get("session_summary") or ""
        folder_title = _basename(info.get("cwd") or "") or "Grok"
        clipped = _clip_title(title) or folder_title
        last_activity = epoch_of(summary.get("last_active_at") or summary.get("updated_at")) or int(mtime)

        buckets = _grok_usage(session_dir, session_id, model)
        tokens = billing.total_tokens(buckets)
        bits = []
        if folder_title != clipped:
            bits.append(folder_title)
        if tokens > 0:
            bits.append(f"{_format_tokens(tokens)} tok")
        if model:
            bits.append(model)
        out.append(
            _with_usage_cost(
                _entry(
                    "grok",
                    clipped,
                    last_activity,
                    session_name="Grok CLI",
                    detail=" · ".join(bits),
                    session_id=session_id,
                    full_title=title if isinstance(title, str) else "",
                ),
                "grok",
                buckets,
            )
        )
    return out


def _grok_entries(*, include_all=False):
    sessions_dir = os.path.join(grok_home(), "sessions")
    if not os.path.isdir(sessions_dir):
        return []
    try:
        out = _grok_summary_entries(sessions_dir, include_all=include_all)
    except OSError:
        out = []
    for record in discover_sessions(sessions_dir):
        if not record.signals_path:
            continue
        try:
            with open(record.signals_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        models = data.get("modelsUsed") or []
        model = models[0] if isinstance(models, list) and models else ""
        tokens = num(data.get("contextTokensUsed"))
        detail_bits = []
        if tokens > 0:
            detail_bits.append(f"{_format_tokens(tokens)} tok")
        if model:
            detail_bits.append(str(model))
        # The session dir sits inside the url-encoded cwd ("%2F…"); that
        # parent is the workspace, the dir name itself is the session id.
        session_id = record.session_id
        out.append(
            _entry(
                "grok",
                str(model) if model else "Grok",
                record.signals_mtime,
                session_name="Grok CLI",
                detail=" · ".join(detail_bits),
                session_id=session_id if session_id and session_id != "sessions" else "",
            )
        )
        if not include_all and len(out) >= _MAX_SESSIONS:
            return out
    return out


def _opencode_identity(record):
    return f"{record.database_path}\x00{record.session_id}"


def _opencode_records(reader, *, include_all=False):
    selected = {}
    for record in reader(include_all=include_all):
        previous = selected.get(record.session_id)
        if previous is None or (
            record.last_activity,
            record.created_at,
            record.database_path,
        ) > (
            previous.last_activity,
            previous.created_at,
            previous.database_path,
        ):
            selected[record.session_id] = record
    records = sorted(
        selected.values(),
        key=lambda record: (-record.last_activity, record.session_id),
    )
    return records if include_all else records[:_MAX_SESSIONS]


def _provider_costs(buckets, source=""):
    """Per-provider cost rollups for a multi-provider OpenCode session."""
    catalog = pricing.cached_catalog()
    by_provider = {}
    for bucket in buckets:
        by_provider.setdefault(bucket.provider, []).append(bucket)
    costs = {}
    for provider, provider_buckets in by_provider.items():
        result = billing.aggregate_session_usage(provider_buckets, {provider: catalog.get(provider, {})})
        if result.get("costStatus") != "unavailable":
            result["tokens"] = billing.total_tokens(provider_buckets)
            if source == "opencode":
                result["costBilling"] = billing_mode.mode_for(provider, source)
            costs[provider] = result
    return costs


def _opencode_entries(*, include_all=False):
    out = []
    for record in _opencode_records(opencode.read_recent_sessions, include_all=include_all):
        title = _clip_title(record.title) or _basename(record.directory) or "OpenCode"
        upstream = {bucket.provider for bucket in record.usage}
        provider = next(iter(upstream)) if len(upstream) == 1 else "opencode"
        entry = _with_usage_cost(
            _entry(
                provider,
                title,
                record.last_activity,
                session_name="via OpenCode",
                detail=_basename(record.directory),
                session_id=_opencode_identity(record),
                source="opencode",
            ),
            "opencode",
            record.usage,
        )
        if entry is None:
            continue
        if len(upstream) == 1:
            entry["billingProvider"] = provider
        else:
            provider_costs = _provider_costs(record.usage, "opencode")
            if provider_costs:
                entry["providerCosts"] = provider_costs
        out.append(entry)
    return out


def _resolve_zed_provider(model_provider: str, model_id: str) -> str:
    provider = model_provider.strip().lower() if isinstance(model_provider, str) else ""
    if provider == "open_ai":
        provider = "openai"
    if provider and provider not in ("zed", "zed.dev"):
        return provider

    catalog_provider = pricing.provider_for_model(model_id)
    if catalog_provider:
        return catalog_provider

    return provider or "zed"


def _zed_entries(*, include_all=False):
    out = []
    for record in zed_sessions.read_sessions(include_all=include_all):
        provider = _resolve_zed_provider(record.model_provider, record.model_id)
        entry = _entry(
            "zed",
            _clip_title(record.title) or "Zed session",
            record.last_activity,
            session_name="via Zed",
            detail=record.workspace,
            source="zed",
        )
        if entry is None:
            continue

        entry["tokens"] = record.tokens
        if provider:
            entry["billingProvider"] = provider
        if record.model_id:
            entry["billingModel"] = record.model_id
        out.append(entry)
    return out if include_all else out[:_MAX_SESSIONS]


def _opencode_targets():
    return [
        {
            "provider": "opencode",
            "id": record.session_id,
            "keyId": _opencode_identity(record),
            "cwd": record.directory,
        }
        for record in _opencode_records(opencode.read_session_targets, include_all=True)
    ]


def _mimo_entries(*, include_all=False):
    out = []
    for record in _opencode_records(mimo.read_recent_sessions, include_all=include_all):
        title = _clip_title(record.title) or _basename(record.directory) or "MiMo Code"
        upstream = {bucket.provider for bucket in record.usage}
        provider = next(iter(upstream)) if len(upstream) == 1 else "mimo"
        entry = _with_usage_cost(
            _entry(
                provider,
                title,
                record.last_activity,
                session_name="via MiMo Code",
                detail=_basename(record.directory),
                session_id=_opencode_identity(record),
                source="mimo",
            ),
            "mimo",
            record.usage,
        )
        if entry is None:
            continue
        if len(upstream) == 1:
            entry["billingProvider"] = provider
        else:
            provider_costs = _provider_costs(record.usage)
            if provider_costs:
                entry["providerCosts"] = provider_costs
        out.append(entry)
    return out


def _junie_entries(*, include_all=False):
    rows = []
    for record in junie.read_sessions():
        entry = _entry(
            "junie",
            _clip_title(record["title"]) or "Junie",
            record["lastActivity"],
            state=_state(record["lastActivity"], ended=record["state"] in ("FAILED", "COMPLETED", "CANCELLED")),
            detail=_basename(record["directory"].replace("\\", "/")),
            session_id=record["id"],
        )
        if entry is not None:
            if record["usage"]:
                entry["tokens"] = sum(sum(bucket.get(key, 0) for key in junie.TOKEN_FIELDS.values()) for bucket in record["usage"])
                entry["sessionName"] = ", ".join(sorted({bucket["model"] for bucket in record["usage"]}))
            rows.append(entry)
    return rows if include_all else rows[:_MAX_SESSIONS]


def _junie_targets():
    return [{"provider": "junie", "id": row["id"], "cwd": row["directory"]} for row in junie.read_sessions()]


def _mimo_targets():
    return [
        {
            "provider": "mimo",
            "id": record.session_id,
            "keyId": _opencode_identity(record),
            "cwd": record.directory,
        }
        for record in _opencode_records(mimo.read_session_targets, include_all=True)
    ]


def _antigravity_entries(*, include_all=False):
    out = []
    for record in antigravity_sessions.read_recent_sessions(include_all=include_all):
        entry = _entry(
            "antigravity",
            _clip_title(record.title) or "Antigravity",
            record.last_activity,
            session_name="Antigravity",
            session_id=record.session_id,
        )
        if entry:
            if record.usage:
                entry = _with_usage_cost(entry, "antigravity", record.usage)
                if not record.usage_complete and entry["costStatus"] == "exact":
                    entry["costStatus"] = "partial"
                entry["costBilling"] = "subscription"
            out.append(entry)
    return out


def _mistral_entries(*, include_all=False):
    out = []
    for record in mistral_sessions.read_recent_sessions(include_all=include_all):
        detail = []
        tokens = record.input_tokens + record.output_tokens + record.cached_tokens
        if tokens > 0:
            detail.append(f"{_format_tokens(tokens)} tok")
        if record.project:
            detail.append(record.project)
        if record.model:
            detail.append(record.model)
        bucket = billing.UsageBucket(
            "mistral",
            record.model,
            record.session_id,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cache_read_tokens=record.cached_tokens,
            provider_cost_usd=record.cost_usd,
        )
        entry = _with_usage_cost(
            _entry(
                "mistral",
                _clip_title(record.title) or "Mistral Vibe",
                record.last_activity,
                state=_state(record.last_activity, ended=True),
                session_name="Mistral Vibe",
                detail=" · ".join(detail),
            ),
            "mistral",
            [bucket],
        )
        if entry:
            out.append(entry)
    return out


def _cursor_entries(*, include_all=False):
    out = []
    for record in cursor_sessions.read_recent_sessions(include_all=include_all):
        detail = record.project if record.project and record.project != record.title else ""
        entry = _entry(
            "cursor",
            _clip_title(record.title) or "Cursor",
            record.last_activity,
            state=_state(record.last_activity, ended=True),
            session_name="Cursor Agent",
            detail=detail,
        )
        if entry:
            out.append(entry)
    return out


def _antigravity_targets():
    return [
        {
            "provider": "antigravity",
            "id": record.session_id,
            "keyId": record.session_id,
            "cwd": "",
        }
        for record in antigravity_sessions.read_session_targets()
    ]


def _claude_prompt_titles(root):
    """sessionId -> that session's first prompt, from ``history.jsonl``.

    Claude Code writes no separate summary to disk — the CLI's own
    ``--resume`` picker computes its labels on the fly. This is the opening
    prompt itself (the same text `--resume`'s search matches against), kept
    verbatim so ``_claude_entries()`` can derive the clipped row title.
    """
    path = os.path.join(root, "history.jsonl")
    titles = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i > 20000:
                    break
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                sid = row.get("sessionId")
                display = row.get("display")
                if isinstance(sid, str) and sid and isinstance(display, str) and display:
                    # First occurrence wins — the file is append-only in
                    # chronological order, so that is the opening prompt.
                    titles.setdefault(sid, display)
    except OSError:
        return {}
    return titles


def _claude_entries(*, include_all=False):
    """Light scan of Claude Code project folders — mtime only, no transcripts."""
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    projects = os.path.join(root, "projects")
    if not os.path.isdir(projects):
        return []
    prompt_titles = _claude_prompt_titles(root)
    out = []
    try:
        for name in os.listdir(projects):
            project = os.path.join(projects, name)
            if not os.path.isdir(project):
                continue
            project_entries = []
            try:
                for entry in os.listdir(project):
                    if not entry.endswith(".jsonl"):
                        continue
                    try:
                        stamp = os.path.getmtime(os.path.join(project, entry))
                    except OSError:
                        continue
                    project_entries.append((stamp, entry))
            except OSError:
                continue
            if not include_all:
                project_entries = sorted(project_entries, reverse=True)[:1]
            # Project dir names are path-encoded; show a short readable slice.
            folder_title = name.replace("-", "/").strip("/")
            folder_title = _basename(folder_title) or "Claude"
            for newest, newest_entry in project_entries:
                if newest <= 0:
                    continue
                # Resume target: this transcript's file name is the session
                # id claude --resume <id> expects.
                session_id = newest_entry[: -len(".jsonl")]
                full_title = prompt_titles.get(session_id, "")
                title = _clip_title(full_title) or folder_title
                transcript = os.path.join(project, newest_entry)
                buckets = _claude_session_usage(transcript, session_id)
                tokens = billing.total_tokens(buckets)
                bits = []
                if folder_title != title:
                    bits.append(folder_title)
                if tokens > 0:
                    bits.append(f"{_format_tokens(tokens)} tok")
                out.append(
                    _with_usage_cost(
                        _entry(
                            "claude",
                            title,
                            newest,
                            session_name="Claude Code",
                            detail=" · ".join(bits),
                            session_id=session_id,
                            full_title=full_title,
                        ),
                        "anthropic",
                        buckets,
                    )
                )
    except OSError:
        return []
    return out


# ── Resume in terminal ────────────────────────────────────────────────────
# The Sessions tab shows one ⧉ button per row that re-opens that exact session
# in the user's own terminal. The frontend never learns where a session lives:
# it only ever sees the opaque ``openKey`` on the entry, and hands it back to
# ``get-ai-usage --open-session <key>``, which re-scans, validates, and spawns
# the terminal itself. That keeps the redaction boundary intact — paths and ids
# stay in this module — while every frontend (KDE, Hyprland, Windows, macOS)
# shares one launcher instead of re-implementing terminal detection four ways.

# provider → CLI binary → how to resume one session id.
# ``cmd`` is argv *after* the binary; ``{id}`` is the store's session id.
_RESUME_SPECS = {
    "claude": {"bin": "claude", "cmd": ["--resume", "{id}"]},
    "openai": {"bin": "codex", "cmd": ["resume", "{id}"]},
    "opencode": {"bin": "opencode", "cmd": ["--session", "{id}"]},
    "mimo": {"bin": "mimo", "cmd": ["--session", "{id}"]},
    "junie": {"bin": "junie", "cmd": ["--session-id", "{id}"]},
    "antigravity": {"bin": "agy", "cmd": ["--conversation", "{id}"]},
    "grok": {"bin": "grok", "cmd": ["--resume", "{id}"]},
    "cline": {"bin": "cline", "cmd": ["--id", "{id}"]},
    # Muse ships no usable binary here and documents no resume/continue flag
    # — entries stay display-only (openKey "").
}

# Terminals tried in order; ``{cmd}`` is the resume line quoted for sh.
_TERMINAL_TEMPLATES = [
    ("ghostty", ["-e", "sh", "-c", "{cmd}"]),
    ("alacritty", ["-e", "sh", "-c", "{cmd}"]),
    ("kitty", ["sh", "-c", "{cmd}"]),
    ("wezterm", ["start", "--", "sh", "-c", "{cmd}"]),
    ("konsole", ["-e", "sh", "-c", "{cmd}"]),
    ("gnome-terminal", ["--", "sh", "-c", "{cmd}"]),
    ("xfce4-terminal", ["-e", "{cmd}"]),
    ("xterm", ["-e", "sh", "-c", "{cmd}"]),
]


def _matches_query(entry, query):
    needle = query.casefold()
    return any(needle in entry.get(field, "").casefold() for field in SEARCH_FIELDS if isinstance(entry.get(field), str))


# Keyed by the same source ids ``session_manifest.build_manifests()`` emits, in
# the same order: the index stores and merges rows per source, so a collector
# only re-runs when its own store has changed.
# Collectors are named, not referenced: binding the function objects here would
# snapshot them at import, so a later rebind of the module attribute (a test
# double, or any reload) would never be seen by the registry.
SESSION_COLLECTORS = {
    "cline": "_cline_entries",
    "muse": "_muse_entries",
    "codex": "_codex_entries",
    "grok": "_grok_entries",
    "claude": "_claude_entries",
    "opencode": "_opencode_entries",
    "mimo": "_mimo_entries",
    "antigravity": "_antigravity_entries",
    "mistral": "_mistral_entries",
    "cursor": "_cursor_entries",
    "junie": "_junie_entries",
    "zed": "_zed_entries",
}


def collect_source(source_id):
    """Every row one collector can see. Raises if its store is unreadable —
    the index then keeps that source's previous rows rather than dropping them."""
    return globals()[SESSION_COLLECTORS[source_id]](include_all=True)


def _collect_all_sessions():
    merged = []
    complete = True
    for source_id in SESSION_COLLECTORS:
        try:
            merged.extend(collect_source(source_id))
        except Exception:
            # One broken store must not blank the whole tab or replace its cache.
            complete = False
    return merged, complete


def collect_sessions(
    query: str = "",
    limit: int | None = None,
    offset: int = 0,
    source_ids: Sequence[str] | None = None,
) -> SessionQueryResult:
    """Return a page from the shared session index without collecting."""
    return SessionCache().query(
        query,
        limit=limit,
        offset=offset,
        source_ids=source_ids,
    )


def all_session_rows() -> list[SessionRow]:
    """Every indexed session row. ``collect_sessions()`` returns one page, so
    anything that totals spend has to come through here instead."""
    return SessionCache().rows()


def _page_sessions(
    merged: Sequence[SessionRow],
    query: str,
    limit: int | None,
    offset: int,
    source_ids: Sequence[str] | None = None,
) -> SessionQueryResult:
    normalized_query = (query or "").strip()
    merged = [s for s in merged if s]
    sources = source_descriptors(entry["provider"] for entry in merged)
    normalized_source_ids = normalize_source_ids(source_ids)
    if normalized_source_ids is not None:
        selected = frozenset(normalized_source_ids)
        merged = [entry for entry in merged if entry["provider"] in selected]
    merged.sort(key=lambda s: s.get("lastActivityAt") or 0, reverse=True)
    matches = [entry for entry in merged if _matches_query(entry, normalized_query)] if normalized_query else merged
    total = len(matches)
    page_limit = _MAX_SESSIONS if limit is None else limit
    page = matches[offset : offset + page_limit]
    has_more = offset + page_limit < total
    return {
        "updatedAt": int(time.time()),
        "sessions": page,
        "total": total,
        "totalExact": True,
        "offset": offset,
        "limit": page_limit,
        "hasMore": has_more,
        "sources": sources,
    }


def refresh_sessions(
    query: str = "",
    limit: int | None = None,
    offset: int = 0,
    source_ids: Sequence[str] | None = None,
) -> SessionQueryResult:
    """Refresh all session providers, then return the requested page."""
    # Session costs are priced from the shared catalog, and `cached_catalog()`
    # only ever reads it — nothing else fills it, so without this every row
    # reported "Cost unavailable" until the user hit Refresh pricing by hand.
    # load_catalog() is TTL-guarded (weekly, 15 min after an error), so this is
    # a cheap no-op on all but the first run.
    try:
        pricing.load_catalog()
    except Exception:
        # Pricing is an enrichment; a listing without costs still beats none.
        pass
    cache = SessionCache()
    direct_rows = cache.refresh(_collect_all_sessions, collect_source)
    if direct_rows is not None:
        return _page_sessions(direct_rows, query, limit, offset, source_ids)
    return cache.query(
        query,
        limit=limit,
        offset=offset,
        source_ids=source_ids,
    )


def collect_open_targets():
    """Every resumable session, keyed by its opaque handle.

    Internal only — the result carries paths and ids and is never emitted.
    ``collect_sessions()`` publishes just the digest per entry; this re-scan
    is what ``--open-session`` validates a stale or fresh key against.
    """
    targets = {}
    for collector in (
        _codex_targets,
        _grok_targets,
        _claude_targets,
        _cline_targets,
        _opencode_targets,
        _mimo_targets,
        _junie_targets,
        _antigravity_targets,
    ):
        try:
            for target in collector():
                key = _open_key(target["provider"], target.get("keyId") or target["id"])
                if key:
                    targets[key] = target
        except Exception:
            # A broken store must not block resuming the others.
            continue
    return targets


def _codex_targets():
    sessions = os.environ.get("CODEX_SESSIONS_DIR") or os.path.join(codex_home(), "sessions")
    if not os.path.isdir(sessions):
        return []
    out = []
    for root, _dirs, names in os.walk(sessions):
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(root, name)
            session_id = _codex_id_from_path(path)
            if not session_id:
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            out.append({"provider": "openai", "id": session_id, "mtime": mtime, "cwd": ""})
    return out


def _grok_targets():
    sessions_dir = os.path.join(grok_home(), "sessions")
    if not os.path.isdir(sessions_dir):
        return []
    out = []
    for record in discover_sessions(sessions_dir):
        # summary.json is the current layout, signals.json the older one.
        if not record.marker:
            continue
        out.append({"provider": "grok", "id": record.session_id, "mtime": record.mtime, "cwd": record.cwd})
    return out


def _claude_targets():
    """Return every Claude transcript as a resume target."""
    root = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    projects = os.path.join(root, "projects")
    if not os.path.isdir(projects):
        return []
    out = []
    try:
        names = os.listdir(projects)
    except OSError:
        return []
    for name in names:
        project = os.path.join(projects, name)
        if not os.path.isdir(project):
            continue
        try:
            entries = os.listdir(project)
        except OSError:
            continue
        for entry in entries:
            if not entry.endswith(".jsonl"):
                continue
            try:
                stamp = os.path.getmtime(os.path.join(project, entry))
            except OSError:
                continue
            out.append(
                {
                    "provider": "claude",
                    "id": entry[: -len(".jsonl")],
                    "mtime": stamp,
                    "cwd": _decode_claude_dir(name),
                }
            )
    return out


def _decode_claude_dir(name):
    """Best-effort cwd from a Claude project dir name ("mnt-projects-foo").

    Claude encodes the cwd by replacing ``/`` with ``-``; a directory whose
    own name contains ``-`` cannot be split back apart, so this is a guess.
    Ambiguous rows still resume — just from $HOME when the guess misses.
    """
    guess = os.path.join("/", name.replace("-", "/"))
    return guess if os.path.isdir(guess) else ""


def _cline_targets():
    root = os.environ.get("CLINE_SESSIONS_DIR") or os.path.expanduser("~/.cline/data/sessions")
    if not os.path.isdir(root):
        return []
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    for name in names:
        path = os.path.join(root, name, name + ".json")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        started = epoch_of(data.get("started_at") or "")
        if started <= 0:
            continue
        cwd = ""
        raw = data.get("workspace_root") or data.get("cwd") or ""
        if isinstance(raw, str):
            cwd = raw
        # The record's session_id may differ from the directory name; the
        # resume flag wants the id the CLI itself recorded.
        session_id = data.get("session_id") if isinstance(data.get("session_id"), str) and data.get("session_id") else name
        out.append({"provider": "cline", "id": session_id, "mtime": started, "cwd": cwd})
    return out


def _resume_argv(provider, session_id):
    """Full argv to resume this session, or None when impossible."""
    spec = _RESUME_SPECS.get(provider)
    if spec is None:
        return None
    binary = shutil.which(spec["bin"])
    if not binary:
        return None
    return [binary] + [part.format(id=session_id) for part in spec["cmd"]]


def _terminal_launch(resume_argv, cwd):
    """Spawn the resume command in the user's terminal, detached.

    Returns (ok, message). $TERMINAL wins, then the known list; if nothing is
    installed the session still resumes — headless in the background — rather
    than the button doing nothing.
    """
    resume_line = " ".join(shlex.quote(part) for part in resume_argv)
    # Keep the window open afterwards: a resumed TUI that exits immediately
    # (or fails) would otherwise vanish before it can be read.
    hold = f'{resume_line}; exec "${{SHELL:-/bin/sh}}"'
    candidates = []
    term_env = (os.environ.get("TERMINAL") or "").strip()
    if term_env:
        try:
            parsed_terminal = shlex.split(term_env, comments=False, posix=True)
        except ValueError:
            parsed_terminal = []
        metacharacters = ";&|<>$`(){}*?[]!\r\n"
        if parsed_terminal and all(not any(char in metacharacters for char in token) for token in parsed_terminal):
            terminal = shutil.which(parsed_terminal[0])
            if terminal:
                candidates.append((terminal, parsed_terminal[1:] + ["-e", "sh", "-c", hold]))
    for binary, template in _TERMINAL_TEMPLATES:
        candidates.append((binary, [part.format(cmd=hold) for part in template]))
    for binary, args in candidates:
        exe = binary if os.path.isabs(binary) else shutil.which(binary)
        if not exe:
            continue
        try:
            subprocess.Popen(
                [exe] + args,
                cwd=cwd or None,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True, f"opened in {os.path.basename(exe)}"
        except (OSError, ValueError):
            continue
    try:
        subprocess.Popen(
            resume_argv,
            cwd=cwd or None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True, "resumed in the background (no terminal found)"
    except (OSError, ValueError):
        return False, "could not launch session"


def open_cli():
    """Open the terminal frontend (backend/sh/tidesi-cli) in the user's
    terminal, as the session button does for a resume. Returns (ok, message)."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "sh", "tidesi-cli")
    script = os.path.normpath(script)
    if not os.path.isfile(script):
        return False, "the terminal frontend (tidesi-cli) is not part of this install"
    return _terminal_launch([script], os.path.expanduser("~"))


def open_session(open_key):
    """Resume one session (by opaque ``openKey``) in the user's terminal.

    Re-scans first, so a key from an earlier listing either resolves to the
    same session or fails closed. Returns (ok, message); the message is safe
    to show — it names binaries and terminals, never paths.
    """
    key = (open_key or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        return False, "unknown session (stale list — refresh and try again)"
    target = collect_open_targets().get(key)
    if target is None:
        return False, "unknown session (stale list — refresh and try again)"
    provider, session_id = target["provider"], target["id"]
    spec = _RESUME_SPECS.get(provider)
    if spec is None:
        return False, f"{provider} has no resume command here yet"
    argv = _resume_argv(provider, session_id)
    if argv is None:
        return False, f"the {spec['bin']} command was not found (is it on PATH?)"
    cwd = target.get("cwd") or ""
    if cwd and not os.path.isdir(cwd):
        cwd = ""
    return _terminal_launch(argv, cwd)
