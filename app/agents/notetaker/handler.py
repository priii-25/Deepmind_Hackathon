"""
Notetaker agent (Ivy) — joins meetings via Meeting BaaS, transcribes, and summarizes.

Pipeline: start → join_meeting → waiting (poll) → deliver
"""

import logging
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...orchestrator.base_agent import BaseAgent, AgentResponse, AgentStatus
from ...models.meeting import Call
from ...services import meetingbaas
from ...services.llm import chat_simple

logger = logging.getLogger(__name__)

MEETING_URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)?(?:zoom\.us|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com)/\S+",
    re.IGNORECASE,
)


class NotetakerAgent(BaseAgent):
    name = "notetaker"
    display_name = "Meeting Notetaker"
    description = "Joins meetings to transcribe, summarize, and extract action items automatically"
    triggers = [
        "meeting", "notetaker", "record meeting", "transcribe",
        "join my call", "meeting notes", "take notes",
    ]
    capabilities = [
        "Join Zoom, Google Meet, and Teams meetings",
        "Real-time transcription",
        "AI-powered meeting summaries",
        "Action item extraction",
    ]
    required_inputs = ["meeting link"]

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
        logger.info("Notetaker: step=%s", step)

        if step in ("start", "check_calendar"):
            return await self._start(message, state, db, user_id, tenant_id)
        elif step == "join_meeting":
            return await self._join_meeting(message, state, db, user_id, tenant_id)
        elif step == "waiting":
            return await self._poll_and_summarize(state, db)
        elif step == "deliver":
            return await self._deliver(message, state, db)
        else:
            return await self._start(message, {}, db, user_id, tenant_id)

    async def _start(
        self, message: str, state: dict, db: AsyncSession, user_id: str, tenant_id: str,
    ) -> AgentResponse:
        """Parse meeting link from message or ask for one."""
        match = MEETING_URL_RE.search(message)
        if match:
            return await self._do_join(match.group(0), state, db, user_id, tenant_id)

        new_state = self._set_step(state, "join_meeting", AgentStatus.COLLECTING_INPUT)
        return AgentResponse(
            content=(
                "I can join your meeting and take notes!\n\n"
                "Paste your meeting link and I'll join, transcribe, and summarize everything.\n\n"
                "Supported:\n"
                "- Zoom (`zoom.us/j/...`)\n"
                "- Google Meet (`meet.google.com/...`)\n"
                "- Microsoft Teams"
            ),
            state_update=new_state,
            is_complete=False,
            needs_input="Paste your meeting link",
            status=AgentStatus.COLLECTING_INPUT,
        )

    async def _join_meeting(
        self, message: str, state: dict, db: AsyncSession, user_id: str, tenant_id: str,
    ) -> AgentResponse:
        """User provides a meeting link."""
        match = MEETING_URL_RE.search(message)
        if not match:
            return AgentResponse(
                content="I couldn't find a valid meeting link. Please paste a Zoom, Google Meet, or Teams link.",
                state_update=state,
                is_complete=False,
                needs_input="Paste a valid meeting link",
                status=AgentStatus.COLLECTING_INPUT,
            )
        return await self._do_join(match.group(0), state, db, user_id, tenant_id)

    async def _do_join(
        self, meeting_url: str, state: dict, db: AsyncSession, user_id: str, tenant_id: str,
    ) -> AgentResponse:
        """Call Meeting BaaS to send a bot into the meeting."""
        if "zoom.us" in meeting_url:
            platform = "zoom"
        elif "meet.google" in meeting_url:
            platform = "google_meet"
        elif "teams.microsoft" in meeting_url or "teams.live" in meeting_url:
            platform = "teams"
        else:
            platform = "unknown"

        call = Call(
            tenant_id=tenant_id,
            user_id=user_id,
            meeting_link=meeting_url,
            platform=platform,
            status="joining",
        )
        db.add(call)
        await db.flush()

        try:
            result = await meetingbaas.join_meeting(meeting_url)
            bot_id = result.get("bot_id") or result.get("id", "")

            call.meetingbaas_bot_id = bot_id
            await db.commit()

            new_state = self._set_step(state, "waiting", AgentStatus.PROCESSING)
            new_state["call_id"] = call.id
            new_state["bot_id"] = bot_id

            return AgentResponse(
                content=(
                    f"Joining your {platform.replace('_', ' ').title()} meeting now!\n\n"
                    f"**Ivy Notetaker** will appear in the meeting shortly.\n\n"
                    f"Once the meeting ends, just ask me for the notes and I'll have them ready."
                ),
                state_update=new_state,
                is_complete=False,
                status=AgentStatus.PROCESSING,
            )

        except Exception as e:
            logger.error("Failed to join meeting: %s", e)
            call.status = "failed"
            call.call_metadata = {"error": str(e)}
            await db.commit()

            return AgentResponse(
                content=(
                    f"Couldn't join the meeting: {e}\n\n"
                    "Check that the link is valid and the meeting has started."
                ),
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.ERROR,
            )

    async def _poll_and_summarize(self, state: dict, db: AsyncSession) -> AgentResponse:
        """Poll Meeting BaaS for transcript. If ready, summarize with Gemini."""
        bot_id = state.get("bot_id")
        call_id = state.get("call_id")

        if not bot_id:
            return AgentResponse(
                content="Lost track of the meeting. Please start over with a new link.",
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.ERROR,
            )

        # Poll Meeting BaaS
        try:
            data = await meetingbaas.get_meeting_data(bot_id)
        except Exception as e:
            logger.error("Failed to poll meeting data: %s", e)
            return AgentResponse(
                content=f"Couldn't check meeting status: {e}\n\nTry asking again in a moment.",
                state_update=state,
                is_complete=False,
                status=AgentStatus.PROCESSING,
            )

        if not data or not data.get("transcript"):
            return AgentResponse(
                content=(
                    "The meeting is still in progress (or just ended and is being processed).\n\n"
                    "Ask me again once the meeting is over!"
                ),
                state_update=state,
                is_complete=False,
                status=AgentStatus.PROCESSING,
            )

        # Transcript is ready — format it
        transcript_parts = data["transcript"]
        if isinstance(transcript_parts, list):
            transcript_text = "\n".join(
                f"{seg.get('speaker', 'Unknown')}: {seg.get('text', '')}"
                for seg in transcript_parts
            )
        else:
            transcript_text = str(transcript_parts)

        # Save transcript to DB
        if call_id:
            result = await db.execute(select(Call).where(Call.id == call_id))
            call = result.scalar_one_or_none()
            if call:
                call.transcript = transcript_text
                call.status = "processing"

        # Summarize with Gemini
        try:
            summary = await chat_simple(
                prompt=(
                    "Summarize this meeting transcript concisely. Include:\n"
                    "1. Brief summary (2-3 sentences)\n"
                    "2. Key decisions\n"
                    "3. Action items with owners if mentioned\n\n"
                    f"Transcript:\n{transcript_text[:12000]}"
                ),
                system="You are a meeting summarizer. Be concise and actionable.",
                temperature=0.3,
                max_tokens=2048,
            )
        except Exception as e:
            logger.error("Failed to summarize: %s", e)
            summary = "Couldn't generate summary, but transcript is available."

        # Save summary to DB
        if call_id:
            result = await db.execute(select(Call).where(Call.id == call_id))
            call = result.scalar_one_or_none()
            if call:
                call.summary = summary
                call.status = "completed"
                await db.commit()

        new_state = self._set_step(state, "deliver", AgentStatus.PROCESSING)
        new_state["call_id"] = call_id
        new_state["bot_id"] = bot_id

        return AgentResponse(
            content=(
                f"**Meeting Notes:**\n\n{summary}\n\n"
                "Want the full transcript?"
            ),
            state_update=new_state,
            is_complete=False,
            needs_input="Full transcript or done?",
            status=AgentStatus.AWAITING_CONFIRMATION,
        )

    async def _deliver(self, message: str, state: dict, db: AsyncSession) -> AgentResponse:
        """Show full transcript if asked, then complete."""
        call_id = state.get("call_id")
        msg_lower = message.lower()

        if call_id and any(w in msg_lower for w in ["transcript", "full", "yes", "detail", "show"]):
            result = await db.execute(select(Call).where(Call.id == call_id))
            call = result.scalar_one_or_none()

            if call and call.transcript:
                transcript = call.transcript
                if len(transcript) > 8000:
                    transcript = transcript[:8000] + "\n\n... [truncated]"

                return AgentResponse(
                    content=f"**Full Transcript:**\n\n{transcript}",
                    state_update=self._complete(state),
                    is_complete=True,
                    status=AgentStatus.COMPLETE,
                )

        return AgentResponse(
            content="Done! Let me know if you need anything else.",
            state_update=self._complete(state),
            is_complete=True,
            status=AgentStatus.COMPLETE,
        )
