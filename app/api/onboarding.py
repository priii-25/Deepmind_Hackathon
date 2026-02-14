"""
Onboarding API.

POST /v1/onboarding/brand    — Look up brand by domain
GET  /v1/onboarding/state    — Get onboarding state for user
POST /v1/onboarding/state    — Update onboarding state
GET  /v1/onboarding/agents   — List available agents
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.auth import AuthenticatedUser
from ..core.dependencies import require_tenant, get_db
from ..models.onboarding import OnboardingState
from ..services.brandfetch import fetch_brand
from ..orchestrator.registry import get_registry

logger = logging.getLogger(__name__)

onboarding_router = APIRouter(prefix="/onboarding", tags=["onboarding"])


# ── Brand Lookup ─────────────────────────────────────────────────────

class BrandLookupRequest(BaseModel):
    domain: str


class BrandLookupResponse(BaseModel):
    found: bool
    domain: str = ""
    name: str = ""
    description: str = ""
    icon_url: str = ""
    industry: str = ""
    tone_of_voice: str = ""
    region: Optional[str] = None
    colors: list = []
    fonts: list = []
    social_links: dict = {}
    contact_email: Optional[str] = None
    contact_address: Optional[str] = None


@onboarding_router.post("/brand", response_model=BrandLookupResponse)
async def lookup_brand(
    request: BrandLookupRequest,
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Look up brand info for a domain during onboarding."""
    result = await fetch_brand(request.domain, db, user.tenant_id)

    if not result:
        return BrandLookupResponse(found=False, domain=request.domain)

    return BrandLookupResponse(
        found=True,
        domain=result.get("domain", request.domain),
        name=result.get("name", ""),
        description=result.get("description", ""),
        icon_url=result.get("icon_url", ""),
        industry=result.get("industry", ""),
        tone_of_voice=result.get("tone_of_voice", ""),
        region=result.get("region"),
        colors=result.get("colors", []),
        fonts=result.get("fonts", []),
        social_links=result.get("social_links", {}),
        contact_email=result.get("contact_email"),
        contact_address=result.get("contact_address"),
    )


# ── Onboarding State ────────────────────────────────────────────────

class OnboardingStateResponse(BaseModel):
    current_stage: str = "brand_discovery"
    brand_domain: Optional[str] = None
    selected_teammates: list = []
    connected_integrations: list = []
    notification_preferences: dict = {}
    completed: bool = False


class OnboardingStateUpdate(BaseModel):
    current_stage: Optional[str] = None
    brand_domain: Optional[str] = None
    selected_teammates: Optional[list] = None
    connected_integrations: Optional[list] = None
    notification_preferences: Optional[dict] = None


@onboarding_router.get("/state", response_model=OnboardingStateResponse)
async def get_onboarding_state(
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Get onboarding state for the current user."""
    result = await db.execute(
        select(OnboardingState).where(
            OnboardingState.tenant_id == user.tenant_id,
            OnboardingState.user_id == user.user_id,
        )
    )
    state = result.scalar_one_or_none()

    if not state:
        return OnboardingStateResponse()

    return OnboardingStateResponse(
        current_stage=state.current_stage,
        brand_domain=state.brand_domain,
        selected_teammates=state.selected_teammates or [],
        connected_integrations=state.connected_integrations or [],
        notification_preferences=state.notification_preferences or {},
        completed=state.current_stage == "completed",
    )


@onboarding_router.post("/state", response_model=OnboardingStateResponse)
async def update_onboarding_state(
    update: OnboardingStateUpdate,
    user: AuthenticatedUser = Depends(require_tenant),
    db: AsyncSession = Depends(get_db),
):
    """Update onboarding state. Creates if not exists."""
    result = await db.execute(
        select(OnboardingState).where(
            OnboardingState.tenant_id == user.tenant_id,
            OnboardingState.user_id == user.user_id,
        )
    )
    state = result.scalar_one_or_none()

    if not state:
        state = OnboardingState(
            tenant_id=user.tenant_id,
            user_id=user.user_id,
        )
        db.add(state)

    if update.current_stage is not None:
        state.current_stage = update.current_stage
    if update.brand_domain is not None:
        state.brand_domain = update.brand_domain
    if update.selected_teammates is not None:
        state.selected_teammates = update.selected_teammates
    if update.connected_integrations is not None:
        state.connected_integrations = update.connected_integrations
    if update.notification_preferences is not None:
        state.notification_preferences = update.notification_preferences

    await db.flush()

    return OnboardingStateResponse(
        current_stage=state.current_stage,
        brand_domain=state.brand_domain,
        selected_teammates=state.selected_teammates or [],
        connected_integrations=state.connected_integrations or [],
        notification_preferences=state.notification_preferences or {},
        completed=state.current_stage == "completed",
    )


# ── Available Agents ─────────────────────────────────────────────────

class AgentInfo(BaseModel):
    name: str
    display_name: str
    description: str
    capabilities: list[str] = []
    required_inputs: list[str] = []
    active: bool = True


@onboarding_router.get("/agents", response_model=list[AgentInfo])
async def list_agents():
    """List all available agents. Used by frontend to show agent selection during onboarding."""
    registry = get_registry()
    agents = registry.get_agent_descriptions()

    return [
        AgentInfo(
            name=a["name"],
            display_name=a["display_name"],
            description=a["description"],
            capabilities=a.get("capabilities", []),
            required_inputs=a.get("required_inputs", []),
            active=True,
        )
        for a in agents
        if a["name"] != "eve_chat"  # Eve is always there, not selectable
    ]
