import copy
import hashlib
import json

import httpx
import pytest
from openai import OpenAI
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_llm import response_body

from app.brand.assessment import EvidenceAnalysis, Statement, assess_evidence, resolve_conflicts
from app.brand.authority import (
    AuthorityMap,
    authority_status,
    confirm_authority,
    current_authority,
    propose_authority,
    save_version,
)
from app.brand.chunking import build_chunks
from app.brand.context import assemble_context, fingerprint, load_artifact, save_artifact
from app.brand.discovery import discover_folder
from app.brand.ingestion import ingest_documents
from app.brand.retrieval import RetrievalError, SearchHit, corpus_rows
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.llm.client import TrackedOpenAI, strict_output_schema
from app.models import Brand, ContextArtifact, Document, LLMRun


@pytest.fixture
def materials(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'authority.db'}")
    initialize_database(engine)
    root = tmp_path / "source"
    (root / "brand").mkdir(parents=True)
    bodies = {
        "governance": "# Governance\n\nOrder of authority: legal, guidelines, channels, then everything else.\n\nLegal sheet supersedes sales on certifications.\n",
        "legal": "# Certifications\n\nStatus: pursuing.\n\nNever claim authorized.\n",
        "sales": "# Sales\n\nStatus: authorized.\n",
        "channel": "# Channel\n\nLinkedIn limit is 200.\n",
        "voice": "# Voice\n\nLinkedIn limit is 300.\n",
        "notes": "# Notes\n\nAlways use one CTA.\n",
    }
    for name, text in bodies.items():
        (root / "brand" / f"{name}.md").write_text(text)
    brand = ingest_documents(engine, discover_folder(root), "Example").brand_id
    build_chunks(engine, brand)
    with Session(engine) as session:
        docs = {
            doc.relative_path.split("/")[-1].removesuffix(".md"): {
                "id": doc.id,
                "text": doc.raw_text,
            }
            for doc in session.scalars(select(Document))
        }
        other = Brand(name="Other", source_directory="/other")
        session.add(other)
        session.commit()
        other_id = other.id
    yield engine, brand, docs, other_id
    engine.dispose()


def quote(materials, name, text):
    doc = materials[2][name]
    offset = doc["text"].index(text)
    return {
        "document_id": doc["id"],
        "quotation": text,
        "start_line": doc["text"][:offset].count("\n") + 1,
        "end_line": doc["text"][: offset + len(text)].count("\n") + 1,
    }


def mapping(materials):
    ids = materials[2]
    evidence = [
        quote(
            materials,
            "governance",
            "Order of authority: legal, guidelines, channels, then everything else.",
        )
    ]

    def group(id, priority, names, governing=False):
        return {
            "id": id,
            "label": id,
            "priority": priority,
            "document_ids": [ids[name]["id"] for name in names],
            "topics": ["*"],
            "channels": ["*"],
            "governing": governing,
            "evidence": evidence,
        }

    return AuthorityMap.model_validate(
        {
            "groups": [
                group("legal", 1, ["legal"], True),
                group("guidelines", 2, ["governance"]),
                group("channel", 3, ["channel"], True),
                group("other", 4, ["sales", "voice", "notes"]),
            ],
            "supersessions": [],
            "notes": ["Quoted governance for human review"],
        }
    )


def statement(materials, identifier, name, quote_text, value, **updates):
    data = {
        "id": identifier,
        "subject": "Brand",
        "attribute": "FedRAMP.status",
        "value": value,
        "topic": "certification",
        "kind": "fact",
        "scope": {
            "channels": ["not_applicable"],
            "regions": ["not_applicable"],
            "effective_dates": ["not_applicable"],
            "billing_period": "not_applicable",
        },
        "evidence": [quote(materials, name, quote_text)],
    }
    data.update(updates)
    return Statement.model_validate(data)


def analysis(materials):
    return EvidenceAnalysis.model_validate(
        {
            "statements": [
                statement(
                    materials, "legal", "legal", "Status: pursuing.", "pursuing"
                ).model_dump(),
                statement(
                    materials, "sales", "sales", "Status: authorized.", "authorized"
                ).model_dump(),
                statement(
                    materials,
                    "ban",
                    "legal",
                    "Never claim authorized.",
                    "Never claim authorized.",
                    kind="prohibition",
                ).model_dump(),
            ],
            "conflicts": [
                {
                    "statement_ids": ["legal", "sales"],
                    "reason": "Incompatible current certification status",
                }
            ],
            "coverage_complete": True,
            "limitations": [],
        }
    )


