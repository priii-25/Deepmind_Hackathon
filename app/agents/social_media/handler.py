"""
Social Media agent — YouTube video upload and publishing.

Production state machine:
  check_connection → collect_details → generate_metadata → review → upload → complete

This agent handles the full YouTube publishing workflow:
  1. Verifies YouTube OAuth connection
  2. Collects video file + basic description from user
  3. Uses LLM to generate optimized title, description, tags
  4. Presents a review of what will be published
  5. Uploads the video to YouTube via resumable upload
  6. Returns the live YouTube URL

Supports:
  - Videos from local storage or S3 (uploaded via the platform)
  - LLM-powered metadata generation (title, description, tags)
  - Privacy settings (public, unlisted, private)
  - Custom category selection
  - Post-upload status checking
"""

import logging
import os
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...orchestrator.base_agent import BaseAgent, AgentResponse, AgentStatus
from ...services import llm
from . import youtube_client
from .youtube_client import UploadResult, YOUTUBE_CATEGORIES, DEFAULT_CATEGORY_ID

logger = logging.getLogger(__name__)

# ── Steps ─────────────────────────────────────────────────────────────

STEPS = [
    "check_connection",
    "collect_details",
    "generate_metadata",
    "review",
    "upload",
    "complete",
]


class SocialMediaAgent(BaseAgent):
    name = "social_media"
    display_name = "Social Media Manager"
    description = (
        "Uploads and publishes videos to YouTube. Handles OAuth connection, "
        "generates optimized titles/descriptions/tags with AI, and manages "
        "the full upload pipeline."
    )
    triggers = [
        "post to youtube", "upload to youtube", "publish on youtube",
        "youtube video", "upload video", "publish video",
        "post video", "share on youtube", "youtube upload",
        "social media", "publish content",
    ]
    capabilities = [
        "YouTube OAuth connection management",
        "Video upload to YouTube (resumable, production-grade)",
        "AI-powered title, description, and tag generation",
        "Privacy setting control (public, unlisted, private)",
        "YouTube category selection",
        "Post-upload status monitoring",
    ]
    required_inputs = ["video file (uploaded or from another agent)", "topic/description"]

    # ── Main handler ──────────────────────────────────────────────

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
        logger.info("SocialMedia: step=%s user=%s msg=%s", step, user_id, message[:80])

        if step in ("start", "check_connection"):
            return await self._check_connection(message, state, db, user_id, tenant_id, files)
        elif step == "collect_details":
            return await self._collect_details(message, state, db, user_id, tenant_id, files)
        elif step == "generate_metadata":
            return await self._generate_metadata(message, state, db, tenant_id)
        elif step == "review":
            return await self._review(message, state, db, user_id, tenant_id)
        elif step == "upload":
            return await self._do_upload(message, state, db, user_id, tenant_id)
        elif step == "complete":
            # Already done — reset if user wants to upload another
            return await self._check_connection(message, {}, db, user_id, tenant_id, files)
        else:
            return await self._check_connection(message, {}, db, user_id, tenant_id, files)

    # ── Step 1: Check YouTube connection ──────────────────────────

    async def _check_connection(
        self, message: str, state: dict, db: AsyncSession,
        user_id: str, tenant_id: str, files: Optional[list],
    ) -> AgentResponse:
        """Verify the user has connected their YouTube account."""
        tokens = await _load_youtube_tokens(db, user_id, tenant_id)

        if not tokens:
            # Not connected — provide OAuth URL
            try:
                oauth_state = f"{user_id}:{tenant_id}"
                auth_url = youtube_client.get_auth_url(state=oauth_state)

                new_state = self._set_step(state, "check_connection", AgentStatus.COLLECTING_INPUT)
                new_state["_awaiting_oauth"] = True

                return AgentResponse(
                    content=(
                        "To upload videos to YouTube, I need access to your YouTube account.\n\n"
                        f"**[Connect YouTube Account]({auth_url})**\n\n"
                        "Click the link above to authorize access. Once connected, "
                        "come back and tell me what you'd like to upload."
                    ),
                    state_update=new_state,
                    is_complete=False,
                    needs_input="Connect YouTube account, then send a message to continue",
                    status=AgentStatus.COLLECTING_INPUT,
                )
            except ValueError as e:
                return AgentResponse(
                    content=(
                        f"YouTube integration is not configured yet: {e}\n\n"
                        "Please ask your admin to set up GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, "
                        "and YOUTUBE_REDIRECT_URI in the environment."
                    ),
                    state_update=self._complete(state),
                    is_complete=True,
                    status=AgentStatus.ERROR,
                )

        # Connected — check if token is still valid
        if tokens.get("is_expired"):
            refresh = tokens.get("refresh_token")
            if refresh:
                try:
                    new_access, new_expires = await youtube_client.ensure_valid_token(
                        tokens["access_token"], refresh, tokens.get("expires_at"),
                    )
                    if new_expires:
                        await _update_token(db, user_id, tenant_id, new_access, new_expires)
                        tokens["access_token"] = new_access
                except Exception as e:
                    logger.error("Token refresh failed: %s", e)
                    # Clear expired tokens and re-prompt
                    await _delete_youtube_tokens(db, user_id, tenant_id)
                    return await self._check_connection(message, {}, db, user_id, tenant_id, files)

        channel_name = tokens.get("channel_title", "your channel")

        # Move to collect_details
        new_state = self._set_step(state, "collect_details", AgentStatus.COLLECTING_INPUT)
        new_state["youtube_connected"] = True

        # If the user already provided a file or description, fast-forward
        if files or (message and not _is_greeting(message)):
            return await self._collect_details(message, new_state, db, user_id, tenant_id, files)

        return AgentResponse(
            content=(
                f"Connected to YouTube as **{channel_name}**.\n\n"
                "What would you like to upload? You can:\n"
                "- Send me a video file (or tell me the filename if already uploaded)\n"
                "- Describe what the video is about and I'll help with the title, description, and tags\n\n"
                "What video are you working with?"
            ),
            state_update=new_state,
            is_complete=False,
            needs_input="Video file or description of what to upload",
            status=AgentStatus.COLLECTING_INPUT,
        )

    # ── Step 2: Collect video details ─────────────────────────────

    async def _collect_details(
        self, message: str, state: dict, db: AsyncSession,
        user_id: str, tenant_id: str, files: Optional[list],
    ) -> AgentResponse:
        """Collect video file path and basic description."""
        upload_data = state.get("upload", {})

        # Check for files passed in
        if files:
            video_file = _find_video_file(files)
            if video_file:
                upload_data["video_path"] = video_file
                logger.info("Video file provided: %s", video_file)

        # Check if message references a file
        if not upload_data.get("video_path"):
            found = _extract_file_path(message, tenant_id)
            if found:
                upload_data["video_path"] = found
                logger.info("Video file extracted from message: %s", found)

        # Store description/topic
        if message and not _is_file_reference(message):
            upload_data["topic"] = message.strip()

        # Check what we still need
        has_video = bool(upload_data.get("video_path"))
        has_topic = bool(upload_data.get("topic"))

        new_state = dict(state)
        new_state["upload"] = upload_data

        if not has_video:
            new_state = self._set_step(new_state, "collect_details", AgentStatus.COLLECTING_INPUT)
            return AgentResponse(
                content=(
                    "I need the video file to upload. You can:\n"
                    "1. **Upload a video** through the chat\n"
                    "2. Tell me the **filename** of a video you've already uploaded\n"
                    "3. If Kai made a video for you, just say **\"use the latest video\"**"
                ),
                state_update=new_state,
                is_complete=False,
                needs_input="Video file",
                status=AgentStatus.COLLECTING_INPUT,
            )

        if not has_topic:
            new_state = self._set_step(new_state, "collect_details", AgentStatus.COLLECTING_INPUT)
            return AgentResponse(
                content=(
                    f"Got the video: `{Path(upload_data['video_path']).name}`\n\n"
                    "Now tell me:\n"
                    "- What's this video about?\n"
                    "- Who's the target audience?\n"
                    "- Any specific keywords or hashtags to include?\n\n"
                    "A short description is fine. I'll use AI to craft the perfect title and tags."
                ),
                state_update=new_state,
                is_complete=False,
                needs_input="Video description/topic",
                status=AgentStatus.COLLECTING_INPUT,
            )

        # We have both — move to metadata generation
        new_state = self._set_step(new_state, "generate_metadata", AgentStatus.PROCESSING)
        return await self._generate_metadata("", new_state, db, tenant_id)

    # ── Step 3: Generate metadata with LLM ────────────────────────

    async def _generate_metadata(
        self, message: str, state: dict, db: AsyncSession, tenant_id: str,
    ) -> AgentResponse:
        """Use LLM to generate optimized title, description, and tags."""
        upload_data = state.get("upload", {})
        topic = upload_data.get("topic", "")
        brand = state.get("_brand", {})

        # If user is responding to a review with edits, handle that
        if message.strip() and state.get("_metadata_generated"):
            upload_data["user_edits"] = message.strip()

        brand_name = brand.get("name", "")
        brand_industry = brand.get("industry", "")
        brand_tone = brand.get("tone_of_voice", "")

        prompt = f"""Generate YouTube video metadata for an upload.

Video topic/description from the user:
"{topic}"

{f'User requested edits: "{upload_data.get("user_edits", "")}"' if upload_data.get("user_edits") else ''}

Brand context:
- Brand name: {brand_name or 'Not specified'}
- Industry: {brand_industry or 'Not specified'}  
- Tone of voice: {brand_tone or 'Professional and engaging'}

Generate the following in JSON format:
{{
    "title": "A compelling YouTube title (max 100 chars). Use power words, be specific.",
    "description": "A well-structured YouTube description (max 2000 chars). Include:\\n- Brief hook (first 2 lines visible before 'Show more')\\n- Key points\\n- Call to action\\n- Relevant links placeholder",
    "tags": ["tag1", "tag2", "tag3", "...up to 15 relevant tags"],
    "category_id": "YouTube category ID (default '22' for People & Blogs, '26' for Howto, '28' for Science & Tech)",
    "suggested_privacy": "public or unlisted — suggest based on content type"
}}

Rules:
- Title should be SEO-optimized and click-worthy (not clickbait)
- Description first 2 lines are critical (they show in search results)
- Tags should mix broad and specific terms
- Include brand name in tags if provided
- Keep it professional and authentic"""

        try:
            raw_response = await llm.chat_simple(
                prompt=prompt,
                system="You are a YouTube SEO expert. Return ONLY valid JSON, no markdown code fences.",
                temperature=0.7,
                max_tokens=1500,
            )

            # Parse JSON from response
            metadata = _parse_json_response(raw_response)

            if not metadata or "title" not in metadata:
                raise ValueError("LLM did not return valid metadata")

            upload_data["title"] = metadata["title"]
            upload_data["description"] = metadata["description"]
            upload_data["tags"] = metadata.get("tags", [])
            upload_data["category_id"] = metadata.get("category_id", DEFAULT_CATEGORY_ID)
            upload_data["privacy"] = metadata.get("suggested_privacy", "private")

        except Exception as e:
            logger.warning("LLM metadata generation failed: %s — using defaults", e)
            # Fallback to basic metadata
            if not upload_data.get("title"):
                upload_data["title"] = topic[:100] if topic else "Video Upload"
            if not upload_data.get("description"):
                upload_data["description"] = topic or "Uploaded via Teems"
            if not upload_data.get("tags"):
                upload_data["tags"] = []
            upload_data["category_id"] = upload_data.get("category_id", DEFAULT_CATEGORY_ID)
            upload_data["privacy"] = upload_data.get("privacy", "private")

        new_state = self._set_step(state, "review", AgentStatus.AWAITING_CONFIRMATION)
        new_state["upload"] = upload_data
        new_state["_metadata_generated"] = True

        # Format review
        tags_str = ", ".join(upload_data.get("tags", [])[:15])
        category_name = youtube_client.get_category_name(upload_data.get("category_id", DEFAULT_CATEGORY_ID))
        privacy = upload_data.get("privacy", "private")
        video_name = Path(upload_data.get("video_path", "video")).name

        return AgentResponse(
            content=(
                "Here's what I've prepared for YouTube:\n\n"
                f"**Video:** `{video_name}`\n\n"
                f"**Title:** {upload_data['title']}\n\n"
                f"**Description:**\n{upload_data['description'][:500]}{'...' if len(upload_data.get('description', '')) > 500 else ''}\n\n"
                f"**Tags:** {tags_str}\n\n"
                f"**Category:** {category_name}\n"
                f"**Privacy:** {privacy}\n\n"
                "---\n"
                "Options:\n"
                "- **\"Upload\"** or **\"Post it\"** to publish\n"
                "- **\"Make it public/unlisted/private\"** to change privacy\n"
                "- **\"Change the title to ...\"** to edit any field\n"
                "- **\"Regenerate\"** to get new suggestions\n"
                "- **\"Cancel\"** to stop"
            ),
            state_update=new_state,
            is_complete=False,
            needs_input="Confirm upload, edit fields, or cancel",
            status=AgentStatus.AWAITING_CONFIRMATION,
        )

    # ── Step 4: Review and confirm ────────────────────────────────

    async def _review(
        self, message: str, state: dict, db: AsyncSession,
        user_id: str, tenant_id: str,
    ) -> AgentResponse:
        """Handle user review: approve, edit, or cancel."""
        msg = message.strip().lower()
        upload_data = state.get("upload", {})

        # Cancel
        if msg in ("cancel", "stop", "no", "nevermind", "nvm"):
            return AgentResponse(
                content="Upload cancelled. Let me know whenever you want to try again.",
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.COMPLETE,
            )

        # Regenerate
        if msg in ("regenerate", "redo", "try again", "new suggestions"):
            new_state = self._set_step(state, "generate_metadata", AgentStatus.PROCESSING)
            new_state["_metadata_generated"] = False
            return await self._generate_metadata("", new_state, db, tenant_id)

        # Approve — proceed to upload
        if msg in ("upload", "post", "post it", "publish", "go", "yes", "confirm", "do it", "send it", "y", "ok"):
            new_state = self._set_step(state, "upload", AgentStatus.PROCESSING)
            return await self._do_upload("", new_state, db, user_id, tenant_id)

        # Privacy change
        for privacy in ("public", "unlisted", "private"):
            if privacy in msg:
                upload_data["privacy"] = privacy
                new_state = dict(state)
                new_state["upload"] = upload_data
                new_state = self._set_step(new_state, "review", AgentStatus.AWAITING_CONFIRMATION)

                return AgentResponse(
                    content=f"Privacy set to **{privacy}**. Ready to upload? Say **\"upload\"** to proceed.",
                    state_update=new_state,
                    is_complete=False,
                    needs_input="Confirm upload",
                    status=AgentStatus.AWAITING_CONFIRMATION,
                )

        # Title/description/tag edits — send back through metadata gen with edits
        if any(kw in msg for kw in ("change", "edit", "update", "make the", "set the")):
            new_state = self._set_step(state, "generate_metadata", AgentStatus.PROCESSING)
            return await self._generate_metadata(message, new_state, db, tenant_id)

        # Unclear — ask again
        return AgentResponse(
            content=(
                "I didn't catch that. You can:\n"
                "- Say **\"upload\"** to publish to YouTube\n"
                "- Say **\"public\"**, **\"unlisted\"**, or **\"private\"** to change privacy\n"
                "- Describe what you want to change\n"
                "- Say **\"cancel\"** to stop"
            ),
            state_update=state,
            is_complete=False,
            needs_input="Confirm, edit, or cancel",
            status=AgentStatus.AWAITING_CONFIRMATION,
        )

    # ── Step 5: Upload to YouTube ─────────────────────────────────

    async def _do_upload(
        self, message: str, state: dict, db: AsyncSession,
        user_id: str, tenant_id: str,
    ) -> AgentResponse:
        """Execute the actual YouTube upload."""
        upload_data = state.get("upload", {})
        video_path = upload_data.get("video_path", "")

        # Load tokens
        tokens = await _load_youtube_tokens(db, user_id, tenant_id)
        if not tokens:
            new_state = self._set_step(state, "check_connection", AgentStatus.COLLECTING_INPUT)
            return AgentResponse(
                content="YouTube connection lost. Let me reconnect you...",
                state_update=new_state,
                is_complete=False,
                status=AgentStatus.COLLECTING_INPUT,
            )

        # Ensure token is valid
        try:
            access_token, new_expires = await youtube_client.ensure_valid_token(
                tokens["access_token"],
                tokens.get("refresh_token"),
                tokens.get("expires_at"),
            )
            if new_expires:
                await _update_token(db, user_id, tenant_id, access_token, new_expires)
        except Exception as e:
            logger.error("Token validation failed: %s", e)
            return AgentResponse(
                content=f"YouTube authentication error: {e}\nPlease reconnect your YouTube account.",
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.ERROR,
            )

        # Read video file
        try:
            video_bytes = await _read_video_file(video_path, tenant_id)
        except FileNotFoundError as e:
            return AgentResponse(
                content=f"Could not find the video file: {e}\nPlease upload the video again.",
                state_update=self._set_step(state, "collect_details", AgentStatus.COLLECTING_INPUT),
                is_complete=False,
                needs_input="Video file",
                status=AgentStatus.COLLECTING_INPUT,
            )
        except Exception as e:
            return AgentResponse(
                content=f"Error reading video file: {e}",
                state_update=self._complete(state),
                is_complete=True,
                status=AgentStatus.ERROR,
            )

        file_size_mb = len(video_bytes) / (1024 * 1024)
        logger.info("Uploading to YouTube: %.1fMB file=%s", file_size_mb, Path(video_path).name)

        # Execute upload
        result: UploadResult = await youtube_client.upload_video(
            access_token=access_token,
            video_bytes=video_bytes,
            title=upload_data.get("title", "Video Upload"),
            description=upload_data.get("description", ""),
            tags=upload_data.get("tags", []),
            category_id=upload_data.get("category_id", DEFAULT_CATEGORY_ID),
            privacy_status=upload_data.get("privacy", "private"),
        )

        new_state = self._complete(state)
        new_state["upload"] = upload_data

        if result.success:
            new_state["upload"]["video_id"] = result.video_id
            new_state["upload"]["video_url"] = result.video_url

            # Save post record to database
            await _save_post_record(
                db, user_id, tenant_id,
                video_id=result.video_id,
                video_url=result.video_url,
                title=upload_data.get("title", ""),
                description=upload_data.get("description", ""),
                tags=upload_data.get("tags", []),
                privacy=upload_data.get("privacy", "private"),
                category_id=upload_data.get("category_id", DEFAULT_CATEGORY_ID),
            )

            privacy = upload_data.get("privacy", "private")
            privacy_note = ""
            if privacy == "private":
                privacy_note = "\n\nThe video is set to **private**. You can change it to public from YouTube Studio or ask me to update it."
            elif privacy == "unlisted":
                privacy_note = "\n\nThe video is **unlisted**. Only people with the link can see it."

            return AgentResponse(
                content=(
                    f"Video uploaded to YouTube!\n\n"
                    f"**{upload_data.get('title', 'Your Video')}**\n\n"
                    f"**Link:** {result.video_url}\n"
                    f"**Status:** Processing (YouTube is encoding your video){privacy_note}\n\n"
                    "It may take a few minutes for YouTube to finish processing. "
                    "Want to upload another video or do anything else?"
                ),
                media_urls=[result.video_url] if result.video_url else [],
                state_update=new_state,
                is_complete=True,
                status=AgentStatus.COMPLETE,
            )
        else:
            return AgentResponse(
                content=(
                    f"Upload failed: {result.error}\n\n"
                    "Would you like to:\n"
                    "- **\"Try again\"** to retry the upload\n"
                    "- **\"Cancel\"** to stop"
                ),
                state_update=self._set_step(state, "review", AgentStatus.AWAITING_CONFIRMATION),
                is_complete=False,
                needs_input="Retry or cancel",
                status=AgentStatus.ERROR,
            )


