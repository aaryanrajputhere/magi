from types import SimpleNamespace

import pytest
import test_authority
from test_authority import confirmed, retrieval as original_retrieval

from app.brand.evidence_selection import collect_evidence
from app.brand.retrieval import RetrievalError
from app.brand.review import Review
from app.brand.review import review_campaign as run_review
from app.config import Settings


def retrieval(materials):
    """Conflict fixtures explicitly retrieve both sides of governing disputes."""
    from sqlalchemy.orm import Session
    from app.models.context_artifact import ContextArtifact
    artifact = original_retrieval(materials)
    engine, brand, *_ = materials
    _, _, _, hits, sections, _ = collect_evidence(engine, brand, artifact["id"])
    for section in sections:
        for passage in section["passages"]:
            hits.setdefault(passage["chunk_id"], passage)
    with Session(engine) as session:
        row = session.get(ContextArtifact, artifact["id"])
        payload = dict(row.payload)
        payload["groups"] = [{**payload["groups"][0], "results": list(hits.values())}]
        row.payload = payload
        session.commit()
    return {**artifact, **payload}


def review_campaign(*args, client, **kwargs):
    """Provide isolated fake worker clients; expand wire lines for legacy fixtures."""
    import json

    def run(request, **options):
        data = json.loads(request.input)
        if request.operation == "select_evidence":
            from uuid import uuid4

            from sqlalchemy.orm import Session

            from app.models import LLMRun
            selection_id = str(uuid4())
            with Session(args[0]) as session:
                if session.get(LLMRun, selection_id) is None:
                    session.add(LLMRun(id=selection_id, operation="select_evidence", requested_model="fake", timeout_seconds=60, context={}, request_body={}))
                    session.commit()
            return SimpleNamespace(parsed=options["output_model"].model_validate({
                "decisions": {section["decision_key"]: {"decision": "include", "reason": "Relevant", "evidence": [], "dependencies": []} for section in data["sections"]},
                "coverage_complete": True, "limitations": []}), run_id=selection_id)
        if request.operation in {"campaign_reconciliation", "campaign_question_review"}:
            data["evidence_lines"] = {
                ref: {"chunk_id": p["chunk_id"], "text": text}
                for p in data["passages"] for ref, text in p["lines"].items()}
            for p in data["passages"]:
                p["content"] = "\n".join(p["lines"].values())
            request = request.model_copy(update={"operation": "campaign_conflict_review" if request.operation == "campaign_reconciliation" else request.operation, "input": json.dumps(data)})
        if request.operation == "campaign_question_review":
            # Exercise the actual question wire schema and server-side quote copying.
            payload = {
                "supported": [{"text": p["content"], "source_ids": [p["chunk_id"]]}
                              for p in data["passages"]],
                "restricted": [], "conflicts": [], "unresolved": [],
                "coverage_complete": True, "limitations": [],
                "claims": [{
                    "claim_key": ref, "scope": "question", "topic": "general",
                    "requested_wording": "", "approved_wording": line["text"],
                    "decision": "supported", "reason": "Original source line",
                    "evidence_line_ids": [ref],
                } for ref, line in data["evidence_lines"].items()],
            }
            return SimpleNamespace(parsed=options["output_model"].model_validate(payload), run_id="question")
        return client.run(request, **options)

    return run_review(*args, client=SimpleNamespace(run=run),
        worker_client_factory=lambda: SimpleNamespace(run=run, close=lambda: None), **kwargs)


@pytest.fixture
def materials(tmp_path):
    from app.brand.retrieval import build_index

    fixture = test_authority.materials.__wrapped__(tmp_path)
    value = next(fixture)
    build_index(value[0], value[1])
    yield value
    next(fixture, None)


