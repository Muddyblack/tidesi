"""Cached standard token rates from public model pricing catalogs.

models.dev is the primary source for OpenCode provider/model IDs. LiteLLM and
OpenRouter fill exact misses without becoming runtime dependencies.
"""

import json
import os
import re
import tempfile
import threading
import time

from . import config
from .contract import finite_number
from .http import as_json, fetch_json
from .pricing_lock import acquire as _acquire_lock
from .pricing_lock import release as _release_lock

MODELS_DEV_SOURCE_URL = "https://models.dev/api.json"
SOURCE_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
OPENROUTER_SOURCE_URL = "https://openrouter.ai/api/v1/models"
REFRESH_SECONDS = 604800
RETRY_SECONDS = 900
CACHE_FILENAME = "pricing-models-v2.json"
CACHE_VERSION = 2
MAX_PROVIDERS = 256
MAX_MODELS_PER_PROVIDER = 1024
MAX_MODEL_ID_LENGTH = 128
_PROCESS_LOCK = threading.RLock()
_CURRENT_SNAPSHOTS = {}
_REFRESH_GENERATIONS = {}
_CATALOG_CACHE = {}


def valid_rate(value):
    return finite_number(value, minimum=0, maximum=1_000_000) is not None


def _safe_namespace(value):
    if not isinstance(value, str):
        return ""
    namespace = value.strip().lower()
    return namespace if len(namespace) <= 64 and re.fullmatch(r"[a-z0-9._-]+", namespace) else ""


def parse_models_dev_catalog(data):
    """Normalize models.dev's USD-per-million-token catalog."""
    if not isinstance(data, dict):
        raise ValueError("models.dev pricing catalog must be a JSON object")
    tables = {}
    for raw_provider, provider_row in data.items():
        if len(tables) >= MAX_PROVIDERS:
            break
        provider = _safe_namespace(raw_provider)
        if not provider or not isinstance(provider_row, dict):
            continue
        models = provider_row.get("models")
        if not isinstance(models, dict):
            continue
        table = {}
        for model, model_row in models.items():
            if len(table) >= MAX_MODELS_PER_PROVIDER:
                break
            if not isinstance(model, str) or not model or len(model) > MAX_MODEL_ID_LENGTH or not isinstance(model_row, dict):
                continue
            cost = model_row.get("cost")
            if not isinstance(cost, dict):
                continue
            rates = {key: cost.get(key) for key in ("input", "output")}
            if not all(valid_rate(value) for value in rates.values()):
                continue
            price = dict(rates)
            cached = cost.get("cache_read")
            if cached is not None:
                if not valid_rate(cached):
                    continue
                price["cached"] = cached
            table[model] = price
        if table:
            tables[provider] = table
    if not tables:
        raise ValueError("models.dev pricing catalog has no usable rates")
    return tables


def parse_catalog(data):
    """Select direct-provider text rates and convert USD/token to USD/million.

    Exact model IDs only: reseller, regional, batch and fine-tuned prices must
    never become the default rate for another model by trimming their names.
    Unknown/incomplete rows remain unpriced.
    """
    if not isinstance(data, dict):
        raise ValueError("pricing catalog must be a JSON object")
    tables = {}
    for model, row in data.items():
        if not isinstance(model, str) or not model or len(model) > MAX_MODEL_ID_LENGTH or "/" in model or not isinstance(row, dict):
            continue
        provider = row.get("litellm_provider")
        provider = _safe_namespace(provider)
        if not provider or row.get("mode") not in ("chat", "completion", "responses", "embedding"):
            continue
        rates = {key: row.get(key + "_cost_per_token") for key in ("input", "output")}
        if not all(valid_rate(value) and value <= 1 for value in rates.values()):
            continue
        price = {key: value * 1_000_000 for key, value in rates.items()}
        cached = row.get("cache_read_input_token_cost")
        if cached is not None:
            if not valid_rate(cached) or cached > 1:
                continue
            price["cached"] = cached * 1_000_000
        if provider not in tables and len(tables) >= MAX_PROVIDERS:
            continue
        table = tables.setdefault(provider, {})
        if len(table) < MAX_MODELS_PER_PROVIDER:
            table[model] = price
    if not tables:
        raise ValueError("pricing catalog has no usable rates")
    return tables


