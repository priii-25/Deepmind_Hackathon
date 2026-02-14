"""
Realtime notifications. Thin wrapper around core.redis.
Provides typed event helpers for different parts of the system.
"""

from ..core import redis as _redis


# ── Chat events ──────────────────────────────────────────────────────

async def chat_started(tenant_id: str, session_id: str, data: dict = None):
    await _redis.notify_session(tenant_id, session_id, "chat.started", data)


async def chat_progress(tenant_id: str, session_id: str, data: dict = None):
    await _redis.notify_session(tenant_id, session_id, "chat.progress", data)


async def chat_completed(tenant_id: str, session_id: str, data: dict = None):
    await _redis.notify_session(tenant_id, session_id, "chat.completed", data)


async def chat_error(tenant_id: str, session_id: str, data: dict = None):
    await _redis.notify_session(tenant_id, session_id, "chat.error", data)


# ── Agent events ─────────────────────────────────────────────────────

async def agent_started(tenant_id: str, session_id: str, agent_name: str):
    await _redis.notify_session(
        tenant_id, session_id, "agent.started", {"agent": agent_name}
    )


async def agent_progress(tenant_id: str, session_id: str, agent_name: str, step: str):
    await _redis.notify_session(
        tenant_id, session_id, "agent.progress", {"agent": agent_name, "step": step}
    )


async def agent_completed(tenant_id: str, session_id: str, agent_name: str):
    await _redis.notify_session(
        tenant_id, session_id, "agent.completed", {"agent": agent_name}
    )


# ── Document events ──────────────────────────────────────────────────

async def document_processing(tenant_id: str, doc_id: str, status: str):
    await _redis.notify_tenant(
        tenant_id, "document.processing", {"document_id": doc_id, "status": status}
    )


# ── Generic ──────────────────────────────────────────────────────────

async def notify(tenant_id: str, event_type: str, data: dict = None):
    await _redis.notify_tenant(tenant_id, event_type, data)