# ── Database helpers ──────────────────────────────────────────────────

async def _load_youtube_tokens(
    db: AsyncSession, user_id: str, tenant_id: str,
) -> Optional[dict]:
    """Load stored YouTube OAuth tokens from the database."""
    try:
        from ...models.social_media import SocialToken

        result = await db.execute(
            select(SocialToken).where(
                SocialToken.tenant_id == tenant_id,
                SocialToken.user_id == user_id,
                SocialToken.platform == "youtube",
            )
        )
        token_row = result.scalar_one_or_none()

        if not token_row:
            return None

        from datetime import datetime, timezone
        is_expired = False
        expires_at = token_row.expires_at
        if expires_at:
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            is_expired = datetime.now(timezone.utc) >= expires_at

        return {
            "access_token": token_row.access_token,
            "refresh_token": token_row.refresh_token,
            "expires_at": expires_at,
            "platform_user_id": token_row.platform_user_id,
            "channel_title": (token_row.token_metadata or {}).get("channel_title", ""),
            "is_expired": is_expired,
        }
    except Exception as e:
        logger.error("Failed to load YouTube tokens: %s", e)
        return None


async def _update_token(
    db: AsyncSession, user_id: str, tenant_id: str,
    new_access_token: str, new_expires_at,
) -> None:
    """Update the access token after a refresh."""
    try:
        from ...models.social_media import SocialToken

        result = await db.execute(
            select(SocialToken).where(
                SocialToken.tenant_id == tenant_id,
                SocialToken.user_id == user_id,
                SocialToken.platform == "youtube",
            )
        )
        token_row = result.scalar_one_or_none()
        if token_row:
            token_row.access_token = new_access_token
            if new_expires_at:
                token_row.expires_at = new_expires_at
            await db.commit()
    except Exception as e:
        logger.error("Failed to update YouTube token: %s", e)
        await db.rollback()


