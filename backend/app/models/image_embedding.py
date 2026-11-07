"""Brand-scoped, content-addressed image metadata vectors."""
from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ImageEmbedding(Base):
    __tablename__ = 'image_embeddings'
    brand_id: Mapped[str] = mapped_column(String, primary_key=True)
    image_id: Mapped[str] = mapped_column(String, primary_key=True)
    input_hash: Mapped[str] = mapped_column(String)
    vector: Mapped[list[float]] = mapped_column(JSON)


class ImageQueryEmbedding(Base):
    """Exact query vectors keyed by brand and model/dimension/text digest."""
    __tablename__ = 'image_query_embeddings'
    brand_id: Mapped[str] = mapped_column(String, primary_key=True)
    input_hash: Mapped[str] = mapped_column(String, primary_key=True)
    vector: Mapped[list[float]] = mapped_column(JSON)
