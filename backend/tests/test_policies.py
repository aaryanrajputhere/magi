import json
from uuid import uuid4

import httpx
import pytest
from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.sources import get_document
from app.brand.discovery import discover_folder
from app.brand.ingestion import ingest_documents
from app.brand.policies import extract_policies, ground_batch
from app.brand.policy_schema import MAX_DOCUMENT_CHARS, PolicyBatch
from app.config import PROJECT_ROOT, Settings
from app.db import create_database_engine, initialize_database
from app.llm.client import TrackedOpenAI
from app.models import Brand, Document, LLMRun, PolicyExtraction

TEXT = "# Rules\n\nNever claim Acme is certified.\nUse the wording exactly: Acme is pursuing authorization.\nThese rules apply to email only.\n"


def candidate(text=TEXT, quotation="Never claim Acme is certified.", line=3, **overrides):
    return {
        "rule_type": "prohibition",
        "description": "Do not claim certification",
        "exact_wording": None,
        "scope": {
            "subjects": ["Acme"],
            "channels": None,
            "audiences": None,
            "regions": None,
            "applicable_dates": None,
        },
        "conditions": None,
        "exceptions": None,
        "evidence": [{"quotation": quotation, "start_line": line, "end_line": line}],
        "status": "candidate",
        **overrides,
    }


@pytest.fixture
def database(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'magi.db'}")
    initialize_database(engine)
    root = tmp_path / "source"
    (root / "brand").mkdir(parents=True)
    (root / "brand/rules.md").write_text(TEXT)
    brand = ingest_documents(engine, discover_folder(root), "Acme").brand_id
    with Session(engine) as session:
        document = session.scalar(select(Document).where(Document.brand_id == brand))
        identifier = document.id
    yield engine, root, brand, identifier
    engine.dispose()


@pytest.fixture
def model_client(database):
    engine, *_ = database
    state = {"batch": {"candidates": [candidate()]}, "status": "completed", "hook": None}
    calls = []

    def transport(request):
        calls.append(json.loads(request.content))
        if state["hook"]:
            state["hook"]()
        output = [
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": json.dumps(state["batch"]), "annotations": []}
                ],
            }
        ]
        if state["status"] == "refused":
            output[0]["content"] = [{"type": "refusal", "refusal": "Cannot comply"}]
        body = {
            "id": "resp_" + str(uuid4()),
            "object": "response",
            "created_at": 1,
            "model": "gpt-4.1-mini",
            "status": "completed" if state["status"] == "refused" else state["status"],
            "output": output,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens_details": {"reasoning_tokens": 0},
            },
        }
        return httpx.Response(200, json=body, headers={"x-request-id": "req_policy_test"})

    sdk = OpenAI(
        api_key="sk-unit-test-not-real",
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    client = TrackedOpenAI(
        engine, Settings(_env_file=None, openai_api_key="sk-unit-test-not-real"), sdk=sdk
    )
    yield client, state, calls
    sdk.close()


def extract(database, model_client, **options):
    engine, _, brand, identifier = database
    client, *_ = model_client
    return extract_policies(engine, client, brand, [identifier], "gpt-4.1-mini", **options)


def test_tracked_extraction_and_reuse(database, model_client):
    engine, _, brand, identifier = database
    _, _, calls = model_client
    report = extract(database, model_client)
    assert report.status == "success" and report.extracted == 1
    assert report.documents[0].run_id
    assert len(calls) == 1
    assert json.loads(calls[0]["input"].split("\n\n", 1)[1])[2] == {
        "line": 3,
        "text": "Never claim Acme is certified.\n",
    }
    assert "untrusted evidence" in calls[0]["instructions"]
    assert calls[0]["max_output_tokens"] == 6000
    assert calls[0]["text"]["format"]["strict"] is True
    with Session(engine) as session:
        batch = session.scalar(select(PolicyExtraction))
        old_id = batch.candidates[0]["id"]
        quote = batch.candidates[0]["evidence"][0]
        assert TEXT[quote["start_offset"] : quote["end_offset"]] == quote["quotation"]
        assert batch.candidates[0]["scope"]["channels"] is None
        run = session.get(LLMRun, batch.run_id)
        assert run.context["document_id"] == identifier
        assert run.context["brand_id"] == brand
        assert run.estimated_cost_usd is not None
    repeated = extract(database, model_client)
    assert repeated.reused == 1 and len(calls) == 1
    with Session(engine) as session:
        assert session.scalar(select(PolicyExtraction)).candidates[0]["id"] == old_id


@pytest.mark.parametrize(
    "edit", ["invented", "wrong_line", "wrong_wording", "reverse_range", "blank_quote", "ambiguous"]
)
def test_invalid_grounding_rejects_whole_batch(database, model_client, edit):
    engine, *_ = database
    _, state, _ = model_client
    bad = candidate()
    if edit == "invented":
        bad["evidence"][0]["quotation"] = "Acme is fully certified."
    elif edit == "wrong_line":
        bad["evidence"][0]["start_line"] = bad["evidence"][0]["end_line"] = 2
    elif edit == "wrong_wording":
        bad["exact_wording"] = "Acme is fully certified"
    elif edit == "reverse_range":
        bad["evidence"][0].update(start_line=4, end_line=3)
    elif edit == "blank_quote":
        bad["evidence"][0]["quotation"] = " "
    else:
        bad["evidence"] = [{"quotation": "claim", "start_line": 3, "end_line": 4}]
        # Two occurrences in a claimed range make the quotation ambiguous.
        with Session(engine) as session:
            doc = session.scalar(select(Document))
            doc.raw_text = TEXT.replace("Use the wording", "Never claim. Use the wording")
            doc.content_hash = "b" * 64
            session.commit()
    state["batch"] = {"candidates": [candidate(), bad]}
    report = extract(database, model_client)
    assert report.failed == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PolicyExtraction)) == 0
        assert session.scalar(select(LLMRun.status)) == "completed"


