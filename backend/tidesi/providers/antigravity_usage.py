"""Read token usage from Antigravity's local generation databases."""

from __future__ import annotations

import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from ..billing import UsageBucket
from ..session_index import SkipSource
from . import antigravity_usage_cache

_MAX_BLOB = 4 * 1024 * 1024
_QUERY_SECONDS = 10.0
_MAX_FIELD = (1 << 29) - 1
_FORMAT_CHECK_DATABASES = 3


@dataclass(frozen=True)
class AntigravityUsage:
    session_id: str
    last_activity: int
    usage: tuple[UsageBucket, ...]
    source_path: str
    usage_complete: bool = True


@dataclass(frozen=True)
class _Message:
    fields: tuple[tuple[int, int, int | bytes], ...]

    def values(self, number: int, wire: int) -> tuple[int | bytes, ...]:
        return tuple(value for field, kind, value in self.fields if field == number and kind == wire)

    def first(self, number: int, wire: int) -> int | bytes | None:
        return next(iter(self.values(number, wire)), None)


def _varint(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 64, 7):
        if position >= len(data):
            raise ValueError("truncated protobuf varint")
        byte = data[position]
        position += 1
        if shift == 63 and byte > 1:
            raise ValueError("protobuf varint exceeds 64 bits")
        value |= (byte & 127) << shift
        if byte < 128:
            return value, position
    raise ValueError("oversized protobuf varint")


def _parse(data: bytes) -> _Message:
    if len(data) > _MAX_BLOB:
        raise ValueError("oversized protobuf message")
    fields = []
    position = 0
    while position < len(data):
        tag, position = _varint(data, position)
        number, wire = tag >> 3, tag & 7
        if number == 0 or number > _MAX_FIELD:
            raise ValueError("invalid protobuf field")
        if wire == 0:
            value, position = _varint(data, position)
        elif wire == 1:
            if position + 8 > len(data):
                raise ValueError("truncated protobuf fixed64")
            value, position = data[position : position + 8], position + 8
        elif wire == 2:
            size, position = _varint(data, position)
            if size > _MAX_BLOB or position + size > len(data):
                raise ValueError("invalid protobuf length")
            value, position = data[position : position + size], position + size
        elif wire == 5:
            if position + 4 > len(data):
                raise ValueError("truncated protobuf fixed32")
            value, position = data[position : position + 4], position + 4
        else:
            raise ValueError("unsupported protobuf wire type")
        fields.append((number, wire, value))
    return _Message(tuple(fields))


def _nested(message: _Message, number: int) -> _Message | None:
    value = message.first(number, 2)
    return _parse(value) if isinstance(value, bytes) else None


def _text(message: _Message | None, number: int) -> str | None:
    if message is None:
        return None
    value = message.first(number, 2)
    if not isinstance(value, bytes):
        return None
    try:
        text = value.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    return text or None


def _timestamp(message: _Message | None) -> int:
    if message is None:
        return 0
    seconds, nanos = message.first(1, 0), message.first(2, 0)
    nanos = 0 if nanos is None else nanos
    if not isinstance(seconds, int) or not isinstance(nanos, int) or nanos >= 1_000_000_000 or seconds > 253402300799:
        return 0
    return seconds if seconds > 0 else 0


# Antigravity reports rollout and effort variants of public models
# ("claude-opus-4-6-thinking", "gemini-3.7-flash-tiered", "gemini-3-flash-a").
# They are priced as the public model only when stripping these suffixes lands
# on an exact catalog ID; anything else ("gemini-default") stays unpriced.
_VARIANT_SUFFIX = re.compile(r"-(?:thinking|tiered|low|medium|high|exp-[a-z]|[a-z])$")


def price_model(model: str, rates: dict) -> str | None:
    """The catalog ID whose rate a raw Antigravity model ID carries, or None."""
    candidate = re.sub(r"(\d)p(\d)", r"\1.\2", model)  # "gemini-3p7" is "gemini-3.7"
    while True:
        for name in (candidate, candidate + "-preview"):
            if name in rates:
                return name
        stripped = _VARIANT_SUFFIX.sub("", candidate)
        if stripped == candidate:
            return None
        candidate = stripped