def confirmed(materials):
    draft = save_version(materials[0], materials[1], mapping(materials))
    return confirm_authority(materials[0], materials[1], draft["id"])


def retrieval(materials):
    engine, brand, docs, _ = materials
    fp = fingerprint(engine, brand)
    plan = save_artifact(
        engine,
        brand,
        "plan",
        fp,
        {
            "brief": "Five LinkedIn posts about FedRAMP",
            "plan": {"analysis": {"channels": ["linkedin"], "unknowns": []}},
        },
    )
    with Session(engine) as session:
        chunk, doc = next(
            (chunk, doc)
            for chunk, doc in corpus_rows(session, brand)[0]
            if doc.id == docs["sales"]["id"]
        )
        hit = SearchHit(
            rank=1,
            score=1,
            chunk_id=chunk.id,
            document_id=doc.id,
            title=doc.title,
            relative_path=doc.relative_path,
            heading_path=chunk.heading_path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            content=chunk.content,
        ).model_dump()
    return save_artifact(
        engine,
        brand,
        "retrieval",
        fp,
        {
            "groups": [
                {
                    "query": {"id": "claims", "purpose": "claims"},
                    "status": "success",
                    "error": None,
                    "results": [hit],
                }
            ],
            "vector_fingerprint": "vectors",
        },
        plan["id"],
    )


@pytest.fixture
def provider(materials):
    clients = []

    def make(output):
        calls = []

        def handler(request):
            calls.append(json.loads(request.content))
            body = calls[-1]
            rendered = json.loads(json.dumps(output))
            if body.get("text", {}).get("format", {}).get("name") == "ReferencedAnalysis":
                passages = json.loads(body["input"])["passages"]
                for statement in rendered.get("statements", []):
                    references = []
                    for citation in statement["evidence"]:
                        matches = [p for p in passages if p["document_id"] == citation["document_id"]
                                   and citation["quotation"] in p["content"]
                                   and p["start_line"] <= citation["start_line"] <= citation["end_line"] <= p["end_line"]]
                        references.append(matches[0]["chunk_id"] if matches else "invalid-reference")
                    statement["evidence"] = references
            return httpx.Response(200, json=response_body(json.dumps(rendered)))

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


def test_drafts_require_confirmation_and_show_unmapped(materials):
    engine, brand, _, _ = materials
    draft = save_version(engine, brand, mapping(materials))
    assert authority_status(engine, brand)["active"] is None
    with pytest.raises(RetrievalError, match="confirmed authority"):
        current_authority(engine, brand)
    active = confirm_authority(engine, brand, draft["id"])
    assert active["current"] and active["status"] == "confirmed"
    assert current_authority(engine, brand)["id"] == draft["id"]
    assert strict_output_schema(AuthorityMap) and strict_output_schema(EvidenceAnalysis)


def test_new_confirmation_invalidates_old_analysis_but_keeps_retrieval(materials, provider):
    engine, brand, _, _ = materials
    confirmed(materials)
    retrieved = retrieval(materials)
    client, _ = provider(analysis(materials).model_dump())
    assessed = assess_evidence(engine, brand, retrieved["id"], client=client)
    newer = save_version(engine, brand, mapping(materials))
    confirm_authority(engine, brand, newer["id"])
    assert load_artifact(engine, brand, retrieved["id"], "retrieval")["id"] == retrieved["id"]
    with pytest.raises(RetrievalError):
        load_artifact(engine, brand, assessed["id"], "assessment")


def test_source_changes_make_map_stale(materials):
    engine, brand, docs, _ = materials
    confirmed(materials)
    with Session(engine) as session:
        doc = session.get(Document, docs["legal"]["id"])
        doc.title = "Changed"
        session.commit()
    assert not authority_status(engine, brand)["active"]["current"]
    with pytest.raises(RetrievalError):
        current_authority(engine, brand)


def test_cross_brand_confirmation_and_invalid_quotes_rejected(materials):
    engine, brand, _, other = materials
    draft = save_version(engine, brand, mapping(materials))
    with pytest.raises(RetrievalError):
        confirm_authority(engine, other, draft["id"])
    data = mapping(materials).model_dump()
    data["groups"][0]["evidence"][0]["quotation"] = "Invented authority"
    with pytest.raises(RetrievalError, match="does not match"):
        save_version(engine, brand, AuthorityMap.model_validate(data))


