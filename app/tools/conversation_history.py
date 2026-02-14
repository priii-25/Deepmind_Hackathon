"""
Conversation history tool — lets Eve search past conversations.

Allows Eve to answer questions like "what did we discuss before?",
"did I talk about Vera?", "what was my last question?", etc.
"""

import logging

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.conversation import Conversation, Message
from .registry import tool, ToolRisk

logger = logging.getLogger(__name__)


@tool(
    name="conversation_history",
    description=(
        "Search past conversations and messages for the current user. "
        "\n\nWhen to use: User asks about previous conversations, past discussions, "
        "what they talked about before, earlier topics, or references something "
        "from a previous chat session. Also useful when user says 'what did we discuss', "
        "'do you remember', 'last time we talked', 'my previous question', etc. "
        "\n\nReturns: Summary of matching conversations with key messages."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search term to filter messages (e.g. 'vera', 'photos', 'brand'). Leave empty to get recent conversations.",
            },
            "limit": {
                "type": "integer",
                "description": "Max number of conversations to return. Default 5.",
            },
        },
        "required": [],
    },
    risk=ToolRisk.READ,
    category="data",
)
async def conversation_history(
    query: str = "", limit: int = 5,
    db: AsyncSession = None, tenant_id: str = "",
    session_id: str = "", **kwargs,
) -> str:
    """Search past conversations for the tenant."""
    if not db:
        return "Database not available."

    try:
        # Get recent conversations (excluding current session)
        conv_query = (
            select(Conversation)
            .where(Conversation.tenant_id == tenant_id)
            .order_by(Conversation.updated_at.desc())
            .limit(limit + 5)  # fetch extra to filter current
        )
        result = await db.execute(conv_query)
        convos = [c for c in result.scalars().all() if c.session_id != session_id][:limit]

        if not convos:
            return "No previous conversations found. This is the user's first session."

        # If there's a search query, search messages across those conversations
        if query:
            conv_ids = [c.id for c in convos]
            msg_result = await db.execute(
                select(Message)
                .where(
                    Message.conversation_id.in_(conv_ids),
                    Message.content.ilike(f"%{query}%"),
                )
                .order_by(Message.created_at.desc())
                .limit(20)
            )
            messages = list(msg_result.scalars().all())

            if not messages:
                return f"No messages found matching '{query}' in the last {limit} conversations."

            # Group by conversation
            conv_map = {c.id: c for c in convos}
            grouped = {}
            for m in messages:
                cid = m.conversation_id
                if cid not in grouped:
                    grouped[cid] = []
                grouped[cid].append(m)

            lines = [f"Found {len(messages)} message(s) matching '{query}':\n"]
            for cid, msgs in grouped.items():
                conv = conv_map.get(cid)
                title = conv.title if conv else "Untitled"
                lines.append(f"**{title}**:")
                for m in msgs[:5]:  # Max 5 messages per conversation
                    preview = m.content[:150].replace("\n", " ")
                    lines.append(f"  [{m.role}]: {preview}")
                lines.append("")

            return "\n".join(lines)

        # No query — return conversation summaries with recent messages
        lines = [f"Found {len(convos)} previous conversation(s):\n"]
        for conv in convos:
            title = conv.title or "Untitled"
            # Get last few messages for context
            msg_result = await db.execute(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.sequence_number.desc())
                .limit(3)
            )
            recent_msgs = list(msg_result.scalars().all())
            recent_msgs.reverse()

            lines.append(f"**{title}** (session: {conv.session_id}):")
            for m in recent_msgs:
                preview = m.content[:100].replace("\n", " ")
                agent = (m.metadata_ or {}).get("agent", "")
                agent_label = f" [{agent}]" if agent else ""
                lines.append(f"  {m.role}{agent_label}: {preview}")

            # Note any media or agents used
            state = conv.state or {}
            active = state.get("active_agent")
            if active:
                lines.append(f"  (Active agent: {active})")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.error("Conversation history query failed: %s", e)
        return f"Could not retrieve conversation history: {e}"
