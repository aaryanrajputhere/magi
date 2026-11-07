"""Inspect keyword and semantic retrieval; explicit POSTs may embed queries."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.brand.authority import AuthorityMap
from app.brand.hybrid import hybrid_readiness, hybrid_search
from app.brand.retrieval import RetrievalError, build_index, index_status, search_chunks
from app.brand.retrieval_eval import evaluate_suite, registered_suite
from app.brand.semantic import semantic_search, vector_snapshot
from app.config import Settings, get_settings
from app.db import get_session

router = APIRouter()
Database = Annotated[Session, Depends(get_session)]
Configuration = Annotated[Settings, Depends(get_settings)]


def run(operation):
    try:
        return operation()
    except RetrievalError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(
            503,
            {
                "code": "storage_unavailable",
                "message": "Retrieval storage is unavailable. Check database access and refresh retrieval readiness.",
            },
        ) from exc


@router.get("/brands/{brand_id}/retrieval/index")
def get_index(brand_id: str, session: Database):
    return run(lambda: index_status(session, brand_id))


@router.post("/brands/{brand_id}/retrieval/index")
def prepare_index(brand_id: str, session: Database):
    return run(lambda: build_index(session.get_bind(), brand_id))


@router.get("/brands/{brand_id}/search")
def search(brand_id: str, session: Database, q: str, limit: int = 10):
    return run(lambda: search_chunks(session, brand_id, q, limit))


@router.get("/retrieval/suites")
def suites(settings: Configuration):
    def collect():
        return [
            {
                "id": suite.id,
                "name": suite.name,
                "description": suite.description,
                "case_count": len(suite.cases),
            }
            for path in sorted(settings.magi_retrieval_evaluation_dir.glob("*.json"))
            for suite in [registered_suite(settings.magi_retrieval_evaluation_dir, path.stem)]
        ]

    return run(collect)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=10, ge=1, le=50)
    method: Literal["keyword", "semantic", "hybrid"] = "keyword"


@router.get("/brands/{brand_id}/retrieval/embeddings")
def embedding_status(brand_id: str, session: Database):
    return run(lambda: vector_snapshot(session.get_bind(), brand_id).status)


@router.get("/brands/{brand_id}/retrieval/hybrid")
def hybrid_status(brand_id: str, session: Database):
    return run(lambda: hybrid_readiness(session.get_bind(), brand_id)[0])


@router.post("/brands/{brand_id}/search")
def search_post(brand_id: str, body: SearchRequest, session: Database, settings: Configuration):
    return run(
        lambda: (
            hybrid_search(session.get_bind(), brand_id, body.query, body.limit, settings=settings)
            if body.method == "hybrid"
            else semantic_search(
                session.get_bind(), brand_id, body.query, body.limit, settings=settings
            )
            if body.method == "semantic"
            else search_chunks(session, brand_id, body.query, body.limit)
        )
    )


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suite_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    method: Literal["keyword", "semantic", "hybrid"] = "keyword"


@router.post("/brands/{brand_id}/retrieval/evaluate")
def evaluate(brand_id: str, body: EvaluationRequest, session: Database, settings: Configuration):
    return run(
        lambda: evaluate_suite(
            session,
            brand_id,
            registered_suite(settings.magi_retrieval_evaluation_dir, body.suite_id),
            method=body.method,
            settings=settings,
        )
    )


class BriefRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief: str = Field(min_length=1, max_length=6000)


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(min_length=1, max_length=36)


class AssembleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    retrieval_id: str = Field(min_length=1, max_length=36)
    max_tokens: int = Field(default=4000, ge=1000, le=12000)
    assessment_id: str | None = Field(default=None, min_length=1, max_length=36)


@router.post("/brands/{brand_id}/context/plan")
def plan_context(brand_id: str, body: BriefRequest, session: Database, settings: Configuration):
    from app.brand.context import plan_retrieval

    return run(lambda: plan_retrieval(session.get_bind(), brand_id, body.brief, settings=settings))


@router.post("/brands/{brand_id}/context/retrieve")
def retrieve_context(
    brand_id: str, body: ExecuteRequest, session: Database, settings: Configuration
):
    from app.brand.context import execute_retrieval

    return run(
        lambda: execute_retrieval(session.get_bind(), brand_id, body.plan_id, settings=settings)
    )


@router.post("/brands/{brand_id}/context/assemble")
def compact_context(brand_id: str, body: AssembleRequest, session: Database):
    from app.brand.context import assemble_context

    return run(
        lambda: assemble_context(
            session.get_bind(),
            brand_id,
            body.retrieval_id,
            max_tokens=body.max_tokens,
            assessment_id=body.assessment_id,
        )
    )


class AuthorityConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version_id: str = Field(min_length=1, max_length=36)


class SelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retrieval_id: str = Field(min_length=1, max_length=36)


class AssessmentRequest(SelectionRequest):
    selection_id: str | None = Field(default=None, min_length=1, max_length=36)
    include_section_ids: list[str] = Field(default_factory=list, max_length=1000)


@router.post("/brands/{brand_id}/context/select")
def select_context_evidence(
    brand_id: str, body: SelectionRequest, session: Database, settings: Configuration
):
    from app.brand.evidence_selection import select_evidence

    return run(
        lambda: select_evidence(session.get_bind(), brand_id, body.retrieval_id, settings=settings)
    )


@router.get("/brands/{brand_id}/authority")
def get_authority(brand_id: str, session: Database):
    from app.brand.authority import authority_status

    return run(lambda: authority_status(session.get_bind(), brand_id))


@router.post("/brands/{brand_id}/authority/propose")
def propose_map(brand_id: str, session: Database, settings: Configuration):
    from app.brand.authority import propose_authority

    return run(lambda: propose_authority(session.get_bind(), brand_id, settings=settings))


@router.post("/brands/{brand_id}/authority/drafts")
def save_map(brand_id: str, body: AuthorityMap, session: Database):
    from app.brand.authority import save_version

    return run(lambda: save_version(session.get_bind(), brand_id, body))


@router.post("/brands/{brand_id}/authority/confirm")
def confirm_map(brand_id: str, body: AuthorityConfirmation, session: Database):
    from app.brand.authority import confirm_authority

    return run(lambda: confirm_authority(session.get_bind(), brand_id, body.version_id))


@router.post("/brands/{brand_id}/context/assess")
def assess_context(
    brand_id: str, body: AssessmentRequest, session: Database, settings: Configuration
):
    from app.brand.assessment import assess_evidence

    return run(
        lambda: assess_evidence(
            session.get_bind(),
            brand_id,
            body.retrieval_id,
            settings=settings,
            selection_id=body.selection_id,
            allow_batches=True,
            include_section_ids=body.include_section_ids,
        )
    )


@router.get('/brands/{brand_id}/guidelines')
def get_guidelines(brand_id: str, session: Database):
    from app.brand.guidelines import load_guidelines
    return run(lambda: load_guidelines(session.get_bind(), brand_id))


@router.post('/brands/{brand_id}/guidelines/extract')
def prepare_guidelines(brand_id: str, session: Database, settings: Configuration):
    from app.brand.guidelines import extract_guidelines
    from app.llm.client import TrackedOpenAI
    client = TrackedOpenAI(session.get_bind(), settings)
    try:
        return run(lambda: extract_guidelines(session.get_bind(), brand_id, client, settings))
    finally:
        client.close()