def test_proposal_is_audited_but_never_active(materials, provider):
    engine, brand, _, _ = materials
    client, calls = provider(mapping(materials).model_dump())
    draft = propose_authority(engine, brand, client=client)
    assert draft["status"] == "draft" and calls
    assert authority_status(engine, brand)["active"] is None
    with Session(engine) as session:
        assert session.get(LLMRun, draft["run_id"]).operation == "propose_authority"


def test_certification_legal_precedence_and_channel_rules(materials):
    spec = analysis(materials)
    result = resolve_conflicts(spec, mapping(materials))[0]
    assert result["status"] == "resolved" and result["selected_ids"] == ["legal"]
    channel = statement(
        materials,
        "channel",
        "channel",
        "LinkedIn limit is 200.",
        "200",
        subject="LinkedIn",
        attribute="post_limit",
        topic="channel",
    )
    voice = statement(
        materials,
        "voice",
        "voice",
        "LinkedIn limit is 300.",
        "300",
        subject="LinkedIn",
        attribute="post_limit",
        topic="channel",
    )
    channel.scope.channels = voice.scope.channels = ["linkedin"]
    spec.statements = [channel, voice]
    spec.conflicts[0].statement_ids = ["channel", "voice"]
    assert resolve_conflicts(spec, mapping(materials))[0]["selected_ids"] == ["channel"]


def test_same_level_unknown_scope_and_distinct_facts(materials):
    spec = analysis(materials)
    data = mapping(materials)
    data.groups[0].priority = 4
    assert resolve_conflicts(spec, data)[0]["status"] == "unresolved"
    spec.statements[0].scope.channels = None
    assert resolve_conflicts(spec, mapping(materials))[0]["status"] == "unresolved"
    spec.statements[0].subject = "Plan A"
    spec.statements[1].subject = "Plan B"
    assert resolve_conflicts(spec, mapping(materials))[0]["status"] == "not_comparable"
    spec.statements[1].subject = "Plan A"
    spec.statements[0].scope.channels = ["linkedin"]
    spec.statements[1].scope.channels = ["email"]
    assert resolve_conflicts(spec, mapping(materials))[0]["status"] == "not_comparable"


def test_explicit_supersession_and_cycles(materials):
    spec = analysis(materials)
    data = mapping(materials).model_dump()
    data["groups"][0]["priority"] = 4
    link = {
        "newer_id": materials[2]["legal"]["id"],
        "older_id": materials[2]["sales"]["id"],
        "topics": ["certification"],
        "channels": ["*"],
        "evidence": [
            quote(materials, "governance", "Legal sheet supersedes sales on certifications.")
        ],
    }
    data["supersessions"] = [link]
    assert resolve_conflicts(spec, AuthorityMap.model_validate(data))[0]["basis"] == [
        "explicit_supersession"
    ]
    reverse = copy.deepcopy(link)
    reverse["newer_id"], reverse["older_id"] = link["older_id"], link["newer_id"]
    data["supersessions"].append(reverse)
    with pytest.raises(ValueError, match="cycles"):
        AuthorityMap.model_validate(data)


def test_governing_evidence_added_and_rejected_copy_excluded(materials, provider):
    engine, brand, docs, _ = materials
    confirmed(materials)
    retrieved = retrieval(materials)
    client, calls = provider(analysis(materials).model_dump())
    assessed = assess_evidence(engine, brand, retrieved["id"], client=client)
    supplied = json.loads(calls[0]["input"])["passages"]
    assert docs["legal"]["id"] in {hit["document_id"] for hit in supplied}
    assert docs["legal"]["id"] in assessed["governing_documents_added"]
    assert next(item for item in assessed["statements"] if item["id"] == "sales")["rejected"]
    compact = assemble_context(
        engine,
        brand,
        retrieved["id"],
        assessment_id=assessed["id"],
        token_counter=lambda text: len(text) // 4,
    )
    assert compact["authority_resolved"]
    assert "Status: pursuing." in compact["context_text"]
    assert "Status: authorized." not in compact["context_text"]
    assert "Never claim authorized." in compact["context_text"]
    assert compact["rejected_statements"][0]["id"] == "sales"


