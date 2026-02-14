"""
Users and user preferences.
"""

from sqlalchemy import String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class User(TenantBase):
    __tablename__ = "users"

    auth0_user_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=True)


class UserPreferences(TenantBase):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    notification_frequency: Mapped[str] = mapped_column(
        String, nullable=True, default="daily"
    )
    notification_channels: Mapped[dict] = mapped_column(
        JSON, nullable=True, default=dict
    )
    preferences: Mapped[dict] = mapped_column(JSON, nullable=True, default=dict)
