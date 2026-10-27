"""Persistent, redacted SQLite index for local agent sessions."""

from __future__ import annotations

import datetime
import hashlib
import json
import math
import sqlite3
import time
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Final, Protocol, TypedDict

from .contract import finite_number

# Single source of truth for which public fields are searchable, shared with
# the fallback (`sessions.py`) search path so the two never drift apart.
SEARCH_FIELDS: Final = ("provider", "title", "sessionName", "state", "detail")
_COLUMN_BY_FIELD: Final = {
    "provider": "provider",
    "title": "title",
    "sessionName": "session_name",
    "state": "state",
    "detail": "detail",
}
_SEARCH_COLUMNS: Final = tuple(_COLUMN_BY_FIELD[field] for field in SEARCH_FIELDS)
SOURCE_REGISTRY: Final = (
    ("cline", "Cline"),
    ("muse", "Muse"),
    ("openai", "Codex"),
    ("grok", "Grok"),
    ("claude", "Claude Code"),
    ("opencode", "OpenCode"),
    ("mimo", "MiMo Code"),
    ("antigravity", "Antigravity"),
    ("mistral", "Mistral"),
    ("cursor", "Cursor"),
    ("junie", "Junie"),
)


class SkipSource(Exception):
    """Raised by a parser to leave one source's stored rows exactly as they are.

    Its metadata is left stale too, so the next reconcile retries it."""


class SessionSource(Protocol):
    source_id: str
    mtime_ns: int
    size: int


class _SessionRowCore(TypedDict):
    provider: str
    title: str
    sessionName: str
    state: str
    lastActivityAt: int
    detail: str
    openKey: str


class SessionRow(_SessionRowCore, total=False):
    fullTitle: str
    source: str
    costUSD: float
    costStatus: str
    costProvenance: str
    costBreakdown: dict
    billingProvider: str
    providerCosts: dict
    costBilling: str
    tokens: int


class SessionSourceDescriptor(TypedDict):
    id: str
    label: str


class SessionQueryResult(TypedDict):
    updatedAt: int
    sessions: list[SessionRow]
    total: int
    totalExact: bool
    offset: int
    limit: int
    hasMore: bool
    sources: list[SessionSourceDescriptor]


SessionParser = Callable[[SessionSource], Sequence[Mapping[str, str | int]]]


def _source_key(source: SessionSource) -> str:
    return hashlib.sha256(source.source_id.encode("utf-8")).hexdigest()


def _text(value: str | int) -> str:
    return value if isinstance(value, str) else str(value)


def _timestamp(value: str | int) -> int:
    return value if isinstance(value, int) else int(value)


def _json_or_none(value: object) -> str | None:
    return json.dumps(value, separators=(",", ":")) if isinstance(value, dict) else None


def _decode_json_or_none(value: object) -> dict | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        decoded = json.loads(value)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _redact(rows: Sequence[Mapping[str, Any]]) -> list[SessionRow]:
    redacted: list[SessionRow] = []
    for row in rows:
        entry: SessionRow = {
            "provider": _text(row.get("provider", "")),
            "title": _text(row.get("title", "")),
            "sessionName": _text(row.get("sessionName", "")),
            "state": _text(row.get("state", "")),
            "lastActivityAt": _timestamp(row.get("lastActivityAt", 0)),
            "detail": _text(row.get("detail", "")),
            "openKey": _valid_open_key(row.get("openKey", "")),
        }
        full_title = row.get("fullTitle")
        if isinstance(full_title, str) and full_title:
            entry["fullTitle"] = full_title
        source = row.get("source")
        if isinstance(source, str) and source:
            entry["source"] = source
        cost_status = row.get("costStatus")
        if isinstance(cost_status, str) and cost_status:
            entry["costStatus"] = cost_status
        cost_usd = finite_number(row.get("costUSD"))
        if cost_usd is not None:
            entry["costUSD"] = cost_usd
        cost_provenance = row.get("costProvenance")
        if isinstance(cost_provenance, str) and cost_provenance:
            entry["costProvenance"] = cost_provenance
        cost_breakdown = row.get("costBreakdown")
        if isinstance(cost_breakdown, dict):
            entry["costBreakdown"] = cost_breakdown
        billing_provider = row.get("billingProvider")
        if isinstance(billing_provider, str) and billing_provider:
            entry["billingProvider"] = billing_provider
        cost_billing = row.get("costBilling")
        if cost_billing in ("subscription", "api"):
            entry["costBilling"] = cost_billing
        provider_costs = row.get("providerCosts")
        if isinstance(provider_costs, dict):
            entry["providerCosts"] = provider_costs
        tokens = finite_number(row.get("tokens"), minimum=0)
        if tokens:
            entry["tokens"] = int(tokens)
        redacted.append(entry)
    return redacted


