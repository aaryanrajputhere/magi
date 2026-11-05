"""One persisted run per provider attempt. No startup calls or automatic retries."""

import copy
import re
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter
from typing import Any, Literal
from uuid import uuid4

from openai import APIError, APITimeoutError, OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.llm.context import RunContext
from app.llm.costs import ModelPricing, estimate_usage
from app.llm.embeddings import EmbeddingRequest, EmbeddingResult, tracked_embed
from app.models import LLMRun


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    model: str
    instructions: str
    input: str | list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    tool_choice: Literal["auto", "required", "none"] | None = None
    max_output_tokens: int = Field(gt=0)
    context: RunContext = Field(default_factory=RunContext)
    timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    prompt_cache_key: str | None = None

    @field_validator("operation", "model")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


@dataclass(frozen=True)
class ModelResult:
    run_id: str
    text: str
    parsed: BaseModel | None
    response: dict


class ModelRunError(RuntimeError):
    def __init__(self, run_id: str, status: str):
        self.run_id, self.status = run_id, status
        super().__init__(f"Model run {run_id} ended with status {status}; inspect its saved record")


class RunLoggingError(RuntimeError):
    def __init__(self, run_id: str, result: dict | None = None):
        self.run_id, self.result = run_id, result
        super().__init__(
            f"Could not persist model run {run_id}. Do not automatically repeat the request."
        )


def strict_output_schema(output_model: type[BaseModel]) -> dict:
    """Require explicitly closed Pydantic object schemas; never alter their meaning."""
    schema = copy.deepcopy(output_model.model_json_schema())

    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and (
                node.get("additionalProperties") is not False
                or set(node.get("required", [])) != set(node.get("properties", {}))
            ):
                raise ValueError(
                    "Structured output objects must forbid extras and require every field; use required nullable fields instead of defaults"
                )
            for key, value in node.items():
                if key not in {"default", "examples"}:
                    check(value)
        elif isinstance(node, list):
            for item in node:
                check(item)

    if schema.get("type") != "object":
        raise ValueError("Structured output must be an object model")
    check(schema)
    return {
        "format": {
            "type": "json_schema",
            "name": output_model.__name__,
            "schema": schema,
            "strict": True,
        }
    }


def redact(value, secrets: list[str]):
    """Only credential material is redacted; prompts otherwise remain exact."""
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
        return re.sub(r"\bBearer\s+[^\s\"']+", "Bearer [REDACTED]", value, flags=re.IGNORECASE)
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key.lower() in {"authorization", "api_key", "api-key", "x-api-key"}
            else redact(item, secrets)
            for key, item in value.items()
        }
    return value


def connection_diagnostics(exc):
    """Bounded exception chain; never inspect request headers or payloads."""
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen and len(chain) < 10:
        seen.add(id(current))
        entry = {'type': type(current).__name__, 'message': str(current)[:2000]}
        for name in ('errno', 'winerror', 'verify_code', 'verify_message'):
            value = getattr(current, name, None)
            if isinstance(value, (str, int)):
                entry[name] = value
        chain.append(entry)
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    request = getattr(exc, 'request', None)
    url = getattr(request, 'url', None)
    endpoint = ({'scheme': url.scheme, 'host': url.host, 'port': url.port}
                if url is not None else None)
    return {'exception_chain': chain, 'root_cause': chain[-1] if chain else None,
            'endpoint': endpoint}