def test_unresolved_alternatives_are_preserved_and_budgeted(materials, provider):
    engine, brand, _, _ = materials
    data = mapping(materials)
    data.groups[0].priority = 4
    draft = save_version(engine, brand, data)
    confirm_authority(engine, brand, draft["id"])
    retrieved = retrieval(materials)
    client, _ = provider(analysis(materials).model_dump())
    assessed = assess_evidence(engine, brand, retrieved["id"], client=client)
    compact = assemble_context(
        engine, brand, retrieved["id"], assessment_id=assessed["id"], token_counter=lambda _: 100
    )
    assert not compact["authority_resolved"]
    assert "Status: authorized." in compact["context_text"]

    def huge(text):
        return 1100 if "Status: authorized." in text else 100

    with pytest.raises(RetrievalError, match="Essential"):
        assemble_context(
            engine,
            brand,
            retrieved["id"],
            assessment_id=assessed["id"],
            max_tokens=1000,
            token_counter=huge,
        )


def test_unconfirmed_and_invalid_citations_do_not_produce_assessment(materials, provider):
    engine, brand, _, _ = materials
    retrieved = retrieval(materials)
    client, calls = provider(analysis(materials).model_dump())
    with pytest.raises(RetrievalError):
        assess_evidence(engine, brand, retrieved["id"], client=client)
    assert not calls
    confirmed(materials)
    bad = analysis(materials).model_dump()
    bad["statements"][0]["evidence"][0]["quotation"] = "Invented statement"
    client, _ = provider(bad)
    with pytest.raises(RetrievalError, match="assess_evidence failed"):
        assess_evidence(engine, brand, retrieved["id"], client=client)
    with Session(engine) as session:
        assert (
            session.scalar(select(ContextArtifact).where(ContextArtifact.kind == "assessment"))
            is None
        )


def test_large_governing_evidence_reaches_model_without_character_cap(materials, provider):
    engine, brand, docs, _ = materials
    with Session(engine) as session, session.begin():
        doc = session.get(Document, docs["legal"]["id"])
        doc.raw_text += "\n\nAdditional governing policy details. " * 4000
        doc.content_hash = hashlib.sha256(doc.raw_text.encode()).hexdigest()
    build_chunks(engine, brand)
    confirmed(materials)
    retrieved = retrieval(materials)
    client, calls = provider(analysis(materials).model_dump())
    assessed = assess_evidence(engine, brand, retrieved["id"], client=client)
    assert assessed["input_characters"] > 100_000
    assert calls


def test_incomplete_analysis_cannot_be_assembled(materials, provider):
    engine, brand, _, _ = materials
    confirmed(materials)
    retrieved = retrieval(materials)
    data = analysis(materials).model_dump()
    data["coverage_complete"] = False
    data["limitations"] = ["Output cannot cover all relevant restrictions"]
    client, _ = provider(data)
    assessed = assess_evidence(engine, brand, retrieved["id"], client=client)
    with pytest.raises(RetrievalError, match="incomplete evidence coverage"):
        assemble_context(
            engine,
            brand,
            retrieved["id"],
            assessment_id=assessed["id"],
            token_counter=lambda _: 100,
        )


def test_nonconflicting_lower_source_survives_reviewed_assembly(materials, provider):
    engine, brand, docs, _ = materials
    confirmed(materials)
    retrieved = retrieval(materials)
    with Session(engine) as session:
        chunk, doc = next(
            (chunk, doc)
            for chunk, doc in corpus_rows(session, brand)[0]
            if doc.id == docs["notes"]["id"]
        )
        hit = SearchHit(
            rank=2,
            score=0.5,
            chunk_id=chunk.id,
            document_id=doc.id,
            title=doc.title,
            relative_path=doc.relative_path,
            heading_path=chunk.heading_path,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            content=chunk.content,
        ).model_dump()
        artifact = session.get(ContextArtifact, retrieved["id"])
        payload = copy.deepcopy(artifact.payload)
        payload["groups"][0]["results"].append(hit)
        artifact.payload = payload
        session.commit()
    data = analysis(materials).model_dump()
    data["statements"].append(
        statement(
            materials,
            "cta",
            "notes",
            "Always use one CTA.",
            "one",
            attribute="cta_count",
            topic="channel",
            kind="required_wording",
        ).model_dump()
    )
    client, _ = provider(data)
    assessed = assess_evidence(engine, brand, retrieved["id"], client=client)
    compact = assemble_context(
        engine, brand, retrieved["id"], assessment_id=assessed["id"], token_counter=lambda _: 100
    )
    assert "Always use one CTA." in compact["context_text"]


def test_unmapped_source_and_temporal_ambiguity_cannot_win(materials):
    spec = analysis(materials)
    data = mapping(materials)
    data.groups[-1].document_ids.remove(materials[2]["sales"]["id"])
    assert resolve_conflicts(spec, data)[0]["status"] == "unresolved"
    spec.statements[0].scope.effective_dates = ["2026"]
    spec.statements[1].scope.effective_dates = ["2025 onward"]
    assert resolve_conflicts(spec, mapping(materials))[0]["status"] == "unresolved"


