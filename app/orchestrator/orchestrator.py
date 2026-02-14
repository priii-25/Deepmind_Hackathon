"""
Main orchestration loop.

Receive message → route → delegate → respond → update state.

Architecture (post-research):
  Eve is the central manager. She handles all messages and delegates
  to specialized agents via tool calls (Manager Pattern).
  The only routing exception: active multi-turn agents get their messages
  directly until their workflow completes.
"""

import logging
import time
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from .base_agent import AgentResponse
from .registry import get_registry
from .router import route
from .state import (
    get_or_create_conversation,
    get_state,
    set_active_agent,
    get_agent_state,
    update_agent_state,
    add_message,
    get_recent_messages,
    build_history_with_media,
    maybe_summarize_history,
    maybe_extract_memories,
)
from ..services import realtime
from ..services.brand_context import get_brand_context
from ..services.memory import load_memories

logger = logging.getLogger(__name__)


async def handle_message(
    message: str,
    session_id: str,
    tenant_id: str,
    user_id: str,
    db: AsyncSession,
    files: Optional[list] = None,
) -> dict:
    """
    Main entry point for non-streaming requests.
    """
    start = time.monotonic()
    registry = get_registry()

    # 1. Get or create conversation
    convo = await get_or_create_conversation(db, tenant_id, session_id, user_id)

    # 2. Save user message
    await add_message(db, convo, role="user", content=message)

    # 3. Notify frontend
    await realtime.chat_started(tenant_id, session_id, {"message": message[:100]})

    # 4. Read current state
    state = await get_state(convo)

    # 5. Route (Manager Pattern: almost always Eve, unless active multi-turn agent)
    agent_name = await route(message, state, registry, db=db, tenant_id=tenant_id, user_id=user_id)
    agent = registry.get(agent_name)

    if not agent:
        logger.error("Agent '%s' not found — falling back to Eve", agent_name)
        agent = registry.get("eve_chat")
        agent_name = "eve_chat"

    await realtime.agent_started(tenant_id, session_id, agent_name)

    # 6. Build conversation history (with media context)
    recent = await get_recent_messages(db, convo, limit=30)
    history = build_history_with_media(recent, exclude_last=True)

    # 7. Get agent-specific state
    agent_state = await get_agent_state(convo)

    # 8. Maybe summarize old history (keeps context manageable)
    agent_state = await maybe_summarize_history(db, convo, history, agent_state)

    # 8.1. Maybe extract persistent memories from conversation
    await maybe_extract_memories(db, convo, history, tenant_id, user_id)

    # 8.5. Load brand context — available to ALL agents
    brand = await get_brand_context(db, tenant_id)
    if brand:
        agent_state["_brand"] = brand

    # 8.6. Load cross-session user memories
    memories = await load_memories(db, tenant_id, user_id)
    if memories:
        agent_state["_memories"] = memories

    # 9. Delegate to agent
    try:
        response: AgentResponse = await agent.handle(
            message=message,
            state=agent_state,
            db=db,
            user_id=user_id,
            tenant_id=tenant_id,
            files=files,
            history=history,
            session_id=session_id,
        )
    except Exception as e:
        logger.exception("Agent '%s' failed: %s", agent_name, e)
        await realtime.chat_error(tenant_id, session_id, {"error": str(e)})
        response = AgentResponse(
            content="Something went wrong on my end. Please try again.",
            is_complete=True,
        )

    # 10. Save assistant message
    await add_message(
        db, convo, role="assistant", content=response.content,
        metadata={
            "agent": agent_name,
            "media_urls": response.media_urls,
            **(response.metadata or {}),
        },
    )

    # 11. Update state
    if response.state_update:
        await update_agent_state(db, convo, response.state_update)

    # 12. Handle handoff or completion (single set_active_agent call)
    if response.handoff_to:
        await set_active_agent(db, convo, response.handoff_to)
    elif response.is_complete:
        await set_active_agent(db, convo, None)
    else:
        await set_active_agent(db, convo, agent_name)

    elapsed = time.monotonic() - start

    # 13. Notify frontend
    await realtime.chat_completed(tenant_id, session_id, {
        "agent": agent_name,
        "has_media": bool(response.media_urls),
        "elapsed_ms": int(elapsed * 1000),
    })

    return {
        "content": response.content,
        "media_urls": response.media_urls,
        "agent": agent_name,
        "is_complete": response.is_complete,
        "needs_input": response.needs_input,
        "conversation_id": convo.id,
        "session_id": session_id,
        "metadata": response.metadata,
    }


