"""
Presentation agent — Noa.

Generates professional presentations using Gemini 2.5 Flash Image (nanobanana).
When a topic is provided, generates slides immediately in the same turn.
"""

import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ...orchestrator.base_agent import BaseAgent, AgentResponse, AgentStatus
from ...core.storage import get_storage, LocalStorage

logger = logging.getLogger(__name__)


class PresentationAgent(BaseAgent):
    name = "presentation"
    display_name = "Presentation Creator"
    description = (
        "Creates professional presentations with AI-generated slides. "
        "Uses Gemini (nanobanana) to generate beautiful slide images "
        "and packages them into a downloadable PPTX file."
    )
    triggers = [
        "presentation", "create slides", "make a deck",
        "powerpoint", "pptx", "slide deck", "pitch deck",
        "create presentation",
    ]
    capabilities = [
        "AI-generated slide images (Gemini nanobanana)",
        "Professional title and content slides",
        "Downloadable PPTX file output",
        "Clean, modern slide design",
    ]
    required_inputs = ["presentation topic"]

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
        logger.info("Presentation: step=%s msg=%s", step, message[:80])

        if step == "deliver":
            return await self._deliver(state)

        # For start, collect_topic, or generate_slides:
        # Extract topic from message and generate immediately
        topic = self._extract_topic(message, state)

        if not topic or len(topic) < 3:
            return AgentResponse(
                content=(
                    "What would you like the presentation to be about? "
                    "Give me a topic and I'll create a 2-slide deck for you."
                ),
                state_update=self._set_step(state, "collect_topic", AgentStatus.COLLECTING_INPUT),
                is_complete=False,
                needs_input="Presentation topic",
                status=AgentStatus.COLLECTING_INPUT,
            )

        # Topic is ready — generate immediately
        return await self._generate_slides(topic, state, db, user_id, tenant_id)

    # ── Helpers ───────────────────────────────────────────────────────

    def _extract_topic(self, message: str, state: dict) -> str:
        """Extract the topic from the message or existing state."""
        topic = message.strip()
        if topic and len(topic) >= 3:
            return topic
        # Fall back to topic saved in state (from a previous collect step)
        pres = state.get("presentation", {})
        return pres.get("topic", "")

    # ── Generate slides + PPTX (runs in same turn as topic collection) ─

    async def _generate_slides(
        self,
        topic: str,
        state: dict,
        db: AsyncSession,
        user_id: str,
        tenant_id: str,
    ) -> AgentResponse:
        """Generate slide images via Gemini and assemble PPTX."""
        from ...services.presentation_gen import generate_presentation

        logger.info("Generating presentation for topic: %r", topic)

        try:
            pptx_bytes, slide_images = await generate_presentation(topic)
        except Exception as e:
            logger.error("Presentation generation failed: %s", e, exc_info=True)
            new_state = self._set_step(state, "collect_topic", AgentStatus.ERROR)
            return AgentResponse(
                content=(
                    f"Sorry, I ran into an issue generating the slides: {e}\n\n"
                    "Would you like to try again with the same topic or a different one?"
                ),
                state_update=new_state,
                is_complete=False,
                needs_input="Try again or new topic",
                status=AgentStatus.ERROR,
            )

        # Save PPTX to storage
        storage = get_storage()
        safe_name = "".join(c if c.isalnum() or c in " -_" else "" for c in topic)[:50]
        filename = f"{safe_name.strip()}.pptx"

        pres = {"topic": topic, "num_slides": 2}

        try:
            path_or_url = await storage.upload(
                file_bytes=pptx_bytes,
                filename=filename,
                tenant_id=tenant_id,
                folder="presentations",
            )

            # Build servable URL
            if isinstance(storage, LocalStorage):
                file_id = Path(path_or_url).stem
                download_url = f"/v1/upload/{file_id}"
            else:
                download_url = path_or_url

            pres["download_url"] = download_url
            pres["file_id"] = file_id if isinstance(storage, LocalStorage) else ""
            pres["filename"] = filename

        except Exception as e:
            logger.error("Failed to save PPTX: %s", e, exc_info=True)
            new_state = self._set_step(state, "collect_topic", AgentStatus.ERROR)
            return AgentResponse(
                content=f"Generated the slides but failed to save the file: {e}",
                state_update=new_state,
                is_complete=False,
                status=AgentStatus.ERROR,
            )

        # Save slide preview images to storage
        preview_urls = []
        for i, img_bytes in enumerate(slide_images):
            try:
                img_path = await storage.upload(
                    file_bytes=img_bytes,
                    filename=f"slide_{i + 1}.png",
                    tenant_id=tenant_id,
                    folder="presentations",
                )
                if isinstance(storage, LocalStorage):
                    img_id = Path(img_path).stem
                    preview_urls.append(f"/v1/upload/{img_id}")
                else:
                    preview_urls.append(img_path)
            except Exception as e:
                logger.warning("Failed to save slide preview %d: %s", i + 1, e)

        pres["preview_urls"] = preview_urls

        # Save to DB
        try:
            from ...models.presentation import Presentation

            record = Presentation(
                tenant_id=tenant_id,
                user_id=user_id,
                title=topic,
                s3_url=download_url,
                file_id=pres.get("file_id", ""),
                status="completed",
                presentation_metadata={
                    "num_slides": 2,
                    "model": "gemini-2.5-flash-image",
                    "preview_urls": preview_urls,
                },
            )
            db.add(record)
            await db.commit()
        except Exception as e:
            logger.warning("Failed to save presentation record: %s", e)

        new_state = self._set_step(state, "deliver", AgentStatus.COMPLETE)
        new_state["presentation"] = pres

        # Build response with download link and slide previews
        content = (
            f"Your presentation is ready! **{topic}** -- 2 slides\n\n"
            f"[Download PPTX]({download_url})\n\n"
        )

        # Include slide preview images as media URLs
        media = list(preview_urls)

        return AgentResponse(
            content=content,
            media_urls=media,
            state_update=new_state,
            is_complete=True,
            status=AgentStatus.COMPLETE,
            metadata={
                "download_url": download_url,
                "filename": filename,
                "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "preview_urls": preview_urls,
            },
        )

    # ── Deliver (re-entry after completion) ──────────────────────────

    async def _deliver(self, state: dict) -> AgentResponse:
        """Re-deliver the download link or start fresh."""
        pres = state.get("presentation", {})
        download_url = pres.get("download_url")

        if download_url:
            return AgentResponse(
                content=(
                    f"Here's your presentation again: [Download PPTX]({download_url})\n\n"
                    "Would you like me to create a new presentation?"
                ),
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.COMPLETE,
                metadata={
                    "download_url": download_url,
                    "filename": pres.get("filename", "presentation.pptx"),
                },
            )

        # No saved presentation — start fresh
        return AgentResponse(
            content=(
                "What would you like the presentation to be about? "
                "Give me a topic and I'll create a 2-slide deck for you."
            ),
            state_update=self._set_step({}, "collect_topic", AgentStatus.COLLECTING_INPUT),
            is_complete=False,
            needs_input="Presentation topic",
            status=AgentStatus.COLLECTING_INPUT,
        )
