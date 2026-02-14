"""
Production YouTube Data API v3 client.

Handles:
  - OAuth 2.0 authorization flow (auth URL, code exchange, token refresh)
  - Resumable video upload (Google's recommended approach for all file sizes)
  - Video metadata management (title, description, tags, privacy, category)
  - Channel info retrieval
  - Video status polling

Uses raw httpx to stay consistent with the codebase (no google-api-python-client).
All YouTube API v3 REST calls go through the shared httpx client.
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx

from ...core.config import get_settings

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

# Scopes required for video upload + channel read
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

# Resumable upload chunk size (10 MB — Google recommends multiples of 256 KB)
CHUNK_SIZE = 10 * 1024 * 1024

# YouTube video categories (most commonly used)
YOUTUBE_CATEGORIES = {
    "1": "Film & Animation",
    "2": "Autos & Vehicles",
    "10": "Music",
    "15": "Pets & Animals",
    "17": "Sports",
    "19": "Travel & Events",
    "20": "Gaming",
    "22": "People & Blogs",
    "23": "Comedy",
    "24": "Entertainment",
    "25": "News & Politics",
    "26": "Howto & Style",
    "27": "Education",
    "28": "Science & Technology",
    "29": "Nonprofits & Activism",
}

# Default category for marketing/brand content
DEFAULT_CATEGORY_ID = "22"  # People & Blogs


# ── Data classes ──────────────────────────────────────────────────────

@dataclass
class YouTubeTokens:
    access_token: str
    refresh_token: Optional[str]
    expires_at: Optional[datetime]
    channel_id: Optional[str] = None
    channel_title: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return True
        # Consider expired 5 minutes before actual expiry
        return datetime.now(timezone.utc) >= (self.expires_at - timedelta(minutes=5))


@dataclass
class UploadResult:
    success: bool
    video_id: Optional[str] = None
    video_url: Optional[str] = None
    status: str = ""
    error: Optional[str] = None
    upload_status: Optional[str] = None  # "uploaded", "processed", "failed"


@dataclass
class ChannelInfo:
    channel_id: str
    title: str
    description: str
    subscriber_count: int
    video_count: int
    thumbnail_url: str


# ── Shared HTTP client ────────────────────────────────────────────────

_yt_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _yt_client
    if _yt_client is None or _yt_client.is_closed:
        _yt_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=300, write=300, pool=10),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _yt_client


async def close_client():
    global _yt_client
    if _yt_client and not _yt_client.is_closed:
        await _yt_client.aclose()
        _yt_client = None


# ── OAuth 2.0 ─────────────────────────────────────────────────────────

def get_auth_url(state: str = "") -> str:
    """
    Generate the Google OAuth 2.0 authorization URL for YouTube.

    The user visits this URL, grants permission, and Google redirects
    back to our callback with an authorization code.

    Args:
        state: Opaque value passed through the OAuth flow (e.g., user_id:tenant_id)
    """
    settings = get_settings()

    if not settings.google_client_id:
        raise ValueError(
            "GOOGLE_CLIENT_ID is required for YouTube OAuth. "
            "Create credentials at https://console.cloud.google.com/apis/credentials"
        )
    if not settings.youtube_redirect_uri:
        raise ValueError(
            "YOUTUBE_REDIRECT_URI is required. "
            "Set it to your callback URL, e.g., http://localhost:8000/v1/youtube/callback"
        )

    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.youtube_redirect_uri,
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",  # Gets refresh_token
        "prompt": "consent",  # Always show consent screen (ensures refresh_token)
        "include_granted_scopes": "true",
    }
    if state:
        params["state"] = state

    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> YouTubeTokens:
    """
    Exchange an authorization code for access + refresh tokens.

    Args:
        code: The authorization code from Google's OAuth redirect.

    Returns:
        YouTubeTokens with access_token, refresh_token, and expiry.

    Raises:
        httpx.HTTPStatusError: If the token exchange fails.
        ValueError: If credentials are missing.
    """
    settings = get_settings()

    if not settings.google_client_id or not settings.google_client_secret:
        raise ValueError(
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required. "
            "Configure them at https://console.cloud.google.com/apis/credentials"
        )

    payload = {
        "code": code,
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "redirect_uri": settings.youtube_redirect_uri,
        "grant_type": "authorization_code",
    }

    client = _get_client()
    resp = await client.post(GOOGLE_TOKEN_URL, data=payload)

    if resp.status_code != 200:
        error_body = resp.text[:500]
        logger.error("YouTube token exchange failed (%d): %s", resp.status_code, error_body)
        raise httpx.HTTPStatusError(
            f"Token exchange failed: {error_body}",
            request=resp.request,
            response=resp,
        )

    data = resp.json()
    expires_in = data.get("expires_in", 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    tokens = YouTubeTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token"),
        expires_at=expires_at,
    )

    # Fetch channel info to store with the token
    try:
        channel = await get_my_channel(tokens.access_token)
        if channel:
            tokens.channel_id = channel.channel_id
            tokens.channel_title = channel.title
    except Exception as e:
        logger.warning("Could not fetch channel info after token exchange: %s", e)

    logger.info(
        "YouTube OAuth complete: channel=%s expires_in=%ds",
        tokens.channel_title or "unknown", expires_in,
    )
    return tokens


async def refresh_access_token(refresh_token: str) -> YouTubeTokens:
    """
    Refresh an expired access token using the refresh token.

    Args:
        refresh_token: The stored refresh token from initial OAuth.

    Returns:
        YouTubeTokens with new access_token (refresh_token may be the same).
    """
    settings = get_settings()

    payload = {
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    client = _get_client()
    resp = await client.post(GOOGLE_TOKEN_URL, data=payload)

    if resp.status_code != 200:
        error_body = resp.text[:500]
        logger.error("YouTube token refresh failed (%d): %s", resp.status_code, error_body)
        raise httpx.HTTPStatusError(
            f"Token refresh failed: {error_body}",
            request=resp.request,
            response=resp,
        )

    data = resp.json()
    expires_in = data.get("expires_in", 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    logger.info("YouTube token refreshed, expires_in=%ds", expires_in)

    return YouTubeTokens(
        access_token=data["access_token"],
        refresh_token=data.get("refresh_token", refresh_token),
        expires_at=expires_at,
    )


async def ensure_valid_token(
    access_token: str,
    refresh_token: Optional[str],
    expires_at: Optional[datetime],
) -> tuple[str, Optional[datetime]]:
    """
    Ensure the access token is valid. Refresh if expired.

    Returns:
        (valid_access_token, new_expires_at) — expires_at is None if unchanged.
    """
    if expires_at and datetime.now(timezone.utc) < (expires_at - timedelta(minutes=5)):
        return access_token, None  # Still valid

    if not refresh_token:
        raise ValueError(
            "YouTube access token expired and no refresh token available. "
            "Please reconnect your YouTube account."
        )

    logger.info("YouTube token expired, refreshing...")
    new_tokens = await refresh_access_token(refresh_token)
    return new_tokens.access_token, new_tokens.expires_at


# ── Channel Info ──────────────────────────────────────────────────────

async def get_my_channel(access_token: str) -> Optional[ChannelInfo]:
    """
    Get the authenticated user's YouTube channel info.

    Returns None if the user has no channel.
    """
    client = _get_client()
    resp = await client.get(
        f"{YOUTUBE_API_BASE}/channels",
        params={"part": "snippet,statistics", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    if resp.status_code != 200:
        logger.error("YouTube channels API failed (%d): %s", resp.status_code, resp.text[:300])
        return None

    data = resp.json()
    items = data.get("items", [])
    if not items:
        return None

    ch = items[0]
    snippet = ch.get("snippet", {})
    stats = ch.get("statistics", {})

    return ChannelInfo(
        channel_id=ch["id"],
        title=snippet.get("title", ""),
        description=snippet.get("description", ""),
        subscriber_count=int(stats.get("subscriberCount", 0)),
        video_count=int(stats.get("videoCount", 0)),
        thumbnail_url=snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
    )


# ── Video Upload (Resumable) ─────────────────────────────────────────

async def upload_video(
    access_token: str,
    video_bytes: bytes,
    title: str,
    description: str = "",
    tags: Optional[list[str]] = None,
    category_id: str = DEFAULT_CATEGORY_ID,
    privacy_status: str = "private",
    made_for_kids: bool = False,
    notify_subscribers: bool = True,
    thumbnail_bytes: Optional[bytes] = None,
) -> UploadResult:
    """
    Upload a video to YouTube using the resumable upload protocol.

    This is Google's recommended approach for all file sizes.
    Handles chunked upload for large files (>10 MB).

    Args:
        access_token:      Valid OAuth access token.
        video_bytes:       Raw video file content.
        title:             Video title (max 100 chars).
        description:       Video description (max 5000 chars).
        tags:              List of tags (max 500 chars total).
        category_id:       YouTube category ID (default: "22" People & Blogs).
        privacy_status:    "private", "unlisted", or "public".
        made_for_kids:     COPPA compliance flag.
        notify_subscribers: Whether to send a notification to subscribers.
        thumbnail_bytes:   Optional custom thumbnail image bytes.

    Returns:
        UploadResult with video_id, URL, and status.
    """
    start_time = time.monotonic()
    file_size = len(video_bytes)

    logger.info(
        "YouTube upload start: title='%s' size=%.1fMB privacy=%s",
        title[:50], file_size / (1024 * 1024), privacy_status,
    )

    # ── Step 1: Initiate resumable upload ─────────────────────────
    metadata = {
        "snippet": {
            "title": title[:100],
            "description": (description or "")[:5000],
            "tags": (tags or [])[:30],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }

    # Remove the notifySubscribers param if uploading as private
    upload_params = {
        "uploadType": "resumable",
        "part": "snippet,status",
    }
    if privacy_status == "public":
        upload_params["notifySubscribers"] = str(notify_subscribers).lower()

    client = _get_client()

    try:
        init_resp = await client.post(
            YOUTUBE_UPLOAD_URL,
            params=upload_params,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(file_size),
                "X-Upload-Content-Type": "video/*",
            },
            content=json.dumps(metadata),
        )

        if init_resp.status_code != 200:
            error = init_resp.text[:500]
            logger.error("YouTube upload init failed (%d): %s", init_resp.status_code, error)

            # Parse Google API error for better messaging
            friendly_error = _parse_youtube_error(init_resp)
            return UploadResult(
                success=False,
                error=friendly_error,
                status="init_failed",
            )

        upload_url = init_resp.headers.get("Location")
        if not upload_url:
            return UploadResult(
                success=False,
                error="YouTube did not return an upload URL. Please try again.",
                status="init_failed",
            )

        logger.info("YouTube upload URL obtained, starting file transfer...")

        # ── Step 2: Upload file data ──────────────────────────────
        if file_size <= CHUNK_SIZE:
            # Small file — single PUT request
            result = await _upload_single(client, upload_url, video_bytes, access_token)
        else:
            # Large file — chunked upload
            result = await _upload_chunked(client, upload_url, video_bytes, access_token)

        elapsed = time.monotonic() - start_time

        if result.success:
            logger.info(
                "YouTube upload complete: video_id=%s url=%s elapsed=%.1fs",
                result.video_id, result.video_url, elapsed,
            )

            # ── Step 3: Set custom thumbnail (optional) ───────────
            if thumbnail_bytes and result.video_id:
                try:
                    await set_thumbnail(access_token, result.video_id, thumbnail_bytes)
                except Exception as e:
                    logger.warning("Failed to set thumbnail: %s", e)
        else:
            logger.error("YouTube upload failed after %.1fs: %s", elapsed, result.error)

        return result

    except httpx.TimeoutException:
        elapsed = time.monotonic() - start_time
        logger.error("YouTube upload timed out after %.1fs", elapsed)
        return UploadResult(
            success=False,
            error="Upload timed out. The video file may be too large. Try a shorter video or check your connection.",
            status="timeout",
        )
    except Exception as e:
        elapsed = time.monotonic() - start_time
        logger.error("YouTube upload error after %.1fs: %s", elapsed, e)
        return UploadResult(
            success=False,
            error=f"Upload failed: {str(e)}",
            status="error",
        )


async def _upload_single(
    client: httpx.AsyncClient,
    upload_url: str,
    video_bytes: bytes,
    access_token: str,
) -> UploadResult:
    """Upload a small video in a single PUT request."""
    resp = await client.put(
        upload_url,
        content=video_bytes,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "video/*",
            "Content-Length": str(len(video_bytes)),
        },
    )
    return _parse_upload_response(resp)


async def _upload_chunked(
    client: httpx.AsyncClient,
    upload_url: str,
    video_bytes: bytes,
    access_token: str,
) -> UploadResult:
    """Upload a large video in chunks using the resumable upload protocol."""
    file_size = len(video_bytes)
    offset = 0
    chunk_num = 0

    while offset < file_size:
        chunk_end = min(offset + CHUNK_SIZE, file_size)
        chunk = video_bytes[offset:chunk_end]
        is_last = chunk_end >= file_size

        chunk_num += 1
        logger.info(
            "YouTube upload chunk %d: bytes %d-%d of %d (%.0f%%)",
            chunk_num, offset, chunk_end - 1, file_size,
            (chunk_end / file_size) * 100,
        )

        resp = await client.put(
            upload_url,
            content=chunk,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "video/*",
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{chunk_end - 1}/{file_size}",
            },
        )

        if is_last:
            return _parse_upload_response(resp)

        # For non-final chunks, expect 308 Resume Incomplete
        if resp.status_code == 308:
            # Extract the next byte range from the Range header
            range_header = resp.headers.get("Range", "")
            if range_header:
                # Range: bytes=0-1234567
                offset = int(range_header.split("-")[1]) + 1
            else:
                offset = chunk_end
        elif resp.status_code in (200, 201):
            # Upload completed early (server processed all data)
            return _parse_upload_response(resp)
        else:
            error = resp.text[:300]
            logger.error("YouTube chunk upload failed (%d): %s", resp.status_code, error)
            return UploadResult(
                success=False,
                error=f"Upload failed at chunk {chunk_num}: {_parse_youtube_error(resp)}",
                status="chunk_failed",
            )

    return UploadResult(success=False, error="Upload loop ended unexpectedly", status="error")


def _parse_upload_response(resp: httpx.Response) -> UploadResult:
    """Parse the YouTube API response after upload completes."""
    if resp.status_code in (200, 201):
        try:
            data = resp.json()
            video_id = data.get("id", "")
            status_info = data.get("status", {})
            return UploadResult(
                success=True,
                video_id=video_id,
                video_url=f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
                status="uploaded",
                upload_status=status_info.get("uploadStatus", "uploaded"),
            )
        except Exception:
            return UploadResult(
                success=True,
                status="uploaded",
                error="Upload succeeded but could not parse response",
            )
    else:
        return UploadResult(
            success=False,
            error=_parse_youtube_error(resp),
            status="failed",
        )


def _parse_youtube_error(resp: httpx.Response) -> str:
    """Extract a human-readable error from YouTube API response."""
    try:
        data = resp.json()
        error = data.get("error", {})
        if isinstance(error, dict):
            errors = error.get("errors", [])
            if errors:
                first = errors[0]
                reason = first.get("reason", "")
                message = first.get("message", "")

                # Map common errors to user-friendly messages
                error_map = {
                    "quotaExceeded": "YouTube API quota exceeded. Try again tomorrow or check your Google Cloud quota.",
                    "uploadLimitExceeded": "You've exceeded the daily upload limit for YouTube.",
                    "forbidden": "Permission denied. Make sure your YouTube account allows uploads.",
                    "invalidMetadata": f"Invalid video metadata: {message}",
                    "videoAlreadyExists": "This video has already been uploaded.",
                    "insufficientPermissions": "Missing permissions. Please reconnect your YouTube account.",
                }
                return error_map.get(reason, message or f"YouTube error: {reason}")
            return error.get("message", f"HTTP {resp.status_code}")
        return str(error)
    except Exception:
        return f"YouTube API error (HTTP {resp.status_code}): {resp.text[:200]}"


# ── Thumbnail ─────────────────────────────────────────────────────────

async def set_thumbnail(
    access_token: str,
    video_id: str,
    thumbnail_bytes: bytes,
) -> bool:
    """Set a custom thumbnail for an uploaded video."""
    client = _get_client()
    resp = await client.post(
        f"{YOUTUBE_UPLOAD_URL.replace('/videos', '/thumbnails/set')}",
        params={"videoId": video_id},
        content=thumbnail_bytes,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "image/jpeg",
        },
    )

    if resp.status_code == 200:
        logger.info("YouTube thumbnail set for video %s", video_id)
        return True
    else:
        logger.warning("YouTube thumbnail failed (%d): %s", resp.status_code, resp.text[:200])
        return False


# ── Video Status ──────────────────────────────────────────────────────

async def get_video_status(access_token: str, video_id: str) -> Optional[dict]:
    """
    Check the processing status of an uploaded video.

    Returns dict with keys: id, title, status, privacy, url, processing_details
    """
    client = _get_client()
    resp = await client.get(
        f"{YOUTUBE_API_BASE}/videos",
        params={
            "part": "snippet,status,processingDetails",
            "id": video_id,
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )

    if resp.status_code != 200:
        logger.error("YouTube video status failed (%d): %s", resp.status_code, resp.text[:200])
        return None

    data = resp.json()
    items = data.get("items", [])
    if not items:
        return None

    video = items[0]
    snippet = video.get("snippet", {})
    status = video.get("status", {})
    processing = video.get("processingDetails", {})

    return {
        "id": video_id,
        "title": snippet.get("title", ""),
        "status": status.get("uploadStatus", "unknown"),
        "privacy": status.get("privacyStatus", "unknown"),
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "processing_status": processing.get("processingStatus", "unknown"),
        "processing_progress": processing.get("processingProgress", {}),
        "embeddable": status.get("embeddable", False),
        "published_at": snippet.get("publishedAt", ""),
    }


# ── Update Video ──────────────────────────────────────────────────────

async def update_video_metadata(
    access_token: str,
    video_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
    category_id: Optional[str] = None,
    privacy_status: Optional[str] = None,
) -> bool:
    """
    Update metadata on an already-uploaded video.

    Only provided fields are updated. Pass None to leave unchanged.
    """
    # First, get current video data
    client = _get_client()
    resp = await client.get(
        f"{YOUTUBE_API_BASE}/videos",
        params={"part": "snippet,status", "id": video_id},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    if resp.status_code != 200 or not resp.json().get("items"):
        logger.error("Could not fetch video %s for update", video_id)
        return False

    video = resp.json()["items"][0]
    snippet = video.get("snippet", {})
    status = video.get("status", {})

    # Build update payload (merge with existing)
    update_body: dict = {"id": video_id}
    parts = []

    if title is not None or description is not None or tags is not None or category_id is not None:
        parts.append("snippet")
        update_body["snippet"] = {
            "title": title if title is not None else snippet.get("title", ""),
            "description": description if description is not None else snippet.get("description", ""),
            "tags": tags if tags is not None else snippet.get("tags", []),
            "categoryId": category_id if category_id is not None else snippet.get("categoryId", DEFAULT_CATEGORY_ID),
        }

    if privacy_status is not None:
        parts.append("status")
        update_body["status"] = {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": status.get("selfDeclaredMadeForKids", False),
        }

    if not parts:
        return True  # Nothing to update

    resp = await client.put(
        f"{YOUTUBE_API_BASE}/videos",
        params={"part": ",".join(parts)},
        json=update_body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )

    if resp.status_code == 200:
        logger.info("YouTube video %s metadata updated", video_id)
        return True
    else:
        logger.error("YouTube update failed (%d): %s", resp.status_code, resp.text[:300])
        return False


# ── Helpers ───────────────────────────────────────────────────────────

def get_category_name(category_id: str) -> str:
    return YOUTUBE_CATEGORIES.get(category_id, "Unknown")


def get_categories() -> dict[str, str]:
    return dict(YOUTUBE_CATEGORIES)
