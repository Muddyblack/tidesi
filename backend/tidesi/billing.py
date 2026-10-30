"""Organization billing aggregation using rates supplied by collectors."""

from __future__ import annotations

import math
from typing import NamedTuple

from .contract import finite_number, num


class UsageBucket(NamedTuple):
    """One provider-reported usage record before session aggregation."""

    provider: str
    model: str
    session_id: str
    input_tokens: int | float = 0
    output_tokens: int | float = 0
    cache_read_tokens: int | float = 0
    cache_write_tokens: int | float = 0
    reasoning_tokens: int | float = 0
    provider_cost_usd: int | float | None = None
    source: str = ""


def _token_count(value):
    try:
        value = num(value)
        return max(0, math.floor(value)) if math.isfinite(value) else 0
    except (OverflowError, TypeError, ValueError):
        return 0


def _bucket_tokens(bucket):
    return {
        "input": _token_count(bucket.input_tokens),
        "output": _token_count(bucket.output_tokens),
        "cache_read": _token_count(bucket.cache_read_tokens),
        "cache_write": _token_count(bucket.cache_write_tokens),
        "reasoning": _token_count(bucket.reasoning_tokens),
    }


def total_tokens(buckets):
    """Sum of input/output/cache tokens across a set of usage buckets."""
    return sum(
        sum(_bucket_tokens(bucket)[key] for key in ("input", "output", "cache_read", "cache_write"))
        + (_token_count(bucket.reasoning_tokens) if bucket.source == "antigravity" else 0)
        for bucket in buckets
    )


def _bucket_cost(bucket, price):
    if not isinstance(price, dict) or "input" not in price or "output" not in price:
        return None
    input_rate = finite_number(price["input"], minimum=0)
    output_rate = finite_number(price["output"], minimum=0)
    if input_rate is None or output_rate is None:
        return None
    cached_value = price.get("cached", input_rate)
    cached_rate = input_rate if cached_value is None else finite_number(cached_value, minimum=0)
    if cached_rate is None:
        return None
    tokens = _bucket_tokens(bucket)
    # Providers disagree on whether cache reads are counted inside the input
    # total (OpenAI) or reported alongside it (Anthropic). Bill every cache
    # read at the cached rate either way, and only subtract the part that was
    # actually inside input, so neither convention drops tokens or leaves a
    # negative full-rate remainder. Clamping the billed amount to input, as
    # this once did, made a cache-heavy Claude session priced at a fraction
    # of its real cost — the bulk of its tokens vanished from the bill.
    cached = tokens["cache_read"]
    # Antigravity's generation ledger records new input separately from its cached prefix.
    inside_input = 0 if bucket.source == "antigravity" else min(cached, tokens["input"])
    uncached = tokens["input"] - inside_input + tokens["cache_write"]
    output = tokens["output"] + tokens["reasoning"]
    cost = (uncached / 1_000_000) * input_rate
    cost += (cached / 1_000_000) * cached_rate
    cost += (output / 1_000_000) * output_rate
    return cost if math.isfinite(cost) else None


def _provider_cost(bucket, token_total):
    cost = bucket.provider_cost_usd
    if type(cost) not in (int, float):
        return None
    try:
        cost = float(cost)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(cost) or cost < 0:
        return None
    return cost if cost > 0 or token_total == 0 else None


def aggregate_session_usage(buckets, pricing):
    """Price normalized buckets with independent coverage and provenance."""
    costs = []
    actual_costs = []
    estimated_costs = []
    actual = 0
    estimated = 0
    priced = 0
    unpriced = 0
    for bucket in buckets:
        tokens = _bucket_tokens(bucket)
        token_total = sum(tokens.values())
        provider_cost = _provider_cost(bucket, token_total)
        if token_total <= 0 and provider_cost is None:
            continue
        provider_rates = pricing.get(bucket.provider) if isinstance(pricing, dict) else {}
        provider_rates = provider_rates if isinstance(provider_rates, dict) else {}
        price = provider_rates.get(bucket.model) if bucket.model else None
        if provider_cost is not None:
            costs.append(provider_cost)
            actual_costs.append(provider_cost)
            actual += 1
            priced += 1
        elif token_total > 0 and price is not None:
            estimate = _bucket_cost(bucket, price)
            if estimate is not None:
                costs.append(estimate)
                estimated_costs.append(estimate)
                estimated += 1
                priced += 1
            else:
                unpriced += 1
        else:
            unpriced += 1
    if priced == 0:
        return {"costStatus": "unavailable"}
    total_cost = math.fsum(costs)
    actual_cost = math.fsum(actual_costs)
    estimated_cost = math.fsum(estimated_costs)
    if not all(math.isfinite(cost) for cost in (total_cost, actual_cost, estimated_cost)):
        return {"costStatus": "unavailable"}
    provenance = "mixed" if actual and estimated else "actual" if actual else "estimated"
    result = {
        "costUSD": total_cost,
        "costStatus": "partial" if unpriced else "exact",
        "costProvenance": provenance,
    }
    if provenance == "mixed":
        result["costBreakdown"] = {"actualUSD": actual_cost, "estimatedUSD": estimated_cost}
    return result


def price_models(entries, pricing):
    """Group token entries by model and price them against `pricing`
    ({model: {input, output, optional cached}}, USD per million tokens).

    A model missing from `pricing` still shows up, just unpriced. A pricing row
    carrying `cached` bills `cached_tokens` at that lower rate and the rest of
    the prompt at the input rate. An entry without `cached_tokens` bills its
    entire prompt at the input rate, even if the catalog has a cache rate."""
    models = {}
    total_in = 0
    total_out = 0
    for entry in entries:
        name = entry.get("model") or "unknown"
        in_ = _token_count(entry.get("input_tokens"))
        out_ = _token_count(entry.get("output_tokens"))
        cached = _token_count(entry.get("cached_tokens"))
        price = pricing.get(name)
        m = models.setdefault(name, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "priced": False})
        m["input_tokens"] += in_
        m["output_tokens"] += out_
        if price is not None:
            # Bill every cached token at the cached rate, subtracting only the
            # part that was inside the input total — providers differ on
            # whether cached tokens are counted inside input or alongside it,
            # and clamping the billed amount to input dropped the difference.
            billable_cached = cached if price.get("cached") is not None else 0
            inside_input = min(billable_cached, in_)
            m["cost_usd"] += ((in_ - inside_input) / 1000000) * num(price["input"]) + (out_ / 1000000) * num(price["output"])
            if billable_cached:
                m["cost_usd"] += (billable_cached / 1000000) * num(price["cached"])
            m["priced"] = True
        total_in += in_
        total_out += out_
    total_cost = sum(m["cost_usd"] for m in models.values())
    return {
        "models": models,
        "totalInputTokens": total_in,
        "totalOutputTokens": total_out,
        "totalCostUSD": total_cost,
    }


def empty_org_usage():
    return {"models": {}, "totalInputTokens": 0, "totalOutputTokens": 0, "totalCostUSD": 0}