async def _delete_youtube_tokens(
    db: AsyncSession, user_id: str, tenant_id: str,
) -> None:
    """Remove YouTube tokens (used when tokens are irrecoverably expired)."""
    try:
        from ...models.social_media import SocialToken

        result = await db.execute(
            select(SocialToken).where(
                SocialToken.tenant_id == tenant_id,
                SocialToken.user_id == user_id,
                SocialToken.platform == "youtube",
            )
        )
        token_row = result.scalar_one_or_none()
        if token_row:
            await db.delete(token_row)
            await db.commit()
    except Exception as e:
        logger.error("Failed to delete YouTube tokens: %s", e)
        await db.rollback()


async def _save_post_record(
    db: AsyncSession, user_id: str, tenant_id: str, **kwargs,
) -> None:
    """Save a record of the YouTube upload to the social_posts table."""
    try:
        from ...models.social_media import SocialPost

        post = SocialPost(
            tenant_id=tenant_id,
            user_id=user_id,
            platform="youtube",
            platform_post_id=kwargs.get("video_id", ""),
            content_url=kwargs.get("video_url", ""),
            caption=kwargs.get("title", ""),
            hashtags=",".join(kwargs.get("tags", [])),
            status="posted",
            post_metadata={
                "description": kwargs.get("description", ""),
                "privacy": kwargs.get("privacy", "private"),
                "category_id": kwargs.get("category_id", ""),
            },
        )
        db.add(post)
        await db.commit()
        logger.info("Saved YouTube post record: video_id=%s", kwargs.get("video_id"))
    except Exception as e:
        logger.error("Failed to save post record: %s", e)
        await db.rollback()


