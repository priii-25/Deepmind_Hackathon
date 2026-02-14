"""
Social Media agent models.
"""

from datetime import datetime

from sqlalchemy import String, Text, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class SocialToken(TenantBase):
    __tablename__ = "social_tokens"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String, nullable=False)  # tiktok, facebook
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    platform_user_id: Mapped[str] = mapped_column(String, nullable=True)
    token_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)


class SocialPost(TenantBase):
    __tablename__ = "social_posts"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String, nullable=False)
    platform_post_id: Mapped[str] = mapped_column(String, nullable=True)
    content_url: Mapped[str] = mapped_column(Text, nullable=True)
    caption: Mapped[str] = mapped_column(Text, nullable=True)
    hashtags: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="draft"
    )  # draft, posting, posted, failed
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    post_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
