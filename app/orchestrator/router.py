"""
Simplified message router.

In the Manager Pattern (Anthropic/OpenAI), Eve IS the router.
Eve decides whether to handle directly or delegate to agents via tool calls.

Two routing cases:
  1. Active multi-turn agent → route to that agent (unless user wants to exit).
  2. Everything else → Eve handles it.

Old approach: keyword match → active agent → LLM classify → default
New approach: active agent → Eve handles everything
"""

import logging
import re
from typing import Optional

from .registry import AgentRegistry

logger = logging.getLogger(__name__)

DEFAULT_AGENT = "eve_chat"

# Phrases that signal the user wants to exit the current agent and go back to Eve
_EXIT_PATTERNS = re.compile(
    r"\b("
    r"cancel|stop|quit|exit|back|go back|"
    r"back to eve|talk to eve|return to eve|"
    r"never ?mind|nevermind|forget it|"
    r"leave|done with (?:this|vera|the shoot)|"
    r"i[' ]?m done|that[' ]?s enough"
    r")\b",
    re.IGNORECASE,
)


def _is_exit_intent(message: str) -> bool:
    """Check if the user wants to exit the current agent session."""
    # Only match if the message is short (exit intent, not a longer request that happens to contain 'back')
    if len(message) > 100:
        return False
    return bool(_EXIT_PATTERNS.search(message))


async def route(
    message: str,
    state: dict,
    registry: AgentRegistry,
    db=None,
    tenant_id: str = "",
    user_id: str = "",
) -> str:
    """
    Determine which agent should handle a message.

    In the Manager Pattern, Eve handles nearly everything.
    The only exception: if an agent is in the middle of a multi-turn workflow
    (active_agent is set and the agent is not complete), route back to it.

    Returns the agent name (string).
    """

    # ── Check for active multi-turn agent ────────────────────────
    active = state.get("active_agent")
    if active and active != DEFAULT_AGENT:
        # Check if user wants to exit the current agent
        if _is_exit_intent(message):
            logger.info("Router: exit intent detected — leaving '%s' → Eve", active)
            # Deactivate the agent session
            if db:
                try:
                    from ..services.agent_session import deactivate_agent_session
                    await deactivate_agent_session(db, tenant_id, user_id, active)
                except Exception as e:
                    logger.warning("Failed to deactivate session: %s", e)
            return DEFAULT_AGENT

        agent = registry.get(active)
        if agent:
            logger.info("Router: active agent '%s' → continuing direct route", active)
            return active

        # Agent not in registry — fall through to Eve
        logger.warning("Router: active agent '%s' not found in registry → Eve", active)

    # ── Everything else: Eve handles it ──────────────────────────
    # Eve decides via tool calls whether to delegate to specialized agents.
    # This is the Manager Pattern from Anthropic/OpenAI research.
    logger.info("Router: → eve_chat (manager pattern)")
    return DEFAULT_AGENT