# ── File helpers ──────────────────────────────────────────────────────

async def _read_video_file(path: str, tenant_id: str) -> bytes:
    """
    Read a video file from local storage or S3.

    Handles:
      - Absolute local paths (e.g., ./local_storage/dev-tenant/uploads/abc.mp4)
      - Relative paths (e.g., uploads/abc.mp4 → resolved under local_storage)
      - S3 URLs (downloads the file)
    """
    # S3 URL
    if path.startswith("https://") and "s3" in path:
        import httpx
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(path)
            resp.raise_for_status()
            return resp.content

    # Local file
    local_path = Path(path)

    # Try as-is first
    if local_path.exists() and local_path.is_file():
        return local_path.read_bytes()

    # Try under local_storage/tenant_id
    alt_path = Path("local_storage") / tenant_id / path
    if alt_path.exists() and alt_path.is_file():
        return alt_path.read_bytes()

    # Try under local_storage/tenant_id/uploads
    alt_path2 = Path("local_storage") / tenant_id / "uploads" / Path(path).name
    if alt_path2.exists() and alt_path2.is_file():
        return alt_path2.read_bytes()

    raise FileNotFoundError(
        f"Video file not found: {path}. "
        f"Checked: {local_path}, {alt_path}, {alt_path2}"
    )