def redact_rows(rows: Sequence[Mapping[str, Any]]) -> list[SessionRow]:
    """Return only the public fields allowed in a session response."""
    return _redact(rows)


def _row_from_columns(row: Sequence[Any]) -> SessionRow:
    """Rebuild a public ``SessionRow`` from a ``session_rows`` SELECT tuple.

    Column order must match every ``SELECT ... FROM session_rows`` above:
    provider, title, session_name, state, last_activity_at, detail, open_key,
    full_title, source, cost_usd, cost_status, cost_provenance, cost_breakdown,
    billing_provider, provider_costs, cost_billing, tokens.
    """
    entry: SessionRow = {
        "provider": _text(row[0]),
        "title": _text(row[1]),
        "sessionName": _text(row[2]),
        "state": _text(row[3]),
        "lastActivityAt": _timestamp(row[4]),
        "detail": _text(row[5]),
        "openKey": _valid_open_key(row[6]),
    }
    if row[7]:
        entry["fullTitle"] = _text(row[7])
    if row[8]:
        entry["source"] = _text(row[8])
    cost_usd = finite_number(row[9])
    if cost_usd is not None:
        entry["costUSD"] = cost_usd
    if row[10]:
        entry["costStatus"] = _text(row[10])
    if row[11]:
        entry["costProvenance"] = _text(row[11])
    cost_breakdown = _decode_json_or_none(row[12])
    if cost_breakdown is not None:
        entry["costBreakdown"] = cost_breakdown
    if row[13]:
        entry["billingProvider"] = _text(row[13])
    if row[15] in ("subscription", "api"):
        entry["costBilling"] = _text(row[15])
    provider_costs = _decode_json_or_none(row[14])
    if provider_costs is not None:
        entry["providerCosts"] = provider_costs
    if len(row) > 16 and row[16]:
        entry["tokens"] = int(row[16])
    return entry


def normalize_source_ids(source_ids: Sequence[str] | None) -> tuple[str, ...] | None:
    """Normalize selected source IDs while preserving an explicit filter."""
    if source_ids is None or not source_ids:
        return None
    return tuple(dict.fromkeys(source_id.strip() for source_id in source_ids))


def source_descriptors(providers: Iterable[str]) -> list[SessionSourceDescriptor]:
    """Return canonical, public descriptors for cached provider rows."""
    available = frozenset(providers)
    return [{"id": source_id, "label": label} for source_id, label in SOURCE_REGISTRY if source_id in available]


def _valid_open_key(value: object) -> str:
    return value if isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value) else ""


# Shared with the Swift decoder: permit binary-float roundoff only, not a USD mismatch.
_MIXED_COST_REL_TOLERANCE = 1e-9
_MIXED_COST_ABS_TOLERANCE = 1e-12


def _local_contribution(provider, cost, status, provenance, source, billing_provider=None):
    if status not in ("exact", "partial") or not isinstance(provider, str) or not provider:
        return None
    if provider == "cline" and provenance == "actual":
        return None
    if cost is None:
        return None
    rollup_provider = billing_provider if source in ("opencode", "mimo") and isinstance(billing_provider, str) and billing_provider else provider
    rollup_source = source.strip().lower() if isinstance(source, str) and source.strip() else ""
    rollup = f"{rollup_provider}::{rollup_source}" if rollup_source else rollup_provider
    return rollup, cost, status


