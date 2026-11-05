import copy
import json
from decimal import Decimal

import httpx
import pytest
from openai import OpenAI
from pydantic import BaseModel, ConfigDict
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.llm.client import ModelRequest, ModelRunError, RunLoggingError, TrackedOpenAI
from app.llm.costs import LEGACY_MINI, estimate_usage
from app.models import LLMRun

KEY = "sk-test-never-a-real-credential"


def usage():
    return {
        "input_tokens": 1000,
        "output_tokens": 100,
        "total_tokens": 1100,
        "input_tokens_details": {"cached_tokens": 200},
        "output_tokens_details": {"reasoning_tokens": 50},
    }


def response_body(text="ready"):
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 1,
        "model": "gpt-4.1-mini-2025-04-14",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "metadata": {},
        "output": [
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "parallel_tool_calls": False,
        "tools": [],
        "tool_choice": "auto",
        "usage": usage(),
        "service_tier": "default",
    }


@pytest.fixture
def engine(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'magi.db'}")
    initialize_database(engine)
    yield engine
    engine.dispose()


def request(**overrides):
    return ModelRequest.model_validate(
        {
            "operation": "test",
            "model": "gpt-4.1-mini",
            "instructions": "Be brief.\nKeep whitespace.",
            "input": "A sample request",
            "max_output_tokens": 200,
            "context": {
                "brand_id": "brand-test",
                "document_id": "doc-test",
                "source_references": [{"chunk_id": "chunk-test", "source_hash": "abc"}],
            },
            **overrides,
        }
    )


def setup_client(engine, body=None, handler=None, **kwargs):
    calls = []

    def transport(req):
        calls.append(req)
        if handler:
            return handler(req)
        return httpx.Response(
            200,
            json=body if body is not None else response_body(),
            headers={"x-request-id": "req_test"},
        )

    sdk = OpenAI(
        api_key=KEY,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
        max_retries=2,
    )
    tracked = TrackedOpenAI(engine, Settings(_env_file=None, openai_api_key=KEY), sdk=sdk, **kwargs)
    return tracked, sdk, calls


def saved(engine):
    with Session(engine) as session:
        return session.scalar(select(LLMRun).order_by(LLMRun.started_at.desc()))


def test_text_call_records_request_usage_and_pricing(engine):
    tracked, sdk, calls = setup_client(engine)
    try:
        result = tracked.run(request())
        record = saved(engine)
        assert record.id == result.run_id and record.status == "completed"
        assert result.text == "ready" and result.parsed is None
        assert len(calls) == 1
        assert record.request_body == json.loads(calls[0].content)
        assert record.response_body == response_body()
        assert record.context["source_references"][0]["chunk_id"] == "chunk-test"
        assert record.request_body["instructions"] == "Be brief.\nKeep whitespace."
        assert record.request_body["store"] is False and record.request_body["stream"] is False
        assert record.request_id == "req_test" and record.response_id == "resp_test"
        assert record.returned_model == "gpt-4.1-mini-2025-04-14"
        assert record.input_tokens == 1000 and record.output_tokens == 100
        assert record.cached_input_tokens == 200 and record.reasoning_tokens == 50
        assert record.cache_write_tokens is None
        assert record.estimated_cost_usd == Decimal("0.0005")
        assert record.cache_read_ratio == Decimal("0.2")
        assert record.pricing_snapshot["input_per_million"] == "0.40"
        assert record.duration_ms >= 0 and record.timeout_seconds == 60
        assert KEY not in json.dumps(record.request_body)
        assert "authorization" not in json.dumps(record.request_body).lower()
    finally:
        sdk.close()


class ExampleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str