def test_forced_failed_attempt_preserves_prior_success(database, model_client):
    engine, *_ = database
    _, state, calls = model_client
    extract(database, model_client)
    with Session(engine) as session:
        original = session.scalar(select(PolicyExtraction)).run_id
    state["batch"] = {"candidates": [candidate(quotation="Invented evidence")]}
    assert extract(database, model_client, force=True).failed == 1
    assert len(calls) == 2
    with Session(engine) as session:
        assert session.scalar(select(PolicyExtraction)).run_id == original
        assert session.scalar(select(func.count()).select_from(LLMRun)) == 2


@pytest.mark.parametrize("status", ["refused", "incomplete"])
def test_model_failures_are_linked_without_retries(database, model_client, status):
    _, state, calls = model_client
    state["status"] = status
    report = extract(database, model_client)
    assert report.failed == 1 and report.documents[0].run_id
    assert len(calls) == 1


def test_empty_batch_is_reused_and_visible(database, model_client):
    engine, _, brand, identifier = database
    _, state, calls = model_client
    state["batch"] = {"candidates": []}
    assert extract(database, model_client).extracted == 1
    assert extract(database, model_client).reused == 1 and len(calls) == 1
    with Session(engine) as session:
        detail = get_document(brand, identifier, session)
        assert detail.policy_state == "current"
        assert detail.policy_extraction.candidates == []


def test_preflight_checks_all_ids_and_sizes_before_calls(database, model_client):
    engine, _, brand, identifier = database
    client, _, calls = model_client
    with Session(engine) as session:
        other = Brand(name="Other", source_directory="/tmp/other")
        session.add(other)
        session.flush()
        wrong = Document(
            brand_id=other.id,
            relative_path="brand/other.md",
            content_hash="c" * 64,
            raw_text="text",
        )
        session.add(wrong)
        session.commit()
        wrong_id = wrong.id
    with pytest.raises(ValueError, match="selected brand"):
        extract_policies(engine, client, brand, [identifier, wrong_id], "gpt-4.1-mini")
    with Session(engine) as session:
        large = Document(
            brand_id=brand,
            relative_path="brand/large.md",
            raw_text="x" * (MAX_DOCUMENT_CHARS + 1),
            content_hash="d" * 64,
        )
        session.add(large)
        session.commit()
        large_id = large.id
    with pytest.raises(ValueError, match="exceeds"):
        extract_policies(engine, client, brand, [identifier, large_id], "gpt-4.1-mini")
    assert not calls