def _local_contributions(session, provenance):
    if not isinstance(session, dict):
        return []
    provider_costs = session.get("providerCosts")
    if isinstance(provider_costs, dict) and provider_costs:
        # Multi-provider OpenCode session: one contribution per upstream
        # provider; the top-level aggregate is skipped so nothing is counted
        # twice.
        contributions = []
        for provider, cost_row in provider_costs.items():
            if not isinstance(cost_row, dict):
                continue
            child = {**cost_row, "provider": provider, "source": session.get("source")}
            if child.get("costBilling") not in ("subscription", "api"):
                child["costBilling"] = _billing_mode(session)
            contributions.extend(_local_contributions(child, provenance))
        return contributions

    session_provenance = session.get("costProvenance")
    mixed = session_provenance == "mixed"
    if session_provenance == provenance:
        cost = finite_number(session.get("costUSD"), minimum=0) or None
    elif mixed:
        parent_cost = finite_number(session.get("costUSD"), minimum=0)
        breakdown = session.get("costBreakdown")
        if not isinstance(breakdown, dict):
            return []
        actual_cost = finite_number(breakdown.get("actualUSD"), minimum=0)
        estimated_cost = finite_number(breakdown.get("estimatedUSD"), minimum=0)
        if (
            parent_cost is None
            or actual_cost is None
            or estimated_cost is None
            or not math.isclose(
                math.fsum((actual_cost, estimated_cost)),
                parent_cost,
                rel_tol=_MIXED_COST_REL_TOLERANCE,
                abs_tol=_MIXED_COST_ABS_TOLERANCE,
            )
        ):
            return []
        cost = actual_cost if provenance == "actual" else estimated_cost
    else:
        return []
    contribution = _local_contribution(
        session.get("provider"),
        cost,
        session.get("costStatus"),
        provenance,
        session.get("source"),
        billing_provider=session.get("billingProvider"),
    )
    return [(_billing_mode(session), *contribution, finite_number(session.get("tokens"), minimum=0))] if contribution is not None else []


def _billing_mode(session):
    mode = session.get("costBilling") if isinstance(session, dict) else None
    return mode if mode in ("subscription", "api") else "api"


def _session_date(session):
    """Local calendar day a session's cost lands on, or "" when unknown.

    Local, not UTC, for the same reason the per-provider stats use local days:
    "which day did I spend that" is a question about the user's own calendar.
    """
    activity = finite_number(session.get("lastActivityAt"), minimum=0) if isinstance(session, dict) else None
    if not activity:
        return ""
    try:
        return datetime.datetime.fromtimestamp(activity).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def _row_cost_groups(row, provenance=None):
    """Materialized contribution rows for one redacted session row.

    Returns (billing, provenance, rollup, cost, status, day, token_share)
    tuples — one per contribution, for both provenances unless one is asked
    for. The token share is the session's tokens split across that
    provenance's contributions, so a multi-provider session never counts its
    tokens once per upstream provider.
    """
    day = _session_date(row)
    tokens = finite_number(row.get("tokens"), minimum=0) or 0
    contributions = []
    for prov in ("actual", "estimated"):
        contributions.extend((prov, *item) for item in _local_contributions(row, prov))
    counts = {}
    token_values = {}
    for prov, billing, rollup, cost, status, component_tokens in contributions:
        counts[rollup] = counts.get(rollup, 0) + 1
        token_values[rollup] = component_tokens
    has_component_tokens = all(value is not None for value in token_values.values())
    has_component_tokens = has_component_tokens and sum(token_values.values()) <= tokens
    groups = []
    for prov, billing, rollup, cost, status, component_tokens in contributions:
        if provenance is not None and prov != provenance:
            continue
        share = token_values[rollup] / counts[rollup] if has_component_tokens else tokens / len(contributions)
        groups.append((billing, prov, rollup, cost, status, day, share if day else 0))
    return groups


