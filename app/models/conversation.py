"""
Conversations and messages. Used by Eve and all agents.
State is stored as a JSON column — agents read/write their own state here.
"""

from sqlalchemy import String, Text, Integer, JSON, ForeignKey
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import TenantBase


class Conversation(TenantBase):
    __tablename__ = "conversations"

    session_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=True)

    # Orchestrator state — which agent is active, what stage, accumulated data
    # MutableDict enables SQLAlchemy to detect in-place dict mutations (e.g.
    # state["active_agent"] = "fashion_photo") so they are flushed to the DB.
    state: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), nullable=True, default=dict)
    # Example:
    # {
    #   "active_agent": "ugc_video",
    #   "agent_state": {
    #     "stage": "script_generation",
    #     "selected_avatars": [1, 3],
    #     "uploaded_images": ["s3://..."],
    #   }
    # }

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.sequence_number",
    )


class Message(TenantBase):
    __tablename__ = "messages"

    conversation_id: Mapped[str] = mapped_column(
        String, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False)  # user, assistant, system, tool
    content: Mapped[str] = mapped_column(Text, nullable=True)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSON, nullable=True, default=dict
    )
    # Stores: tool_calls, tool_results, media_urls, agent_name, etc.

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
