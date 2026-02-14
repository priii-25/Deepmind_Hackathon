"""
YouTube OAuth 2.0 endpoints.

Handles the Google OAuth flow for connecting YouTube accounts:
  GET  /v1/youtube/auth-url   → Returns the Google authorization URL
  GET  /v1/youtube/callback   → Handles the OAuth redirect, stores tokens
  GET  /v1/youtube/status     → Check if YouTube is connected + channel info
  POST /v1/youtube/disconnect → Remove YouTube connection
  GET  /v1/youtube/video/{id} → Check processing status of an uploaded video
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.auth import AuthenticatedUser
from ..core.dependencies import require_tenant, get_db
from ..agents.social_media import youtube_client

logger = logging.getLogger(__name__)

youtube_router = APIRouter(prefix="/youtube", tags=["youtube"])


# ── Response models ───────────────────────────────────────────────────

class AuthURLResponse(BaseModel):
    auth_url: str
    message: str = "Redirect the user to this URL to authorize YouTube access."


class OAuthCallbackResponse(BaseModel):
    success: bool
    channel_title: Optional[str] = None
    channel_id: Optional[str] = None
    message: str = ""


class YouTubeStatusResponse(BaseModel):
    connected: bool
    channel_title: Optional[str] = None
    channel_id: Optional[str] = None
    subscriber_count: Optional[int] = None
    video_count: Optional[int] = None
    expires_at: Optional[str] = None


class VideoStatusResponse(BaseModel):
    video_id: str
    title: str = ""
    status: str = ""
    privacy: str = ""
    url: str = ""
    processing_status: str = ""


# ── GET /v1/youtube/auth-url ──────────────────────────────────────────

@youtube_router.get("/auth-url", response_model=AuthURLResponse)
async def get_auth_url(
    user: AuthenticatedUser = Depends(require_tenant),
):
    """
    Generate the Google OAuth 2.0 authorization URL.

    Frontend should redirect the user to this URL. After authorization,
    Google redirects back to the callback endpoint with an auth code.
    """
    try:
        state = f"{user.user_id}:{user.tenant_id}"
        auth_url = youtube_client.get_auth_url(state=state)
        return AuthURLResponse(auth_url=auth_url)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


# ── GET /v1/youtube/callback ─────────────────────────────────────────

@youtube_router.get("/callback", response_model=OAuthCallbackResponse)
async def oauth_callback(
    code: str = Query(..., description="Authorization code from Google"),
    state: str = Query(default="", description="user_id:tenant_id passed through OAuth"),
    error: Optional[str] = Query(default=None, description="Error from Google if authorization failed"),
    db: AsyncSession = Depends(get_db),
):
    """
    Handle the Google OAuth redirect callback.

    Google redirects here with ?code=... after the user grants access.
    We exchange the code for tokens and store them.

    NOTE: This endpoint doesn't require auth header because it's called
    by Google's redirect. The state parameter carries user identity.
    """
    if error:
        logger.warning("YouTube OAuth denied: %s", error)
        return OAuthCallbackResponse(
            success=False,
            message=f"Authorization was denied: {error}. Please try again.",
        )

    # Parse state
    user_id, tenant_id = "", ""
    if ":" in state:
        parts = state.split(":", 1)
        user_id = parts[0]
        tenant_id = parts[1]

    if not user_id or not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state parameter. Cannot identify user.",
        )

    # Exchange code for tokens
    try:
        tokens = await youtube_client.exchange_code(code)
    except Exception as e:
        logger.error("YouTube token exchange failed: %s", e)
        return OAuthCallbackResponse(
            success=False,
            message=f"Failed to connect YouTube: {e}",
        )

    # Store tokens in database
    try:
        from ..models.social_media import SocialToken

        # Check for existing token
        result = await db.execute(
            select(SocialToken).where(
                SocialToken.tenant_id == tenant_id,
                SocialToken.user_id == user_id,
                SocialToken.platform == "youtube",
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update existing token
            existing.access_token = tokens.access_token
            if tokens.refresh_token:
                existing.refresh_token = tokens.refresh_token
            existing.expires_at = tokens.expires_at
            existing.platform_user_id = tokens.channel_id or existing.platform_user_id
            existing.token_metadata = {
                "channel_title": tokens.channel_title or "",
                "channel_id": tokens.channel_id or "",
            }
        else:
            # Create new token
            token_row = SocialToken(
                tenant_id=tenant_id,
                user_id=user_id,
                platform="youtube",
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token or "",
                expires_at=tokens.expires_at,
                platform_user_id=tokens.channel_id or "",
                token_metadata={
                    "channel_title": tokens.channel_title or "",
                    "channel_id": tokens.channel_id or "",
                },
            )
            db.add(token_row)

        await db.commit()
        logger.info(
            "YouTube tokens stored: user=%s channel=%s",
            user_id, tokens.channel_title,
        )

    except Exception as e:
        logger.error("Failed to store YouTube tokens: %s", e)
        await db.rollback()
        return OAuthCallbackResponse(
            success=False,
            message=f"Connected to YouTube but failed to save tokens: {e}",
        )

    return OAuthCallbackResponse(
        success=True,
        channel_title=tokens.channel_title,
        channel_id=tokens.channel_id,
        message=f"Successfully connected to YouTube channel: {tokens.channel_title or 'your channel'}",
    )


# ── GET /v1/youtube/status ────────────────────────────────────────────

@youtube_router.get("/status", response_model=YouTubeStatusResponse)
async def get_youtube_status(
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Check if the user has a connected YouTube account and get channel info."""
    from ..models.social_media import SocialToken

    result = await db.execute(
        select(SocialToken).where(
            SocialToken.tenant_id == user.tenant_id,
            SocialToken.user_id == user.user_id,
            SocialToken.platform == "youtube",
        )
    )
    token_row = result.scalar_one_or_none()

    if not token_row:
        return YouTubeStatusResponse(connected=False)

    metadata = token_row.token_metadata or {}

    # Optionally fetch fresh channel info
    channel_info = None
    try:
        access_token, new_expires = await youtube_client.ensure_valid_token(
            token_row.access_token,
            token_row.refresh_token,
            token_row.expires_at,
        )
        if new_expires:
            token_row.access_token = access_token
            token_row.expires_at = new_expires
            await db.commit()

        channel_info = await youtube_client.get_my_channel(access_token)
    except Exception as e:
        logger.warning("Could not fetch fresh channel info: %s", e)

    return YouTubeStatusResponse(
        connected=True,
        channel_title=channel_info.title if channel_info else metadata.get("channel_title"),
        channel_id=channel_info.channel_id if channel_info else metadata.get("channel_id"),
        subscriber_count=channel_info.subscriber_count if channel_info else None,
        video_count=channel_info.video_count if channel_info else None,
        expires_at=token_row.expires_at.isoformat() if token_row.expires_at else None,
    )


