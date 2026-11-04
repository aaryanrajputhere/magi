import hashlib
import json

import httpx
import pytest
import test_authority
from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_authority import (
    analysis,
    confirmed,
    mapping,
    retrieval,
)
from test_llm import response_body

from app.brand.assessment import assess_evidence
from app.brand.authority import confirm_authority, save_version
from app.brand.chunking import build_chunks
from app.brand.context import load_artifact
from app.brand.evidence_selection import (
    SELECTION_BATCH_SECTIONS,
    SectionReview,
    collect_evidence,
    load_selected_evidence,
    section_review_schema,
    select_evidence,
    selected_passages,
    serialized_size,
)
from app.brand.retrieval import RetrievalError
from app.config import Settings
from app.llm.client import TrackedOpenAI, strict_output_schema
from app.models import ContextArtifact, Document

materials = test_authority.materials
provider = test_authority.provider


def test_keyed_schema_requires_one_result_for_each_exact_section():
    schema = section_review_schema([{"id": "parent"}, {"id": "child"}])
    provider_schema = strict_output_schema(schema)["format"]["schema"]
    choices = provider_schema["$defs"]["SectionChoices"]
    assert choices["required"] == ["section_01", "section_02"]
    assert choices["additionalProperties"] is False
    choice = {"decision": "include", "reason": "Applicable", "evidence": [], "dependencies": []}
    payload = {
        "decisions": {"section_01": choice, "section_02": choice},
        "coverage_complete": True,
        "limitations": [],
    }
    assert schema.model_validate(payload).decisions.section_02.decision == "include"
    del payload["decisions"]["section_02"]
    with pytest.raises(ValidationError):
        schema.model_validate(payload)
    payload["decisions"]["parent"] = choice
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


