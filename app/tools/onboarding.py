"""
Onboarding tools — Eve uses these to drive the onboarding flow.

Stages: brand_discovery → suggested_teammates → connect_world → personalization → completed

Eve manages the conversation naturally. These tools handle state persistence.
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.flags import get_flags
from ..models.onboarding import OnboardingState
from ..services.brandfetch import fetch_brand
from .registry import tool, ToolRisk

logger = logging.getLogger(__name__)

# Valid stage transitions
STAGES = ["brand_discovery", "suggested_teammates", "connect_world", "personalization", "completed"]

STAGE_DESCRIPTIONS = {
    "brand_discovery": "Ask for website, fetch brand info, confirm with user",
    "suggested_teammates": "Recommend Teem Mates based on brand/industry, let user pick",
    "connect_world": "Offer to connect tools (Slack, Google Drive, Notion, etc.) — optional, can skip",
    "personalization": "Set notification preferences and update frequency — last step",
    "completed": "Onboarding done. Workspace is ready.",
}


@tool(
    name="get_onboarding_state",
    description=(
        "Check the current onboarding state for the user. "
        "\n\nWhen to use: "
        "- At the start of a new conversation to check if onboarding is needed "
        "- To know which stage the user is in "
        "- To resume an interrupted onboarding flow "
        "\n\nReturns: Current stage, brand info if fetched, selected teammates, integrations."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    risk=ToolRisk.READ,
    category="onboarding",
)
async def get_onboarding_state_tool(db=None, tenant_id: str = "", **kwargs) -> str:
    """Get onboarding state."""
    if not db:
        return "Error: No database session."

    user_id = kwargs.get("user_id", "")
    result = await db.execute(
        select(OnboardingState).where(
            OnboardingState.tenant_id == tenant_id,
            OnboardingState.user_id == user_id,
        )
    )
    state = result.scalar_one_or_none()

    if not state:
        return (
            "No onboarding started yet. This is a new user.\n"
            "Current stage: brand_discovery (first stage)\n"
            "Next action: Ask for their website to fetch brand details."
        )

    parts = [f"Current stage: {state.current_stage}"]

    if state.brand_domain:
        parts.append(f"Brand domain: {state.brand_domain}")
    if state.selected_teammates:
        names = ", ".join(state.selected_teammates) if isinstance(state.selected_teammates, list) else str(state.selected_teammates)
        parts.append(f"Selected teammates: {names}")
    if state.connected_integrations:
        integ = ", ".join(state.connected_integrations) if isinstance(state.connected_integrations, list) else str(state.connected_integrations)
        parts.append(f"Connected integrations: {integ}")
    if state.notification_preferences:
        parts.append(f"Notification prefs: {state.notification_preferences}")
    if state.current_stage == "completed":
        parts.append("Onboarding is complete. User's workspace is ready.")
    else:
        idx = STAGES.index(state.current_stage) if state.current_stage in STAGES else 0
        parts.append(f"Stage {idx + 1} of {len(STAGES)}")
        parts.append(f"What to do now: {STAGE_DESCRIPTIONS.get(state.current_stage, '')}")

    return "\n".join(parts)


@tool(
    name="advance_onboarding",
    description=(
        "Advance the onboarding flow to the next stage and save any data collected. "
        "\n\nStage flow: brand_discovery → suggested_teammates → connect_world → personalization → completed "
        "\n\nWhen to use: "
        "- User confirmed brand info → advance to suggested_teammates "
        "- User selected teammates → advance to connect_world "
        "- User connected integrations or chose to skip → advance to personalization "
        "- User set notification preferences → advance to completed "
        "\n\nProvide the target stage and any data to save."
    ),
    parameters={
        "type": "object",
        "properties": {
            "target_stage": {
                "type": "string",
                "enum": STAGES,
                "description": "The stage to advance to.",
            },
            "brand_domain": {
                "type": "string",
                "description": "Brand domain (set during brand_discovery).",
            },
            "selected_teammates": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of selected teammate names (e.g. ['kai', 'vera', 'chad']).",
            },
            "connected_integrations": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of connected integration types (e.g. ['slack', 'google_drive']).",
            },
            "notification_preferences": {
                "type": "object",
                "description": "Notification settings (e.g. {frequency: 'daily', channels: ['email', 'slack']}).",
            },
        },
        "required": ["target_stage"],
    },
    risk=ToolRisk.WRITE,
    category="onboarding",
)
async def advance_onboarding_tool(
    target_stage: str,
    brand_domain: Optional[str] = None,
    selected_teammates: Optional[list] = None,
    connected_integrations: Optional[list] = None,
    notification_preferences: Optional[dict] = None,
    db=None,
    tenant_id: str = "",
    **kwargs,
) -> str:
    """Advance onboarding and save data."""
    if not db:
        return "Error: No database session."
    if target_stage not in STAGES:
        return f"Error: Invalid stage '{target_stage}'. Valid: {', '.join(STAGES)}"

    user_id = kwargs.get("user_id", "")

    # Get or create state
    result = await db.execute(
        select(OnboardingState).where(
            OnboardingState.tenant_id == tenant_id,
            OnboardingState.user_id == user_id,
        )
    )
    state = result.scalar_one_or_none()

    if not state:
        state = OnboardingState(
            tenant_id=tenant_id,
            user_id=user_id,
            current_stage="brand_discovery",
        )
        db.add(state)

    # Update state
    state.current_stage = target_stage

    if brand_domain is not None:
        state.brand_domain = brand_domain
    if selected_teammates is not None:
        state.selected_teammates = selected_teammates
    if connected_integrations is not None:
        state.connected_integrations = connected_integrations
    if notification_preferences is not None:
        state.notification_preferences = notification_preferences

    await db.flush()

    # Build response
    idx = STAGES.index(target_stage)
    if target_stage == "completed":
        return (
            "Onboarding complete! The user's workspace is now set up.\n"
            "Celebrate this milestone. Remind them you're always here to help.\n"
            "Offer to help them get started with their first task using one of their selected teammates."
        )
    else:
        next_stage = STAGES[idx + 1] if idx + 1 < len(STAGES) else "completed"
        return (
            f"Advanced to: {target_stage} (stage {idx + 1} of {len(STAGES)})\n"
            f"What to do now: {STAGE_DESCRIPTIONS[target_stage]}\n"
            f"Next stage after this: {next_stage}"
        )
