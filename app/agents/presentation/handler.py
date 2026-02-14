"""
Presentation agent — state-machine-based workflow.

Pipeline: collect_topic → generate_outline → confirm_outline → create_slides → deliver
"""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ...orchestrator.base_agent import BaseAgent, AgentResponse, AgentStatus

logger = logging.getLogger(__name__)


class PresentationAgent(BaseAgent):
    name = "presentation"
    display_name = "Presentation Creator"
    description = "Creates and edits professional presentations with AI-generated content, branding, and templates"
    triggers = [
        "presentation", "create slides", "make a deck",
        "powerpoint", "pptx", "slide deck", "pitch deck",
        "create presentation",
    ]
    capabilities = [
        "AI-generated slide content",
        "Professional templates and layouts",
        "Brand-consistent styling",
        "Data visualization slides",
        "Export to PPTX format (via SlideSpeak)",
    ]
    required_inputs = ["presentation topic", "number of slides", "audience type"]

    async def handle(
        self,
        message: str,
        state: dict,
        db: AsyncSession,
        user_id: str,
        tenant_id: str,
        files: Optional[list] = None,
        history: Optional[list[dict]] = None,
    ) -> AgentResponse:

        step = self.get_step(state)
        logger.info("Presentation: step=%s", step)

        if step in ("start", "collect_topic"):
            return await self._collect_topic(message, state)
        elif step == "generate_outline":
            return await self._generate_outline(state)
        elif step == "confirm_outline":
            return await self._confirm_outline(message, state)
        elif step == "create_slides":
            return await self._create_slides(state)
        else:
            return await self._collect_topic(message, {})

    async def _collect_topic(self, message: str, state: dict) -> AgentResponse:
        """Collect presentation topic and requirements."""
        pres = state.get("presentation", {})
        pres["topic"] = message.strip()
        pres.setdefault("num_slides", 10)
        pres.setdefault("audience", "professional")

        new_state = self._set_step(state, "generate_outline", AgentStatus.PROCESSING)
        new_state["presentation"] = pres

        return AgentResponse(
            content=f"Creating a presentation about: **{pres['topic']}**\n\nGenerating outline...",
            state_update=new_state,
            is_complete=False,
            status=AgentStatus.PROCESSING,
        )

    async def _generate_outline(self, state: dict) -> AgentResponse:
        """Generate presentation outline. (LLM placeholder)"""
        pres = state.get("presentation", {})
        topic = pres.get("topic", "Topic")
        num = pres.get("num_slides", 10)

        # TODO: Use LLM to generate outline
        outline = [
            "1. Title Slide",
            f"2. Introduction to {topic}",
            "3. Problem Statement",
            "4. Market Opportunity",
            "5. Our Solution",
            "6. Key Features",
            "7. Competitive Advantage",
            "8. Traction & Metrics",
            "9. Roadmap",
            f"10. Summary & Call to Action",
        ][:num]

        pres["outline"] = outline
        new_state = self._set_step(state, "confirm_outline", AgentStatus.AWAITING_CONFIRMATION)
        new_state["presentation"] = pres

        return AgentResponse(
            content=(
                f"Here's the outline for your {num}-slide deck:\n\n"
                + "\n".join(outline)
                + "\n\nLooks good? Say 'yes' to generate slides, or tell me what to change."
            ),
            state_update=new_state,
            is_complete=False,
            needs_input="Confirm outline or request changes",
            status=AgentStatus.AWAITING_CONFIRMATION,
        )

    async def _confirm_outline(self, message: str, state: dict) -> AgentResponse:
        """Confirm or revise the outline."""
        if message.lower().strip() in ("yes", "y", "go", "proceed", "looks good", "ok"):
            new_state = self._set_step(state, "create_slides", AgentStatus.PROCESSING)
            return AgentResponse(
                content="Generating your presentation slides...",
                state_update=new_state,
                is_complete=False,
                status=AgentStatus.PROCESSING,
            )
        else:
            new_state = self._set_step(state, "collect_topic", AgentStatus.COLLECTING_INPUT)
            return AgentResponse(
                content="Got it. Tell me what you'd like to change about the outline.",
                state_update=new_state,
                is_complete=False,
                needs_input="What changes would you like?",
                status=AgentStatus.COLLECTING_INPUT,
            )

    async def _create_slides(self, state: dict) -> AgentResponse:
        """Create the actual slides. (SlideSpeak API placeholder)"""
        pres = state.get("presentation", {})
        outline = pres.get("outline", [])

        # TODO: Call SlideSpeak API to create slides
        new_state = self._complete(state)

        return AgentResponse(
            content=(
                f"Your presentation is ready! ({len(outline)} slides)\n\n"
                f"Topic: {pres.get('topic', 'N/A')}\n"
                f"Slides: {len(outline)}\n\n"
                "(SlideSpeak API integration coming soon — will generate downloadable PPTX.)\n\n"
                "Need any changes or a new presentation?"
            ),
            state_update=new_state,
            is_complete=True,
            status=AgentStatus.COMPLETE,
        )
