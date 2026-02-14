"""
Agent catalog and assignments.
"""

from sqlalchemy import String, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class AgentCatalog(TenantBase):
    __tablename__ = "agent_catalog"

    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String, nullable=True)
    icon_url: Mapped[str] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    skills: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)
    tools_stack: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)
    work_samples: Mapped[dict] = mapped_column(JSON, nullable=True, default=list)


class AgentAssignment(TenantBase):
    __tablename__ = "agent_assignments"

    agent_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
