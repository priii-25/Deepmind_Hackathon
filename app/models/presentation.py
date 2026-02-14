"""
Presentation agent models.
"""

from sqlalchemy import String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class Presentation(TenantBase):
    __tablename__ = "presentations"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=True, index=True)
    title: Mapped[str] = mapped_column(String, nullable=True)
    s3_url: Mapped[str] = mapped_column(Text, nullable=True)
    slidespeak_task_id: Mapped[str] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default="pending"
    )  # pending, generating, completed, failed
    brand_settings: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
    # logo_url, primary_color, secondary_color, font
    presentation_metadata: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
