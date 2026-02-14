"""
BaseAgent — every agent implements this interface.

No frameworks. Just a class with a handle() method.
Supports:
  - Multi-step workflows with step tracking
  - Handoff signaling (return control to Eve or another agent)
  - Structured metadata for observability
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession


class AgentStatus(str, Enum):
    """Tracks where an agent is in its workflow."""
    IDLE = "idle"
    COLLECTING_INPUT = "collecting_input"
    PROCESSING = "processing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETE = "complete"
    ERROR = "error"


@dataclass
class AgentResponse:
    """What an agent returns after handling a message."""

    content: str = ""                                  # Text reply to user
    media_urls: list[str] = field(default_factory=list) # Generated files / images
    state_update: dict = field(default_factory=dict)    # Persisted agent state (JSON)
    is_complete: bool = False                           # Is this agent's task done?
    needs_input: Optional[str] = None                   # What does it need from user next?
    metadata: dict = field(default_factory=dict)        # Observability data
    handoff_to: Optional[str] = None                    # Agent name to hand off to (or "eve_chat")
    status: AgentStatus = AgentStatus.IDLE              # Current workflow status


class BaseAgent:
    """
    Base class for all agents. Subclass and implement handle().

    Attributes:
        name:         Internal ID ("ugc_video")
        display_name: Human-readable ("UGC Content Creator")
        description:  What it does (used by LLM and delegation tools)
        triggers:     Keywords that hint at this agent (used by keyword router)
        capabilities: Structured list of what this agent can do
        required_inputs: What info the agent needs to start work
    """

    name: str = ""
    display_name: str = ""
    description: str = ""
    triggers: list[str] = []
    capabilities: list[str] = []
    required_inputs: list[str] = []

    async def handle(
        self,
        message: str,
        state: dict,
        db: AsyncSession,
        user_id: str,
        tenant_id: str,
        files: Optional[list] = None,
        history: Optional[list[dict]] = None,
        session_id: str = "",
        **kwargs,
    ) -> AgentResponse:
        """
        Handle a user message. Must be implemented by subclass.

        Args:
            message:    The user's message (or task description from Eve)
            state:      Agent-specific state from conversation JSON
            db:         Database session
            user_id:    Authenticated user ID
            tenant_id:  Tenant ID
            files:      Optional uploaded files
            history:    Recent conversation history [{role, content}, ...]

        Returns:
            AgentResponse with content, media, state updates, etc.
        """
        raise NotImplementedError(f"Agent '{self.name}' must implement handle()")

    def get_status(self, state: dict) -> AgentStatus:
        """Get the agent's current workflow status from persisted state."""
        return AgentStatus(state.get("_status", AgentStatus.IDLE))

    def get_step(self, state: dict) -> str:
        """Get the current step name from persisted state."""
        return state.get("_step", "start")

    def _set_step(self, state: dict, step: str, status: AgentStatus = AgentStatus.PROCESSING) -> dict:
        """Update the step and status in state. Returns the updated state dict."""
        state = dict(state)
        state["_step"] = step
        state["_status"] = status.value
        return state

    def _complete(self, state: dict) -> dict:
        """Mark the agent's workflow as complete."""
        state = dict(state)
        state["_step"] = "done"
        state["_status"] = AgentStatus.COMPLETE.value
        return state

    def describe(self) -> dict:
        """Return a structured description (used by delegation tools and API)."""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "capabilities": self.capabilities,
            "required_inputs": self.required_inputs,
        }
