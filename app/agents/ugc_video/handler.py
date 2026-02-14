"""
UGC Video agent — state-machine-based workflow.

Pipeline: collect_info → generate_script → generate_image → generate_audio → compose_video → deliver
Each step collects what it needs, processes, and advances to the next.
Full external API calls will be wired in later; the state machine is production-ready.
"""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ...orchestrator.base_agent import BaseAgent, AgentResponse, AgentStatus

logger = logging.getLogger(__name__)

# Workflow steps
STEPS = ["collect_info", "confirm_brief", "generate_script", "generate_image", "generate_audio", "compose_video", "deliver"]


class UGCVideoAgent(BaseAgent):
    name = "ugc_video"
    display_name = "UGC Content Creator"
    description = "Creates UGC-style marketing videos with AI-generated images, scripts, voiceover, and lip-sync"
    triggers = [
        "ugc", "ugc video", "content creator", "create video",
        "make a video", "marketing video", "tiktok content",
        "video ad", "product video",
    ]
    capabilities = [
        "AI image generation for video scenes",
        "Script writing for marketing videos",
        "Text-to-speech voiceover (ElevenLabs)",
        "Lip-sync video composition (Sync.so)",
        "Multiple video styles (casual, professional, energetic)",
    ]
    required_inputs = ["product/brand name", "target audience", "video style"]

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
        logger.info("UGC Video: step=%s, message=%s", step, message[:80])

        if step in ("start", "collect_info"):
            return await self._collect_info(message, state)
        elif step == "confirm_brief":
            return await self._confirm_brief(message, state)
        elif step == "generate_script":
            return await self._generate_script(state)
        elif step in ("generate_image", "generate_audio", "compose_video"):
            return await self._pipeline_step(step, state)
        elif step == "deliver":
            return await self._deliver(state)
        else:
            # Unknown step — restart
            return await self._collect_info(message, {})

    async def _collect_info(self, message: str, state: dict) -> AgentResponse:
        """Step 1: Collect product, audience, style info from user message."""
        info = state.get("brief", {})

        # Auto-populate from brand context if available
        brand = state.get("_brand", {})
        if brand and not info.get("brand_name"):
            info["brand_name"] = brand.get("name", "")
            if brand.get("tone_of_voice"):
                info["tone"] = brand["tone_of_voice"]
            if brand.get("industry"):
                info["industry"] = brand["industry"]

        # Parse info from message (basic extraction — LLM-powered extraction comes later)
        msg_lower = message.lower()
        if not info.get("product"):
            info["product"] = message.strip()

        # Check what we still need
        missing = []
        if not info.get("product"):
            missing.append("the product or brand name")
        if not info.get("audience"):
            info["audience"] = "general audience"  # Default
        if not info.get("style"):
            info["style"] = info.get("tone", "casual, energetic")  # Use brand tone if available

        new_state = self._set_step(state, "confirm_brief", AgentStatus.COLLECTING_INPUT)
        new_state["brief"] = info

        brand_line = f"- Brand: {info['brand_name']}\n" if info.get("brand_name") else ""
        brief_summary = (
            f"Here's what I have so far:\n"
            f"{brand_line}"
            f"- Product/Brand: {info.get('product', 'Not specified')}\n"
            f"- Target Audience: {info.get('audience', 'General')}\n"
            f"- Style: {info.get('style', 'Casual')}\n\n"
            f"Does this look right? Say 'yes' to proceed, or tell me what to change."
        )

        return AgentResponse(
            content=brief_summary,
            state_update=new_state,
            is_complete=False,
            needs_input="Confirm the brief or tell me what to change",
            status=AgentStatus.COLLECTING_INPUT,
        )

    async def _confirm_brief(self, message: str, state: dict) -> AgentResponse:
        """Step 2: User confirms or modifies the brief."""
        msg_lower = message.lower().strip()

        if msg_lower in ("yes", "y", "looks good", "proceed", "go", "ok", "correct", "confirm"):
            new_state = self._set_step(state, "generate_script", AgentStatus.PROCESSING)
            return AgentResponse(
                content="Great! Starting script generation for your UGC video...",
                state_update=new_state,
                is_complete=False,
                status=AgentStatus.PROCESSING,
            )
        else:
            # User wants changes — go back to collect info
            new_state = self._set_step(state, "collect_info", AgentStatus.COLLECTING_INPUT)
            return AgentResponse(
                content="Got it. Tell me what you'd like to change about the brief.",
                state_update=new_state,
                is_complete=False,
                needs_input="What would you like to change?",
                status=AgentStatus.COLLECTING_INPUT,
            )

    async def _generate_script(self, state: dict) -> AgentResponse:
        """Step 3: Generate video script. (API integration placeholder)"""
        brief = state.get("brief", {})
        product = brief.get("product", "your product")
        style = brief.get("style", "casual")

        # TODO: Call LLM to generate actual script
        script = (
            f"[Scene 1] Close-up of {product} on a clean background\n"
            f"[VO] \"Hey everyone! Let me show you something amazing...\"\n"
            f"[Scene 2] Product in use, {style} setting\n"
            f"[VO] \"I've been using {product} for a week and honestly...\"\n"
            f"[Scene 3] Results/testimonial shot\n"
            f"[VO] \"...it completely changed my routine. You NEED to try this.\"\n"
            f"[CTA] Link in bio!"
        )

        new_state = self._set_step(state, "generate_image", AgentStatus.PROCESSING)
        new_state["script"] = script

        return AgentResponse(
            content=f"Script generated:\n\n{script}\n\nNow generating visuals...",
            state_update=new_state,
            is_complete=False,
            status=AgentStatus.PROCESSING,
        )

    async def _pipeline_step(self, step: str, state: dict) -> AgentResponse:
        """Steps 4-6: Image gen, audio gen, video composition. (Placeholders)"""
        step_messages = {
            "generate_image": ("Generating AI images for video scenes...", "generate_audio"),
            "generate_audio": ("Generating voiceover audio...", "compose_video"),
            "compose_video": ("Composing final video with lip-sync...", "deliver"),
        }

        message, next_step = step_messages.get(step, ("Processing...", "deliver"))

        # TODO: Wire in actual API calls (AIML for images, ElevenLabs for audio, Sync.so for lipsync)
        new_state = self._set_step(state, next_step, AgentStatus.PROCESSING)
        new_state[f"{step}_complete"] = True

        return AgentResponse(
            content=f"{message}\n\n(Pipeline step '{step}' — external API integration coming soon)",
            state_update=new_state,
            is_complete=False,
            status=AgentStatus.PROCESSING,
        )

    async def _deliver(self, state: dict) -> AgentResponse:
        """Final step: Deliver the completed video."""
        new_state = self._complete(state)

        return AgentResponse(
            content=(
                "Your UGC video has been created! "
                "(Full pipeline with actual video delivery coming soon.)\n\n"
                "Summary:\n"
                f"- Product: {state.get('brief', {}).get('product', 'N/A')}\n"
                f"- Style: {state.get('brief', {}).get('style', 'N/A')}\n"
                f"- Script: Generated\n"
                f"- Images: Generated\n"
                f"- Audio: Generated\n"
                f"- Video: Composed\n\n"
                "Would you like to make another video or do something else?"
            ),
            state_update=new_state,
            is_complete=True,
            status=AgentStatus.COMPLETE,
            metadata={"pipeline_steps_completed": [s for s in STEPS if state.get(f"{s}_complete")]},
        )
