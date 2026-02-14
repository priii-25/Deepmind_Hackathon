"""
Agent session persistence — load/save multi-turn agent state.

Used by:
  - Delegation tools (_try_delegate) to persist state between Eve→Agent calls
  - Agent handlers (e.g. Vera) to load state when called directly by the router
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.agent_session import AgentSession

logger = logging.getLogger(__name__)

# Keys to strip from persisted state (non-serializable or ephemeral)
_EPHEMERAL_KEYS = {"_gemini_chat", "_brand"}


def _clean_state(state: dict) -> dict:
    """Remove non-serializable / ephemeral keys before persisting."""
    return {k: v for k, v in state.items() if k not in _EPHEMERAL_KEYS}


async def load_agent_session(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    agent_name: str,
) -> Optional[dict]:
    """
    Load the most recent active session state for an agent.
    Returns the session_state dict or None if no active session.
    """
    try:
        result = await db.execute(
            select(AgentSession)
            .where(
                AgentSession.tenant_id == tenant_id,
                AgentSession.user_id == user_id,
                AgentSession.agent_name == agent_name,
                AgentSession.is_active == True,  # noqa: E712
            )
            .order_by(AgentSession.updated_at.desc())
            .limit(1)
        )
        session = result.scalar_one_or_none()
        if session:
            logger.debug("Loaded session for %s/%s/%s (step=%s)", tenant_id, user_id, agent_name, session.current_step)
            return session.session_state or {}
        return None
    except Exception as e:
        logger.warning("Failed to load agent session: %s", e)
        return None


async def save_agent_session(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    agent_name: str,
    state: dict,
    is_complete: bool = False,
) -> None:
    """
    Save agent session state. Creates or updates the active session.
    If is_complete, marks the session inactive.
    """
    try:
        clean = _clean_state(state)
        current_step = clean.get("current_step", "unknown")

        result = await db.execute(
            select(AgentSession)
            .where(
                AgentSession.tenant_id == tenant_id,
                AgentSession.user_id == user_id,
                AgentSession.agent_name == agent_name,
                AgentSession.is_active == True,  # noqa: E712
            )
            .order_by(AgentSession.updated_at.desc())
            .limit(1)
        )
        session = result.scalar_one_or_none()

        if session:
            session.session_state = clean
            session.current_step = current_step
            session.is_active = not is_complete
        else:
            session = AgentSession(
                tenant_id=tenant_id,
                user_id=user_id,
                agent_name=agent_name,
                session_state=clean,
                current_step=current_step,
                is_active=not is_complete,
            )
            db.add(session)

        await db.flush()
        logger.debug("Saved session for %s/%s/%s (step=%s, active=%s)", tenant_id, user_id, agent_name, current_step, not is_complete)
    except Exception as e:
        logger.warning("Failed to save agent session: %s", e)


async def deactivate_agent_session(
    db: AsyncSession,
    tenant_id: str,
    user_id: str,
    agent_name: str,
) -> None:
    """Deactivate (end) an agent session — used when user exits or cancels."""
    try:
        result = await db.execute(
            select(AgentSession)
            .where(
                AgentSession.tenant_id == tenant_id,
                AgentSession.user_id == user_id,
                AgentSession.agent_name == agent_name,
                AgentSession.is_active == True,  # noqa: E712
            )
        )
        sessions = result.scalars().all()
        for s in sessions:
            s.is_active = False
        await db.flush()
        if sessions:
            logger.info("Deactivated %d session(s) for %s/%s/%s", len(sessions), tenant_id, user_id, agent_name)
    except Exception as e:
        logger.warning("Failed to deactivate agent session: %s", e)