class _GroupState:
    __slots__ = ("totals", "partial", "costs", "daily", "daily_tokens")

    def __init__(self) -> None:
        self.totals: dict[str, float] = {}
        self.partial: set[str] = set()
        self.costs: list[float] = []
        self.daily: dict[str, dict[str, float]] = {}
        self.daily_tokens: dict[str, dict[str, float]] = {}

    def add(self, rollup: str, cost: float, status: str, day: str, token_share: float) -> None:
        self.totals[rollup] = self.totals.get(rollup, 0) + cost
        self.costs.append(cost)
        if status == "partial":
            self.partial.add(rollup)
        if day:
            per_provider = self.daily.setdefault(rollup, {})
            per_provider[day] = per_provider.get(day, 0) + cost
        if day and token_share:
            per_provider_tokens = self.daily_tokens.setdefault(rollup, {})
            per_provider_tokens[day] = per_provider_tokens.get(day, 0) + token_share

    def finish(self, provenance: str) -> dict:
        if not self.totals:
            return {"costStatus": "unavailable"}
        total = math.fsum(self.costs)
        if not math.isfinite(total) or any(not math.isfinite(cost) for cost in self.totals.values()):
            return {"costStatus": "unavailable"}
        return {
            "totalUSD": total,
            "costStatus": "partial" if self.partial else "exact",
            "costProvenance": provenance,
            "providers": {
                provider: {
                    "costUSD": cost,
                    "costStatus": "partial" if provider in self.partial else "exact",
                    "costProvenance": provenance,
                    **(
                        {"dailyUSD": [{"date": date, "usd": usd} for date, usd in sorted(self.daily[provider].items())]}
                        if self.daily.get(provider)
                        else {}
                    ),
                    **(
                        {"dailyTokens": [{"date": date, "total": round(total)} for date, total in sorted(self.daily_tokens[provider].items())]}
                        if self.daily_tokens.get(provider)
                        else {}
                    ),
                    **({"source": provider.rsplit("::", 1)[1]} if "::" in provider else {}),
                }
                for provider, cost in self.totals.items()
            },
        }


def _accumulate_group(contributions, provenance):
    """Accumulate (rollup, cost, status, day, token_share) tuples into one group."""
    state = _GroupState()
    for contribution in contributions:
        state.add(*contribution)
    return state.finish(provenance)


def _spend_groups_from_rows(rows):
    """Accumulate materialized contribution rows into the four spend groups."""
    states = {
        ("api", "actual"): _GroupState(),
        ("api", "estimated"): _GroupState(),
        ("subscription", "estimated"): _GroupState(),
        ("subscription", "actual"): _GroupState(),
    }
    for billing, provenance, rollup, cost, status, day, token_share in rows:
        states[(billing, provenance)].add(rollup, cost, status, day, token_share)
    return {
        "actual": states[("api", "actual")].finish("actual"),
        "estimated": states[("api", "estimated")].finish("estimated"),
        "subscription": states[("subscription", "estimated")].finish("estimated"),
        "subscriptionActual": states[("subscription", "actual")].finish("actual"),
    }


def _parse_sources(parser, items):
    """Yield ``(item, rows)`` for each changed source; rows is None when the
    parser skipped it. One source (the usual refresh) parses inline; several
    overlap their file IO on a few threads, finishing in any order."""

    def run(item):
        try:
            return _redact(parser(item[2]))
        except SkipSource:
            return None

    if len(items) <= 1:
        for item in items:
            yield item, run(item)
        return
    # Parsers are mostly file IO under the GIL; a few workers overlap the
    # waits, more only add threads.
    with ThreadPoolExecutor(max_workers=min(4, len(items))) as executor:
        pending = {executor.submit(run, item): item for item in items}
        for future in as_completed(pending):
            yield pending[future], future.result()


