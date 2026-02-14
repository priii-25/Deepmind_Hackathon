"""
User memory persistence.

Stores key facts about users that persist across sessions.
Categories: style_preference, product_info, personal_fact, project_context, agent_feedback
"""

from sqlalchemy import String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class UserMemory(TenantBase):
    __tablename__ = "user_memories"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    category: Mapped[str] = mapped_column(String, nullable=False, index=True)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False, default="extracted")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