def parse_openrouter_catalog(data):
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("OpenRouter pricing catalog must contain a data list")
    tables = {}
    for row in data["data"]:
        if not isinstance(row, dict):
            continue
        model_id = row.get("id")
        pricing = row.get("pricing")
        if not isinstance(model_id, str) or not model_id or len(model_id) > MAX_MODEL_ID_LENGTH or not isinstance(pricing, dict):
            continue
        provider, separator, model = model_id.partition("/")
        provider = _safe_namespace(provider)
        if not separator or not provider or not model:
            continue
        prompt = pricing.get("prompt")
        completion = pricing.get("completion")
        try:
            prompt = float(prompt)
            completion = float(completion)
        except (TypeError, ValueError):
            continue
        if any(type(value) is bool for value in (pricing.get("prompt"), pricing.get("completion"))):
            continue
        if not valid_rate(prompt) or not valid_rate(completion) or prompt > 1 or completion > 1:
            continue
        price = {"input": prompt * 1_000_000, "output": completion * 1_000_000}
        cached = pricing.get("input_cache_read")
        if cached is not None:
            if type(cached) is bool:
                continue
            try:
                cached = float(cached)
            except (TypeError, ValueError):
                continue
            if not valid_rate(cached) or cached > 1:
                continue
            price["cached"] = cached * 1_000_000
        if provider not in tables and len(tables) >= MAX_PROVIDERS:
            continue
        table = tables.setdefault(provider, {})
        if len(table) < MAX_MODELS_PER_PROVIDER:
            table[model_id] = price
    return tables


def _requested_models(models):
    if not isinstance(models, dict):
        return []
    requested = []
    for raw_provider, values in models.items():
        provider = _safe_namespace(raw_provider)
        if not provider:
            continue
        candidates = (values,) if isinstance(values, str) else values if isinstance(values, (list, tuple, set)) else ()
        requested.extend((provider, model) for model in candidates if isinstance(model, str) and model)
    return requested


def _merge_openrouter_missing(tables, fallback, models):
    missing = []
    for provider, model in _requested_models(models):
        if model in tables.get(provider, {}):
            continue
        candidates = (model, f"{provider}/{model}")
        row = next((fallback.get(provider, {}).get(candidate) for candidate in candidates if candidate in fallback.get(provider, {})), None)
        if row is None:
            missing.append((provider, model))
            continue
        tables.setdefault(provider, {})[model] = row
    return missing


def _valid_tables(tables):
    if not isinstance(tables, dict) or not tables or len(tables) > MAX_PROVIDERS:
        return False
    for provider, table in tables.items():
        if provider != _safe_namespace(provider):
            return False
        if not isinstance(table, dict) or not table or len(table) > MAX_MODELS_PER_PROVIDER:
            return False
        for model, price in table.items():
            if (
                not isinstance(model, str)
                or not model
                or len(model) > MAX_MODEL_ID_LENGTH
                or not isinstance(price, dict)
                or not {"input", "output"} <= price.keys()
            ):
                return False
            if not all(valid_rate(value) for value in price.values()):
                return False
    return True


def _read_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            cached = json.load(f)
        if not isinstance(cached, dict) or cached.get("source") != MODELS_DEV_SOURCE_URL or cached.get("version") != CACHE_VERSION:
            return {}
        if not all(type(cached.get(key)) in (int, float) and 0 <= cached[key] <= 1e12 for key in ("fetchedAt", "checkedAt")):
            return {}
        if not _valid_tables(cached.get("providers")) and cached.get("providers") != {}:
            return {}
        return cached
    except (OSError, ValueError):
        return {}


