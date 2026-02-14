"""
Conversation state management.

State lives in the conversations.state JSON column.
Supports:
  - Agent-specific state (workflow step, collected inputs, etc.)
  - Active agent tracking (for multi-turn routing)
  - Conversation summarization (Anthropic: "summarize completed work phases")
  - Checkpoint/resume pattern
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..models.conversation import Conversation, Message
from ..services import llm

logger = logging.getLogger(__name__)

# When history exceeds this many messages, summarize older ones
SUMMARIZE_THRESHOLD = 10


async def get_or_create_conversation(
    db: AsyncSession,
    tenant_id: str,
    session_id: str,
    user_id: str = "",
) -> Conversation:
    """Get existing conversation or create a new one."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.session_id == session_id,
        )
    )
    convo = result.scalar_one_or_none()

    if convo is None:
        convo = Conversation(
            tenant_id=tenant_id,
            session_id=session_id,
            user_id=user_id,
            state={},
        )
        db.add(convo)
        await db.flush()
        logger.info("Created conversation: %s (session=%s)", convo.id, session_id)

    return convo


async def get_state(convo: Conversation) -> dict:
    """Read the orchestrator state from a conversation."""
    return convo.state or {}


async def update_state(
    db: AsyncSession,
    convo: Conversation,
    updates: dict,
) -> dict:
    """Merge updates into conversation state. Returns the new state."""
    current = convo.state or {}
    current.update(updates)
    convo.state = current
    flag_modified(convo, "state")
    await db.flush()
    return current


async def set_active_agent(
    db: AsyncSession,
    convo: Conversation,
    agent_name: Optional[str],
) -> None:
    """Set or clear the active agent."""
    state = convo.state or {}
    if agent_name:
        state["active_agent"] = agent_name
    else:
        state.pop("active_agent", None)
        # Don't clear agent_state on completion — preserve for context
    convo.state = state
    flag_modified(convo, "state")
    await db.flush()


async def get_agent_state(convo: Conversation) -> dict:
    """Get the agent-specific state."""
    state = convo.state or {}
    return state.get("agent_state", {})


async def update_agent_state(
    db: AsyncSession,
    convo: Conversation,
    agent_state: dict,
) -> None:
    """Update the agent-specific state."""
    state = convo.state or {}
    state["agent_state"] = agent_state
    convo.state = state
    flag_modified(convo, "state")
    await db.flush()


async def add_message(
    db: AsyncSession,
    convo: Conversation,
    role: str,
    content: str,
    metadata: dict = None,
) -> Message:
    """Add a message to the conversation."""
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == convo.id)
        .order_by(Message.sequence_number.desc())
        .limit(1)
    )
    last = result.scalar_one_or_none()
    seq = (last.sequence_number + 1) if last else 1

    msg = Message(
        conversation_id=convo.id,
        tenant_id=convo.tenant_id,
        role=role,
        content=content,
        sequence_number=seq,
        metadata_=metadata or {},
    )
    db.add(msg)
    await db.flush()

    # Auto-generate title from first user message
    if role == "user" and not convo.title and seq <= 2:
        convo.title = content[:80].strip()
        if len(content) > 80:
            convo.title += "..."
        await db.flush()

    return msg


async def get_recent_messages(
    db: AsyncSession,
    convo: Conversation,
    limit: int = 30,
) -> list[Message]:
    """Get recent messages for context."""
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == convo.id)
        .order_by(Message.sequence_number.desc())
        .limit(limit)
    )
    messages = list(result.scalars().all())
    messages.reverse()  # Oldest first
    return messages


def build_history_with_media(
    messages: list[Message],
    exclude_last: bool = True,
) -> list[dict]:
    """
    Build LLM-compatible history that includes media context from metadata.

    When a message has media_urls in its metadata, append a text note
    so the LLM knows images were generated/shared in that turn.
    """
    source = messages[:-1] if (exclude_last and messages) else messages
    history = []
    for m in source:
        if not m.content:
            continue
        content = m.content
        meta = m.metadata_ or {}
        media_urls = meta.get("media_urls") or []
        if media_urls:
            count = len(media_urls)
            agent = meta.get("agent", "")
            agent_label = f" by {agent}" if agent else ""
            content += f"\n[{count} image(s) were generated{agent_label} in this message]"
        history.append({"role": m.role, "content": content})
    return history


# ── Conversation Summarization ───────────────────────────────────────
# Anthropic: "Agents summarize completed work phases and store essential
# information in external memory before proceeding to new tasks."

async def maybe_summarize_history(
    db: AsyncSession,
    convo: Conversation,
    history: list[dict],
    agent_state: dict,
) -> dict:
    """
    If the conversation is getting long, summarize older messages
    and store the summary in agent_state for future context.
    Returns the updated agent_state.
    """
    if len(history) < SUMMARIZE_THRESHOLD:
        return agent_state

    # Check if we already summarized recently
    last_summary_at = agent_state.get("_last_summary_at", 0)
    if len(history) - last_summary_at < SUMMARIZE_THRESHOLD:
        return agent_state

    # Summarize the older half of the conversation
    older_messages = history[: len(history) // 2]
    if not older_messages:
        return agent_state

    try:
        # Build text to summarize
        text_to_summarize = "\n".join(
            f"{m['role']}: {m['content'][:200]}" for m in older_messages if m.get("content")
        )

        summary = await llm.chat_simple(
            prompt=(
                "Summarize this conversation history in 2-3 concise sentences. "
                "Focus on: key topics discussed, decisions made, information gathered, "
                "and any pending tasks or questions. Be factual and brief.\n\n"
                f"{text_to_summarize}"
            ),
            system="You are a concise conversation summarizer. Output only the summary.",
            temperature=0,
            max_tokens=300,
        )

        # Store in agent state
        agent_state = dict(agent_state)
        agent_state["_conversation_summary"] = summary.strip()
        agent_state["_last_summary_at"] = len(history)

        # Persist
        await update_agent_state(db, convo, agent_state)
        logger.info("Summarized %d messages for conversation %s", len(older_messages), convo.id)

    except Exception as e:
        logger.warning("Failed to summarize conversation: %s", e)
        # Non-fatal — conversation continues without summary

    return agent_state