def test_document_manifest_additions_require_reconfirmation(materials):
    engine, brand, _, _ = materials
    confirmed(materials)
    with Session(engine) as session:
        session.add(
            Document(
                brand_id=brand,
                relative_path="brand/new.md",
                title="New",
                content_hash="newhash",
                raw_text="New source",
            )
        )
        session.commit()
    assert not authority_status(engine, brand)["active"]["current"]


def test_stale_confirmation_and_cross_brand_assessment_ids_rejected(materials, provider):
    engine, brand, docs, other = materials
    draft = save_version(engine, brand, mapping(materials))
    with Session(engine) as session:
        doc = session.get(Document, docs["sales"]["id"])
        doc.title = "Changed title"
        session.commit()
    with pytest.raises(RetrievalError):
        confirm_authority(engine, brand, draft["id"])
    fresh = save_version(engine, brand, mapping(materials))
    confirm_authority(engine, brand, fresh["id"])
    retrieved = retrieval(materials)
    client, calls = provider(analysis(materials).model_dump())
    with pytest.raises(RetrievalError):
        assess_evidence(engine, other, retrieved["id"], client=client)
    assert not calls


def legacy_saved_run(materials, provider, alter=None):
    from app.brand.authority import call_model

    engine, brand, docs, _ = materials
    with Session(engine) as session:
        manifest = [
            {"id": doc.id, "path": doc.relative_path, "title": doc.title}
            for doc in session.scalars(select(Document).where(Document.brand_id == brand))
        ]
    data = {
        "documents": manifest,
        "governance_candidates": [
            {
                "document_id": docs["governance"]["id"],
                "lines": "\n".join(
                    f"{i + 1}: {line}"
                    for i, line in enumerate(docs["governance"]["text"].splitlines())
                ),
            }
        ],
    }
    proposal = mapping(materials).model_dump()
    for group in proposal["groups"]:
        for citation in group["evidence"]:
            citation["quotation"] = f"{citation['start_line']}: {citation['quotation']}"
    if alter:
        alter(proposal)
    client, calls = provider(proposal)
    result = call_model(
        engine, brand, "propose_authority", "Fixture", data, AuthorityMap, client=client
    )
    return result.run_id, calls


def test_legacy_prefix_replay_is_exact_audited_and_free(materials, provider, monkeypatch):
    from app.brand import authority

    run_id, calls = legacy_saved_run(materials, provider)
    with Session(materials[0]) as session:
        original = copy.deepcopy(session.get(LLMRun, run_id).response_body)
    monkeypatch.setattr(
        authority, "TrackedOpenAI", lambda *_: pytest.fail("Replay constructed an API client")
    )
    result = authority.replay_authority(materials[0], materials[1], run_id)
    assert (
        result["status"] == "draft"
        and authority_status(materials[0], materials[1])["active"] is None
    )
    assert result["map"]["groups"][0]["evidence"][0]["quotation"].startswith("Order of authority:")
    assert any("presentation adjustment" in note for note in result["map"]["notes"])
    assert authority.replay_authority(materials[0], materials[1], run_id)["id"] == result["id"]
    with Session(materials[0]) as session:
        assert session.get(LLMRun, run_id).response_body == original
    assert len(calls) == 1


@pytest.mark.parametrize(
    "alter",
    [
        lambda data: data["groups"][0]["evidence"][0].update(quotation="3: invented words"),
        lambda data: data["groups"][0]["evidence"][0].update(start_line=4, end_line=4),
    ],
)
def test_replay_never_repairs_words_or_wrong_lines(materials, provider, alter):
    from app.brand.authority import replay_authority

    run_id, _ = legacy_saved_run(materials, provider, alter)
    with pytest.raises(RetrievalError):
        replay_authority(materials[0], materials[1], run_id)


def test_replay_rejects_changed_governance(materials, provider):
    from app.brand.authority import replay_authority

    run_id, _ = legacy_saved_run(materials, provider)
    with Session(materials[0]) as session:
        doc = session.get(Document, materials[2]["governance"]["id"])
        doc.raw_text = doc.raw_text.replace("legal, guidelines", "guidelines, legal")
        doc.content_hash = hashlib.sha256(doc.raw_text.encode()).hexdigest()
        session.commit()
    with pytest.raises(RetrievalError, match="Governance source changed"):
        replay_authority(materials[0], materials[1], run_id)
