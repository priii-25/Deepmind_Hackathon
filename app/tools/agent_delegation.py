"""
Agent delegation tools with built-in upselling.

When an agent IS active  → delegate the task normally.
When an agent is NOT active → return a rich upsell pitch so Eve
can introduce the Teem Mate and offer to onboard them.

Activation logic (checked in order):
1. Global feature flag is ON  → agent is available, delegate.
2. Flag is OFF but tenant has the agent in selected_teammates → still delegate
   (user explicitly added this agent during onboarding).
3. Both off → return upsell pitch.
"""

import logging

from sqlalchemy import select

from ..core.flags import get_flags
from ..orchestrator.registry import get_registry
from .registry import tool, ToolRisk

logger = logging.getLogger(__name__)


async def _is_teammate_selected(agent_key: str, db, tenant_id: str, **kwargs) -> bool:
    """Check if the tenant selected this agent during onboarding."""
    if not db or not tenant_id:
        return False
    try:
        from ..models.onboarding import OnboardingState
        user_id = kwargs.get("user_id", "")
        result = await db.execute(
            select(OnboardingState).where(
                OnboardingState.tenant_id == tenant_id,
                OnboardingState.user_id == user_id,
            )
        )
        state = result.scalar_one_or_none()
        if state and state.selected_teammates:
            teammates = state.selected_teammates
            if isinstance(teammates, list):
                # Check both the key (e.g. "vera") and full name variants
                return agent_key in [t.lower() for t in teammates]
        return False
    except Exception as e:
        logger.debug("Could not check selected_teammates: %s", e)
        return False


# ── Handoff marker ────────────────────────────────────────────────────
# When a multi-turn agent needs further interaction, the delegation tool
# returns this marker so Eve's handler can detect it and hand off routing.
HANDOFF_PREFIX = "[AGENT_HANDOFF:"

# ── Helpers ───────────────────────────────────────────────────────────

async def _try_delegate(agent_name: str, task: str, db, tenant_id: str, **kwargs) -> str:
    """
    Common delegation logic with session persistence.

    1. Loads any existing agent session (so multi-turn agents resume).
    2. Injects brand context.
    3. Calls the agent.
    4. Saves agent state to AgentSession table.
    5. If agent is NOT complete, wraps response with [AGENT_HANDOFF:name]
       so Eve's handler can detect it and set active_agent for direct routing.

    Returns agent response string (possibly with handoff marker).
    """
    from ..services.agent_session import load_agent_session, save_agent_session, deactivate_agent_session

    agent = get_registry().get(agent_name)
    if not agent:
        return None

    user_id = kwargs.get("user_id", "")

    # Fresh delegation from Eve → deactivate any stale session and start clean.
    # Multi-turn continuation (user talking directly to agent) goes through
    # the orchestrator's direct routing path, not through _try_delegate.
    await deactivate_agent_session(db, tenant_id, user_id, agent_name)
    agent_state = {}

    # Inject brand context
    if "_brand" in kwargs:
        agent_state["_brand"] = kwargs["_brand"]
    elif db:
        try:
            from ..services.brand_context import get_brand_context
            brand = await get_brand_context(db, tenant_id)
            if brand:
                agent_state["_brand"] = brand
        except Exception:
            pass

    # Load recent conversation history so the agent has context
    # (what the user discussed with Eve before this delegation)
    history = None
    try:
        from ..models.conversation import Conversation, Message
        from ..orchestrator.state import build_history_with_media
        from sqlalchemy import select

        # Find the current conversation by session_id (preferred) or fallback to most recent
        session_id = kwargs.get("session_id", "")
        if session_id:
            conv_result = await db.execute(
                select(Conversation)
                .where(
                    Conversation.tenant_id == tenant_id,
                    Conversation.session_id == session_id,
                )
                .limit(1)
            )
        else:
            conv_result = await db.execute(
                select(Conversation)
                .where(Conversation.tenant_id == tenant_id)
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )
        convo = conv_result.scalar_one_or_none()
        if convo:
            msg_result = await db.execute(
                select(Message)
                .where(Message.conversation_id == convo.id)
                .order_by(Message.sequence_number.desc())
                .limit(20)
            )
            msgs = list(msg_result.scalars().all())
            msgs.reverse()
            history = build_history_with_media(msgs, exclude_last=False)
    except Exception as e:
        logger.debug("Could not load conversation history for delegation: %s", e)

    # Call the agent
    response = await agent.handle(
        message=task,
        state=agent_state,
        db=db,
        user_id=user_id,
        tenant_id=tenant_id,
        history=history,
    )

    # Persist session state
    state_to_save = response.state_update if response.state_update else agent_state
    await save_agent_session(
        db, tenant_id, user_id, agent_name,
        state_to_save, response.is_complete,
    )

    # If the agent needs more interaction, signal handoff
    if not response.is_complete:
        step = state_to_save.get("current_step", "?")
        return f"{HANDOFF_PREFIX}{agent_name}:{step}]\n{response.content}"

    return response.content