def test_changed_source_during_call_is_not_saved(database, model_client):
    engine, _, _, identifier = database
    _, state, calls = model_client

    def update():
        with Session(engine) as session:
            doc = session.get(Document, identifier)
            doc.content_hash = "e" * 64
            session.commit()

    state["hook"] = update
    report = extract(database, model_client)
    assert report.failed == 1 and "Source changed" in report.documents[0].error
    assert len(calls) == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PolicyExtraction)) == 0
        assert session.scalar(select(func.count()).select_from(LLMRun)) == 1


def test_candidate_persistence_failure_keeps_run_and_does_not_retry(database, model_client):
    engine, *_ = database
    _, _, calls = model_client

    def fail(_conn, _cursor, statement, params, _context, _many):
        if statement.startswith("INSERT INTO policy_extractions"):
            raise OperationalError(statement, params, Exception("test failure"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        report = extract(database, model_client)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert report.failed == 1 and report.documents[0].run_id
    assert len(calls) == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LLMRun)) == 1
        assert session.scalar(select(func.count()).select_from(PolicyExtraction)) == 0


def test_ingestion_invalidation_and_stale_api_state(database, model_client):
    engine, root, brand, identifier = database
    with Session(engine) as session:
        assert get_document(brand, identifier, session).policy_state == "missing"
    extract(database, model_client)
    with Session(engine) as session:
        batch = session.scalar(select(PolicyExtraction))
        batch.prompt_version = "old-version"
        session.commit()
        assert get_document(brand, identifier, session).policy_state == "stale"
    (root / "brand/rules.md").write_text(TEXT + "\nNew condition.")
    ingest_documents(engine, discover_folder(root), "Acme")
    with Session(engine) as session:
        assert get_document(brand, identifier, session).policy_state == "missing"
        assert session.scalar(select(func.count()).select_from(LLMRun)) == 1


def test_partial_dates_and_exclusions_are_preserved():
    text = "Applies after January 2027.\r\nUse names in email.\r\nPaid advertising is excluded.\r\n"
    scope = {
        "subjects": None,
        "channels": ["email"],
        "audiences": None,
        "regions": None,
        "applicable_dates": ["after January 2027"],
    }
    data = candidate(
        quotation=text, line=1, scope=scope, exceptions=["Paid advertising is excluded."]
    )
    data["evidence"][0]["end_line"] = 3
    result = ground_batch(PolicyBatch(candidates=[data]), text)[0]
    assert result.scope.applicable_dates == ["after January 2027"]
    assert result.exceptions == ["Paid advertising is excluded."]
    assert result.evidence[0].end_offset == len(text)


def test_certification_and_customer_policy_evaluation_fixtures():
    root = PROJECT_ROOT / "wickhold-candidate-package/candidate/brand/legal"
    certification = (root / "certifications-status.md").read_text()
    customer = (root / "customer-logo-and-quote-policy.md").read_text()

    def evidence(text, phrase):
        start = text.index(phrase)
        line = text[:start].count("\n") + 1
        return {"quotation": phrase, "start_line": line, "end_line": line + phrase.count("\n")}

    wording = "Wickhold is pursuing FedRAMP Moderate authorization."
    forbidden = "Never claim FedRAMP authorization or certification."
    c = candidate(description="FedRAMP authorization is not held", exact_wording=wording)
    c["evidence"] = [evidence(certification, forbidden), evidence(certification, wording)]
    assert ground_batch(PolicyBatch(candidates=[c]), certification)[0].exact_wording == wording
    vaskell = "Do not use the name, logo, people, quotes or results in anything external, and do not use them in a customer-facing deck."
    v = candidate(
        description="Vaskell is not cleared externally",
        scope={
            "subjects": ["Vaskell Health"],
            "channels": ["external", "customer-facing deck"],
            "audiences": None,
            "regions": None,
            "applicable_dates": None,
        },
    )
    v["evidence"] = [evidence(customer, vaskell)]
    allowance = "Approvals cover website, email, social, sales decks, reports and event slides."
    exclusion = "They do not cover paid advertising or press releases."
    a = candidate(
        rule_type="permission",
        description="Approved customer use is limited",
        conditions=["Customer must be listed as approved"],
        exceptions=[exclusion],
    )
    a["evidence"] = [
        evidence(customer, allowance),
        evidence(customer, exclusion),
        evidence(customer, "Cite only customers listed as approved below."),
    ]
    grounded = ground_batch(PolicyBatch(candidates=[v, a]), customer)
    assert grounded[0].scope.subjects == ["Vaskell Health"]
    assert grounded[1].exceptions == [exclusion]