class TrackedOpenAI:
    def __init__(
        self,
        engine: Engine,
        settings: Settings | None = None,
        *,
        sdk: OpenAI | None = None,
        pricing: dict[str, ModelPricing] | None = None,
    ):
        self.engine = engine
        self.settings = settings or get_settings()
        self._sdk = sdk
        self._owns_sdk = sdk is None
        self.pricing = pricing

    @property
    def secrets(self) -> list[str]:
        return [
            secret.get_secret_value()
            for secret in [self.settings.openai_api_key, self.settings.firecrawl_api_key]
            if secret and secret.get_secret_value()
        ]

    def close(self):
        if self._owns_sdk and self._sdk is not None:
            self._sdk.close()
            self._sdk = None

    def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        return tracked_embed(self, request)

    def _start(self, identifier: str, request: ModelRequest | EmbeddingRequest, body: dict) -> None:
        try:
            with Session(self.engine) as session, session.begin():
                session.add(
                    LLMRun(
                        id=identifier,
                        operation=redact(request.operation, self.secrets),
                        requested_model=redact(request.model, self.secrets),
                        context=redact(request.context.model_dump(mode="json"), self.secrets),
                        request_body=redact(body, self.secrets),
                        timeout_seconds=Decimal(str(getattr(request, "timeout_seconds", None) or self.settings.magi_openai_timeout_seconds)),
                    )
                )
        except SQLAlchemyError as exc:
            raise RunLoggingError(identifier) from exc

    def _finish(self, identifier: str, values: dict) -> None:
        try:
            with Session(self.engine) as session, session.begin():
                run = session.get(LLMRun, identifier)
                if run is None:
                    raise RunLoggingError(identifier, redact(values, self.secrets))
                for key, value in values.items():
                    setattr(run, key, redact(value, self.secrets))
        except SQLAlchemyError as exc:
            raise RunLoggingError(identifier, redact(values, self.secrets)) from exc

    def run(
        self, request: ModelRequest, *, output_model: type[BaseModel] | None = None
    ) -> ModelResult:
        body = {
            "model": request.model,
            "instructions": request.instructions,
            "input": request.input,
            "max_output_tokens": request.max_output_tokens,
            "stream": False,
            "store": False,
            "service_tier": "default",
        }
        if request.tools is not None:
            body["tools"] = request.tools
            body["parallel_tool_calls"] = False
            if request.tool_choice is not None:
                body["tool_choice"] = request.tool_choice
        if output_model is not None:
            body["text"] = strict_output_schema(output_model)
        if request.prompt_cache_key is not None:
            body["prompt_cache_key"] = request.prompt_cache_key
        identifier = str(uuid4())
        self._start(identifier, request, body)
        started = perf_counter()
        values: dict[str, Any] = {}
        response_body = None
        parsed = None
        output_text = ""
        key = (
            self.settings.openai_api_key.get_secret_value() if self.settings.openai_api_key else ""
        )
        if not key.strip():
            values = {
                "status": "configuration_error",
                "error": {"type": "missing_api_key", "message": "OPENAI_API_KEY is not configured"},
                "cost_status": "not_sent",
                "cost_reason": "No provider request was sent",
                "estimated_cost_usd": Decimal(0),
            }
        else:
            try:
                if self._sdk is None:
                    self._sdk = OpenAI(
                        api_key=key,
                        timeout=self.settings.magi_openai_timeout_seconds,
                        max_retries=0,
                    )
                # Explicit per-call options prevent injected clients from hiding retries.
                raw_response = self._sdk.with_options(
                    max_retries=0, timeout=request.timeout_seconds or self.settings.magi_openai_timeout_seconds
                ).responses.with_raw_response.create(**body)
                response_body = raw_response.http_response.json()
                usage = response_body.get("usage")
                estimate = estimate_usage(usage, response_body.get("model"), self.pricing)
                if response_body.get("service_tier") not in {None, "default"}:
                    estimate.estimated_cost_usd = None
                    estimate.cost_status = "unknown"
                    estimate.cost_reason = (
                        "Returned service tier is unsupported by standard pricing"
                    )
                values.update(estimate.model_dump())
                values.update(
                    response_body=response_body,
                    response_id=response_body.get("id"),
                    request_id=raw_response.headers.get("x-request-id"),
                    returned_model=response_body.get("model"),
                    raw_usage=usage,
                )
                response = raw_response.parse()
                output_text = (
                    response.output_text if response_body.get("status") == "completed" else ""
                )
                refusal = any(
                    part.get("type") == "refusal"
                    for item in (response_body.get("output") or [])
                    for part in (item.get("content") or [])
                )
                provider_status = response_body.get("status")
                if provider_status == "failed" or response_body.get("error"):
                    values.update(
                        status="provider_error",
                        error=response_body.get("error")
                        or {"message": "Provider returned failed status"},
                    )
                elif refusal:
                    values.update(
                        status="refused",
                        error={"type": "refusal", "message": "Provider refused the request"},
                    )
                elif provider_status != "completed":
                    values.update(
                        status="incomplete",
                        error={
                            "type": "incomplete_response",
                            "details": response_body.get("incomplete_details"),
                        },
                    )
                elif output_model is not None:
                    try:
                        parsed = output_model.model_validate_json(output_text, strict=True)
                        values["status"] = "completed"
                    except ValidationError as exc:
                        values.update(
                            status="validation_error",
                            error={
                                "type": "output_validation",
                                "details": exc.errors(
                                    include_input=False, include_context=False, include_url=False
                                ),
                            },
                        )
                else:
                    values["status"] = "completed"
            except APIError as exc:
                # Provider exception bodies can contain request text. Do not log headers.
                values.update(
                    status="timeout" if isinstance(exc, APITimeoutError) else "provider_error",
                    error={
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "status_code": getattr(exc, "status_code", None),
                        "body": getattr(exc, "body", None),
                        **connection_diagnostics(exc),
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
                if isinstance(error_body, dict) and "usage" in error_body:
                    values.update(
                        estimate_usage(
                            error_body.get("usage"), error_body.get("model"), self.pricing
                        ).model_dump()
                    )
                    if error_body.get("service_tier") not in {None, "default"}:
                        values.update(
                            estimated_cost_usd=None,
                            cost_status="unknown",
                            cost_reason="Returned service tier is unsupported by standard pricing",
                        )
                    values.update(
                        response_body=error_body,
                        raw_usage=error_body.get("usage"),
                        returned_model=error_body.get("model"),
                        response_id=error_body.get("id"),
                    )
            except Exception as exc:  # noqa: BLE001 — preserve audit state for unexpected client failures
                values.update(
                    status="client_error", error={"type": type(exc).__name__, "message": str(exc)}
                )
                values.setdefault("cost_status", "unknown")
                values.setdefault("cost_reason", "Client failed; provider billing is unknown")
        values["duration_ms"] = round((perf_counter() - started) * 1000)
        self._finish(identifier, values)
        if values["status"] != "completed":
            raise ModelRunError(identifier, values["status"])
        return ModelResult(
            run_id=identifier,
            text=output_text,
            parsed=parsed,
            response=redact(response_body, self.secrets),
        )
