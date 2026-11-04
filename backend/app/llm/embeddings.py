"""Audited embedding calls, kept separate from text response parsing."""

import math
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import uuid4

from openai import APIError, APITimeoutError, OpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.llm.context import RunContext
from app.llm.costs import estimate_embedding_usage

if TYPE_CHECKING:
    from app.llm.client import TrackedOpenAI


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str = "embed_chunks"
    model: str = "text-embedding-3-small"
    dimensions: int = Field(default=1536, ge=1)
    input: list[str] = Field(min_length=1, max_length=32)
    context: RunContext = Field(default_factory=RunContext)

    @field_validator("input")
    @classmethod
    def nonempty(cls, inputs: list[str]):
        if any(not value.strip() for value in inputs):
            raise ValueError("Embedding inputs must not be blank")
        return inputs


def validate_vectors(body: dict, model: str, dimensions: int, size: int) -> list[list[float]]:
    if not isinstance(body, dict) or body.get("model") != model:
        raise ValueError("Embedding response model does not match the request")
    data = body.get("data")
    if not isinstance(data, list) or len(data) != size:
        raise ValueError("Embedding response count does not match the request")
    vectors = {}
    for item in data:
        if not isinstance(item, dict):
            raise TypeError("Invalid embedding response item")
        index, vector = item.get("index"), item.get("embedding")
        if type(index) is not int or not 0 <= index < size or index in vectors:
            raise ValueError("Embedding indexes must cover each requested input exactly once")
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise ValueError("Embedding dimensions do not match the request")
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
            raise ValueError("Embedding contains invalid or non-finite values")
        norm = math.hypot(*vector)
        if not math.isfinite(norm) or norm == 0:
            raise ValueError("Embedding must have finite, nonzero magnitude")
        vectors[index] = vector
    return [vectors[index] for index in range(size)]


@dataclass(frozen=True)
class EmbeddingResult:
    run_id: str
    vectors: list[list[float]]
    response: dict


def tracked_embed(client: "TrackedOpenAI", request: EmbeddingRequest) -> EmbeddingResult:
    from app.llm.client import ModelRunError, redact

    body = {
        "model": request.model,
        "input": request.input,
        "dimensions": request.dimensions,
        "encoding_format": "float",
    }
    identifier = str(uuid4())
    client._start(identifier, request, body)
    started = perf_counter()
    values = {}
    vectors = []
    response_body = None
    key = (
        client.settings.openai_api_key.get_secret_value() if client.settings.openai_api_key else ""
    )
    if not key.strip():
        values.update(
            status="configuration_error",
            error={"type": "missing_api_key", "message": "OPENAI_API_KEY is not configured"},
            cost_status="not_sent",
            cost_reason="No provider request was sent",
            estimated_cost_usd=Decimal(0),
        )
    else:
        try:
            if client._sdk is None:
                client._sdk = OpenAI(
                    api_key=key, timeout=client.settings.magi_openai_timeout_seconds, max_retries=0
                )
            raw = client._sdk.with_options(
                max_retries=0, timeout=client.settings.magi_openai_timeout_seconds
            ).embeddings.with_raw_response.create(**body)
            response_body = raw.http_response.json()
            if not isinstance(response_body, dict):
                raise TypeError("Invalid embedding response body")
            values.update(
                response_body=response_body,
                returned_model=response_body.get("model"),
                request_id=raw.headers.get("x-request-id"),
                raw_usage=response_body.get("usage"),
            )
            values.update(
                estimate_embedding_usage(
                    response_body.get("usage"), response_body.get("model"), client.pricing
                ).model_dump()
            )
            try:
                vectors = validate_vectors(
                    response_body, request.model, request.dimensions, len(request.input)
                )
                values["status"] = "completed"
            except (TypeError, ValueError, OverflowError) as exc:
                values.update(
                    status="validation_error",
                    error={"type": "embedding_validation", "message": str(exc)},
                )
        except APIError as exc:
            values.update(
                status="timeout" if isinstance(exc, APITimeoutError) else "provider_error",
                error={
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "status_code": getattr(exc, "status_code", None),
                },
                request_id=getattr(exc, "request_id", None),
                cost_status="unknown",
                cost_reason="No usable usage response; provider billing is unknown",
            )
            error_body = getattr(exc, "body", None)
            http_response = getattr(exc, "response", None)
            if http_response is not None:
                try:
                    error_body = http_response.json()
                except ValueError:
                    pass
            if isinstance(error_body, dict):
                values["response_body"] = error_body
                if "usage" in error_body:
                    values.update(
                        estimate_embedding_usage(
                            error_body.get("usage"), error_body.get("model"), client.pricing
                        ).model_dump()
                    )
                    values.update(
                        raw_usage=error_body.get("usage"), returned_model=error_body.get("model")
                    )
        except Exception as exc:  # noqa: BLE001 — retain the audit record after client failures
            values.update(
                status="client_error", error={"type": type(exc).__name__, "message": str(exc)}
            )
            values.setdefault("cost_status", "unknown")
            values.setdefault("cost_reason", "Client failed; provider billing is unknown")
    values["duration_ms"] = round((perf_counter() - started) * 1000)
    client._finish(identifier, values)
    if values["status"] != "completed":
        raise ModelRunError(identifier, values["status"])
    return EmbeddingResult(identifier, vectors, redact(response_body, client.secrets))
