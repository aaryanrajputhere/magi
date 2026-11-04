from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class KeywordIndexState(Base):
    __tablename__ = "keyword_index_states"
    brand_id: Mapped[str] = mapped_column(ForeignKey("brands.id"), primary_key=True)
    version: Mapped[str] = mapped_column(String)
    fingerprint: Mapped[str] = mapped_column(String(64))
    chunk_count: Mapped[int] = mapped_column(Integer)
    built_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
