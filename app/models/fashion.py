"""
Fashion Photo agent models.
"""

from sqlalchemy import String, Text, JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class FashionSession(TenantBase):
    __tablename__ = "fashion_sessions"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=True, default=dict)


class FashionImage(TenantBase):
    __tablename__ = "fashion_images"

    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    s3_url: Mapped[str] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=True)
    scene_description: Mapped[str] = mapped_column(Text, nullable=True)
    angle: Mapped[str] = mapped_column(String, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    image_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)


class Apparel(TenantBase):
    __tablename__ = "apparels"

    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=True)
    s3_url: Mapped[str] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
