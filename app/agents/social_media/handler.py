"""
Social Media agent — state-machine-based workflow.

Pipeline: collect_content → generate_caption → select_platform → post → confirm
"""

import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ...orchestrator.base_agent import BaseAgent, AgentResponse, AgentStatus

logger = logging.getLogger(__name__)


class SocialMediaAgent(BaseAgent):
    name = "social_media"
    display_name = "Social Media Manager"
    description = "Helps create and post content to TikTok and Facebook with caption and hashtag generation"
    triggers = [
        "post to tiktok", "post to facebook", "social media",
        "publish content", "schedule post", "share on",
        "tiktok post", "facebook post", "instagram",
    ]
    capabilities = [
        "Caption generation with AI",
        "Hashtag research and generation",
        "Post to TikTok via API",
        "Post to Facebook via API",
        "Content scheduling",
    ]
    required_inputs = ["content/media to post", "target platform"]

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
        logger.info("Social Media: step=%s", step)

        if step in ("start", "collect_content"):
            return await self._collect_content(message, state, files)
        elif step == "generate_caption":
            return await self._generate_caption(message, state)
        elif step == "select_platform":
            return await self._select_platform(message, state)
        elif step == "confirm_post":
            return await self._confirm_post(message, state)
        else:
            return await self._collect_content(message, {}, files)

    async def _collect_content(self, message: str, state: dict, files: Optional[list]) -> AgentResponse:
        """Collect content and media for posting."""
        post = state.get("post", {})
        post["description"] = message.strip()

        if files:
            post["media_files"] = [str(f) for f in files]

        new_state = self._set_step(state, "generate_caption", AgentStatus.PROCESSING)
        new_state["post"] = post

        return AgentResponse(
            content=(
                f"I'll help you create a social media post about: \"{message[:100]}\"\n\n"
                "Let me generate a caption and hashtags for you..."
            ),
            state_update=new_state,
            is_complete=False,
            status=AgentStatus.PROCESSING,
        )

    async def _generate_caption(self, message: str, state: dict) -> AgentResponse:
        """Generate caption and hashtags. (LLM integration placeholder)"""
        post = state.get("post", {})
        description = post.get("description", "")

        # TODO: Use LLM to generate contextual captions
        caption = f"Check this out! {description[:100]} #trending #viral"
        hashtags = "#marketing #content #socialmedia #brand"

        post["caption"] = caption
        post["hashtags"] = hashtags

        new_state = self._set_step(state, "select_platform", AgentStatus.COLLECTING_INPUT)
        new_state["post"] = post

        return AgentResponse(
            content=(
                f"Here's your draft post:\n\n"
                f"**Caption:** {caption}\n"
                f"**Hashtags:** {hashtags}\n\n"
                "Which platform? Choose:\n"
                "1. TikTok\n"
                "2. Facebook\n"
                "3. Both\n"
                "Or edit the caption by typing your changes."
            ),
            state_update=new_state,
            is_complete=False,
            needs_input="Choose platform (1/2/3) or edit caption",
            status=AgentStatus.COLLECTING_INPUT,
        )

    async def _select_platform(self, message: str, state: dict) -> AgentResponse:
        """Select target platform."""
        post = state.get("post", {})
        choice = message.strip().lower()

        if choice in ("1", "tiktok"):
            post["platforms"] = ["tiktok"]
        elif choice in ("2", "facebook"):
            post["platforms"] = ["facebook"]
        elif choice in ("3", "both"):
            post["platforms"] = ["tiktok", "facebook"]
        else:
            # User edited caption
            post["caption"] = message
            new_state = self._set_step(state, "select_platform", AgentStatus.COLLECTING_INPUT)
            new_state["post"] = post
            return AgentResponse(
                content=f"Updated caption: \"{message[:100]}\"\n\nNow choose platform: 1. TikTok  2. Facebook  3. Both",
                state_update=new_state,
                is_complete=False,
                needs_input="Choose platform",
                status=AgentStatus.COLLECTING_INPUT,
            )

        new_state = self._set_step(state, "confirm_post", AgentStatus.AWAITING_CONFIRMATION)
        new_state["post"] = post
        platforms = ", ".join(post["platforms"])

        return AgentResponse(
            content=(
                f"Ready to post to **{platforms}**:\n\n"
                f"Caption: {post.get('caption', '')}\n"
                f"Hashtags: {post.get('hashtags', '')}\n\n"
                "Confirm? (yes/no)"
            ),
            state_update=new_state,
            is_complete=False,
            needs_input="Confirm posting (yes/no)",
            status=AgentStatus.AWAITING_CONFIRMATION,
        )

    async def _confirm_post(self, message: str, state: dict) -> AgentResponse:
        """Confirm and execute post."""
        if message.lower().strip() in ("yes", "y", "post", "confirm", "go"):
            post = state.get("post", {})
            platforms = post.get("platforms", [])

            # TODO: Call TikTok/Facebook APIs
            new_state = self._complete(state)

            return AgentResponse(
                content=(
                    f"Posted to {', '.join(platforms)}! "
                    "(API integration coming soon — post will be live once connected.)\n\n"
                    "Need to post anything else?"
                ),
                state_update=new_state,
                is_complete=True,
                status=AgentStatus.COMPLETE,
            )
        else:
            new_state = self._set_step(state, "generate_caption", AgentStatus.COLLECTING_INPUT)
            return AgentResponse(
                content="No problem. Tell me what you'd like to change.",
                state_update=new_state,
                is_complete=False,
                needs_input="What would you like to change?",
                status=AgentStatus.COLLECTING_INPUT,
            )
