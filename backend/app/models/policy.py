from uuid import uuid4

from sqlalchemy import JSON, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PolicyExtraction(Base):
    __tablename__ = "policy_extractions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), unique=True)
    source_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String)
    extractor_version: Mapped[str] = mapped_column(String)
    prompt_version: Mapped[str] = mapped_column(String)
    schema_hash: Mapped[str] = mapped_column(String(64))
    output_limit: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.id"))
    candidates: Mapped[list[dict]] = mapped_column(JSON)
