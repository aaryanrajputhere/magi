from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LLMRun(Base):
    __tablename__ = "llm_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    operation: Mapped[str] = mapped_column(String, index=True)
    context: Mapped[dict] = mapped_column(JSON)
    requested_model: Mapped[str] = mapped_column(String)
    returned_model: Mapped[str | None] = mapped_column(String)
    request_body: Mapped[dict] = mapped_column(JSON)
    response_body: Mapped[dict | None] = mapped_column(JSON)
    response_id: Mapped[str | None] = mapped_column(String)
    request_id: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    timeout_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    status: Mapped[str] = mapped_column(String, default="started", index=True)
    error: Mapped[dict | None] = mapped_column(JSON)
    raw_usage: Mapped[dict | None] = mapped_column(JSON)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    cache_write_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    cache_read_ratio: Mapped[Decimal | None] = mapped_column(Numeric(16, 12))
    pricing_snapshot: Mapped[dict | None] = mapped_column(JSON)
    cost_status: Mapped[str] = mapped_column(String, default="unknown")
    cost_reason: Mapped[str | None] = mapped_column(Text)
