"""
Onboarding state and user integrations.
"""

from sqlalchemy import String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class OnboardingState(TenantBase):
    __tablename__ = "onboarding_states"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    current_stage: Mapped[str] = mapped_column(
        String, nullable=False, default="brand_discovery"
    )
    # Stages: brand_discovery, suggested_teammates, connect_world, personalization, completed
    conversation_id: Mapped[str] = mapped_column(String, nullable=True, unique=True)
    brand_domain: Mapped[str] = mapped_column(String, nullable=True)
    selected_teammates: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)
    connected_integrations: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)
    notification_preferences: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
    extra_data: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)


class UserIntegration(TenantBase):
    __tablename__ = "user_integrations"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    integration_type: Mapped[str] = mapped_column(
        String, nullable=False
    )  # slack, google_drive, notion, github, etc.
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="disconnected"
    )  # connected, disconnected
    extra_data: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)


class BrandRecord(TenantBase):
    __tablename__ = "brand_records"

    domain: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    icon_url: Mapped[str] = mapped_column(String, nullable=True)
    industry: Mapped[str] = mapped_column(String, nullable=True)
    tone_of_voice: Mapped[str] = mapped_column(String, nullable=True)
    # Contact & location (from OG brandfetch)
    contact_email: Mapped[str] = mapped_column(String, nullable=True)
    contact_phone: Mapped[str] = mapped_column(String, nullable=True)
    contact_address: Mapped[str] = mapped_column(String, nullable=True)
    region: Mapped[str] = mapped_column(String, nullable=True)
    language: Mapped[str] = mapped_column(String, nullable=True)
    # Rich brand assets
    colors: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)
    fonts: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)
    social_links: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
    raw_data: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
