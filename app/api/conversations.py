"""
Conversations API.

GET    /v1/conversations              — List user conversations
GET    /v1/conversations/{session_id} — Get conversation with messages
DELETE /v1/conversations/{session_id} — Delete a conversation
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.auth import AuthenticatedUser
from ..core.dependencies import require_tenant, get_db
from ..models.conversation import Conversation, Message

logger = logging.getLogger(__name__)

conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])


class ConversationSummary(BaseModel):
    id: str
    session_id: str
    title: Optional[str] = None
    active_agent: Optional[str] = None
    created_at: str
    updated_at: str


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    sequence_number: int
    metadata: dict = {}
    created_at: str


class ConversationDetail(BaseModel):
    id: str
    session_id: str
    active_agent: Optional[str] = None
    messages: list[MessageOut] = []


@conversations_router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    """List conversations for the current user."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.tenant_id == user.tenant_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    convos = result.scalars().all()

    return [
        ConversationSummary(
            id=str(c.id),
            session_id=c.session_id,
            title=c.title,
            active_agent=(c.state or {}).get("active_agent"),
            created_at=c.created_at.isoformat() if c.created_at else "",
            updated_at=c.updated_at.isoformat() if c.updated_at else "",
        )
        for c in convos
    ]


@conversations_router.get("/{session_id}", response_model=ConversationDetail)
async def get_conversation(
    session_id: str,
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Get a conversation with its messages."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.tenant_id == user.tenant_id,
            Conversation.session_id == session_id,
        )
    )
    convo = result.scalar_one_or_none()

    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get messages
    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == convo.id)
        .order_by(Message.sequence_number.asc())
    )
    messages = msg_result.scalars().all()

    return ConversationDetail(
        id=str(convo.id),
        session_id=convo.session_id,
        active_agent=(convo.state or {}).get("active_agent"),
        messages=[
            MessageOut(
                id=str(m.id),
                role=m.role,
                content=m.content,
                sequence_number=m.sequence_number,
                metadata=m.metadata_ or {},
                created_at=m.created_at.isoformat() if m.created_at else "",
            )
            for m in messages
        ],
    )


@conversations_router.delete("/{session_id}")
async def delete_conversation(
    session_id: str,
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Delete a conversation and all its messages."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.tenant_id == user.tenant_id,
            Conversation.session_id == session_id,
        )
    )
    convo = result.scalar_one_or_none()

    if not convo:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Delete messages first
    await db.execute(
        sql_delete(Message).where(Message.conversation_id == convo.id)
    )
    # Delete conversation
    await db.delete(convo)
    await db.flush()

    return {"deleted": True, "session_id": session_id}
