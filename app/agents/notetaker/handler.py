"""
Notetaker agent (Ivy) — joins meetings via Meeting BaaS v2, transcribes, and summarizes.

Pipeline: start → join_meeting → waiting (poll status) → deliver
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

# Statuses that mean "bot is done, check for transcript"
_COMPLETED_STATUSES = {"ended", "completed"}
# Statuses that mean "still in meeting"
_ACTIVE_STATUSES = {"joining", "in_waiting_room", "in_call", "in call", "recording"}


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
        elif step == "select_meeting":
            return await self._select_meeting(message, state, db)
        elif step == "waiting":
            return await self._poll_and_summarize(state, db)
        elif step == "deliver":
            return await self._deliver(message, state, db)
        else:
            return await self._start(message, {}, db, user_id, tenant_id)

    async def _start(
        self, message: str, state: dict, db: AsyncSession, user_id: str, tenant_id: str,
    ) -> AgentResponse:
        """Parse meeting link from message, check for past meetings, or ask for a link."""
        # 1. If there's a meeting URL, join it
        match = MEETING_URL_RE.search(message)
        if match:
            return await self._do_join(match.group(0), state, db, user_id, tenant_id)

        # 2. If user is asking about past meetings/transcripts, look them up
        msg_lower = message.lower()
        wants_past = any(w in msg_lower for w in [
            "transcript", "summary", "notes", "last meeting", "older meeting",
            "previous meeting", "past meeting", "history", "action item",
            "what did we discuss", "what happened in",
        ])
        if wants_past:
            return await self._lookup_past_meetings(message, state, db, tenant_id)

        # 3. Default: offer to join a new meeting
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

    async def _lookup_past_meetings(
        self, message: str, state: dict, db: AsyncSession, tenant_id: str,
    ) -> AgentResponse:
        """Look up past meetings with transcripts from the database."""
        try:
            result = await db.execute(
                select(Call)
                .where(Call.tenant_id == tenant_id, Call.status == "completed")
                .order_by(Call.created_at.desc())
                .limit(5)
            )
            calls = result.scalars().all()
        except Exception as e:
            logger.error("Failed to look up past meetings: %s", e)
            calls = []

        if not calls:
            return AgentResponse(
                content=(
                    "I don't have any past meeting transcripts yet.\n\n"
                    "Want me to join a meeting? Paste a Zoom, Google Meet, or Teams link."
                ),
                state_update=self._set_step(state, "join_meeting", AgentStatus.COLLECTING_INPUT),
                is_complete=False,
                needs_input="Paste a meeting link or say done",
                status=AgentStatus.COLLECTING_INPUT,
            )

        # Show available meetings
        lines = ["Here are your recent meetings:\n"]
        for i, call in enumerate(calls, 1):
            title = call.title or call.platform or "Untitled"
            date = call.created_at.strftime("%b %d, %Y") if call.created_at else "Unknown date"
            has_transcript = "Transcript available" if call.transcript else "No transcript"
            has_summary = " + Summary" if call.summary else ""
            lines.append(f"**{i}.** {title} — {date} ({has_transcript}{has_summary})")

        lines.append("\nWhich meeting do you want to see? Reply with the number, or say **all** for the most recent.")

        # Store call IDs for selection
        new_state = self._set_step(state, "select_meeting", AgentStatus.COLLECTING_INPUT)
        new_state["past_call_ids"] = [call.id for call in calls]

        return AgentResponse(
            content="\n".join(lines),
            state_update=new_state,
            is_complete=False,
            needs_input="Which meeting?",
            status=AgentStatus.COLLECTING_INPUT,
        )

    async def _select_meeting(
        self, message: str, state: dict, db: AsyncSession,
    ) -> AgentResponse:
        """User picks a past meeting to view."""
        past_ids = state.get("past_call_ids", [])
        msg_lower = message.lower()

        # Determine which call to show
        call_id = None
        if "all" in msg_lower or "recent" in msg_lower or "latest" in msg_lower or "1" == msg_lower.strip():
            call_id = past_ids[0] if past_ids else None
        else:
            # Try to parse a number
            for word in message.split():
                if word.isdigit():
                    idx = int(word) - 1
                    if 0 <= idx < len(past_ids):
                        call_id = past_ids[idx]
                    break

        if not call_id and past_ids:
            call_id = past_ids[0]  # Default to most recent

        if not call_id:
            return AgentResponse(
                content="Couldn't find that meeting. Please try again with a number from the list.",
                state_update=state,
                is_complete=False,
                status=AgentStatus.COLLECTING_INPUT,
            )

        result = await db.execute(select(Call).where(Call.id == call_id))
        call = result.scalar_one_or_none()

        if not call:
            return AgentResponse(
                content="Meeting not found. It may have been deleted.",
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.ERROR,
            )

        # Show summary if available, otherwise transcript
        content_parts = []
        title = call.title or call.platform or "Meeting"
        date = call.created_at.strftime("%b %d, %Y at %I:%M %p") if call.created_at else ""
        content_parts.append(f"**{title}** — {date}\n")

        if call.summary:
            content_parts.append(f"**Summary:**\n{call.summary}\n")

        if call.transcript:
            content_parts.append("Want the full transcript?")
        else:
            content_parts.append("No transcript available for this meeting.")

        new_state = self._set_step(state, "deliver", AgentStatus.PROCESSING)
        new_state["call_id"] = call.id
        new_state["bot_id"] = state.get("bot_id")

        return AgentResponse(
            content="\n".join(content_parts),
            state_update=new_state,
            is_complete=False,
            needs_input="Full transcript or done?",
            status=AgentStatus.AWAITING_CONFIRMATION,
        )

    async def _join_meeting(
        self, message: str, state: dict, db: AsyncSession, user_id: str, tenant_id: str,
    ) -> AgentResponse:
        """User provides a meeting link, or asks about past meetings."""
        match = MEETING_URL_RE.search(message)
        if match:
            return await self._do_join(match.group(0), state, db, user_id, tenant_id)

        # No link — check if they're asking about past meetings instead
        msg_lower = message.lower()
        wants_past = any(w in msg_lower for w in [
            "transcript", "summary", "notes", "last meeting", "older", "previous",
            "past", "history", "action item", "what did we discuss",
        ])
        if wants_past:
            return await self._lookup_past_meetings(message, state, db, tenant_id)

        return AgentResponse(
            content="I couldn't find a valid meeting link. Please paste a Zoom, Google Meet, or Teams link.\n\nOr ask me about **past meeting transcripts** if you want to look up an older meeting.",
            state_update=state,
            is_complete=False,
            needs_input="Paste a valid meeting link",
            status=AgentStatus.COLLECTING_INPUT,
        )

    async def _do_join(
        self, meeting_url: str, state: dict, db: AsyncSession, user_id: str, tenant_id: str,
    ) -> AgentResponse:
        """Call Meeting BaaS v2 to send a bot into the meeting."""
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
        """
        Poll Meeting BaaS v2 for bot status. If meeting is done, fetch
        transcript from presigned S3 URL and summarize with Gemini.
        """
        bot_id = state.get("bot_id")
        call_id = state.get("call_id")

        if not bot_id:
            return AgentResponse(
                content="Lost track of the meeting. Please start over with a new link.",
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.ERROR,
            )

        # 1. Lightweight status check first
        try:
            status_data = await meetingbaas.get_bot_status(bot_id)
        except Exception as e:
            logger.error("Failed to poll bot status: %s", e)
            return AgentResponse(
                content=f"Couldn't check meeting status: {e}\n\nTry asking again in a moment.",
                state_update=state,
                is_complete=False,
                status=AgentStatus.PROCESSING,
            )

        if not status_data:
            return AgentResponse(
                content="Couldn't find the meeting bot. It may have expired. Try starting a new session.",
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.ERROR,
            )

        bot_status = status_data.get("status", "").lower()
        logger.info("Notetaker: bot %s status=%s", bot_id, bot_status)

        # 2. Still in meeting — tell user to wait
        if bot_status in _ACTIVE_STATUSES:
            return AgentResponse(
                content=(
                    f"The bot is currently **{bot_status.replace('_', ' ')}**.\n\n"
                    "Ask me again once the meeting is over!"
                ),
                state_update=state,
                is_complete=False,
                status=AgentStatus.PROCESSING,
            )

        # 3. Failed
        if bot_status == "failed":
            if call_id:
                result = await db.execute(select(Call).where(Call.id == call_id))
                call = result.scalar_one_or_none()
                if call:
                    call.status = "failed"
                    await db.commit()
            return AgentResponse(
                content="The meeting bot failed. The meeting may have ended before the bot could join, or access was denied.",
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.ERROR,
            )

        # 4. Meeting ended/completed — fetch full details for transcript
        try:
            details = await meetingbaas.get_bot_details(bot_id)
        except Exception as e:
            logger.error("Failed to get bot details: %s", e)
            return AgentResponse(
                content=f"Meeting ended but couldn't retrieve data: {e}\n\nTry again in a moment.",
                state_update=state,
                is_complete=False,
                status=AgentStatus.PROCESSING,
            )

        if not details:
            return AgentResponse(
                content="Meeting ended but data isn't available yet. Try again in a moment.",
                state_update=state,
                is_complete=False,
                status=AgentStatus.PROCESSING,
            )

        # 5. Extract transcript — v2 provides a presigned S3 URL
        transcript_text = await self._extract_transcript(details)

        if not transcript_text:
            return AgentResponse(
                content=(
                    "The meeting ended but the transcript isn't ready yet "
                    "(transcription may still be processing).\n\n"
                    "Ask me again in a minute!"
                ),
                state_update=state,
                is_complete=False,
                status=AgentStatus.PROCESSING,
            )

        # 6. Save transcript to DB
        if call_id:
            result = await db.execute(select(Call).where(Call.id == call_id))
            call = result.scalar_one_or_none()
            if call:
                call.transcript = transcript_text
                call.status = "processing"

        # 7. Summarize with Gemini
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

        # 8. Save summary to DB
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

    async def _extract_transcript(self, details: dict) -> Optional[str]:
        """
        Extract transcript text from v2 bot details.

        v2 provides transcript as a presigned S3 URL (key: "transcription")
        or may include inline transcript data.
        """
        # Try presigned S3 URL first (v2 primary path)
        transcript_url = details.get("transcription")
        if transcript_url and isinstance(transcript_url, str) and transcript_url.startswith("http"):
            segments = await meetingbaas.fetch_transcript(transcript_url)
            if segments:
                return self._format_segments(segments)

        # Fallback: inline transcript array (v1-style, some endpoints still return this)
        inline = details.get("transcript")
        if inline:
            if isinstance(inline, list):
                return self._format_segments(inline)
            return str(inline)

        return None

    @staticmethod
    def _format_segments(segments: list[dict]) -> str:
        """Format transcript segments into readable text.

        Handles multiple formats:
        - {"speaker": "X", "text": "..."} (standard)
        - {"speaker": "X", "words": [{"word": "..."}]} (word-level)
        - {"speaker": 0, "transcription": "..."} (Gladia)
        - {"channel": "X", "alternatives": [{"transcript": "..."}]} (some STT providers)
        """
        lines = []
        for seg in segments:
            speaker = seg.get("speaker", seg.get("channel", "Unknown"))
            if isinstance(speaker, int):
                speaker = f"Speaker {speaker}"

            # Try multiple text field names
            text = (
                seg.get("text")
                or seg.get("transcription")
                or seg.get("transcript")
                or seg.get("content")
                or seg.get("words")
            )

            # Nested alternatives format (e.g. Google STT)
            if not text and "alternatives" in seg:
                alts = seg["alternatives"]
                if isinstance(alts, list) and alts:
                    text = alts[0].get("transcript", "")

            # If text is a list of word objects, join them
            if isinstance(text, list):
                text = " ".join(
                    w.get("word", w.get("text", "")) if isinstance(w, dict) else str(w)
                    for w in text
                )

            if text:
                lines.append(f"{speaker}: {text}")

        if not lines:
            # Last resort: dump raw segment data
            logger.warning("Could not extract text from %d segments, dumping raw", len(segments))
            return "\n".join(str(seg) for seg in segments[:50])

        return "\n".join(lines)

    async def _deliver(self, message: str, state: dict, db: AsyncSession) -> AgentResponse:
        """Show full transcript if asked, handle follow-ups, then complete."""
        call_id = state.get("call_id")
        msg_lower = message.lower()

        # User wants to leave
        if any(w in msg_lower for w in ["done", "no", "nope", "that's it", "thanks", "thank you", "bye", "exit"]):
            return AgentResponse(
                content="Done! Let me know if you need anything else.",
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.COMPLETE,
            )

        # Show full transcript
        if call_id and any(w in msg_lower for w in ["transcript", "full", "yes", "detail", "show"]):
            result = await db.execute(select(Call).where(Call.id == call_id))
            call = result.scalar_one_or_none()

            if call and call.transcript:
                transcript = call.transcript
                if len(transcript) > 8000:
                    transcript = transcript[:8000] + "\n\n... [truncated]"

                # Stay active for follow-ups instead of completing immediately
                new_state = self._set_step(state, "deliver", AgentStatus.PROCESSING)
                new_state["call_id"] = call_id
                new_state["bot_id"] = state.get("bot_id")
                new_state["_transcript_shown"] = True

                return AgentResponse(
                    content=(
                        f"**Full Transcript:**\n\n{transcript}\n\n"
                        "Anything else about this meeting? Say **done** when you're finished."
                    ),
                    state_update=new_state,
                    is_complete=False,
                    status=AgentStatus.AWAITING_CONFIRMATION,
                )

        # Follow-up question about the meeting — re-summarize with the question
        if call_id and state.get("_transcript_shown"):
            result = await db.execute(select(Call).where(Call.id == call_id))
            call = result.scalar_one_or_none()
            if call and call.transcript:
                try:
                    answer = await chat_simple(
                        prompt=(
                            f"Based on this meeting transcript, answer the user's question.\n\n"
                            f"User question: {message}\n\n"
                            f"Transcript:\n{call.transcript[:12000]}"
                        ),
                        system="You are a meeting assistant. Answer concisely based only on the transcript.",
                        temperature=0.3,
                        max_tokens=1024,
                    )
                except Exception as e:
                    logger.error("Failed to answer follow-up: %s", e)
                    answer = "Sorry, I couldn't process that question."

                new_state = self._set_step(state, "deliver", AgentStatus.PROCESSING)
                new_state["call_id"] = call_id
                new_state["bot_id"] = state.get("bot_id")
                new_state["_transcript_shown"] = True

                return AgentResponse(
                    content=f"{answer}\n\nAnything else? Say **done** when you're finished.",
                    state_update=new_state,
                    is_complete=False,
                    status=AgentStatus.AWAITING_CONFIRMATION,
                )

        # Default: complete
        return AgentResponse(
            content="Done! Let me know if you need anything else.",
            state_update=self._complete(state),
            is_complete=True,
            status=AgentStatus.COMPLETE,
        )
