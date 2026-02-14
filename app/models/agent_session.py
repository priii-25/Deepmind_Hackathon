"""
Agent session persistence.

Stores per-agent workflow state so multi-turn agents (like Vera)
can resume from where they left off across messages.
"""

from sqlalchemy import String, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column

from .base import TenantBase


class AgentSession(TenantBase):
    __tablename__ = "agent_sessions"

    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    agent_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Full agent state dict (step, phase, extracted fields, vera_messages, etc.)
    session_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    current_step: Mapped[str] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