@pytest.mark.parametrize("same_scope", [True, False])
@pytest.mark.parametrize("nominate_winner", ["legal", "sales", None])
def test_review_precedence_and_unknown_scope(materials, same_scope, nominate_winner):
    engine, brand, docs, _ = materials
    confirmed(materials)
    artifact = retrieval(materials)
    _, _, _, hits, sections, _ = collect_evidence(engine, brand, artifact["id"])
    for section in sections:
        for passage in section["passages"]:
            hits.setdefault(passage["chunk_id"], passage)
    legal = next(key for key, p in hits.items() if p["document_id"] == docs["legal"]["id"])
    sales = next(key for key, p in hits.items() if p["document_id"] == docs["sales"]["id"])
    report = Review(
        supported=[],
        restricted=[],
        unresolved=[],
        conflicts=[
            {
                "evidence": [{"chunk_id": key, "quote": hits[key]["content"]} for key in [legal, sales]],
                    "explanation": "Certification claims disagree",
                "topic": "certification",
                "source_ids": [legal, sales],
                "preferred_source_id": {"legal": legal, "sales": sales}.get(nominate_winner),
                "same_scope": same_scope,
                "scope_explanation": "Same company and channel",
                "allowed_wording": "Authorization is in process",
            }
        ],
    )
    client = SimpleNamespace(run=lambda *a, **kw: SimpleNamespace(parsed=report, run_id="mock"))
    reviewed = review_campaign(
        engine,
        brand,
        artifact["id"],
        client=client,
        settings=Settings(_env_file=None),
        campaign_id="campaign",
    )
    conflict = reviewed["report"]["conflicts"][0]
    assert conflict["status"] == ("resolved" if same_scope else "unresolved")
    if not same_scope:
        assert conflict["allowed_wording"] is None
    assert reviewed["evidence"]


def test_unknown_review_source_is_rejected(materials, monkeypatch):
    engine, brand, _, _ = materials
    confirmed(materials)
    artifact = retrieval(materials)
    report = Review(
        supported=[{"text": "Invented", "source_ids": ["unknown"]}],
        restricted=[],
        conflicts=[],
        unresolved=[],
    )
    client = SimpleNamespace(run=lambda *a, **kw: SimpleNamespace(parsed=report, run_id="mock"))
    with pytest.raises(RetrievalError, match="unknown source"):
        review_campaign(
            engine,
            brand,
            artifact["id"],
            client=client,
            settings=Settings(_env_file=None),
            campaign_id="campaign",
        )


def test_review_schema_restricts_every_reference():
    from pydantic import ValidationError

    from app.brand.review import review_schema
    from app.llm.client import strict_output_schema

    schema = review_schema(["passage-a", "passage-b"])
    strict_output_schema(schema)
    payload = {
        "supported": [{"text": "Fact", "source_ids": ["passage-a"]}],
        "restricted": [],
        "conflicts": [],
        "unresolved": [],
    }
    assert schema.model_validate(payload).supported[0].source_ids == ["passage-a"]
    payload["supported"][0]["source_ids"] = ["document-id"]
    with pytest.raises(ValidationError):
        schema.model_validate(payload)
    payload["supported"] = []
    payload["conflicts"] = [
        {
            "explanation": "Conflict",
            "topic": "certification",
            "source_ids": ["passage-a", "passage-b"],
            "preferred_source_id": "document-id",
            "same_scope": True,
            "scope_explanation": "Same",
            "allowed_wording": None,
        }
    ]
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


