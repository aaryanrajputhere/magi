"""Vectors remain attached to the exact chunk input that produced them."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (UniqueConstraint("chunk_id", "model", "dimensions", "input_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String)
    dimensions: Mapped[int] = mapped_column(Integer)
    input_version: Mapped[str] = mapped_column(String)
    source_hash: Mapped[str] = mapped_column(String(64))
    chunker_version: Mapped[str] = mapped_column(String)
    input_hash: Mapped[str] = mapped_column(String(64))
    vector: Mapped[list[float]] = mapped_column(JSON)
    run_id: Mapped[str] = mapped_column(ForeignKey("llm_runs.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