@pytest.fixture
def section_provider(materials):
    clients = []

    def make(transform=lambda result: result):
        calls = []

        def handler(request):
            data = json.loads(json.loads(request.content)["input"])
            calls.append(data)
            decisions = []
            for section in data["sections"]:
                passage = section["passages"][0]
                email = "Only email campaigns:" in passage["content"]
                decisions.append(
                    {
                        "section_id": section["id"],
                        "decision": "exclude" if email else "include",
                        "reason": "Explicitly email only" if email else "Applicable rule",
                        "evidence": [
                            {
                                "document_id": passage["document_id"],
                                "quotation": passage["content"],
                                "start_line": passage["start_line"],
                                "end_line": passage["end_line"],
                            }
                        ]
                        if email
                        else [],
                        "dependencies": [],
                    }
                )
            result = transform(
                {"decisions": decisions, "coverage_complete": True, "limitations": []}
            )
            # Simulate the provider's section-keyed structured output. Duplicate-ID
            # regressions deliberately retain the old invalid list response.
            identities = [d["section_id"] for d in result["decisions"]]
            if len(identities) == len(set(identities)):
                keys = {s["id"]: s["decision_key"] for s in data["sections"]}
                result["decisions"] = {
                    keys[d["section_id"]]: {k: v for k, v in d.items() if k != "section_id"}
                    for d in result["decisions"]
                }
            return httpx.Response(200, json=response_body(json.dumps(result)))

        sdk = OpenAI(
            api_key="sk-fake", http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        clients.append(sdk)
        return TrackedOpenAI(
            materials[0], Settings(_env_file=None, openai_api_key="sk-fake"), sdk=sdk
        ), calls

    yield make
    for sdk in clients:
        sdk.close()


def large_materials(materials):
    engine, brand, docs, _ = materials
    with Session(engine) as session, session.begin():
        doc = session.get(Document, docs["legal"]["id"])
        doc.raw_text += "".join(
            f"\n\n## Email rule {i}\n\nOnly email campaigns: " + "Email layout detail. " * 55
            for i in range(100)
        )
        doc.content_hash = hashlib.sha256(doc.raw_text.encode()).hexdigest()
    build_chunks(engine, brand)
    confirmed(materials)
    return retrieval(materials)


def test_small_selection_preserves_evidence_without_model_call(materials, provider):
    engine, brand, _, _ = materials
    confirmed(materials)
    retrieved = retrieval(materials)
    client, calls = provider(analysis(materials).model_dump())
    selection = select_evidence(engine, brand, retrieved["id"], client=client)
    assert not calls
    assert selection["coverage_complete"]
    assert all(section["included"] for section in selection["sections"])
    assessed = assess_evidence(
        engine, brand, retrieved["id"], selection_id=selection["id"], client=client
    )
    payload = json.loads(calls[0]["input"])
    assert assessed["input_characters"] == selection["input_characters"]
    assert all("score" not in p and "rank" not in p for p in payload["passages"])
    assert strict_output_schema(SectionReview)


@pytest.mark.parametrize(
    "channels,expected",
    [(["email"], False), (None, True), ([], True), (["*"], True), (["LinkedIn"], True)],
)
def test_confirmed_channel_scopes_and_unknowns(materials, channels, expected):
    engine, brand, docs, _ = materials
    data = mapping(materials)
    data.groups[2].channels = channels
    draft = save_version(engine, brand, data)
    confirm_authority(engine, brand, draft["id"])
    retrieved = retrieval(materials)
    *_, sections, documents = collect_evidence(engine, brand, retrieved["id"])
    assert any(s["document_id"] == docs["channel"]["id"] for s in sections) == expected
    assert (
        next(d for d in documents if d["document_id"] == docs["channel"]["id"])["included"]
        == expected
    )


def test_large_selection_preserves_relevant_rules_and_historical_hits(
    materials, section_provider, provider
):
    engine, brand, docs, _ = materials
    retrieved = large_materials(materials)
    client, calls = section_provider()
    selection = select_evidence(engine, brand, retrieved["id"], client=client)
    assert selection["original_characters"] > 100_000
    assert selection["input_characters"] < 100_000
    assert len(calls) >= 2
    assert all(len(call["sections"]) <= SELECTION_BATCH_SECTIONS for call in calls)
    assert sum(len(call["sections"]) for call in calls) == len(selection["sections"])
    assert all(s["evidence"] for s in selection["sections"] if not s["included"])
    _, passages, _ = load_selected_evidence(engine, brand, retrieved["id"], selection["id"])
    assert any("Never claim authorized." in p["content"] for p in passages)
    assert any(p["document_id"] == docs["sales"]["id"] for p in passages)
    assessment_client, assessment_calls = provider(analysis(materials).model_dump())
    assessed = assess_evidence(
        engine, brand, retrieved["id"], selection_id=selection["id"], client=assessment_client
    )
    assert assessed["resolutions"][0]["status"] == "resolved"
    assert (
        serialized_size(json.loads(assessment_calls[0]["input"])) == selection["input_characters"]
    )


def test_unknown_selection_and_incomplete_coverage_do_not_silently_drop_rules(
    materials, section_provider
):
    engine, brand, _, _ = materials
    retrieved = large_materials(materials)

    def uncertain(result):
        for decision in result["decisions"]:
            decision["decision"] = "uncertain"
        return result

    client, _ = section_provider(uncertain)
    selection = select_evidence(engine, brand, retrieved["id"], client=client)
    assert all(s["included"] for s in selection["sections"])
    assert "input_limit" not in selection
    assert selection["input_characters"] > 100_000

    def incomplete(result):
        result["coverage_complete"] = False
        result["limitations"] = ["Unresolved policy reference"]
        return result

    client, _ = section_provider(incomplete)
    selection = select_evidence(engine, brand, retrieved["id"], client=client)
    assert not selection["coverage_complete"]
    with pytest.raises(RetrievalError, match="incomplete coverage"):
        load_selected_evidence(engine, brand, retrieved["id"], selection["id"])


@pytest.mark.parametrize(
    "fault",
    ["missing", "duplicate", "unknown_dependency", "bad_quote", "wrong_lines", "missing_quote"],
)
def test_invalid_section_reviews_are_rejected(materials, section_provider, fault):
    engine, brand, _, _ = materials
    retrieved = large_materials(materials)

    def invalid(result):
        if fault == "missing":
            result["decisions"].pop()
        elif fault == "duplicate":
            result["decisions"].append(result["decisions"][0])
        elif fault == "unknown_dependency":
            result["decisions"][0]["dependencies"] = ["invented"]
        else:
            decision = next(d for d in result["decisions"] if d["decision"] == "exclude")
            if fault == "missing_quote":
                decision["evidence"] = []
            elif fault == "bad_quote":
                decision["evidence"][0]["quotation"] = "Invented quotation"
            else:
                decision["evidence"][0]["end_line"] = decision["evidence"][0]["start_line"]
        return result

    client, _ = section_provider(invalid)
    if fault in {"bad_quote", "wrong_lines", "missing_quote"}:
        selection = select_evidence(engine, brand, retrieved["id"], client=client)
        assert selection["validation_warnings"]
        _, passages, included = load_selected_evidence(engine, brand, retrieved["id"], selection["id"])
        for warning in selection["validation_warnings"]:
            section = next(s for s in selection["sections"] if s["id"] == warning["section_id"])
            assert section["included"] and section["decision"] == "include"
            assert warning["proposed_decision"]["decision"] == "exclude"
            assert section["id"] in included
            assert all(p in passages for p in section["passages"])
        assert any(not s["included"] for s in selection["sections"])
        return
    with pytest.raises(RetrievalError):
        select_evidence(engine, brand, retrieved["id"], client=client)
    with Session(engine) as session:
        assert (
            session.scalar(select(ContextArtifact).where(ContextArtifact.kind == "selection"))
            is None
        )


def test_dependency_and_ancestor_closure_and_user_restoration():
    def section(identifier, path, decision="exclude", deps=()):
        return {
            "id": identifier,
            "heading_path": path,
            "decision": decision,
            "document_id": "doc",
            "dependencies": list(deps),
            "passages": [{"chunk_id": identifier, "content": f"Full {identifier} text"}],
        }

    sections = [
        section("root", ["Policy"]),
        section("rule", ["Policy", "Rule"], "include", ["exception"]),
        section("exception", ["Policy", "Exception"], deps=["rule"]),
        section("optional", ["Policy", "Other"]),
    ]
    passages, included = selected_passages({"history": {"chunk_id": "history"}}, sections)
    assert included == {"root", "rule", "exception"}
    assert {p["chunk_id"] for p in passages} == included | {"history"}
    _, included = selected_passages({}, sections, ["optional"])
    assert included == {s["id"] for s in sections}
    with pytest.raises(RetrievalError, match="Unknown evidence section"):
        selected_passages({}, sections, ["invented"])


def test_restoration_rechecks_budget_and_authority_invalidates_selection(
    materials, section_provider
):
    engine, brand, _, _ = materials
    retrieved = large_materials(materials)
    client, _ = section_provider()
    selection = select_evidence(engine, brand, retrieved["id"], client=client)
    all_ids = [s["id"] for s in selection["sections"]]
    _, passages, _ = load_selected_evidence(
        engine, brand, retrieved["id"], selection["id"], additional_ids=all_ids
    )
    assert passages
    draft = save_version(engine, brand, mapping(materials))
    confirm_authority(engine, brand, draft["id"])
    with pytest.raises(RetrievalError, match="confirmed authority"):
        load_artifact(engine, brand, selection["id"], "selection")


def test_selection_is_bound_to_retrieval(materials, provider):
    engine, brand, _, _ = materials
    confirmed(materials)
    first, second = retrieval(materials), retrieval(materials)
    client, _ = provider(analysis(materials).model_dump())
    selection = select_evidence(engine, brand, first["id"], client=client)
    with pytest.raises(RetrievalError, match="does not belong"):
        load_selected_evidence(engine, brand, second["id"], selection["id"])


def test_complete_oversized_section_is_retained_without_character_cap(materials, provider):
    engine, brand, docs, _ = materials
    with Session(engine) as session, session.begin():
        doc = session.get(Document, docs["legal"]["id"])
        doc.raw_text += "\n\nA policy condition that must travel with its section. " * 3000
        doc.content_hash = hashlib.sha256(doc.raw_text.encode()).hexdigest()
    build_chunks(engine, brand)
    confirmed(materials)
    retrieved = retrieval(materials)
    client, calls = provider(analysis(materials).model_dump())
    selected = select_evidence(engine, brand, retrieved["id"], client=client)
    assert selected["input_characters"] > 100_000
    assert all(section["included"] for section in selected["sections"])
    assert not calls


def test_selection_api_and_assessment_share_reviewed_evidence(materials, provider, monkeypatch):
    from app.api.retrieval import (
        AssessmentRequest,
        SelectionRequest,
        assess_context,
        select_context_evidence,
    )
    from app.brand import assessment

    engine, brand, _, _ = materials
    confirmed(materials)
    retrieved = retrieval(materials)
    client, calls = provider(analysis(materials).model_dump())
    original = assessment.assess_evidence
    monkeypatch.setattr(
        assessment,
        "assess_evidence",
        lambda *args, **kwargs: original(*args, **kwargs, client=client),
    )
    with Session(engine) as session:
        selection = select_context_evidence(
            brand, SelectionRequest(retrieval_id=retrieved["id"]), session, Settings(_env_file=None)
        )
        result = assess_context(
            brand,
            AssessmentRequest(retrieval_id=retrieved["id"], selection_id=selection["id"]),
            session,
            Settings(_env_file=None),
        )
    assert result["selection_id"] == selection["id"]
    assert result["input_characters"] == selection["input_characters"]
    assert len(calls) == 1


def test_dependency_schema_only_accepts_catalog_section_ids():
    from pydantic import ValidationError

    from app.brand.evidence_selection import section_review_schema

    schema = section_review_schema([{"id": "first"}], [{"id": "first"}, {"id": "other"}])
    payload = {"decisions": {"section_01": {"decision": "include", "reason": "Relevant",
               "evidence": [], "dependencies": ["other"]}},
               "coverage_complete": True, "limitations": []}
    assert schema.model_validate(payload).decisions.section_01.dependencies == ["other"]
    payload["decisions"]["section_01"]["dependencies"] = ["invented"]
    with pytest.raises(ValidationError):
        schema.model_validate(payload)


def test_retained_sections_use_original_passages_not_model_quotations(materials, section_provider):
    engine, brand, _, _ = materials
    retrieved = large_materials(materials)

    def paraphrased_inclusion(result):
        for choice in result["decisions"]:
            if choice["decision"] != "exclude" and choice["evidence"]:
                choice["evidence"][0]["quotation"] = "Invented summary ... not a quotation"
        return result

    client, _ = section_provider(paraphrased_inclusion)
    selection = select_evidence(engine, brand, retrieved["id"], client=client)
    assert all(not section["evidence"] for section in selection["sections"]
               if section["decision"] != "exclude")
    assert any(section["included"] for section in selection["sections"])


def test_assessment_api_enables_bounded_batches(monkeypatch):
    from types import SimpleNamespace

    from app.api.retrieval import AssessmentRequest, assess_context

    captured = {}
    def assess(*args, **kwargs):
        captured.update(kwargs)
        return {"id": "assessment"}

    monkeypatch.setattr("app.brand.assessment.assess_evidence", assess)
    response = assess_context("brand", AssessmentRequest(retrieval_id="retrieval", selection_id="selection"),
                             SimpleNamespace(get_bind=lambda: None), Settings(_env_file=None))
    assert response == {"id": "assessment"}
    assert captured["allow_batches"] is True
    assert captured["selection_id"] == "selection"
