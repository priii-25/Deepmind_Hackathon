"""
Meeting BaaS API client.

Sends bots to join video meetings (Zoom, Google Meet, Teams),
records + transcribes. We poll for results when the user asks.

Docs: https://docs.meetingbaas.com
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


async def join_meeting(meeting_url: str, bot_name: str = "Ivy Notetaker") -> dict:
    """
    Send a bot to join a meeting. Returns {"bot_id": "..."}.
    """
    settings = get_settings()
    base_url = settings.meetingbaas_base_url.rstrip("/")

    payload = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "bot_image": "https://teems.ai/favicon.ico",
        "entry_message": "Hi! I'm Ivy, the Teems notetaker. I'll be taking notes for this meeting.",
        "reserved": False,
        "speech_to_text": {
            "provider": "Default",
        },
    }

    client = _get_client()
    resp = await client.post(f"{base_url}/bots", json=payload, headers=_headers())

    if resp.status_code >= 400:
        logger.error("Meeting BaaS join error %d: %s", resp.status_code, resp.text[:500])
    resp.raise_for_status()

    data = resp.json()
    logger.info("Meeting BaaS bot created: %s", data.get("bot_id", data))
    return data


async def get_meeting_data(bot_id: str) -> Optional[dict]:
    """
    Poll for meeting data (transcript + status).
    Returns None if meeting is still in progress.
    Returns dict with transcript if complete.
    """
    settings = get_settings()
    base_url = settings.meetingbaas_base_url.rstrip("/")

    client = _get_client()
    resp = await client.get(
        f"{base_url}/bots/meeting_data",
        params={"bot_id": bot_id},
        headers=_headers(),
    )

    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        logger.error("Meeting BaaS get_data error %d: %s", resp.status_code, resp.text[:500])
    resp.raise_for_status()

    return resp.json()