# ── UGC Video — Kai ──────────────────────────────────────────────────

KAI_UPSELL = """[TEEM_MATE_UPSELL]
name: Kai
role: UGC Creator
status: not_yet_active
pitch: Kai is your UGC creator. He doesn't just make a one-off video and vanish. He runs your whole UGC machine.
capabilities:
- Daily drops, weekly series, A/B tests, trend spins, all automated
- Casts the right AI creator for your audience from a curated avatar library
- Proposes 3-5 strong content angles before writing a single script
- Generates hooks, scripts, shot lists, on-screen text, and A/B variations
- Handles the full pipeline: AI image generation, voiceover (ElevenLabs), lip-sync
- Platform-native output for TikTok, Reels, and YouTube Shorts
workflow: Kai starts by analyzing your brand, picking the right product and creator combo, then proposing angles. You pick, he executes. Full video packages ready to post.
sample_offer: Want me to bring Kai onto your team? He can start by reviewing your brand and proposing a few content angles.
[/TEEM_MATE_UPSELL]"""

@tool(
    name="agent_ugc_video",
    description=(
        "Delegate to Kai, the UGC Creator. Kai generates short-form UGC marketing videos "
        "using Veo 3.1 — complete 8-second videos with native audio from text prompts. "
        "\n\nWhen to use: User wants UGC videos, marketing videos, TikTok content, "
        "video ads, product videos, short-form content, or mentions video creation. "
        "\n\nProvide: A clear task description with product/brand, audience, style."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "What the user wants Kai to do."},
        },
        "required": ["task"],
    },
    risk=ToolRisk.EXTERNAL,
    category="orchestration",
)
async def delegate_ugc_video(task: str, db=None, tenant_id: str = "", **kwargs) -> str:
    flags = get_flags()
    if not flags.enable_ugc_video:
        if not await _is_teammate_selected("kai", db, tenant_id, **kwargs):
            return KAI_UPSELL
        logger.info("Kai flag OFF but tenant selected him — delegating anyway")

    try:
        result = await _try_delegate("ugc_video", task, db, tenant_id, **kwargs)
        return result or "Kai is not available right now. Please try again."
    except Exception as e:
        logger.error("Kai (UGC) delegation failed: %s", e)
        return f"Kai ran into an issue: {e}. Let me try a different approach."


# ── Fashion Photo — Vera ─────────────────────────────────────────────

VERA_UPSELL = """[TEEM_MATE_UPSELL]
name: Vera
role: Fashion Photographer
status: not_yet_active
pitch: Vera is your Fashion Photographer. She takes your products and turns them into stunning AI-generated photoshoots.
capabilities:
- Full AI photoshoots: model selection, scene design, lighting, creative direction
- Lookbooks, campaign shoots, hero images, e-commerce product sets, detail shots
- Prompt-based editing: change hair, outfit colors, face style, background, lighting
- Multi-format output: square, portrait, landscape, all optimized per platform
- Iterative refinement: preview, tweak, approve, then generate finals
workflow: Vera starts by understanding your product and brand positioning. She picks models that match your audience, designs scenes, and generates preview images. You refine together until it's perfect, then she produces final multi-format output.
sample_offer: Want me to bring Vera onto your team? She can start with a quick preview of your product in a curated setting.
[/TEEM_MATE_UPSELL]"""