# ── POST /v1/youtube/disconnect ───────────────────────────────────────

@youtube_router.post("/disconnect")
async def disconnect_youtube(
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Remove the YouTube connection for this user."""
    from ..models.social_media import SocialToken

    result = await db.execute(
        select(SocialToken).where(
            SocialToken.tenant_id == user.tenant_id,
            SocialToken.user_id == user.user_id,
            SocialToken.platform == "youtube",
        )
    )
    token_row = result.scalar_one_or_none()

    if not token_row:
        return {"success": True, "message": "No YouTube connection found."}

    await db.delete(token_row)
    await db.commit()

    logger.info("YouTube disconnected: user=%s tenant=%s", user.user_id, user.tenant_id)
    return {"success": True, "message": "YouTube account disconnected."}


# ── GET /v1/youtube/video/{video_id} ─────────────────────────────────

@youtube_router.get("/video/{video_id}", response_model=VideoStatusResponse)
async def get_video_status(
    video_id: str,
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Check the processing status of an uploaded YouTube video."""
    from ..models.social_media import SocialToken

    result = await db.execute(
        select(SocialToken).where(
            SocialToken.tenant_id == user.tenant_id,
            SocialToken.user_id == user.user_id,
            SocialToken.platform == "youtube",
        )
    )
    token_row = result.scalar_one_or_none()

    if not token_row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="YouTube not connected. Connect your account first.",
        )

    # Ensure token is valid
    try:
        access_token, new_expires = await youtube_client.ensure_valid_token(
            token_row.access_token,
            token_row.refresh_token,
            token_row.expires_at,
        )
        if new_expires:
            token_row.access_token = access_token
            token_row.expires_at = new_expires
            await db.commit()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"YouTube token expired: {e}. Please reconnect.",
        )

    video_status = await youtube_client.get_video_status(access_token, video_id)

    if not video_status:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Video {video_id} not found or not accessible.",
        )

    return VideoStatusResponse(
        video_id=video_status["id"],
        title=video_status.get("title", ""),
        status=video_status.get("status", ""),
        privacy=video_status.get("privacy", ""),
        url=video_status.get("url", ""),
        processing_status=video_status.get("processing_status", ""),
    )
