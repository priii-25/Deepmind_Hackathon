"""
Meeting BaaS v2 API client.

Sends bots to join video meetings (Zoom, Google Meet, Teams),
records + transcribes. We poll for results when the user asks.

Docs: https://docs.meetingbaas.com
API:  https://api.meetingbaas.com/v2/bots
"""

import logging
from typing import Optional

import httpx

from ..core.config import get_settings

logger = logging.getLogger(__name__)

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
        )
    return _client


async def close_client():
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


def _headers() -> dict[str, str]:
    settings = get_settings()
    return {
        "x-meeting-baas-api-key": settings.meetingbaas_api_key,
        "Content-Type": "application/json",
    }


def _base_url() -> str:
    settings = get_settings()
    return settings.meetingbaas_base_url.rstrip("/")


async def join_meeting(meeting_url: str, bot_name: str = "Ivy Notetaker") -> dict:
    """
    Send a bot to join a meeting via v2 API.

    Returns: {"bot_id": "...", "status": "joining"}
    Raises: httpx.HTTPStatusError on API failure.
    """
    payload = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "bot_image": "https://teems.ai/favicon.ico",
        "entry_message": (
            "Hi! I'm Ivy, the Teems notetaker. "
            "I'll be taking notes for this meeting."
        ),
        "recording_mode": "speaker_view",
        "transcription_enabled": True,
        "transcription_config": {
            "provider": "gladia",
        },
        "automatic_leave": {
            "waiting_room_timeout": 600,
            "noone_joined_timeout": 300,
        },
    }

    url = f"{_base_url()}/v2/bots"
    client = _get_client()
    logger.info("Meeting BaaS: POST %s  meeting_url=%s", url, meeting_url)

    resp = await client.post(url, json=payload, headers=_headers())

    if resp.status_code >= 400:
        logger.error(
            "Meeting BaaS join error %d: %s", resp.status_code, resp.text[:500]
        )
    resp.raise_for_status()

    body = resp.json()

    # v2 wraps response in {"success": true, "data": {...}}
    if body.get("success") and "data" in body:
        data = body["data"]
    else:
        # Defensive fallback if shape is unexpected
        data = body

    bot_id = data.get("bot_id") or data.get("id", "")
    logger.info("Meeting BaaS bot created: bot_id=%s status=%s", bot_id, data.get("status"))
    return data


async def get_bot_status(bot_id: str) -> Optional[dict]:
    """
    Lightweight status check via v2 API.

    Returns: {"status": "in_call"} or similar, None on 404.
    """
    url = f"{_base_url()}/v2/bots/{bot_id}/status"
    client = _get_client()

    resp = await client.get(url, headers=_headers())

    if resp.status_code == 404:
        logger.warning("Meeting BaaS: bot %s not found (404)", bot_id)
        return None
    if resp.status_code >= 400:
        logger.error(
            "Meeting BaaS status error %d: %s", resp.status_code, resp.text[:500]
        )
    resp.raise_for_status()

    body = resp.json()
    if body.get("success") and "data" in body:
        return body["data"]
    return body


async def get_bot_details(bot_id: str) -> Optional[dict]:
    """
    Full bot details via v2 API — includes transcription URL, recording URL, etc.

    The transcription URL is a presigned S3 link (valid ~4 hours) pointing to a
    JSON file with speaker-segmented transcript data.

    Returns: full bot object or None on 404.
    """
    url = f"{_base_url()}/v2/bots/{bot_id}"
    client = _get_client()

    resp = await client.get(url, headers=_headers())

    if resp.status_code == 404:
        logger.warning("Meeting BaaS: bot %s not found (404)", bot_id)
        return None
    if resp.status_code >= 400:
        logger.error(
            "Meeting BaaS details error %d: %s", resp.status_code, resp.text[:500]
        )
    resp.raise_for_status()

    body = resp.json()
    if body.get("success") and "data" in body:
        return body["data"]
    return body


async def fetch_transcript(transcript_url: str) -> Optional[list[dict]]:
    """
    Download transcript JSON from a presigned S3 URL.

    The S3 JSON typically contains segments like:
        [{"speaker": "Speaker 0", "start": 1.5, "end": 7.0, "text": "..."}]
    or a wrapper with a "segments" key.

    Returns: list of transcript segments, or None on failure.
    """
    client = _get_client()
    logger.info("Meeting BaaS: fetching transcript from S3 URL")

    try:
        resp = await client.get(transcript_url)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error("Failed to fetch transcript from S3: %s", e)
        return None

    # Handle various transcript formats
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        logger.info(
            "Meeting BaaS transcript keys: %s", list(data.keys())
        )
        # Try common wrapper keys in priority order
        for key in ("segments", "transcript", "transcription", "prediction",
                     "results", "utterances", "entries", "data"):
            if key in data and isinstance(data[key], list):
                logger.info("Meeting BaaS: using transcript key '%s' (%d items)", key, len(data[key]))
                return data[key]

        # Gladia nested format: prediction > transcription > utterances
        if "prediction" in data and isinstance(data["prediction"], dict):
            pred = data["prediction"]
            for key in ("utterances", "transcription", "segments"):
                if key in pred and isinstance(pred[key], list):
                    logger.info("Meeting BaaS: using prediction.%s (%d items)", key, len(pred[key]))
                    return pred[key]

        # Last resort: return the whole dict as a single "segment" so the
        # handler can still show something rather than nothing
        logger.warning(
            "Unexpected transcript dict format, keys=%s — wrapping as single segment",
            list(data.keys()),
        )
        return [data]

    logger.warning("Unexpected transcript type: %s", type(data))
    return None


async def delete_bot(bot_id: str) -> bool:
    """
    Remove a bot from a meeting.

    Returns True on success, False on failure.
    """
    url = f"{_base_url()}/v2/bots/{bot_id}"
    client = _get_client()

    try:
        resp = await client.delete(url, headers=_headers())
        if resp.status_code < 300:
            logger.info("Meeting BaaS: bot %s deleted", bot_id)
            return True
        logger.warning(
            "Meeting BaaS delete %d: %s", resp.status_code, resp.text[:300]
        )
        return False
    except Exception as e:
        logger.error("Meeting BaaS delete failed: %s", e)
        return False
