"""
Main API router. Mounts all sub-routers.
"""

from fastapi import APIRouter, Depends

from ..core.dependencies import require_tenant

router = APIRouter()


# ── Health (no auth) ─────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok", "service": "eve"}


# ── Auth config (no auth) ───────────────────────────────────────────

@router.get("/auth/config")
async def auth_config():
    from ..core.config import get_settings
    from ..core.flags import get_flags

    flags = get_flags()
    if not flags.use_auth0:
        return {"auth_enabled": False, "message": "Dev mode — no auth required"}

    settings = get_settings()
    return {
        "auth_enabled": True,
        "domain": settings.auth0_domain,
        "audience": settings.auth0_audience,
    }


# ── V1 routes (auth required) ───────────────────────────────────────

from .chat import chat_router
from .documents import documents_router
from .onboarding import onboarding_router
from .conversations import conversations_router
from .upload import upload_router
from .youtube_oauth import youtube_router

router.include_router(chat_router, prefix="/v1", dependencies=[Depends(require_tenant)])
router.include_router(documents_router, prefix="/v1", dependencies=[Depends(require_tenant)])
router.include_router(onboarding_router, prefix="/v1", dependencies=[Depends(require_tenant)])
router.include_router(conversations_router, prefix="/v1", dependencies=[Depends(require_tenant)])
router.include_router(upload_router, prefix="/v1")
# YouTube OAuth — callback endpoint has NO auth (Google redirects to it),
# other endpoints require auth via dependency on require_tenant.
router.include_router(youtube_router, prefix="/v1")
