"""
Agent registry. Register agents, look them up, list them.
"""

import logging
from typing import Optional

from .base_agent import BaseAgent
from ..core.flags import get_flags

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Central registry for all agents."""

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Register an agent by its name."""
        if agent.name in self._agents:
            logger.warning("Agent '%s' already registered, overwriting", agent.name)
        self._agents[agent.name] = agent
        logger.info("Registered agent: %s (%s)", agent.name, agent.display_name)

    def get(self, name: str) -> Optional[BaseAgent]:
        """Get an agent by name. Returns None if not found."""
        return self._agents.get(name)

    def list_agents(self) -> list[BaseAgent]:
        """List all registered agents."""
        return list(self._agents.values())

    def get_agent_names(self) -> list[str]:
        """Get names of all registered agents."""
        return list(self._agents.keys())

    def get_agent_descriptions(self) -> list[dict]:
        """Get structured descriptions for all agents."""
        return [a.describe() for a in self._agents.values()]


# ── Global registry ──────────────────────────────────────────────────

_registry: Optional[AgentRegistry] = None


def get_registry() -> AgentRegistry:
    """Get or create the global agent registry."""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
        _register_enabled_agents(_registry)
    return _registry


def _register_enabled_agents(registry: AgentRegistry) -> None:
    """Register agents based on feature flags."""
    flags = get_flags()

    # Eve's default handler is always registered
    from ..agents.eve_chat.handler import EveChatAgent
    registry.register(EveChatAgent())

    if flags.enable_ugc_video:
        try:
            from ..agents.ugc_video.handler import UGCVideoAgent
            registry.register(UGCVideoAgent())
        except Exception as e:
            logger.error("Failed to register UGC Video agent: %s", e)

    if flags.enable_fashion_photo:
        try:
            from ..agents.fashion_photo.handler import FashionPhotoAgent
            registry.register(FashionPhotoAgent())
        except Exception as e:
            logger.error("Failed to register Fashion Photo agent: %s", e)

    if flags.enable_social_media:
        try:
            from ..agents.social_media.handler import SocialMediaAgent
            registry.register(SocialMediaAgent())
        except Exception as e:
            logger.error("Failed to register Social Media agent: %s", e)

    if flags.enable_presentation:
        try:
            from ..agents.presentation.handler import PresentationAgent
            registry.register(PresentationAgent())
        except Exception as e:
            logger.error("Failed to register Presentation agent: %s", e)

    if flags.enable_notetaker:
        try:
            from ..agents.notetaker.handler import NotetakerAgent
            registry.register(NotetakerAgent())
        except Exception as e:
            logger.error("Failed to register Notetaker agent: %s", e)

    logger.info(
        "Agent registry ready: %d agents [%s]",
        len(registry.get_agent_names()),
        ", ".join(registry.get_agent_names()),
    )
