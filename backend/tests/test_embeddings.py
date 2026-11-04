import copy
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI
from sqlalchemy import delete, event, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.brand import embeddings
from app.brand.chunking import build_chunks
from app.brand.discovery import discover_folder
from app.brand.ingestion import ingest_documents
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.llm.cli import run_record
from app.llm.client import RunLoggingError, TrackedOpenAI
from app.llm.costs import estimate_embedding_usage
from app.llm.embeddings import EmbeddingRequest, validate_vectors
from app.models import Brand, ChunkEmbedding, Document, DocumentChunk, LLMRun

KEY = "sk-testing-not-a-real-key"


@pytest.fixture
def corpus(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialize_database(engine)
    root = tmp_path / "source"
    (root / "brand").mkdir(parents=True)
    for number in range(3):
        (root / "brand" / f"{number}.md").write_text(
            f"# Product {number}\n\nApproved description {number}.\n"
        )
    brand = ingest_documents(engine, discover_folder(root), "Example").brand_id
    build_chunks(engine, brand)
    yield engine, brand, root
    engine.dispose()


def payload(body):
    return {
        "object": "list",
        "model": body["model"],
        # Reverse response ordering to ensure index-based mapping.
        "data": [
            {
                "object": "embedding",
                "index": index,
                "embedding": [float(index + 1)] + [0.0] * (body["dimensions"] - 1),
            }
            for index in reversed(range(len(body["input"])))
        ],
        "usage": {"prompt_tokens": 123, "total_tokens": 123},
    }


@pytest.fixture
def clients(corpus):
    engine, _, _ = corpus
    sdks = []

    def create(handler=None, key=KEY):
        calls = []

        def transport(request):
            body = json.loads(request.content)
            calls.append(body)
            if handler:
                return handler(request, body)
            return httpx.Response(
                200, json=payload(body), headers={"x-request-id": "req_embedding"}
            )

        sdk = OpenAI(
            api_key=KEY,
            max_retries=3,
            http_client=httpx.Client(transport=httpx.MockTransport(transport)),
        )
        sdks.append(sdk)
        return TrackedOpenAI(engine, Settings(_env_file=None, openai_api_key=key), sdk=sdk), calls

    yield create
    for sdk in sdks:
        sdk.close()


def prepare(corpus, client=None, **kwargs):
    return embeddings.prepare_embeddings(
        corpus[0], corpus[1], client=client, token_counter=lambda _: 5, **kwargs
    )


def records(engine, model):
    with Session(engine) as session:
        return list(session.scalars(select(model)))


def test_first_run_maps_vectors_and_unchanged_run_is_free(corpus, clients):
    engine, _, _ = corpus
    client, calls = clients()
    result = prepare(corpus, client)
    assert result["status"] == "success"
    assert result["generated"] == 3 and result["pending"] == 0
    assert result["input_tokens"] == 123
    assert Decimal(result["estimated_cost_usd"]) == Decimal(".00000246")
    assert len(calls) == 1
    rows = records(engine, ChunkEmbedding)
    assert len(rows) == 3
    assert [row.vector[0] for row in rows] == [1.0, 2.0, 3.0]
    run = records(engine, LLMRun)[0]
    assert run.request_body == calls[0]
    assert run.request_id == "req_embedding"
    assert run.operation == "embed_chunks" and run.status == "completed"
    assert run.raw_usage == {"prompt_tokens": 123, "total_tokens": 123}
    assert run.cached_input_tokens is None
    assert run.output_tokens == 0
    assert (
        run_record(run, full=True)["pricing_calculation"]["formula"]
        == "input_tokens * input_rate / 1000000"
    )
    no_key, no_calls = clients(key=None)
    again = prepare(corpus, no_key)
    assert again["reused"] == 3 and again["generated"] == again["pending"] == 0
    assert again["run_ids"] == [] and again["estimated_cost_usd"] == "0"
    assert not no_calls
    assert len(records(engine, LLMRun)) == 1


def test_dry_run_is_read_only_and_requires_no_key(corpus, monkeypatch):
    engine, _, _ = corpus
    statements = []

    def record(_connection, _cursor, statement, *_args):
        statements.append(statement.strip().split()[0].upper())

    monkeypatch.setattr(
        embeddings, "TrackedOpenAI", lambda *_: pytest.fail("Dry run constructed an API client")
    )
    event.listen(engine, "before_cursor_execute", record)
    result = prepare(corpus, dry_run=True)
    event.remove(engine, "before_cursor_execute", record)
    assert not set(statements) & {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP"}
    assert result["pending"] == 3 and result["pending_tokens"] == 15
    assert result["planned_batches"] == 1 and result["status"] == "dry_run"
    assert Decimal(result["estimated_pending_cost_usd"]) == Decimal("0.0000003")
    assert not records(engine, LLMRun)


def test_dry_run_before_embedding_table_exists(corpus):
    engine, _, _ = corpus
    ChunkEmbedding.__table__.drop(engine)
    assert prepare(corpus, dry_run=True)["pending"] == 3


def test_title_change_refreshes_only_changed_input(corpus, clients):
    engine, _, _ = corpus
    client, calls = clients()
    prepare(corpus, client)
    with Session(engine) as session:
        doc = session.scalar(select(Document).order_by(Document.relative_path))
        doc.title = "Updated title"
        session.commit()
    result = prepare(corpus, client)
    assert result["stale_embeddings"] == result["generated"] == 1
    assert result["reused"] == 2 and len(calls) == 2
    assert len(records(engine, ChunkEmbedding)) == 3
    assert calls[1]["input"][0].startswith("Title: Updated title\n")


def test_source_update_cascades_vectors_and_reembeds(corpus, clients):
    engine, brand, root = corpus
    client, _ = clients()
    prepare(corpus, client)
    (root / "brand/0.md").write_text("# Product 0\nChanged evidence\n")
    ingest_documents(engine, discover_folder(root), "Example")
    assert len(records(engine, ChunkEmbedding)) == 2
    build_chunks(engine, brand)
    result = prepare(corpus, client)
    assert result["reused"] == 2 and result["generated"] == 1
    assert len(records(engine, ChunkEmbedding)) == 3


def test_rechunking_cascades_vectors(corpus, clients):
    engine, brand, _ = corpus
    client, _ = clients()
    prepare(corpus, client)
    # Changing target size rebuilds chunks, giving them new identities.
    build_chunks(engine, brand, target_chars=1000)
    assert not records(engine, ChunkEmbedding)
    assert prepare(corpus, client)["generated"] == 3


def test_stale_chunks_are_excluded_and_brands_isolated(corpus, clients):
    engine, _, _ = corpus
    with Session(engine) as session:
        chunk = session.scalar(select(DocumentChunk))
        chunk.source_hash = "obsolete"
        other = Brand(name="Other", source_directory="/other")
        session.add(other)
        session.commit()
        other_id = other.id
    client, calls = clients()
    result = prepare(corpus, client)
    assert result["stale_chunks_excluded"] == 1 and result["generated"] == 2
    other = embeddings.prepare_embeddings(
        engine, other_id, client=client, token_counter=lambda _: 5
    )
    assert other["eligible"] == 0 and other["generated"] == 0
    assert len(calls) == 1


def test_partial_failure_retains_completed_batch_and_resumes(corpus, clients, monkeypatch):
    engine, _, _ = corpus
    monkeypatch.setattr(embeddings, "MAX_BATCH_INPUTS", 1)
    counter = 0

    def handler(request, body):
        nonlocal counter
        counter += 1
        if counter == 2:
            return httpx.Response(
                429, json={"error": {"message": "Rate limit", "type": "rate_limit", "code": None}}
            )
        return httpx.Response(200, json=payload(body))

    client, calls = clients(handler)
    first = prepare(corpus, client)
    assert first["status"] == "failed" and first["generated"] == 1 and first["pending"] == 2
    assert first["estimated_cost_usd"] is None and first["cost_status"] == "unknown"
    assert len(calls) == 2  # SDK retries were disabled despite injected retries=3.
    second = prepare(corpus, client)
    assert second["reused"] == 1 and second["generated"] == 2 and second["pending"] == 0
    assert len(records(engine, ChunkEmbedding)) == 3
    assert len(calls) == 4


def test_persistence_failure_replays_completed_response_without_key(corpus, clients, monkeypatch):
    engine, _, _ = corpus
    client, calls = clients()
    save = embeddings.save_batch

    def fail(*_args):
        raise OperationalError("write", {}, Exception("disk unavailable"))

    monkeypatch.setattr(embeddings, "save_batch", fail)
    failed = prepare(corpus, client)
    assert failed["status"] == "failed"
    assert failed["generated"] == 0 and failed["pending"] == 3
    assert records(engine, LLMRun)[0].status == "completed"
    assert Decimal(failed["estimated_cost_usd"]) > 0
    monkeypatch.setattr(embeddings, "save_batch", save)
    preview = prepare(corpus, dry_run=True)
    assert preview["recoverable"] == 3 and preview["planned_batches"] == 0
    no_key, no_calls = clients(key=None)
    recovered = prepare(corpus, no_key)
    assert recovered["recovered"] == 3 and recovered["generated"] == recovered["pending"] == 0
    assert recovered["run_ids"] == [] and recovered["recovered_run_ids"] == failed["run_ids"]
    assert len(records(engine, LLMRun)) == len(calls) == 1 and not no_calls


def test_stale_or_other_brand_logs_cannot_be_recovered(corpus, clients):
    engine, _, _ = corpus
    client, calls = clients()
    prepare(corpus, client)
    with Session(engine) as session:
        session.execute(delete(ChunkEmbedding))
        doc = session.scalar(select(Document).order_by(Document.relative_path))
        doc.title = "New title"
        session.commit()
    result = prepare(corpus, client)
    assert result["recovered"] == 2 and result["generated"] == 1 and len(calls) == 2
    with Session(engine) as session:
        session.execute(delete(ChunkEmbedding))
        for run in session.scalars(select(LLMRun)):
            context = copy.deepcopy(run.context)
            context["brand_id"] = "different-brand"
            run.context = context
        session.commit()
    assert prepare(corpus, client)["generated"] == 3


def test_source_change_during_call_prevents_whole_batch_save(corpus, clients):
    engine, _, _ = corpus

    def handler(request, body):
        # This write also proves the preparation read transaction is closed during I/O.
        with Session(engine) as session:
            doc = session.scalar(select(Document).order_by(Document.relative_path))
            doc.title = "Changed while calling provider"
            session.commit()
        return httpx.Response(200, json=payload(body))

    client, _ = clients(handler)
    report = prepare(corpus, client)
    assert report["status"] == "failed" and "Source changed" in report["error"]
    assert report["generated"] == 0 and not records(engine, ChunkEmbedding)
    assert records(engine, LLMRun)[0].status == "completed"


def test_missing_key_logs_configuration_failure_and_sends_nothing(corpus, clients):
    engine, _, _ = corpus
    client, calls = clients(key=None)
    report = prepare(corpus, client)
    assert report["status"] == "failed" and not calls
    assert report["pending"] == 3
    assert records(engine, LLMRun)[0].status == "configuration_error"
    assert Decimal(report["estimated_cost_usd"]) == 0


def test_oversized_input_preflight_sends_nothing(corpus, clients):
    client, calls = clients()
    report = embeddings.prepare_embeddings(
        corpus[0], corpus[1], client=client, token_counter=lambda _: 8192
    )
    assert report["status"] == "failed" and report["oversized"] == 3
    assert len(report["failed_chunk_ids"]) == 3 and not calls
    assert not records(corpus[0], LLMRun)


def test_batch_count_and_token_limits():
    def item(tokens):
        return embeddings.ChunkInput("c", "d", "h", "v", "i", "text", tokens)

    packed = list(embeddings.batches([item(100)] * 33))
    assert [len(batch) for batch in packed] == [32, 1]
    packed = list(embeddings.batches([item(8191)] * 4))
    assert [len(batch) for batch in packed] == [3, 1]
    assert all(sum(item.tokens for item in batch) <= 32000 for batch in packed)


@pytest.mark.parametrize(
    "change",
    [
        lambda body: body["data"].pop(),
        lambda body: body["data"][0].update(index=True),
        lambda body: body["data"][0].update(index=-1),
        lambda body: body["data"][0].update(index=99),
        lambda body: body["data"][0].update(index=body["data"][1]["index"]),
        lambda body: body["data"][0].update(embedding=[1.0]),
        lambda body: body["data"][0].update(embedding=[0.0] * 1536),
        lambda body: body["data"][0].update(embedding=[True] * 1536),
        lambda body: body.update(model="unexpected-model"),
    ],
)
def test_malformed_response_is_audited_and_not_saved(corpus, clients, change):
    def handler(request, request_body):
        body = payload(request_body)
        change(body)
        return httpx.Response(200, json=body)

    client, calls = clients(handler)
    report = prepare(corpus, client)
    assert report["status"] == "failed" and len(calls) == 1
    assert not records(corpus[0], ChunkEmbedding)
    record = records(corpus[0], LLMRun)[0]
    assert record.status == "validation_error"
    assert record.response_body is not None


@pytest.mark.parametrize("value", [float("inf"), float("nan"), float("-inf")])
def test_nonfinite_vectors_rejected(value):
    with pytest.raises(ValueError):
        validate_vectors(
            {"model": "test", "data": [{"index": 0, "embedding": [value]}]}, "test", 1, 1
        )


def test_timeout_is_audited_with_unknown_cost_and_no_retries(corpus, clients):
    def handler(request, _body):
        raise httpx.ReadTimeout("Timed out", request=request)

    client, calls = clients(handler)
    report = prepare(corpus, client)
    assert report["status"] == "failed" and report["cost_status"] == "unknown"
    assert report["estimated_cost_usd"] is None and len(calls) == 1
    assert records(corpus[0], LLMRun)[0].status == "timeout"


@pytest.mark.parametrize(
    "usage,model,expected",
    [
        ({"prompt_tokens": 1000, "total_tokens": 1000}, embeddings.MODEL, Decimal(".00002")),
        ({"prompt_tokens": 0, "total_tokens": 0}, embeddings.MODEL, Decimal(0)),
        (None, embeddings.MODEL, None),
        ({"prompt_tokens": -1}, embeddings.MODEL, None),
        ({"prompt_tokens": True}, embeddings.MODEL, None),
        ({"prompt_tokens": 10, "total_tokens": 11}, embeddings.MODEL, None),
        ({"prompt_tokens": 10}, "unknown-model", None),
    ],
)
def test_embedding_specific_costs(usage, model, expected):
    estimate = estimate_embedding_usage(usage, model)
    assert estimate.estimated_cost_usd == expected


def test_missing_usage_does_not_prevent_valid_vector_storage(corpus, clients):
    def handler(request, request_body):
        body = payload(request_body)
        body.pop("usage")
        return httpx.Response(200, json=body)

    client, _ = clients(handler)
    result = prepare(corpus, client)
    assert result["status"] == "success" and result["generated"] == 3
    assert result["input_tokens"] is None and result["estimated_cost_usd"] is None
    assert result["cost_status"] == "unknown"


def test_no_provider_call_if_run_start_cannot_be_logged(corpus, clients, monkeypatch):
    client, calls = clients()

    def fail_start(identifier, *_args):
        raise RunLoggingError(identifier)

    monkeypatch.setattr(client, "_start", fail_start)
    result = prepare(corpus, client)
    assert result["status"] == "failed" and not calls
    assert result["cost_status"] == "unknown"


def test_cli_dry_run_and_json_error_without_tokenizer(corpus, monkeypatch, capsys):
    engine, brand, _ = corpus
    monkeypatch.setattr(
        embeddings, "get_settings", lambda: SimpleNamespace(magi_database_url=str(engine.url))
    )
    monkeypatch.setattr("sys.argv", ["embeddings", "--brand-id", brand, "--dry-run"])
    monkeypatch.setattr(embeddings, "count_tokens", lambda _: 5)
    assert embeddings.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "dry_run"

    def missing(_):
        raise ValueError("Token counter unavailable")

    monkeypatch.setattr(embeddings, "count_tokens", missing)
    assert embeddings.main() == 1
    assert json.loads(capsys.readouterr().out)["error"] == "Token counter unavailable"


def test_embedding_inputs_are_nonempty():
    with pytest.raises(ValueError):
        EmbeddingRequest(input=[" "])


def test_logged_response_failure_never_triggers_an_automatic_retry(corpus, clients, monkeypatch):
    client, calls = clients()

    def fail_finish(identifier, values):
        raise RunLoggingError(identifier, values)

    monkeypatch.setattr(client, "_finish", fail_finish)
    result = prepare(corpus, client)
    assert len(calls) == 1 and result["status"] == "failed"
    assert result["pending"] == 3 and result["estimated_cost_usd"] is None
    assert not records(corpus[0], ChunkEmbedding)
    assert records(corpus[0], LLMRun)[0].status == "started"


def test_completed_reuse_never_constructs_a_model_client(corpus, clients, monkeypatch):
    client, _ = clients()
    prepare(corpus, client)
    monkeypatch.setattr(
        embeddings,
        "TrackedOpenAI",
        lambda *_: pytest.fail("Reuse attempted to construct a model client"),
    )
    report = prepare(corpus)
    assert report["status"] == "success" and report["reused"] == 3


def test_stale_chunker_version_is_excluded(corpus, clients):
    with Session(corpus[0]) as session:
        chunk = session.scalar(select(DocumentChunk))
        chunk.chunker_version = "old-version"
        session.commit()
    client, calls = clients()
    report = prepare(corpus, client)
    assert report["stale_chunks_excluded"] == 1 and report["generated"] == 2
    assert len(calls[0]["input"]) == 2


def test_tokenizer_uses_model_encoding_and_counts_special_text_literally(monkeypatch):
    import sys

    class Encoder:
        def encode(self, text, *, disallowed_special):
            assert text == "<|endoftext|>" and disallowed_special == ()
            return [1, 2, 3]

    def encoding(name):
        assert name == "cl100k_base"
        return Encoder()

    embeddings.tokenizer.cache_clear()
    monkeypatch.setitem(sys.modules, "tiktoken", SimpleNamespace(get_encoding=encoding))
    try:
        assert embeddings.count_tokens("<|endoftext|>") == 3
    finally:
        embeddings.tokenizer.cache_clear()