def with_price_aliases(rates: dict, buckets) -> dict:
    """Copy of `rates` where each raw model ID also maps to its public model's rate."""
    out = {provider: dict(provider_rates) for provider, provider_rates in rates.items()}
    for bucket in buckets:
        provider_rates = out.get(bucket.provider)
        if provider_rates and bucket.model and bucket.model not in provider_rates:
            public = price_model(bucket.model, provider_rates)
            if public is not None:
                provider_rates[bucket.model] = provider_rates[public]
    return out


def _provider(model: str) -> str:
    prefix = model.split("-", 1)[0].lower()
    return {"gemini": "google", "claude": "anthropic", "gpt": "openai"}.get(prefix, "")


def _model_assignments(blobs: tuple[bytes, ...]) -> tuple[dict[str, str], str | None]:
    labeled: dict[str, set[str]] = {}
    concrete: set[str] = set()
    unresolved: set[str] = set()
    for blob in blobs:
        chat = _nested(_parse(blob), 1)
        if chat is None:
            continue
        model, label = _text(chat, 19), _text(chat, 21)
        if model and model.lower() != "gemini-default":
            concrete.add(model)
            if label:
                labeled.setdefault(label, set()).add(model)
        elif label:
            unresolved.add(label)
    by_label = {label: next(iter(models)) for label, models in labeled.items() if len(models) == 1}
    sole = next(iter(concrete)) if len(concrete) == 1 and unresolved.issubset(by_label) else None
    return by_label, sole


def _extract(path: Path, connection: sqlite3.Connection) -> AntigravityUsage:
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "gen_metadata" not in tables:
            raise SkipSource("generation table missing")
        # An oversized generation (seen at 49 MB) is skipped on its own: it never
        # shrinks, so failing the database would hide all Antigravity usage for good.
        rows = list(connection.execute("SELECT idx, CASE WHEN length(data) <= ? THEN data END FROM gen_metadata ORDER BY idx", (_MAX_BLOB,)))
        usage_complete = all(isinstance(blob, bytes) for _, blob in rows)
        rows = [(index, blob) for index, blob in rows if isinstance(blob, bytes)]
        blobs = tuple(blob for _, blob in rows)
        by_label, sole = _model_assignments(blobs)
        session_time = 0
        if "trajectory_metadata_blob" in tables:
            trajectory = connection.execute("SELECT data FROM trajectory_metadata_blob LIMIT 1").fetchone()
            if trajectory and isinstance(trajectory[0], bytes) and len(trajectory[0]) <= _MAX_BLOB:
                session_time = _timestamp(_nested(_parse(trajectory[0]), 2))
            elif trajectory:
                raise SkipSource("trajectory metadata outside bounds")
        step_times: dict[str, int] = {}
        indexed_step_times: dict[int, int] = {}
        if "steps" in tables:
            for (metadata,) in connection.execute("SELECT metadata FROM steps WHERE step_type=15"):
                if not isinstance(metadata, bytes) or len(metadata) > _MAX_BLOB:
                    raise SkipSource("step metadata outside bounds")
                step = _parse(metadata)
                timestamp = _timestamp(_nested(step, 1))
                if timestamp > 0:
                    response = _text(_nested(step, 9), 11)
                    step_meta = _nested(step, 20)
                    step_index = step_meta.first(3, 0) if step_meta else None
                    if response:
                        step_times[response] = timestamp
                    if isinstance(step_index, int):
                        indexed_step_times[step_index] = timestamp
        seen: set[str] = set()
        buckets: list[UsageBucket] = []
        last_activity = 0
        for index, blob in rows:
            generation = _parse(blob)
            chat = _nested(generation, 1)
            usage = _nested(chat, 4) if chat else None
            if chat is None or usage is None:
                continue
            response_id = _text(usage, 11)
            if response_id and response_id in seen:
                continue
            if response_id:
                seen.add(response_id)
            model = _text(chat, 19)
            label = _text(chat, 21)
            if not model or model == "gemini-default":
                model = (by_label.get(label) if label else None) or (sole if not label else None) or model or "unknown"
            counts = {number: usage.first(number, 0) for number in (1, 2, 5, 9, 10)}
            values = {number: value if isinstance(value, int) else 0 for number, value in counts.items()}
            tokens = (values[1] + values[2], values[9], values[5], values[10])
            if not any(tokens):
                continue
            timestamp = _timestamp(_nested(_nested(chat, 9), 4) if _nested(chat, 9) else None)
            if timestamp <= 0:
                timestamp = step_times.get(response_id, 0) if response_id else indexed_step_times.get(index, 0)
            last_activity = max(last_activity, timestamp)
            buckets.append(UsageBucket(_provider(model), model, path.stem, tokens[0], tokens[1], tokens[2], 0, tokens[3], None, "antigravity"))
        if not last_activity:
            last_activity = session_time or int(path.stat().st_mtime)
        return AntigravityUsage(path.stem, last_activity, tuple(buckets), str(path), usage_complete)
    except (sqlite3.Error, OSError, ValueError, OverflowError) as error:
        raise SkipSource("generation database unreadable") from error


