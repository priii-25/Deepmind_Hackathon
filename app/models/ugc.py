"""
UGC Video agent models.
"""

from sqlalchemy import String, Text, JSON, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class UGCConversation(TenantBase):
    __tablename__ = "ugc_conversations"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    brand_context: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
    conversation_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)


class UGCAsset(TenantBase):
    __tablename__ = "ugc_assets"

    conversation_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    asset_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # image, script, audio, video, lipsync
    s3_url: Mapped[str] = mapped_column(Text, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=True, default=0)
    asset_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)


class Avatar(TenantBase):
    __tablename__ = "avatars"

    name: Mapped[str] = mapped_column(String, nullable=False)
    image_url: Mapped[str] = mapped_column(String, nullable=True)
    voice_id: Mapped[str] = mapped_column(String, nullable=True)
    avatar_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
