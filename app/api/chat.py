"""
Chat API — regular + streaming endpoints.

POST /v1/chat        — Standard request/response
POST /v1/chat/stream — Server-Sent Events (SSE) streaming
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.auth import AuthenticatedUser
from ..core.dependencies import require_tenant, get_db
from ..orchestrator.orchestrator import handle_message, handle_message_stream

logger = logging.getLogger(__name__)

chat_router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str
    files: Optional[list[str]] = None


class ChatResponse(BaseModel):
    content: str
    media_urls: list[str] = []
    agent: str = ""
    is_complete: bool = True
    needs_input: Optional[str] = None
    conversation_id: str = ""
    session_id: str = ""
    metadata: Optional[dict] = None


@chat_router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Send a message to Eve. She routes to the right agent and responds."""
    result = await handle_message(
        message=request.message,
        session_id=request.session_id,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        db=db,
        files=request.files,
    )

    return ChatResponse(
        content=result["content"],
        media_urls=result.get("media_urls", []),
        agent=result.get("agent", ""),
        is_complete=result.get("is_complete", True),
        needs_input=result.get("needs_input"),
        conversation_id=result.get("conversation_id", ""),
        session_id=result.get("session_id", ""),
        metadata=result.get("metadata"),
    )


class StreamRequest(BaseModel):
    message: str
    session_id: str
    files: Optional[list[str]] = None  # File IDs from upload endpoint


@chat_router.post("/chat/stream")
async def chat_stream(
    request: StreamRequest,
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """
    Stream Eve's response via Server-Sent Events (SSE).

    Events:
      data: {"type": "agent", "agent": "eve_chat"}
      data: {"type": "token", "content": "Hello"}
      data: {"type": "tool_start", "name": "web_search", "args": {...}}
      data: {"type": "tool_result", "name": "web_search", "result": "..."}
      data: {"type": "generating", "agent": "fashion_photo", "status": "started|done"}
      data: {"type": "media", "url": "data:image/png;base64,...", "agent": "fashion_photo"}
      data: {"type": "done", "metadata": {...}}
    """

    async def event_generator():
        try:
            async for chunk in handle_message_stream(
                message=request.message,
                session_id=request.session_id,
                tenant_id=user.tenant_id,
                user_id=user.user_id,
                db=db,
                files=request.files,
            ):
                yield f"data: {json.dumps(chunk)}\n\n"
        except Exception as e:
            logger.error("SSE stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