def test_structured_output_and_exact_schema_logging(engine):
    tracked, sdk, calls = setup_client(engine, response_body('{"answer":"ready"}'))
    try:
        result = tracked.run(request(), output_model=ExampleOutput)
        assert result.parsed.answer == "ready"
        schema = saved(engine).request_body["text"]["format"]
        assert schema["strict"] is True
        assert schema["schema"]["additionalProperties"] is False
        assert schema["schema"]["required"] == ["answer"]
        assert saved(engine).request_body == json.loads(calls[0].content)
    finally:
        sdk.close()


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("validation", "validation_error"),
        ("refusal", "refused"),
        ("incomplete", "incomplete"),
        ("failed", "provider_error"),
    ],
)
def test_unusable_responses_keep_usage_and_cost(engine, kind, expected):
    body = response_body('{"answer":5}')
    if kind == "refusal":
        body["output"][0]["content"] = [{"type": "refusal", "refusal": "Cannot comply"}]
    elif kind == "incomplete":
        body["status"] = "incomplete"
        body["incomplete_details"] = {"reason": "max_output_tokens"}
    elif kind == "failed":
        body["status"] = "failed"
        body["error"] = {"code": "server_error", "message": "Failed generation"}
    tracked, sdk, calls = setup_client(engine, body)
    try:
        with pytest.raises(ModelRunError) as caught:
            tracked.run(request(), output_model=ExampleOutput)
        assert caught.value.status == expected
        record = saved(engine)
        assert record.status == expected and record.error
        assert record.input_tokens == 1000 and record.estimated_cost_usd == Decimal("0.0005")
        assert record.response_body["id"] == "resp_test" and len(calls) == 1
    finally:
        sdk.close()


@pytest.mark.parametrize("code", [400, 429, 500])
def test_http_failures_do_not_retry_and_have_unknown_billing(engine, code):
    def handler(req):
        return httpx.Response(
            code,
            json={"error": {"message": f"Failure {KEY}", "type": "test_error"}},
            headers={"x-request-id": "req_failed"},
        )

    tracked, sdk, calls = setup_client(engine, handler=handler)
    try:
        with pytest.raises(ModelRunError):
            tracked.run(request())
        assert len(calls) == 1
        record = saved(engine)
        assert record.status == "provider_error" and record.request_id == "req_failed"
        assert record.estimated_cost_usd is None and record.raw_usage is None
        assert KEY not in json.dumps(record.error)
    finally:
        sdk.close()


def test_timeout_is_logged_once(engine):
    def handler(req):
        raise httpx.ReadTimeout("read timed out", request=req)

    tracked, sdk, calls = setup_client(engine, handler=handler)
    try:
        with pytest.raises(ModelRunError) as caught:
            tracked.run(request())
        assert caught.value.status == "timeout"
        assert len(calls) == 1 and saved(engine).estimated_cost_usd is None
    finally:
        sdk.close()


def test_missing_key_does_not_construct_a_client(engine, monkeypatch):
    def forbidden(**kwargs):
        raise AssertionError("SDK must remain lazy")

    monkeypatch.setattr("app.llm.client.OpenAI", forbidden)
    tracked = TrackedOpenAI(engine, Settings(_env_file=None, openai_api_key=None))
    with pytest.raises(ModelRunError) as caught:
        tracked.run(request())
    assert caught.value.status == "configuration_error"
    assert saved(engine).cost_status == "not_sent"
    assert saved(engine).estimated_cost_usd == 0 and saved(engine).input_tokens is None


@pytest.mark.parametrize("phase", ["before", "after"])
def test_database_failures_never_trigger_another_call(engine, phase):
    tracked, sdk, calls = setup_client(engine)

    def reject(_conn, _cursor, statement, params, _context, _many):
        verb = "INSERT INTO llm_runs" if phase == "before" else "UPDATE llm_runs"
        if statement.startswith(verb):
            raise OperationalError(statement, params, Exception("simulated database failure"))

    event.listen(engine, "before_cursor_execute", reject)
    try:
        with pytest.raises(RunLoggingError) as caught:
            tracked.run(request())
        assert len(calls) == (0 if phase == "before" else 1)
        assert caught.value.run_id
        if phase == "after":
            assert caught.value.result["response_body"]["id"] == "resp_test"
            assert saved(engine).status == "started"
    finally:
        event.remove(engine, "before_cursor_execute", reject)
        sdk.close()