def _write_cache(path, snapshot):
    temporary = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(path), delete=False) as f:
            temporary = f.name
            json.dump(snapshot, f, allow_nan=False, separators=(",", ":"))
        os.replace(temporary, path)
    except OSError:
        # Read-only caches must not prevent pricing this refresh.
        pass
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _snapshot(path):
    with _PROCESS_LOCK:
        identity = (_cache_file_identity(path), _catalog_memory_identity(path))
        cached = _CATALOG_CACHE.get(path)
        if cached is not None and cached[0] == identity:
            return cached[1]
        disk = _read_cache(path)
        memory = _CURRENT_SNAPSHOTS.get(path, {})
        snapshot = memory if memory.get("checkedAt", 0) >= disk.get("checkedAt", 0) else disk
        _CATALOG_CACHE[path] = (identity, snapshot)
        return snapshot


def _cache_file_identity(path):
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def _catalog_memory_identity(path):
    snapshot = _CURRENT_SNAPSHOTS.get(path)
    if snapshot is None:
        return 0, None, 0, 0
    return (
        _REFRESH_GENERATIONS.get(path, 0),
        id(snapshot),
        snapshot.get("checkedAt", 0),
        snapshot.get("fetchedAt", 0),
    )


def _fetch_openrouter():
    response = fetch_json(OPENROUTER_SOURCE_URL, timeout=10)
    if response.status != 200:
        raise ValueError(f"OpenRouter pricing download failed (HTTP {response.status})")
    data = as_json(response.body)
    return parse_openrouter_catalog(data)


def _fetch_models_dev():
    response = fetch_json(
        MODELS_DEV_SOURCE_URL,
        headers={"Accept": "application/json", "User-Agent": "tidesi/1.0"},
        timeout=10,
    )
    if response.status != 200:
        raise ValueError(f"models.dev pricing download failed (HTTP {response.status})")
    return parse_models_dev_catalog(as_json(response.body))


def _fetch_litellm():
    response = fetch_json(SOURCE_URL, timeout=10)
    if response.status != 200:
        raise ValueError(f"pricing download failed (HTTP {response.status})")
    return parse_catalog(as_json(response.body))


def _merge_tables(tables, fallback, providers=None):
    for provider, models in fallback.items():
        if providers is not None and provider not in providers:
            continue
        target = tables.setdefault(provider, {})
        for model, price in models.items():
            target.setdefault(model, price)


def load_catalog(*, force=False, models=None):
    """Refresh weekly, retry failures after 15 minutes, and retain last-good rates."""
    path = os.path.join(config.cache_dir(), CACHE_FILENAME)
    generation = _REFRESH_GENERATIONS.get(path, 0)
    with _PROCESS_LOCK:
        cached = _snapshot(path)
        initial = cached
        now = time.time()
        ttl = RETRY_SECONDS if cached.get("error") else REFRESH_SECONDS
        age = now - cached.get("checkedAt", 0)
        handle = _acquire_lock(path)
        if handle is None:
            return _snapshot(path)
        try:
            cached = _snapshot(path)
            if force and (_REFRESH_GENERATIONS.get(path, 0) != generation or cached.get("checkedAt") != initial.get("checkedAt")):
                return cached
            now = time.time()
            ttl = RETRY_SECONDS if cached.get("error") else REFRESH_SECONDS
            age = now - cached.get("checkedAt", 0)
            fresh = bool(cached) and not force and 0 <= age < ttl
            if fresh:
                missing = _merge_openrouter_missing(cached["providers"], {}, models)
                if not missing or cached.get("error"):
                    return cached
            snapshot = {
                "version": CACHE_VERSION,
                "source": MODELS_DEV_SOURCE_URL,
                "fetchedAt": cached.get("fetchedAt", 0),
                "checkedAt": now,
                "providers": cached.get("providers", {}) if fresh else {},
                "error": "",
            }
            primary_error = ""
            source_loaded = False
            if not fresh:
                try:
                    snapshot["providers"] = _fetch_models_dev()
                    source_loaded = True
                except (OSError, ValueError) as error:
                    primary_error = str(error)
                try:
                    _merge_tables(snapshot["providers"], _fetch_litellm(), {"anthropic", "openai"})
                    source_loaded = True
                except (OSError, ValueError) as error:
                    if primary_error:
                        primary_error += "; " + str(error)
                    else:
                        primary_error = str(error)

            missing = _merge_openrouter_missing(snapshot["providers"], {}, models)
            if missing:
                try:
                    missing = _merge_openrouter_missing(snapshot["providers"], _fetch_openrouter(), models)
                except (OSError, ValueError) as fallback_error:
                    snapshot["error"] = str(fallback_error)
            if missing:
                snapshot["error"] = "OpenRouter pricing fallback unavailable"
            if not fresh:
                _merge_tables(snapshot["providers"], cached.get("providers", {}))
                if not snapshot["error"] and not source_loaded:
                    snapshot["error"] = primary_error or "No usable pricing rates"
            if not snapshot["error"]:
                snapshot["fetchedAt"] = now
            _CURRENT_SNAPSHOTS[path] = snapshot
            _REFRESH_GENERATIONS[path] = _REFRESH_GENERATIONS.get(path, 0) + 1
            _write_cache(path, snapshot)
            return snapshot
        finally:
            _release_lock(path, handle)