def test_schema_rejects_automatic_approval():
    data = candidate(status="approved")
    with pytest.raises(ValidationError):
        PolicyBatch(candidates=[data])


def test_schema_failure_keeps_tracked_run_without_candidates(database, model_client):
    engine, *_ = database
    _, state, calls = model_client
    state["batch"] = {"candidates": [{"status": "approved"}]}
    report = extract(database, model_client)
    assert report.failed == 1 and len(calls) == 1
    with Session(engine) as session:
        assert session.scalar(select(LLMRun.status)) == "validation_error"
        assert session.scalar(select(func.count()).select_from(PolicyExtraction)) == 0


def test_model_and_prompt_configuration_changes_require_new_extraction(database, model_client):
    engine, _, brand, identifier = database
    client, _, calls = model_client
    extract(database, model_client)
    assert extract_policies(engine, client, brand, [identifier], "another-model").extracted == 1
    assert len(calls) == 2
    with Session(engine) as session:
        session.scalar(select(PolicyExtraction)).prompt_version = "old-prompt"
        session.commit()
    assert extract_policies(engine, client, brand, [identifier], "another-model").extracted == 1
    assert len(calls) == 3


def test_failed_update_restores_invalidated_policy_batch(database, model_client):
    engine, root, _, _ = database
    extract(database, model_client)
    with Session(engine) as session:
        batch = session.scalar(select(PolicyExtraction))
        original = (batch.id, batch.run_id)
    (root / "brand/rules.md").write_text(TEXT + "\nChanged")

    def fail(_conn, _cursor, statement, params, _context, _many):
        if statement.startswith("UPDATE documents"):
            raise OperationalError(statement, params, Exception("test failure"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        report = ingest_documents(engine, discover_folder(root), "Acme")
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert report.status == "failed"
    with Session(engine) as session:
        batch = session.scalar(select(PolicyExtraction))
        assert (batch.id, batch.run_id) == original


def test_cli_output_and_preflight_failure(database, model_client, monkeypatch, capsys):
    from app.brand.policies import main

    engine, _, brand, identifier = database
    client, _, calls = model_client
    monkeypatch.setattr(
        "app.brand.policies.get_settings",
        lambda: Settings(_env_file=None, magi_database_url=str(engine.url)),
    )
    monkeypatch.setattr("app.brand.policies.TrackedOpenAI", lambda *_args: client)
    monkeypatch.setattr(
        "sys.argv",
        ["policies", "--brand-id", brand, "--document-id", identifier, "--model", "gpt-4.1-mini"],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["extracted"] == 1
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["reused"] == 1
    monkeypatch.setattr(
        "sys.argv",
        ["policies", "--brand-id", brand, "--document-id", "missing", "--model", "gpt-4.1-mini"],
    )
    assert main() == 1
    assert "selected brand" in capsys.readouterr().err
    assert len(calls) == 1


def test_source_records_preserve_markdown_quotes_unicode_and_line_endings():
    from app.brand.policies import numbered_source

    text = "**Label.** Never claim authorization.\r\n> Quoted “wording”.\r\n"
    records = json.loads(numbered_source(text))
    assert records[0] == {"line": 1, "text": "**Label.** Never claim authorization.\r\n"}
    assert "".join(row["text"] for row in records) == text


def test_rendered_markdown_is_rejected_and_literal_source_is_accepted():
    text = "**FedRAMP.** Never claim authorization.\n"
    rendered = candidate(quotation="FedRAMP. Never claim authorization.", line=1)
    with pytest.raises(ValueError, match="does not match"):
        ground_batch(PolicyBatch(candidates=[rendered]), text)
    literal = candidate(quotation="**FedRAMP.** Never claim authorization.", line=1)
    assert ground_batch(PolicyBatch(candidates=[literal]), text)[0].evidence[
        0
    ].quotation == text.rstrip("\n")


def test_required_wording_needs_defining_evidence_not_only_cross_reference():
    text = 'Approved wording: "Pursuing authorization."\nUse the approved sentence in the form.\n'
    rule = candidate(
        quotation="Use the approved sentence in the form.",
        line=2,
        exact_wording="Pursuing authorization.",
    )
    with pytest.raises(ValueError, match="absent"):
        ground_batch(PolicyBatch(candidates=[rule]), text)
    rule["evidence"].append(
        {"quotation": 'Approved wording: "Pursuing authorization."', "start_line": 1, "end_line": 1}
    )
    assert (
        ground_batch(PolicyBatch(candidates=[rule]), text)[0].exact_wording
        == "Pursuing authorization."
    )


@pytest.mark.parametrize(
    "quotation",
    [
        "...Never claim Acme is certified.",
        "… Never claim Acme is certified. …",
        "Never claim Acme is certified....",
    ],
)
def test_boundary_ellipsis_keeps_literal_source_and_records_adjustment(quotation):
    batch = PolicyBatch(candidates=[candidate(quotation=quotation)])
    evidence = ground_batch(batch, TEXT)[0].evidence[0]
    assert evidence.quotation == "Never claim Acme is certified."
    assert evidence.model_quotation == quotation
    assert evidence.match_method == "boundary_ellipsis"
    assert TEXT[evidence.start_offset : evidence.end_offset] == evidence.quotation


@pytest.mark.parametrize(
    "quotation",
    ["Never ... Acme is certified.", "...Never claim Acme is fully certified.", "...", "…"],
)
def test_ellipsis_does_not_allow_internal_edits_or_invented_words(quotation):
    with pytest.raises(ValueError):
        ground_batch(PolicyBatch(candidates=[candidate(quotation=quotation)]), TEXT)


def test_replay_is_local_and_retains_original_run(database, model_client, monkeypatch, capsys):
    from sqlalchemy import delete

    from app.brand.policies import main

    engine, _, brand, identifier = database
    _, state, calls = model_client
    state["batch"] = {"candidates": [candidate(quotation="...Never claim Acme is certified.")]}
    report = extract(database, model_client)
    run_id = report.documents[0].run_id
    with Session(engine) as session:
        original = session.get(LLMRun, run_id).response_body
        session.execute(delete(PolicyExtraction))  # Emulate an earlier rejected batch.
        session.commit()

    def forbidden(*_args):
        raise AssertionError("Replay must never construct a model client")

    monkeypatch.setattr(
        "app.brand.policies.get_settings",
        lambda: Settings(_env_file=None, magi_database_url=str(engine.url), openai_api_key=None),
    )
    monkeypatch.setattr("app.brand.policies.TrackedOpenAI", forbidden)
    monkeypatch.setattr(
        "sys.argv",
        [
            "policies",
            "--brand-id",
            brand,
            "--document-id",
            identifier,
            "--model",
            "gpt-4.1-mini",
            "--from-run",
            run_id,
        ],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["replayed"] == 1
    assert len(calls) == 1
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LLMRun)) == 1
        assert session.get(LLMRun, run_id).response_body == original
        detail = get_document(brand, identifier, session)
        assert (
            detail.policy_extraction.candidates[0].evidence[0].match_method == "boundary_ellipsis"
        )


@pytest.mark.parametrize("change", ["hash", "model", "prompt", "operation"])
def test_replay_refuses_incompatible_saved_runs(database, model_client, change):
    from app.brand.policies import replay_policy_run

    engine, _, brand, identifier = database
    report = extract(database, model_client)
    run_id = report.documents[0].run_id
    with Session(engine) as session:
        run = session.get(LLMRun, run_id)
        if change == "hash":
            session.get(Document, identifier).content_hash = "e" * 64
        elif change == "model":
            run.requested_model = "different-model"
        elif change == "prompt":
            run.request_body = {**run.request_body, "instructions": "different prompt"}
        else:
            run.operation = "another-operation"
        session.commit()
    result = replay_policy_run(engine, brand, identifier, "gpt-4.1-mini", run_id)
    assert result.failed == 1
    assert len(model_client[2]) == 1