class SessionIndex:
    """Keep redacted session rows keyed by a private source fingerprint."""

    def __init__(self, cache_path: str | Path) -> None:
        self._cache_path = Path(cache_path)
        self._cache_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self._cache_path.parent.chmod(0o700)
        except OSError:
            pass

    def _open(self, *, discard_stale: bool = False) -> sqlite3.Connection:
        from .session_index_storage import open_index

        return open_index(self._cache_path, discard_stale=discard_stale)

    def reconcile(
        self,
        sources: Iterable[SessionSource],
        parser: SessionParser,
        *,
        force: bool = False,
    ) -> None:
        """Refresh changed sources and remove sources absent from the scan."""
        source_map = {_source_key(source): source for source in sources}
        connection = self._open(discard_stale=True)
        try:
            meta = {
                str(row[0]): (int(row[1]), int(row[2]), int(row[3]))
                for row in connection.execute("SELECT source_key, mtime_ns, size, source_order FROM source_meta")
            }
            stored = {key: (mtime_ns, size) for key, (mtime_ns, size, _order) in meta.items()}
            stored_orders = {key: order for key, (_mtime_ns, _size, order) in meta.items()}
            # A source that cached no rows is never taken at its word. A
            # parse that succeeds but yields nothing — a collector run
            # somewhere it could not see the store, e.g. from a desktop
            # shell with a different HOME — otherwise writes a fingerprint
            # that makes the source look up to date forever, hiding every
            # one of its sessions until those files happen to change.
            cached_rows = {
                str(row[0]): int(row[1]) for row in connection.execute("SELECT source_key, COUNT(*) FROM session_rows GROUP BY source_key")
            }
            mutated = False
            removed = stored.keys() - source_map.keys()
            if removed:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    for key in removed:
                        mutated = True
                        connection.execute("DELETE FROM session_rows WHERE source_key = ?", (key,))
                        connection.execute("DELETE FROM session_cost_groups WHERE source_key = ?", (key,))
                        connection.execute("DELETE FROM source_meta WHERE source_key = ?", (key,))

            to_parse = []
            reordered = []
            for source_order, (key, source) in enumerate(source_map.items()):
                unchanged = stored.get(key) == (source.mtime_ns, source.size)
                if not force and unchanged and cached_rows.get(key, 0) > 0:
                    if stored_orders.get(key) != source_order:
                        reordered.append((source_order, key))
                    continue
                to_parse.append((key, source_order, source))
            if reordered:
                mutated = True
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.executemany("UPDATE source_meta SET source_order = ? WHERE source_key = ?", reordered)

            for (key, source_order, source), rows in _parse_sources(parser, to_parse):
                if rows is None:
                    with connection:
                        connection.execute(
                            "INSERT INTO source_meta (source_key, mtime_ns, size, source_order) "
                            "VALUES (?, -1, -1, ?) ON CONFLICT(source_key) DO UPDATE SET mtime_ns = -1, size = -1",
                            (key, source_order),
                        )
                    mutated = True
                    continue

                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "INSERT INTO source_meta (source_key, mtime_ns, size, source_order) "
                        "VALUES (?, ?, ?, ?) ON CONFLICT(source_key) DO UPDATE SET "
                        "mtime_ns = excluded.mtime_ns, size = excluded.size, "
                        "source_order = excluded.source_order",
                        (key, source.mtime_ns, source.size, source_order),
                    )
                    connection.execute("DELETE FROM session_rows WHERE source_key = ?", (key,))
                    connection.executemany(
                        "INSERT INTO session_rows (source_key, row_order, provider, "
                        "title, session_name, state, last_activity_at, detail, "
                        "open_key, full_title, source, cost_usd, cost_status, "
                        "cost_provenance, cost_breakdown, billing_provider, "
                        "provider_costs, cost_billing, tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            (
                                key,
                                row_order,
                                row["provider"],
                                row["title"],
                                row["sessionName"],
                                row["state"],
                                row["lastActivityAt"],
                                row["detail"],
                                row["openKey"],
                                row.get("fullTitle", ""),
                                row.get("source", ""),
                                row.get("costUSD"),
                                row.get("costStatus", "unavailable"),
                                row.get("costProvenance"),
                                _json_or_none(row.get("costBreakdown")),
                                row.get("billingProvider", ""),
                                _json_or_none(row.get("providerCosts")),
                                row.get("costBilling", "api"),
                                int(row.get("tokens") or 0),
                            )
                            for row_order, row in enumerate(rows)
                        ),
                    )
                    connection.execute("DELETE FROM session_cost_groups WHERE source_key = ?", (key,))
                    connection.executemany(
                        "INSERT INTO session_cost_groups (source_key, row_order, billing, "
                        "provenance, rollup, cost, status, day, token_share) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            (key, row_order, billing, provenance, rollup, cost, status, day, token_share)
                            for row_order, row in enumerate(rows)
                            for billing, provenance, rollup, cost, status, day, token_share in _row_cost_groups(row)
                        ),
                    )
                mutated = True
            if mutated:
                try:
                    connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
                except sqlite3.DatabaseError:
                    pass
        finally:
            connection.close()

    def rows(self) -> list[SessionRow]:
        """Return the cached merged rows in their original collector order."""
        connection = self._open()
        try:
            page = connection.execute(
                "SELECT session_rows.provider, session_rows.title, session_rows.session_name, "
                "session_rows.state, session_rows.last_activity_at, session_rows.detail, "
                "session_rows.open_key, session_rows.full_title, session_rows.source, "
                "session_rows.cost_usd, session_rows.cost_status, session_rows.cost_provenance, "
                "session_rows.cost_breakdown, session_rows.billing_provider, session_rows.provider_costs, "
                "session_rows.cost_billing, session_rows.tokens "
                "FROM session_rows "
                "LEFT JOIN source_meta ON source_meta.source_key = session_rows.source_key "
                "ORDER BY source_meta.source_order, session_rows.row_order"
            ).fetchall()
        finally:
            connection.close()
        return [_row_from_columns(row) for row in page]

    def spend_groups(self) -> dict | None:
        """The four local-spend groups from the materialized contribution table.

        Returns None when the cache was built by different code: the caller
        then falls back to the full-row path, which is what the old envelope
        did with that cache.
        """
        from .session_index_storage import _schema_version

        connection = self._open()
        try:
            row = connection.execute("PRAGMA user_version").fetchone()
            stored = int(row[0]) if row else 0
            if stored != _schema_version():
                return None
            rows = connection.execute(
                "SELECT g.billing, g.provenance, g.rollup, g.cost, g.status, g.day, g.token_share "
                "FROM session_cost_groups g "
                "JOIN source_meta m ON m.source_key = g.source_key "
                "ORDER BY m.source_order, g.row_order"
            ).fetchall()
        finally:
            connection.close()
        return _spend_groups_from_rows(rows)

    def query(
        self,
        query: str = "",
        limit: int = 60,
        offset: int = 0,
        source_ids: Sequence[str] | None = None,
    ) -> SessionQueryResult:
        """Return a literal, case-insensitive substring page of public rows."""
        needle = query.strip().casefold()
        normalized_source_ids = normalize_source_ids(source_ids)
        connection = self._open()
        try:
            available_providers = [str(row[0]) for row in connection.execute("SELECT DISTINCT provider FROM session_rows").fetchall()]
            predicates: list[str] = []
            parameters: list[str] = []
            if needle:
                predicates.append("(" + " OR ".join(f"instr(casefold({column}), ?) > 0" for column in _SEARCH_COLUMNS) + ")")
                parameters.extend((needle,) * len(_SEARCH_COLUMNS))
            if normalized_source_ids is not None:
                predicates.append("(" + " OR ".join("provider = ?" for _ in normalized_source_ids) + ")")
                parameters.extend(normalized_source_ids)
            where = " WHERE " + " AND ".join(predicates) if predicates else ""
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM session_rows" + where,
                    tuple(parameters),
                ).fetchone()[0]
            )
            page = connection.execute(
                "SELECT session_rows.provider, session_rows.title, session_rows.session_name, "
                "session_rows.state, session_rows.last_activity_at, session_rows.detail, "
                "session_rows.open_key, session_rows.full_title, session_rows.source, "
                "session_rows.cost_usd, session_rows.cost_status, session_rows.cost_provenance, "
                "session_rows.cost_breakdown, session_rows.billing_provider, session_rows.provider_costs, "
                "session_rows.cost_billing, session_rows.tokens "
                "FROM session_rows "
                "LEFT JOIN source_meta ON source_meta.source_key = session_rows.source_key" + where + " ORDER BY session_rows.last_activity_at DESC, "
                "source_meta.source_order ASC, session_rows.row_order ASC LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            ).fetchall()
            sessions: list[SessionRow] = [_row_from_columns(row) for row in page]
        finally:
            connection.close()
        return {
            "updatedAt": int(time.time()),
            "sessions": sessions,
            "total": total,
            "totalExact": True,
            "offset": offset,
            "limit": limit,
            "hasMore": offset + limit < total,
            "sources": source_descriptors(available_providers),
        }