def test_credentials_are_removed_from_echoed_content_and_context(engine):
    tracked, sdk, _ = setup_client(engine, response_body(KEY))
    try:
        tracked.run(
            request(
                input=f"A credential {KEY}",
                context={"source_references": [{"api_key": KEY, "note": f"Bearer {KEY}"}]},
            )
        )
        record = saved(engine)
        combined = json.dumps(
            {
                "request": record.request_body,
                "response": record.response_body,
                "context": record.context,
            }
        )
        assert KEY not in combined
        assert "[REDACTED]" in combined
    finally:
        sdk.close()


def test_defaulted_output_schema_rejected_before_network(engine):
    class BadOutput(BaseModel):
        answer: str = "default"

    tracked, sdk, calls = setup_client(engine)
    try:
        with pytest.raises(ValueError, match="forbid extras"):
            tracked.run(request(), output_model=BadOutput)
        assert not calls
        assert saved(engine) is None
    finally:
        sdk.close()


def test_usage_missing_or_unknown_returned_model_stays_unknown(engine):
    for field, value in [
        ("usage", None),
        ("model", "unpriced-model"),
        ("service_tier", "priority"),
    ]:
        body = response_body()
        body[field] = value
        tracked, sdk, _ = setup_client(engine, body)
        try:
            result = tracked.run(request())
            assert result.text == "ready"
            assert saved(engine).estimated_cost_usd is None
            assert saved(engine).cost_status == "unknown"
        finally:
            sdk.close()


def test_cache_rates_and_reasoning_not_double_counted():
    assert estimate_usage(usage(), "gpt-4.1-mini").estimated_cost_usd == Decimal("0.0005")
    data = usage()
    data["input_tokens_details"]["cached_tokens"] = 0
    assert estimate_usage(data, "gpt-4.1-mini").estimated_cost_usd == Decimal("0.00056")
    data["input_tokens_details"] = {"cached_tokens": 200, "cache_write_tokens": 100}
    pricing = LEGACY_MINI.model_copy(update={"cache_write_per_million": Decimal("0.50")})
    assert estimate_usage(data, "new", {"new": pricing}).estimated_cost_usd == Decimal("0.00051")
    assert estimate_usage(data, "gpt-4.1-mini").estimated_cost_usd is None
    data["input_tokens_details"].pop("cache_write_tokens")
    assert estimate_usage(data, "new", {"new": pricing}).estimated_cost_usd is None


@pytest.mark.parametrize(
    "alter",
    [
        {"input_tokens": -1},
        {"input_tokens": True},
        {"output_tokens": "100"},
        {"total_tokens": 1},
        {"input_tokens_details": {"cached_tokens": 1100}},
        {"input_tokens_details": {"cached_tokens": 900, "cache_write_tokens": 200}},
        {"output_tokens_details": {"reasoning_tokens": 101}},
        {"input_tokens_details": {}},
        {"input_tokens_details": ["invalid"]},
    ],
)
def test_bad_usage_is_not_priced(alter):
    data = copy.deepcopy(usage())
    data.update(alter)
    assert estimate_usage(data, "gpt-4.1-mini").estimated_cost_usd is None


def test_zero_input_ratio_and_cost():
    result = estimate_usage(
        {"input_tokens": 0, "output_tokens": 0, "input_tokens_details": {"cached_tokens": 0}},
        "gpt-4.1-mini",
    )
    assert result.estimated_cost_usd == 0 and result.cache_read_ratio == 0


