"""Read-only inspection of stored brand sources and their derived evidence."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.brand.chunking import CHUNKER_VERSION
from app.brand.policy_schema import GroundedCandidate, extraction_current
from app.brand.source_metadata import EXTRACTOR_VERSION, MetadataCandidate
from app.db import get_session
from app.models import Brand, Document, DocumentChunk, PolicyExtraction, SourceAnnotation

router = APIRouter()
Database = Annotated[Session, Depends(get_session)]
DerivedState = Literal["missing", "current", "stale"]


class BrandSummary(BaseModel):
    id: str
    name: str
    document_count: int


class DocumentSummary(BaseModel):
    id: str
    title: str | None
    relative_path: str
    chunk_count: int
    chunk_state: DerivedState
    metadata_state: DerivedState


class ChunkView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    position: int
    heading_path: list[str]
    content: str
    start_offset: int
    end_offset: int
    start_line: int
    end_line: int
    source_hash: str
    chunker_version: str
    target_chars: int
    oversized: bool
    state: DerivedState


class AnnotationView(BaseModel):
    id: str
    source_hash: str
    extractor_version: str
    candidates: list[MetadataCandidate]


class PolicyView(BaseModel):
    id: str
    model: str
    run_id: str
    source_hash: str
    extractor_version: str
    prompt_version: str
    schema_hash: str
    output_limit: int
    candidates: list[GroundedCandidate]


class DocumentDetail(DocumentSummary):
    brand_id: str
    raw_text: str
    content_hash: str
    chunks: list[ChunkView]
    annotation: AnnotationView | None
    policy_state: DerivedState
    policy_extraction: PolicyView | None


def require_brand(session: Session, brand_id: str) -> None:
    if session.get(Brand, brand_id) is None:
        raise HTTPException(404, "Brand not found")


def annotation_state(annotation: SourceAnnotation | None, source_hash: str) -> DerivedState:
    if annotation is None:
        return "missing"
    return (
        "current"
        if annotation.source_hash == source_hash
        and annotation.extractor_version == EXTRACTOR_VERSION
        else "stale"
    )


@router.get("/brands", response_model=list[BrandSummary])
def list_brands(session: Database):
    rows = session.execute(
        select(Brand.id, Brand.name, func.count(Document.id))
        .outerjoin(Document, Document.brand_id == Brand.id)
        .group_by(Brand.id, Brand.name)
        .order_by(Brand.name, Brand.id)
    )
    return [
        BrandSummary(id=identifier, name=name, document_count=count)
        for identifier, name, count in rows
    ]


@router.get("/brands/{brand_id}/documents", response_model=list[DocumentSummary])
def list_documents(brand_id: str, session: Database):
    require_brand(session, brand_id)
    counts = dict(
        session.execute(
            select(DocumentChunk.document_id, func.count(DocumentChunk.id))
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(Document.brand_id == brand_id)
            .group_by(DocumentChunk.document_id)
        ).all()
    )
    stale_ids = set(
        session.scalars(
            select(DocumentChunk.document_id)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.brand_id == brand_id,
                (DocumentChunk.source_hash != Document.content_hash)
                | (DocumentChunk.chunker_version != CHUNKER_VERSION),
            )
        )
    )
    annotations = {
        a.document_id: a
        for a in session.scalars(
            select(SourceAnnotation)
            .join(Document, Document.id == SourceAnnotation.document_id)
            .where(Document.brand_id == brand_id)
        )
    }
    return [
        DocumentSummary(
            id=document.id,
            title=document.title,
            relative_path=document.relative_path,
            chunk_count=counts.get(document.id, 0),
            chunk_state="missing"
            if not counts.get(document.id)
            else "stale"
            if document.id in stale_ids
            else "current",
            metadata_state=annotation_state(annotations.get(document.id), document.content_hash),
        )
        for document in session.scalars(
            select(Document).where(Document.brand_id == brand_id).order_by(Document.relative_path)
        )
    ]


@router.get("/brands/{brand_id}/documents/{document_id}", response_model=DocumentDetail)
def get_document(brand_id: str, document_id: str, session: Database):
    require_brand(session, brand_id)
    document = session.scalar(
        select(Document).where(Document.id == document_id, Document.brand_id == brand_id)
    )
    if document is None:
        raise HTTPException(404, "Document not found")
    chunks = []
    for chunk in session.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document.id)
        .order_by(DocumentChunk.position)
    ):
        state = (
            "current"
            if chunk.source_hash == document.content_hash
            and chunk.chunker_version == CHUNKER_VERSION
            else "stale"
        )
        chunks.append(
            ChunkView.model_validate(
                {
                    **{
                        field: getattr(chunk, field)
                        for field in ChunkView.model_fields
                        if field != "state"
                    },
                    "state": state,
                }
            )
        )
    annotation = session.scalar(
        select(SourceAnnotation).where(SourceAnnotation.document_id == document.id)
    )
    policy = session.scalar(
        select(PolicyExtraction).where(PolicyExtraction.document_id == document.id)
    )
    return DocumentDetail(
        id=document.id,
        brand_id=brand_id,
        title=document.title,
        relative_path=document.relative_path,
        content_hash=document.content_hash,
        raw_text=document.raw_text,
        chunk_count=len(chunks),
        chunk_state="missing"
        if not chunks
        else "stale"
        if any(c.state == "stale" for c in chunks)
        else "current",
        metadata_state=annotation_state(annotation, document.content_hash),
        policy_state="missing"
        if policy is None
        else "current"
        if extraction_current(policy, document.content_hash)
        else "stale",
        policy_extraction=None
        if policy is None
        else PolicyView(
            id=policy.id,
            model=policy.model,
            run_id=policy.run_id,
            source_hash=policy.source_hash,
            extractor_version=policy.extractor_version,
            prompt_version=policy.prompt_version,
            schema_hash=policy.schema_hash,
            output_limit=policy.output_limit,
            candidates=[
                GroundedCandidate.model_validate(candidate) for candidate in policy.candidates
            ],
        ),
        chunks=chunks,
        annotation=None
        if annotation is None
        else AnnotationView(
            id=annotation.id,
            source_hash=annotation.source_hash,
            extractor_version=annotation.extractor_version,
            candidates=[MetadataCandidate.model_validate(c) for c in annotation.candidates],
        ),
    )
