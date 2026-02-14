"""
Documents — full text stored directly. No chunks, no embeddings.
Search via PostgreSQL tsvector + pg_trgm.
"""

from sqlalchemy import String, Text, BigInteger, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class Document(TenantBase):
    __tablename__ = "documents"

    title: Mapped[str] = mapped_column(String, nullable=True)
    filename: Mapped[str] = mapped_column(String, nullable=True)
    full_text: Mapped[str] = mapped_column(Text, nullable=True)
    s3_url: Mapped[str] = mapped_column(Text, nullable=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="uploaded"
    )  # uploaded, processing, completed, failed
    doc_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
    # mime_type, page_count, used_ocr, etc.