@tool(
    name="agent_fashion_photo",
    description=(
        "Delegate to Vera, the Fashion Photographer. Vera creates AI-generated "
        "photoshoots with model selection, scene design, and iterative refinement. "
        "\n\nWhen to use: User wants product photos, fashion shots, lookbooks, "
        "campaign images, hero shots, flat-lays, photoshoots, or visual content. "
        "\n\nProvide: What the user wants. Vera guides the conversation from here."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "What the user wants Vera to do."},
        },
        "required": ["task"],
    },
    risk=ToolRisk.EXTERNAL,
    category="orchestration",
)
async def delegate_fashion_photo(task: str, db=None, tenant_id: str = "", **kwargs) -> str:
    flags = get_flags()
    if not flags.enable_fashion_photo:
        if not await _is_teammate_selected("vera", db, tenant_id, **kwargs):
            return VERA_UPSELL
        logger.info("Vera flag OFF but tenant selected her — delegating anyway")

    try:
        result = await _try_delegate("fashion_photo", task, db, tenant_id, **kwargs)
        return result or "Vera is not available right now. Please try again."
    except Exception as e:
        logger.error("Vera (Fashion) delegation failed: %s", e)
        return f"Vera ran into an issue: {e}. Let me try a different approach."


# ── Social Media — Chad ──────────────────────────────────────────────

CHAD_UPSELL = """[TEEM_MATE_UPSELL]
name: Chad
role: Social Media Manager
status: not_yet_active
pitch: Chad is your Social Media Manager. He handles the full YouTube publishing pipeline — from connecting your account to uploading and optimizing your videos.
capabilities:
- YouTube account connection via Google OAuth
- Video upload to YouTube with resumable upload (handles any file size)
- AI-powered title, description, and tag generation (SEO-optimized)
- Privacy control: public, unlisted, or private
- Category selection for proper YouTube classification
- Post-upload status monitoring
- Works with videos from Kai (UGC) or your own uploads
workflow: Chad connects to your YouTube account, takes your video, uses AI to generate the perfect title, description, and tags, then uploads it directly. You review everything before it goes live.
sample_offer: Want me to bring Chad onto your team? He can connect your YouTube account and help you publish your first video in minutes.
[/TEEM_MATE_UPSELL]"""

@tool(
    name="agent_social_media",
    description=(
        "Delegate to Chad, the Social Media Manager. Chad uploads and publishes videos "
        "to YouTube with AI-generated titles, descriptions, and tags. "
        "\n\nWhen to use: User wants to upload a video to YouTube, publish content, "
        "post to YouTube, share a video, manage their YouTube channel, or mentions YouTube. "
        "\n\nProvide: What video to upload, topic/description, and any preferences."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "What the user wants Chad to do."},
        },
        "required": ["task"],
    },
    risk=ToolRisk.WRITE,
    category="orchestration",
)
async def delegate_social_media(task: str, db=None, tenant_id: str = "", **kwargs) -> str:
    flags = get_flags()
    if not flags.enable_social_media:
        if not await _is_teammate_selected("chad", db, tenant_id, **kwargs):
            return CHAD_UPSELL
        logger.info("Chad flag OFF but tenant selected him — delegating anyway")

    try:
        result = await _try_delegate("social_media", task, db, tenant_id, **kwargs)
        return result or "Chad is not available right now. Please try again."
    except Exception as e:
        logger.error("Chad (Social) delegation failed: %s", e)
        return f"Chad ran into an issue: {e}. Let me try a different approach."


# ── Presentation — Noa ───────────────────────────────────────────────