def _find_video_file(files: list) -> Optional[str]:
    """Find a video file from the files list."""
    video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
    for f in files:
        f_str = str(f)
        ext = Path(f_str).suffix.lower()
        if ext in video_extensions:
            return f_str
    # If no video extension match, return the first file (might be a video with no extension)
    return str(files[0]) if files else None


def _extract_file_path(message: str, tenant_id: str) -> Optional[str]:
    """Try to extract a file path from the user's message."""
    # Check for quoted paths
    import re
    quoted = re.findall(r'["\']([^"\']+)["\']', message)
    for q in quoted:
        if _looks_like_file(q):
            return q

    # Check for file-like tokens
    for word in message.split():
        if _looks_like_file(word):
            # Try to resolve
            p = Path(word)
            if p.exists():
                return word
            # Try under local_storage
            alt = Path("local_storage") / tenant_id / "uploads" / p.name
            if alt.exists():
                return str(alt)
            return word

    return None


def _looks_like_file(s: str) -> bool:
    """Check if a string looks like a file path/name."""
    video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv", ".m4v"}
    return Path(s).suffix.lower() in video_extensions


def _is_file_reference(message: str) -> bool:
    """Check if the message is primarily a file reference."""
    return _looks_like_file(message.strip())


def _is_greeting(message: str) -> bool:
    """Check if message is just a greeting."""
    greetings = {"hi", "hello", "hey", "yo", "sup", "howdy", "greetings"}
    return message.strip().lower().rstrip("!.") in greetings


def _parse_json_response(text: str) -> Optional[dict]:
    """Parse JSON from LLM response, handling markdown code fences."""
    import json
    import re

    # Remove markdown code fences
    text = re.sub(r'```json\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    return None
