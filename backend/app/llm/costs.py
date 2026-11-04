"""Decimal estimates from provider usage and explicit model pricing snapshots."""

from decimal import Decimal

from pydantic import BaseModel


class ModelPricing(BaseModel):
    version: str
    source: str
    verified_on: str
    input_per_million: Decimal
    cached_input_per_million: Decimal
    output_per_million: Decimal
    # None means no supported separately priced cache-write billing rule.
    cache_write_per_million: Decimal | None = None


LEGACY_MINI = ModelPricing(
    version="gpt-4.1-mini-text-standard-2026-11-01",
    source="https://developers.openai.com/api/docs/models/gpt-4.1-mini",
    verified_on="2026-11-01",
    input_per_million=Decimal("0.40"),
    cached_input_per_million=Decimal("0.10"),
    output_per_million=Decimal("1.60"),
)
PRICING = {"gpt-4.1-mini": LEGACY_MINI, "gpt-4.1-mini-2025-04-14": LEGACY_MINI}


class UsageEstimate(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    estimated_cost_usd: Decimal | None = None
    cache_read_ratio: Decimal | None = None
    pricing_snapshot: dict | None = None
    cost_status: str = "unknown"
    cost_reason: str | None = None


def count(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def estimate_usage(
    usage: dict | None, model: str | None, registry: dict[str, ModelPricing] | None = None
) -> UsageEstimate:
    pricing = (PRICING if registry is None else registry).get(model)
    estimate = UsageEstimate(pricing_snapshot=pricing.model_dump(mode="json") if pricing else None)
    if not isinstance(usage, dict):
        estimate.cost_reason = "Usage not reported; provider billing is unknown"
        return estimate
    inputs = usage.get("input_tokens_details") or {}
    outputs = usage.get("output_tokens_details") or {}
    if not isinstance(inputs, dict) or not isinstance(outputs, dict):
        estimate.cost_reason = "Invalid usage details"
        return estimate
    estimate.input_tokens = count(usage.get("input_tokens"))
    estimate.output_tokens = count(usage.get("output_tokens"))
    estimate.cached_input_tokens = count(inputs.get("cached_tokens"))
    estimate.cache_write_tokens = count(inputs.get("cache_write_tokens"))
    estimate.reasoning_tokens = count(outputs.get("reasoning_tokens"))
    for values in [usage, inputs, outputs]:
        keys = {
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "cached_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        }
        if any(
            key in keys and value is not None and count(value) is None
            for key, value in values.items()
        ):
            estimate.cost_reason = "Invalid token count"
            return estimate
    incoming, outgoing, cached = (
        estimate.input_tokens,
        estimate.output_tokens,
        estimate.cached_input_tokens,
    )
    writes = estimate.cache_write_tokens
    if incoming is None or outgoing is None or cached is None:
        estimate.cost_reason = "Required usage counts not reported"
        return estimate
    if cached + (writes or 0) > incoming or (estimate.reasoning_tokens or 0) > outgoing:
        estimate.cost_reason = "Inconsistent token breakdown"
        return estimate
    if usage.get("total_tokens") is not None and usage["total_tokens"] != incoming + outgoing:
        estimate.cost_reason = "Inconsistent total tokens"
        return estimate
    estimate.cache_read_ratio = Decimal(cached) / incoming if incoming else Decimal(0)
    if pricing is None:
        estimate.cost_reason = "No verified pricing for returned model"
        return estimate
    if any(
        rate < 0
        for rate in [
            pricing.input_per_million,
            pricing.cached_input_per_million,
            pricing.output_per_million,
        ]
    ):
        estimate.cost_reason = "Invalid pricing rates"
        return estimate
    if pricing.cache_write_per_million is None:
        if writes:
            estimate.cost_reason = "Cache-write billing is unsupported by this pricing snapshot"
            return estimate
        writes = 0  # Legacy snapshot has no separate cache-write charge.
    elif writes is None:
        estimate.cost_reason = "Cache-write count not reported for separate-write pricing"
        return estimate
    elif pricing.cache_write_per_million < 0:
        estimate.cost_reason = "Invalid cache-write rate"
        return estimate
    estimate.estimated_cost_usd = (
        Decimal(incoming - cached - writes) * pricing.input_per_million
        + Decimal(cached) * pricing.cached_input_per_million
        + Decimal(writes) * (pricing.cache_write_per_million or 0)
        + Decimal(outgoing) * pricing.output_per_million
    ) / Decimal(1_000_000)
    estimate.cost_status = "estimated"
    return estimate


EMBEDDING_SMALL = ModelPricing(
    version="text-embedding-3-small-standard-2026-11-02",
    source="https://developers.openai.com/api/docs/models/text-embedding-3-small",
    verified_on="2026-11-02",
    input_per_million=Decimal("0.02"),
    cached_input_per_million=Decimal(0),
    output_per_million=Decimal(0),
)
EMBEDDING_PRICING = {"text-embedding-3-small": EMBEDDING_SMALL}


def estimate_embedding_usage(
    usage: dict | None, model: str | None, registry: dict[str, ModelPricing] | None = None
) -> UsageEstimate:
    pricing = (EMBEDDING_PRICING if registry is None else registry).get(model)
    estimate = UsageEstimate(pricing_snapshot=pricing.model_dump(mode="json") if pricing else None)
    if not isinstance(usage, dict) or count(usage.get("prompt_tokens")) is None:
        estimate.cost_reason = (
            "Embedding input usage not reported or invalid; provider billing is unknown"
        )
        return estimate
    estimate.input_tokens = usage["prompt_tokens"]
    estimate.output_tokens = 0  # Embeddings have no billed output tokens.
    if "total_tokens" in usage and (
        count(usage["total_tokens"]) is None or usage["total_tokens"] != estimate.input_tokens
    ):
        estimate.cost_reason = "Inconsistent embedding total tokens"
        return estimate
    if pricing is None or pricing.input_per_million < 0:
        estimate.cost_reason = "No verified embedding pricing for returned model"
        return estimate
    estimate.estimated_cost_usd = (
        Decimal(estimate.input_tokens) * pricing.input_per_million / Decimal(1_000_000)
    )
    estimate.cost_status = "estimated"
    return estimate
