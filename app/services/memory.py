"""
Cross-session user memory — load, save, and format persistent user facts.

Used by the orchestrator to inject user context into every request,
and by the `remember` tool so Eve can explicitly store facts.
"""

import logging
from typing import Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.memory import UserMemory

logger = logging.getLogger(__name__)


async def load_memories(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    limit: int = 20,
) -> list[dict]:
    """Load active memories for a user. Returns list of {category, key, value}."""
    try:
        result = await db.execute(
            select(UserMemory)
            .where(
                UserMemory.tenant_id == tenant_id,
                UserMemory.user_id == user_id,
                UserMemory.is_active == True,  # noqa: E712
            )
            .order_by(UserMemory.updated_at.desc())
            .limit(limit)
        )
        memories = result.scalars().all()
        return [
            {"category": m.category, "key": m.key, "value": m.value}
            for m in memories
        ]
    except Exception as e:
        logger.warning("Failed to load memories: %s", e)
        return []


async def save_memory(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    category: str,
    key: str,
    value: str,
    source: str = "extracted",
) -> None:
    """Upsert a memory by tenant + user + key."""
    try:
        result = await db.execute(
            select(UserMemory).where(
                UserMemory.tenant_id == tenant_id,
                UserMemory.user_id == user_id,
                UserMemory.key == key,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = value
            existing.category = category
            existing.source = source
            existing.is_active = True
        else:
            mem = UserMemory(
                tenant_id=tenant_id,
                user_id=user_id,
                category=category,
                key=key,
                value=value,
                source=source,
            )
            db.add(mem)

        await db.flush()
        logger.debug("Saved memory: %s/%s/%s = %s", tenant_id, user_id, key, value[:50])
    except Exception as e:
        logger.warning("Failed to save memory: %s", e)


def format_memories_for_prompt(memories: list[dict]) -> str:
    """Format memories as a system prompt section."""
    if not memories:
        return ""

    lines = []
    for m in memories:
        lines.append(f"- {m['key']}: {m['value']}")

    return (
        "[USER MEMORY] Known facts about this user (persist across sessions):\n"
        + "\n".join(lines)
        + "\n\nUse these facts to personalize your responses. "
        "If the user corrects any of these, update them with the remember tool."
    )