def test_cli_lists_and_inspects_without_network(engine, monkeypatch, capsys):
    from app.llm.cli import main

    tracked, sdk, _ = setup_client(engine)
    try:
        run = tracked.run(request())
    finally:
        sdk.close()
    settings = Settings(_env_file=None, magi_database_url=str(engine.url), openai_api_key=None)
    monkeypatch.setattr("app.llm.cli.get_settings", lambda: settings)
    monkeypatch.setattr("sys.argv", ["cli", "list"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == run.run_id
    monkeypatch.setattr("sys.argv", ["cli", "show", run.run_id])
    assert main() == 0
    output = capsys.readouterr().out
    assert "request_body" in output and "pricing_snapshot" in output and KEY not in output
    assert json.loads(output)["pricing_calculation"]["ordinary_input_tokens"] == 800
    monkeypatch.setattr("sys.argv", ["cli", "show", "missing"])
    assert main() == 1
    assert "Unknown run" in capsys.readouterr().err


def test_interruption_leaves_started_run_without_automatic_resume(engine):
    def handler(_req):
        raise KeyboardInterrupt

    tracked, sdk, calls = setup_client(engine, handler=handler)
    try:
        with pytest.raises(KeyboardInterrupt):
            tracked.run(request())
        assert len(calls) == 1
        assert saved(engine).status == "started"
        assert saved(engine).estimated_cost_usd is None
    finally:
        sdk.close()


def test_provider_error_body_usage_is_preserved(engine):
    body = response_body()
    body["error"] = {"message": "Unavailable", "type": "server_error"}

    def handler(_req):
        return httpx.Response(500, json=body)

    tracked, sdk, calls = setup_client(engine, handler=handler)
    try:
        with pytest.raises(ModelRunError):
            tracked.run(request())
        assert len(calls) == 1
        record = saved(engine)
        assert record.raw_usage == usage()
        assert record.estimated_cost_usd == Decimal("0.0005")
    finally:
        sdk.close()


def test_app_lifespan_does_not_construct_sdk_without_key(engine, monkeypatch):
    import asyncio

    from app.main import create_app

    def forbidden(**kwargs):
        raise AssertionError("Unexpected SDK construction")

    monkeypatch.setattr("app.llm.client.OpenAI", forbidden)
    app = create_app(
        Settings(_env_file=None, magi_database_url=str(engine.url), openai_api_key=None)
    )

    async def lifespan():
        async with app.router.lifespan_context(app):
            assert app.state.sessions is not None

    asyncio.run(lifespan())


@pytest.mark.parametrize("choice", [None, "required"])
def test_tool_choice_is_sent_and_logged(engine, choice):
    from app.campaign_agent import TOOLS

    tracked, _sdk, calls = setup_client(engine)
    try:
        tracked.run(request(tools=TOOLS, tool_choice=choice))
        body = json.loads(calls[0].content)
        assert body["parallel_tool_calls"] is False
        if choice is None:
            assert "tool_choice" not in body
        else:
            assert body["tool_choice"] == "required"
        assert saved(engine).request_body == body
    finally:
        tracked.close()


def test_per_request_timeout_is_applied_and_logged(engine):
    tracked, sdk, calls = setup_client(engine)
    try:
        tracked.run(request(timeout_seconds=300))
        assert calls[0].extensions['timeout']['read'] == 300
        assert saved(engine).timeout_seconds == 300
        assert 'timeout_seconds' not in saved(engine).request_body
    finally:
        sdk.close()


def test_connection_failure_logs_underlying_cause_without_credentials(engine):
    def handler(req):
        try:
            raise OSError(111, 'Connection refused ' + KEY)
        except OSError as cause:
            raise httpx.ConnectError('transport failed', request=req) from cause

    tracked, sdk, calls = setup_client(engine, handler=handler)
    try:
        with pytest.raises(ModelRunError):
            tracked.run(request())
        error = saved(engine).error
        assert [e['type'] for e in error['exception_chain']] == ['APIConnectionError', 'ConnectError', 'ConnectionRefusedError']
        assert error['root_cause']['errno'] == 111
        assert KEY not in json.dumps(error)
        assert error['endpoint']['host'] == 'api.openai.com'
        assert len(calls) == 1
    finally:
        sdk.close()
