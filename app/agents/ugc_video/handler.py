"""
UGC Video agent — Veo 3.1 powered.

Pipeline: collect_info → confirm_brief → generate_video → deliver
Generates complete 8-second marketing videos with native audio from text prompts.
"""

import logging
import uuid
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ...orchestrator.base_agent import BaseAgent, AgentResponse, AgentStatus

logger = logging.getLogger(__name__)

STEPS = ["collect_info", "confirm_brief", "generate_video", "deliver"]


class UGCVideoAgent(BaseAgent):
    name = "ugc_video"
    display_name = "UGC Content Creator"
    description = "Creates UGC-style marketing videos with AI-generated video, audio, and scripts using Veo 3.1"
    triggers = [
        "ugc", "ugc video", "content creator", "create video",
        "make a video", "marketing video", "tiktok content",
        "video ad", "product video",
    ]
    capabilities = [
        "AI video generation with native audio (Veo 3.1)",
        "Script and prompt optimization for marketing videos",
        "Multiple aspect ratios (9:16 for TikTok/Reels, 16:9 for YouTube)",
        "Brand-consistent video content",
        "Platform-native output for TikTok, Reels, and YouTube Shorts",
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
        **kwargs,
    ) -> AgentResponse:

        step = self.get_step(state)
        logger.info("UGC Video: step=%s, message=%s", step, message[:80])

        if step in ("start", "collect_info"):
            return await self._collect_info(message, state)
        elif step == "confirm_brief":
            return await self._confirm_brief(message, state, db, user_id, tenant_id)
        elif step == "generate_video":
            return await self._generate_video(state, db, user_id, tenant_id)
        elif step == "deliver":
            return await self._deliver(state)
        else:
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

        if not info.get("product"):
            info["product"] = message.strip()

        if not info.get("audience"):
            info["audience"] = "general audience"
        if not info.get("style"):
            info["style"] = info.get("tone", "casual, energetic")

        # Defaults for video generation
        info.setdefault("aspect_ratio", "9:16")

        new_state = self._set_step(state, "confirm_brief", AgentStatus.COLLECTING_INPUT)
        new_state["brief"] = info

        brand_line = f"- Brand: {info['brand_name']}\n" if info.get("brand_name") else ""
        brief_summary = (
            f"Here's what I have so far:\n"
            f"{brand_line}"
            f"- Product/Topic: {info.get('product', 'Not specified')}\n"
            f"- Target Audience: {info.get('audience', 'General')}\n"
            f"- Style: {info.get('style', 'Casual')}\n"
            f"- Aspect Ratio: {info.get('aspect_ratio', '9:16')}\n\n"
            f"Does this look right? Say 'yes' to proceed, or tell me what to change."
        )

        return AgentResponse(
            content=brief_summary,
            state_update=new_state,
            is_complete=False,
            needs_input="Confirm the brief or tell me what to change",
            status=AgentStatus.COLLECTING_INPUT,
        )

    async def _confirm_brief(
        self, message: str, state: dict,
        db: AsyncSession, user_id: str, tenant_id: str,
    ) -> AgentResponse:
        """Step 2: User confirms → immediately generate prompt + video in one shot."""
        msg_lower = message.lower().strip()

        if msg_lower in ("yes", "y", "looks good", "proceed", "go", "ok", "correct", "confirm"):
            # Auto-advance: generate prompt + video in one go
            return await self._generate_video(state, db, user_id, tenant_id)
        else:
            new_state = self._set_step(state, "collect_info", AgentStatus.COLLECTING_INPUT)
            return AgentResponse(
                content="Got it. Tell me what you'd like to change about the brief.",
                state_update=new_state,
                is_complete=False,
                needs_input="What would you like to change?",
                status=AgentStatus.COLLECTING_INPUT,
            )

    async def _build_veo_prompt(self, brief: dict) -> str:
        """Use LLM to convert the brief into an optimized Veo video prompt."""
        from ...services.llm import chat_simple

        product = brief.get("product", "product")
        audience = brief.get("audience", "general audience")
        style = brief.get("style", "casual, energetic")
        brand_name = brief.get("brand_name", "")

        brand_context = f"Brand: {brand_name}. " if brand_name else ""

        user_prompt = (
            f"{brand_context}"
            f"Product/Topic: {product}. "
            f"Target audience: {audience}. "
            f"Style/Tone: {style}."
        )

        system_prompt = """You are an expert video prompt engineer for Veo 3.1 (Google's AI video model).

Your job: Convert a marketing brief into a single, detailed video generation prompt.

The video will be 8 seconds long with native audio. Write a vivid, specific prompt that describes:
1. The visual scene (setting, lighting, camera angles, movement)
2. The subject/person (appearance, actions, expressions)
3. The product placement and how it's showcased
4. The audio/dialogue (what the person says, background sounds)
5. The mood and energy level

Guidelines:
- Write in present tense, describing what's happening in the video
- Be specific about camera movements (tracking shot, close-up, pull-back, etc.)
- Include natural dialogue that feels authentic UGC (not scripted/corporate)
- Mention lighting and color grading for professional look
- Keep it to one focused scene that tells a complete mini-story
- The video should feel like genuine user-generated content, not a polished ad

Output ONLY the prompt text. No explanations, no markdown, no labels."""

        try:
            veo_prompt = await chat_simple(
                prompt=user_prompt,
                system=system_prompt,
                temperature=0.8,
                max_tokens=500,
            )
            return veo_prompt.strip()
        except Exception as e:
            logger.error("Failed to generate Veo prompt via LLM: %s", e)
            return (
                f"A person enthusiastically reviewing {product} in a well-lit room, "
                f"speaking directly to camera in a {style} tone, "
                f"holding up the product and showing its features, "
                f"natural lighting, UGC style video"
            )

    async def _generate_video(
        self, state: dict, db: AsyncSession, user_id: str, tenant_id: str,
    ) -> AgentResponse:
        """Generate Veo prompt + call Veo 3.1 + upload + save asset. All in one step."""
        from ...services.veo import generate_video
        from ...core.storage import get_storage, LocalStorage

        brief = state.get("brief", {})
        aspect_ratio = brief.get("aspect_ratio", "9:16")

        # Step A: Generate optimized Veo prompt
        veo_prompt = state.get("veo_prompt", "")
        if not veo_prompt:
            veo_prompt = await self._build_veo_prompt(brief)

        logger.info("Veo prompt: %s", veo_prompt[:100])

        # Step B: Call Veo 3.1
        try:
            video_bytes = await generate_video(
                prompt=veo_prompt,
                aspect_ratio=aspect_ratio,
            )

            # Step C: Upload to storage
            storage = get_storage()
            filename = f"ugc_video_{uuid.uuid4().hex[:8]}.mp4"
            path_or_url = await storage.upload(
                file_bytes=video_bytes,
                filename=filename,
                tenant_id=tenant_id,
                folder="ugc_videos",
            )

            # For local storage, convert file path to servable URL (keep .mp4 extension)
            if isinstance(storage, LocalStorage):
                file_name = Path(path_or_url).name
                video_url = f"/v1/upload/{file_name}"
            else:
                video_url = path_or_url

            # Step D: Save UGC asset record
            try:
                from ...models.ugc import UGCAsset
                asset = UGCAsset(
                    tenant_id=tenant_id,
                    conversation_id=state.get("_conversation_id", ""),
                    asset_type="video",
                    s3_url=video_url,
                    prompt=veo_prompt,
                    status="completed",
                    asset_metadata={
                        "generator": "veo-3.1",
                        "aspect_ratio": aspect_ratio,
                        "brief": brief,
                    },
                )
                db.add(asset)
                await db.flush()
                logger.info("Created UGCAsset: id=%s url=%s", asset.id, video_url)
            except Exception as e:
                logger.warning("Could not save UGCAsset record: %s", e)

            # Step E: Deliver
            new_state = self._complete(state)
            new_state["video_url"] = video_url
            new_state["veo_prompt"] = veo_prompt

            return AgentResponse(
                content=(
                    f"Your UGC video is ready!\n\n"
                    f"**Prompt used:** *\"{veo_prompt}\"*\n\n"
                    f"- Product: {brief.get('product', 'N/A')}\n"
                    f"- Style: {brief.get('style', 'N/A')}\n"
                    f"- Aspect Ratio: {aspect_ratio}\n\n"
                    f"Would you like to make another video or do something else?"
                ),
                state_update=new_state,
                media_urls=[video_url],
                is_complete=True,
                status=AgentStatus.COMPLETE,
            )

        except TimeoutError as e:
            logger.error("Veo generation timed out: %s", e)
            new_state = self._set_step(state, "generate_video", AgentStatus.ERROR)
            new_state["veo_prompt"] = veo_prompt
            return AgentResponse(
                content=(
                    "The video generation timed out. This can happen with complex prompts.\n\n"
                    "Say 'retry' to try again, or tell me what to change about the brief."
                ),
                state_update=new_state,
                is_complete=False,
                needs_input="Retry or adjust?",
                status=AgentStatus.ERROR,
            )

        except Exception as e:
            logger.error("Veo generation failed: %s", e)
            new_state = self._set_step(state, "generate_video", AgentStatus.ERROR)
            new_state["veo_prompt"] = veo_prompt
            return AgentResponse(
                content=(
                    f"Video generation failed: {e}\n\n"
                    "Say 'retry' to try again, or tell me what to change."
                ),
                state_update=new_state,
                is_complete=False,
                needs_input="Retry or adjust?",
                status=AgentStatus.ERROR,
            )

    async def _deliver(self, state: dict) -> AgentResponse:
        """Fallback deliver step (normally reached via _generate_video directly)."""
        brief = state.get("brief", {})
        video_url = state.get("video_url", "")
        new_state = self._complete(state)

        return AgentResponse(
            content=(
                "Your UGC video is ready!\n\n"
                f"- Product: {brief.get('product', 'N/A')}\n"
                f"- Style: {brief.get('style', 'N/A')}\n"
                f"- Aspect Ratio: {brief.get('aspect_ratio', '9:16')}\n\n"
                "Would you like to make another video or do something else?"
            ),
            state_update=new_state,
            media_urls=[video_url] if video_url else [],
            is_complete=True,
            status=AgentStatus.COMPLETE,
        )