@pytest.mark.parametrize("invalid", [None, "duplicate", "quotation", "source", "line_reference", "coverage"])
def test_reconciliation_replaces_stale_findings_and_uses_one_final_pass(
    materials, invalid, monkeypatch
):
    import json

    from app.brand.review import ReconciledReview

    engine, brand, docs, _ = materials
    confirmed(materials)
    artifact = retrieval(materials)
    calls = []
    original_collect = collect_evidence

    def five_questions(*args, **kwargs):
        authority, retrieved, plan, hits, sections, documents = original_collect(*args, **kwargs)
        retrieved["groups"] = retrieved["groups"] * 5
        return authority, retrieved, plan, hits, sections, documents

    monkeypatch.setattr("app.brand.review.collect_evidence", five_questions)

    def run(request, **kwargs):
        calls.append(request.operation)
        assert 'coverage_complete measures only' in request.instructions
        assert 'requested publication date' in request.instructions
        data = json.loads(request.input)
        if request.operation != "campaign_conflict_review":
            return SimpleNamespace(
                parsed=Review(
                    supported=[],
                    restricted=[],
                    conflicts=[],
                    unresolved=["Missing approved wording", "Missing optional completion date"],
                ),
                run_id="batch",
            )
        legal = next(
            p
            for p in data["passages"]
            if p["document_id"] == docs["legal"]["id"] and "Status: pursuing." in p["content"]
        )
        assert data["precedence"]
        claim = {
            "claim_key": "fedramp_status",
            "scope": "company / linkedin",
            "topic": "certification",
            "requested_wording": "Authorized",
            "approved_wording": "Status: pursuing.",
            "decision": "corrected",
            "reason": "Legal overrides sales",
            "source_ids": [legal["chunk_id"]],
            "evidence_quotes": ["Status: pursuing."],
        }
        if invalid == "quotation":
            claim["evidence_quotes"] = ["Invented quotation"]
        if invalid == "source":
            claim["source_ids"] = ["unknown"]
        if invalid in {"line_reference", "wrong_line_source"}:
            from app.brand.review import reconciliation_schema

            claim.pop("evidence_quotes")
            claim.pop("source_ids")
            claim["evidence_line_ids"] = [next(
                key for key, value in data["evidence_lines"].items()
                if (value["chunk_id"] == legal["chunk_id"] and value["text"] == "Status: pursuing.")
            )] if invalid == "line_reference" else [next(
                key for key, value in data["evidence_lines"].items()
                if value["chunk_id"] != legal["chunk_id"]
            )]
            return SimpleNamespace(parsed=reconciliation_schema(
                [p["chunk_id"] for p in data["passages"]], data["evidence_lines"]
            ).model_validate({
                "supported": [], "restricted": [], "conflicts": [], "unresolved": [],
                "claims": [claim], "coverage_complete": True, "limitations": [],
            }), run_id="final")
        final = ReconciledReview(
            supported=[],
            restricted=[],
            conflicts=[],
            unresolved=[],
            claims=[claim, claim] if invalid == "duplicate" else [claim],
            coverage_complete=invalid != "coverage",
            limitations=["Evidence was truncated"] if invalid == "coverage" else [],
        )
        return SimpleNamespace(parsed=final, run_id="final")

    if invalid and invalid not in {"line_reference", "wrong_line_source"}:
        with pytest.raises(RetrievalError, match="Evidence was truncated" if invalid == "coverage" else None):
            review_campaign(
                engine,
                brand,
                artifact["id"],
                client=SimpleNamespace(run=run),
                settings=Settings(_env_file=None),
                campaign_id="campaign",
            )
        return
    reviewed = review_campaign(
        engine,
        brand,
        artifact["id"],
        client=SimpleNamespace(run=run),
        settings=Settings(_env_file=None),
        campaign_id="campaign",
    )
    assert calls.count("campaign_question_review") == 0
    assert calls.count("campaign_conflict_review") == 1
    assert "campaign_combined_review" not in calls
    assert reviewed["report"]["unresolved"] == []
    assert reviewed["report"]["claims"][0]["decision"] == "corrected"
    assert reviewed["run_id"] == "final"
    assert reviewed["pipeline_version"] == 7
    assert reviewed["report"]["claims"][0]["evidence_quotes"] == ["Status: pursuing."]


def test_reconciliation_schema_validates_claim_sources():
    from pydantic import ValidationError

    from app.brand.review import reconciliation_schema
    from app.llm.client import strict_output_schema

    schema = reconciliation_schema(["legal"])
    strict_output_schema(schema)
    payload = {
        "supported": [],
        "restricted": [],
        "conflicts": [],
        "unresolved": [],
        "claims": [
            {
                "claim_key": "soc2_status",
                "scope": "company",
                "topic": "certification",
                "requested_wording": "certified",
                "approved_wording": "Wickhold holds SOC 2 Type II.",
                "decision": "corrected",
                "reason": "Exact approved wording",
                "source_ids": ["unknown"],
                "evidence_quotes": ["Wickhold holds SOC 2 Type II."],
            }
        ],
    }
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


@pytest.mark.parametrize("query", [" ".join(f"term{i}" for i in range(80)), " ".join("x" * 100 + str(i) for i in range(12))])
def test_policy_queries_preserve_terms_within_search_limits(query):
    from app.brand.retrieval import query_terms
    from app.brand.review import policy_queries

    batches = policy_queries(query)
    assert len(batches) > 1
    assert [term for batch in batches for term in query_terms(batch)] == query.split()