def _cached_record(path: Path, cached: tuple[int, tuple[tuple[str, str, int, int, int, int], ...], bool]) -> AntigravityUsage:
    activity, rows, usage_complete = cached
    buckets = tuple(
        UsageBucket(provider, model, path.stem, counts[0], counts[1], counts[2], 0, counts[3], None, "antigravity")
        for provider, model, *counts in rows
    )
    return AntigravityUsage(path.stem, activity, buckets, str(path), usage_complete)


def _cache_rows(record: AntigravityUsage) -> tuple[tuple[str, str, int, int, int, int], ...]:
    return tuple(
        (bucket.provider, bucket.model, bucket.input_tokens, bucket.output_tokens, bucket.cache_read_tokens, bucket.reasoning_tokens)
        for bucket in record.usage
    )


def read_database(path: Path, fingerprint: str | None = None) -> list[AntigravityUsage]:
    """Read one database read-only; incomplete databases raise SkipSource."""
    if fingerprint is None:
        fingerprint = antigravity_usage_cache.parser_fingerprint((Path(__file__), Path(antigravity_usage_cache.__file__)))
    state = antigravity_usage_cache.signature(path, fingerprint)
    if state is not None:
        cached = antigravity_usage_cache.load(path, fingerprint, state)
        if cached is not None:
            return [_cached_record(path, cached)]
    deadline = time.monotonic() + _QUERY_SECONDS
    uri = f"file:{quote(str(path.resolve()))}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            record = _extract(path, connection)
        rows = _cache_rows(record)
        if state is not None and antigravity_usage_cache.signature(path, fingerprint) == state:
            antigravity_usage_cache.save(path, fingerprint, state, record.last_activity, rows, record.usage_complete)
        return [_cached_record(path, (record.last_activity, rows, record.usage_complete))]
    except (sqlite3.Error, OSError) as error:
        raise SkipSource("generation database unavailable") from error


def discover_paths(root: Path) -> list[Path]:
    """Return conversation databases in both Antigravity stores."""
    paths = []
    for client in ("antigravity-cli", "antigravity-ide", "antigravity"):
        try:
            paths.extend((root / client / "conversations").glob("*.db"))
        except OSError:
            continue
    return sorted(path for path in paths if path.is_file())


def read_usage(root: Path) -> list[AntigravityUsage]:
    """Read every discovered database. A locked or interrupted one fails the scan
    so it is retried; a corrupt one is skipped, since it would never recover."""
    fingerprint = antigravity_usage_cache.parser_fingerprint((Path(__file__), Path(antigravity_usage_cache.__file__)))
    records = []
    paths = discover_paths(root)
    for path in paths:
        try:
            records.extend(read_database(path, fingerprint))
        except SkipSource as error:
            if isinstance(error.__cause__, sqlite3.OperationalError):
                raise
    # The field numbers are undocumented. If Antigravity renumbers them, every
    # database still parses but yields no counts: report that instead of zero usage.
    if sum(record.usage_complete for record in records) >= _FORMAT_CHECK_DATABASES and not any(record.usage for record in records):
        raise SkipSource("Antigravity usage format not recognized")
    antigravity_usage_cache.prune(paths)
    return records
