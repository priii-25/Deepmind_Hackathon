"""
Social Media agent models.

Supports YouTube OAuth tokens and post tracking.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, JSON, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class SocialToken(TenantBase):
    """
    OAuth tokens for social media platforms.

    Stores access_token, refresh_token, and expiry for each platform connection.
    Currently supports: youtube (Google OAuth 2.0)
    """
    __tablename__ = "social_tokens"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)  # youtube, tiktok, facebook
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    platform_user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # YouTube channel ID
    token_metadata: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, default=dict
    )  # channel_title, channel_id, scopes, etc.

    __table_args__ = (
        Index("ix_social_tokens_user_platform", "tenant_id", "user_id", "platform", unique=True),
    )


class SocialPost(TenantBase):
    """
    Record of posts/uploads to social platforms.

    Tracks every video upload to YouTube with metadata for history and analytics.
    """
    __tablename__ = "social_posts"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)  # youtube, tiktok, facebook
    platform_post_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # YouTube video ID
    content_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # YouTube video URL
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Video title
    hashtags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # Comma-separated tags
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft"
    )  # draft, uploading, posted, failed, processing
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    post_metadata: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, default=dict
    )  # description, privacy, category_id, upload_status, processing_status