def test_question_claim_quotes_are_copied_from_source_lines():
    from app.brand.review import copy_claim_evidence, reconciliation_schema

    lines = {
        "voice:L1": {"chunk_id": "voice", "text": "First sentence. Middle sentence. Last sentence."},
        "format:L3": {"chunk_id": "format", "text": "- A LinkedIn post is 180 words at most."},
    }
    schema = reconciliation_schema(["voice", "format"], lines)
    payload = {
        "supported": [], "restricted": [], "conflicts": [], "unresolved": [],
        "coverage_complete": True, "limitations": [],
        "claims": [{
            "claim_key": "voice", "scope": "linkedin", "topic": "voice",
            "requested_wording": "Brand voice", "approved_wording": "Use brand voice",
            "decision": "supported", "reason": "Source guidance",
            "evidence_line_ids": ["voice:L1"],
        }],
    }
    report = schema.model_validate(payload).model_dump()
    claim = report["claims"][0]
    copy_claim_evidence(claim, lines, run_id="test")
    assert claim["evidence_quotes"] == [lines["voice:L1"]["text"]]
    assert "evidence_line_ids" not in claim
    assert "evidence_quotes" not in schema.model_fields["claims"].annotation.__args__[0].model_fields


@pytest.mark.parametrize("references", [[], ["missing:L1"]])
def test_question_claim_rejects_missing_or_wrong_source_lines(references):
    from app.brand.review import copy_claim_evidence

    claim = {"source_ids": ["voice"], "evidence_line_ids": references}
    lines = {"format:L3": {"chunk_id": "format", "text": "180 words"}}
    with pytest.raises(RetrievalError, match="run test-run"):
        copy_claim_evidence(claim, lines, run_id="test-run")


def test_review_input_transmits_text_once_and_preserves_line_ids():
    import json

    from app.brand.review import review_input

    text = 'An original governing restriction with its exact qualifier.'
    passage = {'chunk_id': 'policy', 'content': text, 'document_id': 'doc'}
    lines = {'policy:L3': {'chunk_id': 'policy', 'text': text}}
    original = {'passages': [passage], 'evidence_lines': lines, 'brief': 'Campaign'}
    compact = review_input(original, lines)
    assert json.dumps(compact).count(text) == 1
    assert len(json.dumps(compact)) < len(json.dumps(original))
    assert compact['passages'][0]['lines'] == {'policy:L3': text}
    assert original['passages'][0]['content'] == text


def test_claim_sources_are_derived_from_all_selected_lines():
    from app.brand.review import copy_claim_evidence

    lines = {'a:L1': {'chunk_id': 'a', 'text': 'First'},
             'b:L2': {'chunk_id': 'b', 'text': 'Second'}}
    claim = {'evidence_line_ids': ['a:L1', 'b:L2', 'a:L1']}
    copy_claim_evidence(claim, lines, run_id='regression')
    assert claim['source_ids'] == ['a', 'b']
    assert claim['evidence_quotes'] == ['First', 'Second', 'First']


def test_campaign_review_never_expands_unretrieved_policy(materials):
    import json
    engine, brand, docs, _ = materials
    confirmed(materials)
    artifact = original_retrieval(materials)
    seen = []

    def run(request, **kwargs):
        seen.append(request.operation)
        data = json.loads(request.input)
        assert request.operation != 'select_evidence'
        assert all(p['document_id'] == docs['sales']['id'] for p in data['passages'])
        assert all('evidence' not in g for g in data['authority']['groups'])
        return SimpleNamespace(parsed=Review(supported=[], restricted=[], conflicts=[], unresolved=[]), run_id='final')

    result = review_campaign(engine, brand, artifact['id'], client=SimpleNamespace(run=run),
                             settings=Settings(_env_file=None), campaign_id='campaign')
    assert result['pipeline_version'] == 7
    assert 'select_evidence' not in seen
    assert all(p['document_id'] == docs['sales']['id'] for p in result['evidence'])