async def handle_message_stream(
    message: str,
    session_id: str,
    tenant_id: str,
    user_id: str,
    db: AsyncSession,
    files: list[str] | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Streaming entry point. Yields SSE chunks.
    Falls back to non-streaming for agents without handle_stream.
    """
    registry = get_registry()

    convo = await get_or_create_conversation(db, tenant_id, session_id, user_id)
    await add_message(db, convo, role="user", content=message)

    state = await get_state(convo)
    agent_name = await route(message, state, registry, db=db, tenant_id=tenant_id, user_id=user_id)
    agent = registry.get(agent_name) or registry.get("eve_chat")
    agent_name = agent.name

    yield {"type": "agent", "agent": agent_name}

    # Build history (with media context)
    recent = await get_recent_messages(db, convo, limit=30)
    history = build_history_with_media(recent, exclude_last=True)
    agent_state = await get_agent_state(convo)
    agent_state = await maybe_summarize_history(db, convo, history, agent_state)

    # Extract persistent memories from conversation
    await maybe_extract_memories(db, convo, history, tenant_id, user_id)

    # Load brand context for streaming path too
    brand = await get_brand_context(db, tenant_id)
    if brand:
        agent_state["_brand"] = brand

    # Load cross-session user memories
    memories = await load_memories(db, tenant_id, user_id)
    if memories:
        agent_state["_memories"] = memories

    # Inject uploaded file IDs so agents can access them
    if files:
        agent_state["_pending_files"] = files

    # If the agent supports streaming, use it
    if hasattr(agent, "handle_stream") and agent_name == "eve_chat":
        full_content = ""
        pending_handoff = None
        logger.info("Orchestrator: streaming via %s", agent_name)
        try:
            async for chunk in agent.handle_stream(
                message=message,
                state=agent_state,
                db=db,
                user_id=user_id,
                tenant_id=tenant_id,
                history=history,
                session_id=session_id,
            ):
                if chunk.get("type") == "token":
                    full_content += chunk.get("content", "")
                elif chunk.get("type") == "done":
                    logger.info("Orchestrator: stream done, content=%d chars", len(full_content))
                elif chunk.get("type") == "handoff":
                    pending_handoff = chunk.get("agent")
                # Also detect handoff from 'done' metadata (backup)
                elif chunk.get("type") == "done" and chunk.get("metadata", {}).get("handoff"):
                    pending_handoff = pending_handoff or chunk["metadata"]["handoff"]
                yield chunk
        except Exception as e:
            logger.exception("Streaming failed: %s", e)
            yield {"type": "error", "content": str(e)}
            full_content = f"Error: {e}"

        await add_message(db, convo, role="assistant", content=full_content, metadata={"agent": agent_name, "media_urls": []})

        # Handle agent handoff — set the delegated agent as active
        if pending_handoff:
            logger.info("Orchestrator: handoff to %s", pending_handoff)
            await set_active_agent(db, convo, pending_handoff)
        else:
            await set_active_agent(db, convo, None)
    else:
        # Non-streaming fallback (used for direct agent routing, e.g. Vera)

        # Show "generating" indicator if this turn might trigger image generation.
        # The state machine advances scene_select → preview → generation inside handle(),
        # but agent_state still holds the PREVIOUS turn's step. So we check for the
        # last data-collection step (scene_select) AND the preview steps themselves.
        is_generating = (
            agent_name == "fashion_photo"
            and agent_state.get("current_step") in ("scene_select", "preview", "preview_feedback")
            and agent_state.get("phase") not in ("preview_shown", "refining", "complete")
        )
        if is_generating:
            yield {"type": "generating", "agent": agent_name, "status": "started"}

        try:
            response = await agent.handle(
                message=message, state=agent_state, db=db,
                user_id=user_id, tenant_id=tenant_id, files=files,
                history=history, session_id=session_id,
            )
        except Exception as e:
            response = AgentResponse(content=f"Error: {e}", is_complete=True)

        # Hide generating indicator
        if is_generating:
            yield {"type": "generating", "agent": agent_name, "status": "done"}

        yield {"type": "token", "content": response.content}

        # Send media URLs (generated images) if present
        if response.media_urls:
            for url in response.media_urls:
                yield {"type": "media", "url": url, "agent": agent_name}

        yield {"type": "done", "metadata": response.metadata}

        await add_message(
            db, convo, role="assistant", content=response.content,
            metadata={
                "agent": agent_name,
                "media_urls": response.media_urls or [],
                **(response.metadata or {}),
            },
        )

        if response.state_update:
            await update_agent_state(db, convo, response.state_update)

        # Handle handoff or completion
        if response.handoff_to:
            await set_active_agent(db, convo, response.handoff_to)
        elif response.is_complete:
            await set_active_agent(db, convo, None)
        else:
            await set_active_agent(db, convo, agent_name)