NOA_UPSELL = """[TEEM_MATE_UPSELL]
name: Noa
role: Presentation Maker
status: not_yet_active
pitch: Noa is your Presentation Maker. She takes your topic and generates beautiful AI-powered slide decks — real PPTX files you can download and present.
capabilities:
- AI-generated slide images using Gemini (nanobanana) with professional design
- Title slides and content slides with clean modern layouts
- Downloadable PPTX files ready for presenting
- Professional dark-theme designs with clear typography
- Instant generation — just provide a topic
workflow: Noa takes your topic, generates professional slide images using AI, and packages them into a downloadable PowerPoint file. No templates needed — every slide is uniquely designed.
sample_offer: Want me to bring Noa onto your team? She can create a presentation deck for you in seconds — just give her a topic.
[/TEEM_MATE_UPSELL]"""

@tool(
    name="agent_presentation",
    description=(
        "Delegate to Noa, the Presentation Maker. Noa generates professional "
        "slide decks using Gemini AI image generation and delivers downloadable PPTX files. "
        "\n\nWhen to use: User wants slides, presentations, pitch decks, PowerPoint, "
        "PPTX, or any slide-based content. "
        "\n\nProvide: The topic or brief for the presentation."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The presentation topic or brief."},
        },
        "required": ["task"],
    },
    risk=ToolRisk.EXTERNAL,
    category="orchestration",
)
async def delegate_presentation(task: str, db=None, tenant_id: str = "", **kwargs) -> str:
    flags = get_flags()
    if not flags.enable_presentation:
        if not await _is_teammate_selected("noa", db, tenant_id, **kwargs):
            return NOA_UPSELL
        logger.info("Noa flag OFF but tenant selected her — delegating anyway")

    try:
        result = await _try_delegate("presentation", task, db, tenant_id, **kwargs)
        return result or "Noa is not available right now. Please try again."
    except Exception as e:
        logger.error("Noa (Presentation) delegation failed: %s", e)
        return f"Noa ran into an issue: {e}. Let me try a different approach."


# ── Notetaker — Ivy ──────────────────────────────────────────────────

IVY_UPSELL = """[TEEM_MATE_UPSELL]
name: Ivy
role: Notetaker & Personal Assistant
status: not_yet_active
pitch: Ivy is your Notetaker. She captures meeting notes, produces crisp summaries, creates handoff briefs, and surfaces follow-ups you might miss.
capabilities:
- Joins meetings automatically based on your calendar and time zone
- Meeting summaries with decisions, action items, and risks
- Executive briefs ready to forward
- Action lists with owners, due dates, and status tracking
- Handoff briefs that other Teem Mates can execute without back and forth
- Calendar sync via Google/Outlook
workflow: Ivy connects to your calendar, joins meetings automatically, captures everything, and delivers structured summaries with clear next steps. She also creates handoff briefs to other Teem Mates when follow-up work is needed.
sample_offer: Want me to bring Ivy onto your team? She can connect to your calendar and start joining your next meeting.
[/TEEM_MATE_UPSELL]"""

@tool(
    name="agent_notetaker",
    description=(
        "Delegate to Ivy, the Notetaker and Personal Assistant. Ivy joins meetings, "
        "captures notes, produces summaries, extracts action items, and syncs calendars. "
        "\n\nWhen to use: User wants meeting notes, transcriptions, action items, "
        "calendar management, or meeting summaries. "
        "\n\nProvide: Meeting details, or what kind of meeting action is needed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "What the user wants Ivy to do."},
        },
        "required": ["task"],
    },
    risk=ToolRisk.EXTERNAL,
    category="orchestration",
)
async def delegate_notetaker(task: str, db=None, tenant_id: str = "", **kwargs) -> str:
    flags = get_flags()
    if not flags.enable_notetaker:
        if not await _is_teammate_selected("ivy", db, tenant_id, **kwargs):
            return IVY_UPSELL
        logger.info("Ivy flag OFF but tenant selected her — delegating anyway")

    try:
        result = await _try_delegate("notetaker", task, db, tenant_id, **kwargs)
        return result or "Ivy is not available right now. Please try again."
    except Exception as e:
        logger.error("Ivy (Notetaker) delegation failed: %s", e)
        return f"Ivy ran into an issue: {e}. Let me try a different approach."