def cached_catalog():
    path = os.path.join(config.cache_dir(), CACHE_FILENAME)
    return _snapshot(path).get("providers", {})


def get_pricing(provider, models=None):
    requested = {provider: models} if models is not None else None
    return load_catalog(models=requested)["providers"].get(provider, {})


# Direct vendors first; the catalog is otherwise dominated by resellers whose
# ids sort ahead of them ("302ai" before "anthropic"), which buried the rates
# most people open this table to look up.
_PRIMARY_PROVIDERS = (
    "anthropic",
    "openai",
    "google",
    "google-vertex",
    "xai",
    "mistral",
    "deepseek",
    "moonshotai",
    "zhipuai",
    "meta",
    "openrouter",
)


def _provider_rank(provider):
    try:
        return (0, _PRIMARY_PROVIDERS.index(provider), provider)
    except ValueError:
        return (1, 0, provider)


def provider_for_model(model):
    """Resolve a model ID to a catalog provider without guessing from its name.

    Prefer the catalog's primary provider order when a model ID is shared by
    multiple vendors/resellers. An otherwise unknown model is only resolved if
    exactly one provider has an exact rate entry for it.
    """
    if not isinstance(model, str) or not model:
        return ""
    catalog = cached_catalog()
    matches = [provider for provider, models in catalog.items() if isinstance(models, dict) and model in models]
    primary = [provider for provider in matches if provider in _PRIMARY_PROVIDERS]
    if primary:
        return min(primary, key=_provider_rank)
    return matches[0] if len(matches) == 1 else ""


def catalog_rows(query="", limit=60, offset=0):
    """A searchable, paged view of the cached rate table for the frontends.

    Reads the cache only — never fetches — so opening the table cannot block on
    the network. Rows are sorted by provider then model so paging is stable.
    """
    path = os.path.join(config.cache_dir(), CACHE_FILENAME)
    snapshot = _snapshot(path)
    needle = (query or "").strip().casefold()
    rows = []
    for provider in sorted(snapshot.get("providers") or {}, key=_provider_rank):
        models = snapshot["providers"][provider]
        if not isinstance(models, dict):
            continue
        for model in sorted(models):
            rates = models[model]
            if not isinstance(rates, dict):
                continue
            if needle and needle not in provider.casefold() and needle not in model.casefold():
                continue
            row = {"provider": provider, "model": model}
            for field in ("input", "output", "cached"):
                rate = finite_number(rates.get(field), minimum=0)
                if rate is not None:
                    row[field] = rate
            rows.append(row)
    total = len(rows)
    page_limit = max(1, int(limit or 60))
    page_offset = max(0, int(offset or 0))
    return {
        "schemaVersion": 1,
        "fetchedAt": int(snapshot.get("fetchedAt", 0) or 0),
        "checkedAt": int(snapshot.get("checkedAt", 0) or 0),
        "error": str(snapshot.get("error") or ""),
        "unit": "USD per 1M tokens",
        "rows": rows[page_offset : page_offset + page_limit],
        "total": total,
        "offset": page_offset,
        "limit": page_limit,
        "hasMore": page_offset + page_limit < total,
    }
