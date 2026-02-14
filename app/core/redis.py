"""
Redis pub/sub for realtime notifications OR silent no-op.
Controlled by FF_USE_REDIS flag.
"""

import json
import logging
from typing import Any, Optional

from .config import get_settings
from .flags import get_flags

logger = logging.getLogger(__name__)

_redis_client = None


async def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis

        settings = get_settings()
        _redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
        )
    return _redis_client


async def publish(channel: str, event_type: str, data: Any = None) -> None:
    """
    Publish a realtime event. If Redis is disabled, this is a no-op.
    """
    flags = get_flags()
    if not flags.use_redis:
        return

    try:
        client = await _get_redis()
        payload = json.dumps({"type": event_type, "data": data})
        await client.publish(channel, payload)
    except Exception as e:
        # Never crash on notification failure
        logger.warning("Redis publish failed (channel=%s): %s", channel, e)


async def notify_tenant(
    tenant_id: str, event_type: str, data: Any = None
) -> None:
    """Publish to tenant-scoped channel."""
    await publish(f"tenant:{tenant_id}", event_type, data)


async def notify_session(
    tenant_id: str, session_id: str, event_type: str, data: Any = None
) -> None:
    """Publish to session-scoped channel."""
    await publish(f"session:{tenant_id}:{session_id}", event_type, data)


async def close_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
        logger.info("Redis connection closed")
