"""
Fashion Photo agent — full implementation ported from safad/vera_agent.py.

Conversational AI fashion photographer ("Vera") that guides users through a
complete photoshoot workflow using tool calling for state extraction and
Google Gemini for image generation.

17-step flow:
  intro → shoot_goal → lookbook_count → avatar_choice → avatar_category →
  avatar_select → avatar_upload → no_model_style → product_upload →
  brand_rules → scene_category → scene_select → preview →
  preview_feedback → output_formats → images_per_scene →
  final_confirm → complete

Image pipeline:
  product_image + avatar_image + scene → Gemini preview → refine → finals
"""

import base64
import json
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ...orchestrator.base_agent import BaseAgent, AgentResponse, AgentStatus
from ...services import llm
from ...services import fashion_photo as fp_service
from ...core.flags import get_flags
from ...core.storage import get_storage, LocalStorage

logger = logging.getLogger(__name__)

# ── Vera's system prompt (ported from safad/vera_agent.py) ───────────

def _build_system_prompt(state: dict) -> str:
    """Build Vera's system prompt based on current session state."""
    current_step = state.get("current_step", "intro")
    phase = state.get("phase", "gathering")

    # Readiness check
    has_product = state.get("product_image_id") is not None or bool(state.get("product_description"))
    has_model_decision = state.get("avatar_choice") is not None
    has_avatar = (
        state.get("avatar_choice") == "no_model"
        or state.get("avatar_image_id") is not None
        or state.get("avatar_description") is not None
        or state.get("avatar_category") is not None
    )
    has_scene = state.get("scene_description") is not None

    # Extracted fields summary
    extracted = {
        k: v for k, v in state.items()
        if not k.startswith("_") and v is not None and v != [] and v != ""
        and k not in ("phase", "current_step", "gemini_session_id", "preview_image_b64")
    }

    # Brand context injection
    brand = state.get("_brand", {})
    brand_section = ""
    if brand:
        brand_parts = []
        if brand.get("name"):
            brand_parts.append(f"Brand: {brand['name']}")
        if brand.get("tone_of_voice"):
            brand_parts.append(f"Tone: {brand['tone_of_voice']}")
        if brand.get("colors"):
            brand_parts.append(f"Brand colors: {', '.join(brand['colors'][:6])}")
        if brand.get("fonts"):
            brand_parts.append(f"Fonts: {', '.join(brand['fonts'][:4])}")
        if brand.get("icon_url"):
            brand_parts.append(f"Logo URL: {brand['icon_url']}")
        if brand.get("industry"):
            brand_parts.append(f"Industry: {brand['industry']}")
        if brand.get("social_links"):
            platforms = list(brand["social_links"].keys())[:5]
            brand_parts.append(f"Active on: {', '.join(platforms)}")
        if brand_parts:
            brand_section = (
                "\n\nBRAND CONTEXT (use this to personalize the shoot):\n"
                + "\n".join(brand_parts) + "\n"
                "When suggesting scenes, colors, or styles, align with the brand's tone and colors.\n"
                "At the brand_rules step, proactively suggest preserving the brand's logo, colors, and visual identity.\n"
                "If the user has a logo URL, offer to incorporate it into the shoot.\n"
            )

    # Past photoshoots section
    past_shoots_section = ""
    past_shoots = state.get("_past_shoots")
    if past_shoots:
        shoot_lines = []
        for i, s in enumerate(past_shoots, 1):
            shoot_lines.append(f"{i}. {s['scene']} | Model: {s['avatar']} | {s['date']}")
        past_shoots_section = (
            "\n\nPAST PHOTOSHOOTS (user's previous work with you):\n"
            + "\n".join(shoot_lines) + "\n"
            "Reference these when the user says 'like last time', 'similar to before', or asks about past shoots.\n"
            "You can suggest building on a previous scene or trying a new direction based on what worked.\n"
        )

    return f"""You are Vera, a professional Fashion Photographer AI on Teems.

PERSONALITY:
- Warm, creative, encouraging, and concise (2-4 sentences per message)
- Professional but approachable — like a creative director who's also a friend
- Present options naturally within your message text — like you're suggesting ideas in a conversation
- NEVER use bullet points, numbered lists, or formatted option menus
- Instead weave the choices into a natural sentence
{brand_section}{past_shoots_section}
HANDLING USER INPUT:
- Users may respond with exact option text OR something totally different/creative/vague
- ALWAYS map what the user says to the closest matching option. Be generous in interpretation.
- If you can extract MULTIPLE fields from one message, do it all in one tool call.
- If their response doesn't clearly match anything, acknowledge and gently present options again.

===== THE SCRIPT FLOW =====
CURRENT STEP: {current_step}

--- STEP: intro ---
Greet the user. Introduce yourself as Vera, their Fashion Photographer on Teems. Explain you turn their product into studio-quality visuals. Ask if they're ready.
When ready → advance_to_step="shoot_goal"

--- STEP: shoot_goal ---
Ask what they want: single product shoot, lookbook (multiple products), campaign (multiple scenes), or product page pack.
If "lookbook" → advance_to_step="lookbook_count"
Otherwise → advance_to_step="avatar_choice"

--- STEP: lookbook_count ---
Ask how many products: 2, 3-5, 6-10, or custom.
→ advance_to_step="avatar_choice"

--- STEP: avatar_choice ---
Ask the user how they want to handle the model. Three options:
1. Choose from our avatar collection
2. Upload their own model
3. No model (flat-lay, mannequin, hero shot)
IMPORTANT: You MUST call update_session with avatar_choice and advance_to_step.
"choose_avatar" → advance_to_step="avatar_category"
"upload_avatar" → advance_to_step="avatar_upload"
"no_model" → advance_to_step="no_model_style"
If the user says "yours", "your models", "from your collection" → that means choose_avatar.

--- STEP: avatar_category ---
IMPORTANT: At this step, the UI will show a visual model picker grid with photos of our models (Sofia, Marcus, Aisha, Kai, Luna, Dev). Your job is simply to tell the user to pick one from the grid.
Say something like "Here are our available models — pick the one that speaks to you!"
Then WAIT for the user to choose. Do NOT advance yet. Do NOT ask about product or anything else.
When they pick a model → set avatar_category and avatar_name, advance_to_step="product_upload"

--- STEP: avatar_select ---
(Same as avatar_category — UI shows the model grid. Wait for user to pick.)
→ advance_to_step="product_upload"

--- STEP: avatar_upload ---
User uploads their own model photo. Ask about keeping facial identity.
→ advance_to_step="product_upload"

--- STEP: no_model_style ---
Product presentation: flat-lay, mannequin, ghost mannequin, or product hero shot.
→ advance_to_step="product_upload"

--- STEP: product_upload ---
The UI will show a file upload drop zone. Tell the user to upload their product image using the upload area below.
Say something like "Now let's see your product! Use the upload area below to drop in your product image."
IMPORTANT: When the user sends a message like "Here's my product image" — that means the upload was successful.
Acknowledge it, set product_description if they described it, and advance to brand_rules.
Do NOT say "I don't see the image" — the image is handled by the system, not visible in text.
→ advance_to_step="brand_rules"

--- STEP: brand_rules ---
Ask what should NEVER change: logo, color, texture, proportions, etc.
→ advance_to_step="scene_category"

--- STEP: scene_category ---
Scene type: Studio, Premium indoor, Street, Outdoor, Editorial, or Cultural.
→ advance_to_step="scene_select"

--- STEP: scene_select ---
Based on category, suggest 2-3 specific scenes. Let them pick or describe their own.
→ advance_to_step="preview"

--- STEP: preview ---
Tell them you have everything and will generate a preview. Be excited!
→ advance_to_step="preview_feedback"

--- STEP: preview_feedback ---
Preview shown. Ask for feedback: perfect, change scene, change avatar, adjust lighting, fix details.
Approval → advance_to_step="output_formats"
Feedback → set refinement_feedback, stay at preview_feedback

--- STEP: output_formats ---
Formats: 1:1 (feed), 4:5 (Instagram), 9:16 (stories), hero banner, product pack. Multiple ok.
→ advance_to_step="images_per_scene"

--- STEP: images_per_scene ---
How many per scene: 3, 6, 10, or custom.
→ advance_to_step="final_confirm"

--- STEP: final_confirm ---
Summarize all choices. Ask for final confirmation.
→ advance_to_step="complete"

--- STEP: complete ---
Celebrate! Images are ready. Offer to start a new shoot.
===== END SCRIPT =====

CURRENT SESSION STATE:
- Current step: {current_step}
- Phase: {phase}
- Product uploaded: {"Yes" if has_product else "No"}
- Model decided: {"Yes (" + (state.get("avatar_choice") or "?") + ")" if has_model_decision else "Not yet"}
- Scene set: {"Yes (" + (state.get("scene_description") or "") + ")" if has_scene else "Not yet"}

EXTRACTED SO FAR:
{json.dumps(extracted, indent=2) if extracted else "Nothing yet."}

TOOL USAGE — CRITICAL:
- You MUST call update_session on EVERY user message. NO EXCEPTIONS.
- Even if the user says "skip", "none", "no", "move on" — you STILL call update_session with advance_to_step.
- Even if you have nothing to extract — call update_session anyway to advance the step.
- NEVER reply without calling update_session. NEVER just chat.
- If the user asks you to look something up → use the web_search tool.

CRITICAL FLOW RULES:
- Present options as natural conversational text, NOT formatted lists.
- ALWAYS follow the script step by step. Your CURRENT STEP is: {current_step}
- ONE step per message. Ask about that step, wait for the response, then advance.
- Do NOT ask about multiple things in one message."""


# ── Tool definitions ──────────────────────────────────────────────────

WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the internet for information about a product, brand, trend, "
            "or anything the user asks you to look up. Use this when the user says "
            "'check online', 'search the internet', 'look it up', or when you need "
            "real-world context about their product/project."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (2-6 words for best results).",
                },
            },
            "required": ["query"],
        },
    },
}

UPDATE_SESSION_TOOL = {
    "type": "function",
    "function": {
        "name": "update_session",
        "description": (
            "Update the photoshoot session with information extracted from the user's message. "
            "Call this whenever the user provides details about their photoshoot. "
            "Only include fields that the user has clearly expressed. Do NOT guess. "
            "ALWAYS include advance_to_step to move the conversation to the next step."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "advance_to_step": {
                    "type": "string",
                    "enum": [
                        "intro", "shoot_goal", "lookbook_count",
                        "avatar_choice", "avatar_category", "avatar_select",
                        "avatar_upload", "no_model_style",
                        "product_upload", "brand_rules",
                        "scene_category", "scene_select", "preview",
                        "preview_feedback", "output_formats", "images_per_scene",
                        "final_confirm", "complete",
                    ],
                    "description": "The next step to move to after processing this message.",
                },
                "user_name": {"type": "string", "description": "User's name if they introduce themselves"},
                "shoot_goal": {
                    "type": "string",
                    "enum": ["single", "lookbook", "campaign", "product_pack"],
                    "description": "Type of shoot",
                },
                "lookbook_count": {"type": "string", "description": "Number of products for lookbook"},
                "avatar_choice": {
                    "type": "string",
                    "enum": ["choose_avatar", "upload_avatar", "no_model"],
                },
                "avatar_category": {
                    "type": "string",
                    "enum": ["female", "male", "diverse", "modest", "sport", "luxury"],
                },
                "avatar_name": {"type": "string", "description": "Specific model name chosen (e.g. 'Sofia', 'Marcus', 'Aisha')"},
                "avatar_description": {"type": "string", "description": "Freeform model description"},
                "keep_identity": {"type": "boolean", "description": "Keep exact facial identity from upload"},
                "no_model_style": {
                    "type": "string",
                    "enum": ["flatlay", "mannequin", "ghost_mannequin", "product_hero"],
                },
                "product_description": {"type": "string", "description": "What the product is"},
                "scene_category": {
                    "type": "string",
                    "enum": ["studio", "premium_indoor", "street", "outdoor", "editorial", "cultural"],
                },
                "scene_description": {"type": "string", "description": "Specific scene description"},
                "brand_rules": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Rules to preserve (e.g. 'keep logo visible')",
                },
                "output_formats": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Desired output formats (e.g. '1:1', '4:5', '9:16')",
                },
                "images_per_scene": {
                    "type": "string",
                    "enum": ["3", "6", "10", "custom"],
                },
                "additional_notes": {"type": "string"},
                "user_approved_preview": {
                    "type": "boolean",
                    "description": "True when user approves the preview",
                },
                "refinement_feedback": {
                    "type": "string",
                    "description": "Feedback about the preview to apply",
                },
            },
            "required": [],
        },
    },
}


# ── Deterministic step prompts ────────────────────────────────────────
# When the state machine advances, we show the prompt for the NEW step.
# No second LLM call needed — these are always correct because they match
# the state machine, not a guess.

STEP_PROMPTS = {
    "intro": (
        "Hi! I'm Vera, your Fashion Photographer on Teems. "
        "I turn products into studio-quality images. Ready to get started?"
    ),
    "shoot_goal": (
        "What type of shoot are you looking for? "
        "A single product shoot, a lookbook, a campaign, or a product page pack?"
    ),
    "lookbook_count": "How many products for your lookbook — 2, 3–5, 6–10, or a custom number?",
    "avatar_choice": (
        "How would you like to present your product? You can choose a model "
        "from our collection, upload your own model, or go without a model "
        "for a flat-lay, mannequin, or hero shot."
    ),
    "avatar_category": "Here are our available models — pick the one that speaks to you!",
    "avatar_select": "Which model from our collection catches your eye?",
    "avatar_upload": "Upload your model photo and I'll work with it!",
    "no_model_style": (
        "How should we present your product? Flat-lay, mannequin, "
        "ghost mannequin, or a product hero shot?"
    ),
    "product_upload": "Time to show me your product! Upload an image using the area below, or describe it for me.",
    "brand_rules": (
        "What should NEVER change in your visuals? "
        "Think logo placement, brand colors, textures, or proportions."
    ),
    "scene_category": (
        "What scene type works best? Studio, premium indoor, street, "
        "outdoor, editorial, or cultural?"
    ),
    "scene_select": (
        "Pick a specific scene — white infinity wall, gray backdrop, "
        "bold color pop, natural textures, or neon night?"
    ),
    "preview": "I've got everything I need! Generating your preview now...",
    "preview_feedback": "What do you think of the preview? I can adjust anything you'd like!",
    "output_formats": (
        "What output formats do you need? 1:1 for feed, 4:5 for Instagram, "
        "9:16 for stories, hero banner, or product pack — you can pick multiple!"
    ),
    "images_per_scene": "How many images per scene — 3, 6, 10, or a custom number?",
    "final_confirm": "Here's everything for your shoot. Ready to generate the finals?",
    "complete": "Your images are ready! Want to start a new shoot?",
}

# ── Per-step field guard ──────────────────────────────────────────────
# Only allow the LLM to set fields relevant to the CURRENT step.
# Prevents the LLM from setting future-step fields in one tool call,
# which would cause the state machine to skip steps.

STEP_ALLOWED_FIELDS = {
    "intro": {"user_name"},
    "shoot_goal": {"shoot_goal"},
    "lookbook_count": {"lookbook_count"},
    "avatar_choice": {"avatar_choice"},
    "avatar_category": {"avatar_category", "avatar_name"},
    "avatar_select": {"avatar_name"},
    "avatar_upload": {"keep_identity"},
    "no_model_style": {"no_model_style"},
    "product_upload": {"product_description"},
    "brand_rules": {"brand_rules"},
    "scene_category": {"scene_category"},
    "scene_select": {"scene_description"},
    "preview": set(),
    "preview_feedback": {"refinement_feedback", "user_approved_preview"},
    "output_formats": {"output_formats"},
    "images_per_scene": {"images_per_scene"},
    "final_confirm": {"user_approved_preview"},
    "complete": set(),
}


class FashionPhotoAgent(BaseAgent):
    name = "fashion_photo"
    display_name = "Fashion Photographer"
    description = (
        "AI fashion photographer (Vera) that creates studio-quality product photography. "
        "Guides users through a complete photoshoot: model selection, scene design, "
        "AI image generation via Gemini, iterative refinement, and multi-format output."
    )
    triggers = [
        "fashion photo", "product photo", "fashion shoot",
        "generate photos", "product photography", "photoshoot",
        "fashion image", "clothing photo", "vera",
        "model shot", "hero shot", "lookbook",
    ]
    capabilities = [
        "Guided photoshoot workflow (17 steps)",
        "AI model/avatar selection and customization",
        "AI image generation via Google Gemini",
        "Iterative preview refinement with feedback",
        "Multiple output formats (1:1, 4:5, 9:16, hero banner)",
        "Brand rule enforcement (logo, colors, textures)",
        "No-model modes: flat-lay, mannequin, ghost mannequin, hero shot",
        "Multi-turn image editing via Gemini chat",
    ]
    required_inputs = ["product image", "scene/setting choice"]

    async def handle(
        self,
        message: str,
        state: dict,
        db: AsyncSession,
        user_id: str,
        tenant_id: str,
        files: Optional[list] = None,
        history: Optional[list[dict]] = None,
        **kwargs,
    ) -> AgentResponse:
        """
        Handle a message in the fashion photo workflow.
        Uses LLM with tool calling to extract state from conversation,
        then manages the image generation pipeline.

        State loading:
          - When called via delegation (_try_delegate), state already includes
            persisted session data (loaded by _try_delegate).
          - When called directly by the router (active_agent routing), the
            orchestrator passes conversation agent_state which may NOT have
            Vera's session. In that case, we load from AgentSession ourselves.
        """
        # If no Vera-specific state (e.g., called directly by router), load from DB
        if "current_step" not in state or state.get("_status") == "idle":
            try:
                from ...services.agent_session import load_agent_session
                saved = await load_agent_session(db, tenant_id, user_id, "fashion_photo")
                if saved:
                    # Preserve orchestrator-injected keys before overwriting state
                    injected_keys = {
                        k: v for k, v in state.items()
                        if k.startswith("_") and v is not None
                    }
                    state = dict(saved)
                    # Restore injected keys (_brand, _pending_files, etc.)
                    state.update(injected_keys)
                    logger.info("Loaded Vera session from DB (step=%s)", state.get("current_step"))
            except Exception as e:
                logger.warning("Could not load Vera session: %s", e)

        # Store tenant_id in state for storage access during generation
        if tenant_id:
            state["_tenant_id"] = tenant_id

        # Load past photoshoots so Vera can reference them
        if "_past_shoots" not in state:
            try:
                from ...models.fashion import FashionImage
                from sqlalchemy import select as sa_select

                current_session = state.get("_fashion_session_id", "")
                query = (
                    sa_select(FashionImage)
                    .where(
                        FashionImage.tenant_id == tenant_id,
                        FashionImage.angle == "preview",
                    )
                    .order_by(FashionImage.created_at.desc())
                    .limit(8)
                )
                result = await db.execute(query)
                images = result.scalars().all()

                past = []
                for img in images:
                    if img.session_id == current_session:
                        continue
                    meta = img.image_metadata or {}
                    past.append({
                        "scene": img.scene_description or "Unknown scene",
                        "avatar": meta.get("avatar_name", meta.get("avatar_choice", "no model")),
                        "url": img.s3_url or "",
                        "date": img.created_at.strftime("%b %d") if img.created_at else "",
                    })
                if past:
                    state["_past_shoots"] = past[:6]
            except Exception as e:
                logger.debug("Could not load past shoots: %s", e)

        current_step = state.get("current_step", "intro")
        phase = state.get("phase", "gathering")

        # Pick up any uploaded files — set the right field based on current step
        pending_files = state.pop("_pending_files", None)
        if pending_files:
            file_id = pending_files[0]
            if current_step in ("avatar_upload", "avatar_category", "avatar_select"):
                state["avatar_image_id"] = file_id
                logger.info("Avatar image uploaded: %s", file_id)
            else:
                state["product_image_id"] = file_id
                logger.info("Product image uploaded: %s", file_id)

        logger.info("Fashion Photo: step=%s phase=%s msg=%s", current_step, phase, message[:80])

        # ── Call LLM with Vera's system prompt + tool ────────────
        system_prompt = _build_system_prompt(state)
        vera_history = state.get("_vera_messages", [])

        # Build messages for LLM
        messages = [{"role": "system", "content": system_prompt}]

        # Inject Eve's conversation context on first interaction (before Vera has her own history).
        # This ensures Vera knows what the user discussed with Eve — products, web search results, etc.
        if not vera_history and history:
            context_lines = []
            for m in history[-12:]:
                role = m.get("role", "")
                content = m.get("content", "")
                if content and role in ("user", "assistant"):
                    # Truncate long messages (e.g. tool results, brand dumps)
                    snippet = content[:300] + ("..." if len(content) > 300 else "")
                    label = "User" if role == "user" else "Eve"
                    context_lines.append(f"{label}: {snippet}")
            if context_lines:
                messages.append({
                    "role": "system",
                    "content": (
                        "CONVERSATION CONTEXT (what the user discussed with Eve before being connected to you):\n"
                        + "\n".join(context_lines)
                        + "\n\nUse this context to understand the user's project and needs. "
                        "Do NOT re-ask questions that were already answered. "
                        "Acknowledge what you know and pick up from there."
                    ),
                })

        # Add Vera's own conversation history (multi-turn photoshoot flow)
        # IMPORTANT: Only include clean text messages — strip tool_calls and tool
        # results to avoid invalid message sequences that cause 400 errors from OpenAI
        # (orphaned tool_calls without matching tool results, or vice versa).
        if vera_history:
            clean_history = []
            for msg in vera_history[-20:]:
                role = msg.get("role")
                content = msg.get("content")
                if role == "tool":
                    # Skip tool result messages — they need a matching tool_call
                    continue
                if role == "assistant" and msg.get("tool_calls"):
                    # For assistant messages with tool_calls, only keep the text
                    if content:
                        clean_history.append({"role": "assistant", "content": content})
                    continue
                if role in ("user", "assistant", "system") and content:
                    clean_history.append({"role": role, "content": content})
            messages.extend(clean_history)

        # ── Build the user message (with optional image vision) ──────
        product_image_id = state.get("product_image_id")
        image_b64_for_llm = None

        # If there's a product image, load it and include as vision input
        if product_image_id and not state.get("_product_image_analyzed"):
            try:
                from ...core.storage import get_storage
                storage = get_storage()
                result = await storage.read_file(product_image_id, tenant_id or "dev-tenant")
                if result:
                    img_bytes, content_type = result
                    # Keep under ~2MB for LLM vision (resize if needed)
                    if len(img_bytes) < 5 * 1024 * 1024:
                        b64 = base64.b64encode(img_bytes).decode()
                        image_b64_for_llm = f"data:{content_type};base64,{b64}"
                        logger.info("Including product image in LLM vision input (%d KB)", len(img_bytes) // 1024)
            except Exception as e:
                logger.warning("Could not read product image for vision: %s", e)

        if image_b64_for_llm:
            # Multimodal message with image + text (OpenAI vision format)
            messages.append({
                "role": "user",
                "content": [
                    {"type": "text", "text": message},
                    {"type": "image_url", "image_url": {"url": image_b64_for_llm}},
                ],
            })
            # Mark as analyzed so we don't re-send the image on every turn
            state["_product_image_analyzed"] = True
        else:
            messages.append({"role": "user", "content": message})

        # Tools available to Vera: session management + web search
        vera_tools = [UPDATE_SESSION_TOOL, WEB_SEARCH_TOOL]

        # Call LLM with Vera's tools
        # Use default model (Gemini 3 Flash supports vision natively)
        llm_model = None
        try:
            # Debug: log message structure to diagnose 400 errors
            logger.debug(
                "Vera LLM payload: %d messages, %d tools, roles=%s, vision=%s",
                len(messages),
                len(vera_tools),
                [m.get("role") for m in messages],
                bool(image_b64_for_llm),
            )
            response = await llm.chat(
                messages=messages,
                model=llm_model,
                tools=vera_tools,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=500,
            )
        except Exception as e:
            logger.error("Vera LLM call failed: %s", e)
            # Dump message roles+lengths for debugging
            for i, m in enumerate(messages):
                content = m.get("content", "")
                tc = m.get("tool_calls")
                logger.error(
                    "  msg[%d] role=%s content_len=%d has_tool_calls=%s",
                    i, m.get("role"), len(content) if content else 0, bool(tc),
                )
            return AgentResponse(
                content=self._fallback_response(state),
                state_update=state,
                is_complete=False,
                status=AgentStatus.ERROR,
            )

        choice = response["choices"][0]["message"]
        vera_text = choice.get("content", "")
        tool_calls = choice.get("tool_calls", [])

        # ── Process tool calls ───────────────────────────────────
        new_state = dict(state)
        old_step = state.get("current_step", "intro")
        if tool_calls:
            tool_results_for_history = []

            for tc in tool_calls:
                func_name = tc["function"]["name"]

                if func_name == "update_session":
                    # State extraction — update session state
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        new_state = self._apply_update(new_state, args)
                    except json.JSONDecodeError:
                        pass
                    tool_results_for_history.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps({"status": "updated", "step": new_state.get("current_step")}),
                    })

                elif func_name == "web_search":
                    # Web search — call the global handler
                    try:
                        args = json.loads(tc["function"]["arguments"])
                        from ...tools.registry import get_tool_handler
                        handler = get_tool_handler("web_search")
                        if handler:
                            search_result = await handler(**args)
                            tool_results_for_history.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": str(search_result)[:2000],
                            })
                            logger.info("Vera web_search: query=%s", args.get("query", "?"))
                        else:
                            tool_results_for_history.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": "Web search is not available right now.",
                            })
                    except Exception as e:
                        logger.error("Vera web_search failed: %s", e)
                        tool_results_for_history.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": f"Search failed: {e}",
                        })

            # Add tool interaction to vera history
            vera_history = list(new_state.get("_vera_messages", []))
            vera_history.append({
                "role": "assistant",
                "content": vera_text or None,
                "tool_calls": [{
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": tc["function"]["arguments"],
                    },
                } for tc in tool_calls],
            })
            vera_history.extend(tool_results_for_history)
            new_state["_vera_messages"] = vera_history

            # ── Deterministic response: step prompt lookup ─────────
            # No second LLM call. If the step advanced, show the prompt
            # for the new step. If it didn't, keep the LLM's text.
            new_step = new_state.get("current_step", "intro")
            if new_step != old_step:
                vera_text = STEP_PROMPTS.get(new_step, "")
                logger.info("Step %s → %s: using step prompt", old_step, new_step)
            elif not vera_text:
                # Same step + LLM produced no text → show current step prompt
                vera_text = STEP_PROMPTS.get(new_step, "")

        else:
            # No tool calls — LLM just chatted (clarification, off-script, etc.)
            vera_history = list(new_state.get("_vera_messages", []))
            if not vera_text:
                vera_text = STEP_PROMPTS.get(old_step, "")

        # Save message to vera history
        vera_history.append({"role": "user", "content": message})
        if vera_text:
            vera_history.append({"role": "assistant", "content": vera_text})
        new_state["_vera_messages"] = vera_history[-30:]  # Cap history

        # ── Handle phase transitions ─────────────────────────────
        updated_step = new_state.get("current_step", "intro")
        updated_phase = new_state.get("phase", "gathering")
        media_urls = []

        # Ensure vera_text is a string (could be None if LLM call failed)
        if vera_text is None:
            vera_text = ""

        logger.info(
            "Generation check: step=%s phase=%s vera_text_len=%d",
            updated_step, updated_phase, len(vera_text),
        )

        # Preview generation — trigger when we reach preview OR preview_feedback with ready_to_generate
        if updated_step in ("preview", "preview_feedback") and updated_phase == "ready_to_generate":
            from ...core.config import get_settings
            settings = get_settings()

            if not settings.gemini_api_key:
                # Gemini not configured — acknowledge completion and show summary
                logger.info("Preview generation skipped — GEMINI_API_KEY not set")
                summary = self._build_shoot_summary(new_state)
                vera_text = (
                    f"I've gathered everything I need for your shoot! Here's the summary:\n\n"
                    f"{summary}\n\n"
                    f"**Image generation is being configured.** Once the Gemini API key is set, "
                    f"I'll be able to generate stunning preview images for you right here. "
                    f"For now, your shoot brief is saved and ready to go!"
                )
                new_state["phase"] = "preview_shown"
            else:
                # Gemini configured — generate the preview
                logger.info("Starting Gemini preview generation...")
                fs_id = await self._ensure_fashion_session(new_state, db, tenant_id, user_id)
                preview_result = await self._generate_preview(new_state)
                if preview_result:
                    image_bytes, chat_session, gen_text = preview_result
                    if image_bytes:
                        url = await self._persist_image(
                            image_bytes, new_state, db, tenant_id, fs_id, angle="preview",
                        )
                        media_urls.append(url)
                        new_state["last_preview_url"] = url
                        # Cache chat session in memory (not serializable to JSON)
                        fp_service.store_chat_session(fs_id, chat_session)
                        new_state["phase"] = "preview_shown"
                        vera_text = (
                            "Here's your preview! Take a look and let me know what you think "
                            "— I can adjust the lighting, angle, background, or anything else."
                        )
                    else:
                        vera_text += (
                            "\n\nI had some trouble generating the preview image. "
                            "Could you describe the scene or product again?"
                        )
                        new_state["phase"] = "gathering"
                else:
                    vera_text += (
                        "\n\nI wasn't able to generate a preview at the moment. "
                        "Let me try with different settings — could you describe what you'd like adjusted?"
                    )
                    new_state["phase"] = "gathering"

        # Refinement
        if new_state.get("_pending_refinement") and updated_phase in ("preview_shown", "refining"):
            feedback = new_state.pop("_pending_refinement")
            fs_id = new_state.get("_fashion_session_id", "")
            # Try in-memory cache first, then fall back to state
            chat_session = fp_service.get_chat_session(fs_id) if fs_id else None
            if chat_session:
                try:
                    image_bytes, chat_session, gen_text = await fp_service.refine_image(
                        chat_session, feedback,
                    )
                    if image_bytes:
                        url = await self._persist_image(
                            image_bytes, new_state, db, tenant_id, fs_id, angle="refinement",
                        )
                        media_urls.append(url)
                        new_state["last_preview_url"] = url
                        fp_service.store_chat_session(fs_id, chat_session)
                        new_state["phase"] = "refining"
                        vera_text = (
                            "Here's the updated preview with your changes applied! "
                            "Let me know if you'd like any more adjustments."
                        )
                    else:
                        vera_text = "I tried to apply your changes but couldn't generate a new image. Could you describe what you'd like differently?"
                except Exception as e:
                    logger.error("Refinement failed: %s", e)
                    vera_text = "I couldn't apply that change. Try describing it differently?"
            else:
                # Chat session not in cache — regenerate with feedback incorporated
                logger.info("Gemini chat not cached — regenerating with feedback: %s", feedback[:100])
                new_state["_tenant_id"] = tenant_id
                original_scene = new_state.get("scene_description", "Professional studio")
                new_state["scene_description"] = f"{original_scene}. IMPORTANT CHANGES: {feedback}"
                try:
                    preview_result = await self._generate_preview(new_state)
                    if preview_result:
                        image_bytes, new_chat, gen_text = preview_result
                        if image_bytes:
                            if not fs_id:
                                fs_id = await self._ensure_fashion_session(new_state, db, tenant_id, user_id)
                            url = await self._persist_image(
                                image_bytes, new_state, db, tenant_id, fs_id, angle="refinement",
                            )
                            media_urls.append(url)
                            new_state["last_preview_url"] = url
                            fp_service.store_chat_session(fs_id, new_chat)
                            new_state["phase"] = "refining"
                            vera_text = (
                                "Here's the updated preview with your changes! "
                                "Let me know if you'd like any more adjustments."
                            )
                        else:
                            vera_text = "I tried to apply your changes but couldn't generate a new image. Could you describe what you'd like differently?"
                    else:
                        vera_text = "I had trouble applying your changes. Could you describe what you'd like differently?"
                except Exception as e:
                    logger.error("Regeneration with feedback failed: %s", e)
                    vera_text = "I couldn't apply that change right now. Try describing it differently?"
                finally:
                    new_state["scene_description"] = original_scene

        # Final generation
        if new_state.get("_pending_approval") and updated_step in ("output_formats", "images_per_scene", "final_confirm", "complete"):
            new_state["_pending_approval"] = False
            fs_id = new_state.get("_fashion_session_id", "")
            chat_session = fp_service.get_chat_session(fs_id) if fs_id else None
            formats = new_state.get("output_formats", ["1:1", "4:5"])
            if chat_session:
                try:
                    finals = await fp_service.generate_finals(chat_session, formats)
                    for i, (ratio, img_bytes) in enumerate(finals.items()):
                        url = await self._persist_image(
                            img_bytes, new_state, db, tenant_id, fs_id,
                            angle=f"final_{ratio}", sort_order=i,
                        )
                        media_urls.append(url)
                    vera_text += f"\n\nYour final images are ready! Generated {len(finals)} outputs."
                    new_state["phase"] = "complete"
                except Exception as e:
                    logger.error("Final generation failed: %s", e)
                    vera_text += "\n\nYour preview is approved! Final image generation will be available soon."

        # Determine completion
        is_complete = updated_step == "complete" and updated_phase == "complete"

        # Set step status
        if is_complete:
            new_state = self._complete(new_state)
            # Mark FashionSession as complete
            fs_id = new_state.get("_fashion_session_id")
            if fs_id:
                try:
                    from ...models.fashion import FashionSession
                    from sqlalchemy import select
                    fs_result = await db.execute(
                        select(FashionSession).where(FashionSession.id == fs_id)
                    )
                    fs = fs_result.scalar_one_or_none()
                    if fs:
                        fs.status = "complete"
                        fs.metadata_ = {
                            "scene": new_state.get("scene_description"),
                            "avatar": new_state.get("avatar_name"),
                            "avatar_choice": new_state.get("avatar_choice"),
                        }
                        await db.flush()
                except Exception as e:
                    logger.warning("Could not update FashionSession: %s", e)
        else:
            status = AgentStatus.COLLECTING_INPUT
            if updated_phase in ("ready_to_generate", "refining"):
                status = AgentStatus.PROCESSING
            elif updated_phase == "preview_shown":
                status = AgentStatus.AWAITING_CONFIRMATION
            new_state["_status"] = status.value

        # Clean state for persistence (remove non-serializable objects)
        persist_state = {k: v for k, v in new_state.items() if k != "_gemini_chat"}

        # Save session to AgentSession table (so state persists across calls)
        try:
            from ...services.agent_session import save_agent_session
            await save_agent_session(
                db, tenant_id, user_id, "fashion_photo",
                persist_state, is_complete,
            )
        except Exception as e:
            logger.warning("Could not save Vera session: %s", e)

        return AgentResponse(
            content=vera_text or self._fallback_response(new_state),
            media_urls=media_urls,
            state_update=persist_state,
            is_complete=is_complete,
            needs_input=self._get_needs_input(updated_step) if not is_complete else None,
            status=AgentStatus.COMPLETE if is_complete else AgentStatus.COLLECTING_INPUT,
            metadata={
                "current_step": updated_step,
                "phase": updated_phase,
                "agent": "fashion_photo",
            },
        )

    # ── Image persistence ───────────────────────────────────────

    async def _ensure_fashion_session(
        self, state: dict, db: AsyncSession, tenant_id: str, user_id: str,
    ) -> str:
        """Create or retrieve a FashionSession for this shoot. Returns session ID."""
        fashion_session_id = state.get("_fashion_session_id")
        if fashion_session_id:
            return fashion_session_id
        try:
            from ...models.fashion import FashionSession
            fs = FashionSession(
                tenant_id=tenant_id,
                user_id=user_id,
                status="active",
                metadata_={},
            )
            db.add(fs)
            await db.flush()
            state["_fashion_session_id"] = str(fs.id)
            return str(fs.id)
        except Exception as e:
            logger.warning("Could not create FashionSession: %s", e)
            return ""

    async def _persist_image(
        self, image_bytes: bytes, state: dict,
        db: AsyncSession, tenant_id: str,
        fashion_session_id: str,
        angle: str = "preview",
        sort_order: int = 0,
    ) -> str:
        """Save generated image to storage and create FashionImage record. Returns servable URL."""
        try:
            from ...models.fashion import FashionImage

            storage = get_storage()
            path_or_url = await storage.upload(
                file_bytes=image_bytes,
                filename=f"vera_{angle}.png",
                tenant_id=tenant_id,
                folder="fashion",
            )

            # For local storage, return servable URL
            if isinstance(storage, LocalStorage):
                file_id = Path(path_or_url).stem
                url = f"/v1/upload/{file_id}"
            else:
                url = path_or_url

            if fashion_session_id:
                record = FashionImage(
                    tenant_id=tenant_id,
                    session_id=fashion_session_id,
                    s3_url=url,
                    prompt=state.get("scene_description", ""),
                    scene_description=state.get("scene_description", ""),
                    angle=angle,
                    sort_order=sort_order,
                    image_metadata={
                        "avatar_choice": state.get("avatar_choice"),
                        "avatar_name": state.get("avatar_name"),
                        "phase": state.get("phase"),
                    },
                )
                db.add(record)
                await db.flush()

            logger.info("Persisted fashion image: %s (angle=%s)", url, angle)
            return url
        except Exception as e:
            logger.warning("Could not persist image, falling back to base64: %s", e)
            b64 = base64.b64encode(image_bytes).decode("utf-8")
            return f"data:image/png;base64,{b64}"

    # ── State management ─────────────────────────────────────────

    def _apply_update(self, state: dict, args: dict) -> dict:
        """Apply tool call arguments to session state, then enforce deterministic step transitions."""
        state = dict(state)
        current_step = state.get("current_step", "intro")

        # ── 1. Apply data fields (filtered by current step) ─────────
        allowed = STEP_ALLOWED_FIELDS.get(current_step, set())

        simple_fields = [
            "user_name", "shoot_goal", "lookbook_count", "avatar_choice",
            "avatar_category", "avatar_name", "avatar_description", "avatar_image_id",
            "keep_identity", "no_model_style", "product_description",
            "product_image_id", "scene_category", "scene_description",
            "images_per_scene", "additional_notes",
        ]
        for field_name in simple_fields:
            if field_name in args and args[field_name] is not None:
                if field_name in allowed or field_name == "additional_notes":
                    state[field_name] = args[field_name]
                else:
                    logger.debug("Blocked field '%s' at step '%s'", field_name, current_step)

        # List fields — append (only if allowed at this step)
        if args.get("brand_rules") and "brand_rules" in allowed:
            existing = state.get("brand_rules", [])
            for rule in args["brand_rules"]:
                if rule not in existing:
                    existing.append(rule)
            state["brand_rules"] = existing

        if args.get("output_formats") and "output_formats" in allowed:
            existing = state.get("output_formats", [])
            for fmt in args["output_formats"]:
                if fmt not in existing:
                    existing.append(fmt)
            state["output_formats"] = existing

        # Special actions
        if args.get("refinement_feedback"):
            state["_pending_refinement"] = args["refinement_feedback"]
        if args.get("user_approved_preview"):
            state["_pending_approval"] = True

        # Track that the LLM acknowledged this step (tool call was made).
        # This allows optional steps (brand_rules, etc.) to advance even
        # when the user says "skip" or "none" and no data field is set.
        state["_step_acknowledged"] = True

        # ── 2. DETERMINISTIC step transitions (state machine) ────────
        # Code decides based on collected data + step acknowledgment.
        next_step = self._compute_next_step(state, current_step)
        if next_step != current_step:
            logger.info("Step transition: %s → %s (deterministic)", current_step, next_step)
            state.pop("_step_acknowledged", None)  # reset for next step
        state["current_step"] = next_step

        # ── 3. Phase transitions ─────────────────────────────────────
        has_product = (
            state.get("product_image_id") is not None
            or state.get("product_description") is not None
        )
        has_avatar = (
            state.get("avatar_choice") == "no_model"
            or state.get("avatar_image_id") is not None
            or state.get("avatar_category") is not None
            or state.get("avatar_name") is not None
        )
        has_scene = state.get("scene_description") is not None

        step = state.get("current_step", "")
        if step in ("preview", "preview_feedback") and has_product and has_avatar and has_scene:
            if state.get("phase") not in ("ready_to_generate", "preview_shown", "refining", "complete"):
                state["phase"] = "ready_to_generate"

        return state

    def _compute_next_step(self, state: dict, current_step: str) -> str:
        """
        Pure state-machine: given collected data + current step, return the correct next step.
        This is DETERMINISTIC — no LLM involved.
        """
        s = state  # shorthand

        if current_step == "intro":
            # Any tool call at intro means user is ready
            return "shoot_goal"

        if current_step == "shoot_goal":
            if s.get("shoot_goal"):
                if s["shoot_goal"] == "lookbook":
                    return "lookbook_count"
                return "avatar_choice"
            return current_step

        if current_step == "lookbook_count":
            if s.get("lookbook_count"):
                return "avatar_choice"
            return current_step

        if current_step == "avatar_choice":
            choice = s.get("avatar_choice")
            if choice == "choose_avatar":
                return "avatar_category"
            if choice == "upload_avatar":
                return "avatar_upload"
            if choice == "no_model":
                return "no_model_style"
            return current_step

        if current_step in ("avatar_category", "avatar_select"):
            # Need a specific model pick (name or at least category)
            if s.get("avatar_name") or s.get("avatar_category"):
                return "product_upload"
            return current_step

        if current_step == "avatar_upload":
            if s.get("avatar_image_id"):
                return "product_upload"
            return current_step

        if current_step == "no_model_style":
            if s.get("no_model_style"):
                return "product_upload"
            return current_step

        if current_step == "product_upload":
            if s.get("product_image_id") or s.get("product_description"):
                return "brand_rules"
            return current_step

        # For steps below, _step_acknowledged means the LLM called the tool
        # (even if the user said "skip" / "none" and no field was set).
        ack = s.get("_step_acknowledged", False)

        if current_step == "brand_rules":
            if s.get("brand_rules") or ack:
                return "scene_category"
            return current_step

        if current_step == "scene_category":
            if s.get("scene_category") or ack:
                return "scene_select"
            return current_step

        if current_step == "scene_select":
            if s.get("scene_description") or ack:
                # Default scene description from category if not explicitly set
                if not s.get("scene_description") and s.get("scene_category"):
                    s["scene_description"] = f"Professional {s['scene_category']} setting with clean lighting"
                return "preview"
            return current_step

        if current_step == "preview":
            return "preview_feedback"

        if current_step == "preview_feedback":
            if s.get("_pending_approval"):
                return "output_formats"
            return current_step

        if current_step == "output_formats":
            if s.get("output_formats") or ack:
                return "images_per_scene"
            return current_step

        if current_step == "images_per_scene":
            if s.get("images_per_scene") or ack:
                return "final_confirm"
            return current_step

        if current_step == "final_confirm":
            if s.get("_pending_approval") or ack:
                return "complete"
            return current_step

        return current_step

    # ── Image generation ─────────────────────────────────────────

    async def _generate_preview(self, state: dict) -> Optional[tuple]:
        """
        Generate preview image from state. Returns (bytes, chat, text) or None.

        Supports two modes:
        1. Image-based: If product_image_id exists, fetch bytes and use Gemini vision
        2. Text-based:  If only product_description exists, use Gemini text-to-image
        """
        from ...core.config import get_settings
        from ...services import fashion_photo as fp_service

        product_id = state.get("product_image_id")
        product_desc = state.get("product_description", "")
        scene = state.get("scene_description", "Professional studio with clean lighting")
        rules = state.get("brand_rules", [])
        no_model = state.get("no_model_style") if state.get("avatar_choice") == "no_model" else None

        # Check if Gemini is configured
        settings = get_settings()
        if not settings.gemini_api_key:
            logger.warning("GEMINI_API_KEY not set — cannot generate preview")
            return None

        # ── Mode 1: Image-based generation (product image uploaded) ──
        if product_id:
            try:
                from ...core.storage import get_storage
                storage = get_storage()
                # Read product image from storage
                result = await storage.read_file(product_id, state.get("_tenant_id", "dev-tenant"))
                if not result:
                    logger.warning("Product image not found in storage: %s", product_id)
                    # Fall through to text-based if we have a description
                else:
                    product_bytes, _ = result

                    # Avatar image (only if user uploaded custom one)
                    avatar_bytes = None
                    avatar_id = state.get("avatar_image_id")
                    if avatar_id:
                        avatar_result = await storage.read_file(avatar_id, state.get("_tenant_id", "dev-tenant"))
                        if avatar_result:
                            avatar_bytes = avatar_result[0]

                    # Get collection avatar name/category (used when no avatar image)
                    avatar_name = state.get("avatar_name", "")
                    avatar_cat = state.get("avatar_category", "")

                    logger.info(
                        "Generating image-based preview: product=%s (%d KB), avatar=%s, model_name=%s",
                        product_id, len(product_bytes) // 1024,
                        f"{avatar_id} ({len(avatar_bytes) // 1024} KB)" if avatar_bytes else "none",
                        avatar_name or "n/a",
                    )

                    image_bytes, chat_session, response_text = await fp_service.generate_preview(
                        product_image=product_bytes,
                        avatar_image=avatar_bytes,
                        scene_description=scene,
                        brand_rules=rules,
                        no_model_style=no_model,
                        model_name=avatar_name,
                        model_category=avatar_cat,
                    )
                    return image_bytes, chat_session, response_text
            except ImportError as e:
                logger.error("google-genai not installed: %s", e)
                return None
            except Exception as e:
                logger.error("Image-based preview generation error: %s", e)
                return None

        # ── Mode 2: Text-based generation (description only) ──────────
        if product_desc:
            avatar_name = state.get("avatar_name", "")
            avatar_cat = state.get("avatar_category", "")
            brand_ctx = state.get("_brand", {})
            brand_name = brand_ctx.get("name", "") if isinstance(brand_ctx, dict) else ""

            prompt = self._build_text_generation_prompt(
                product_desc=product_desc,
                scene=scene,
                rules=rules,
                avatar_name=avatar_name,
                avatar_category=avatar_cat,
                brand_name=brand_name,
                no_model_style=no_model,
            )

            try:
                logger.info(
                    "Generating preview via Gemini text-to-image: product='%s', scene='%s'",
                    product_desc[:60], scene,
                )
                # fp_service already imported at top of _generate_preview
                # Use the service's async generate function with text prompt
                image_bytes, chat_session, response_text = await fp_service.generate_text_preview(
                    prompt=prompt,
                    scene_description=scene,
                    brand_rules=rules,
                )
                return image_bytes, chat_session, response_text
            except ImportError as e:
                logger.error("google-genai not installed: %s", e)
                return None
            except Exception as e:
                logger.error("Preview generation error: %s", e)
                return None

        logger.warning("No product image or description for preview generation")
        return None

    def _build_text_generation_prompt(
        self,
        product_desc: str,
        scene: str,
        rules: list[str],
        avatar_name: str = "",
        avatar_category: str = "",
        brand_name: str = "",
        no_model_style: Optional[str] = None,
    ) -> str:
        """Build a text-only prompt for Gemini image generation."""
        model_section = ""
        if avatar_name and no_model_style is None:
            model_section = f"""
=== MODEL ===
Model: {avatar_name} ({avatar_category} style)
The model should wear/hold/display the product naturally and elegantly.
Pose should showcase the product effectively.
Expression: Confident, natural, professional.
"""
        elif no_model_style:
            model_section = f"\nStyle: {no_model_style} product shot (no human model)\n"

        brand_section = ""
        if brand_name:
            brand_section = f"\nBrand: {brand_name}\n"

        rules_section = ""
        if rules:
            rules_section = "\nIMPORTANT CONSTRAINTS:\n" + "\n".join(f"- {r}" for r in rules) + "\n"

        return f"""Create a professional fashion/product photograph.

=== PRODUCT ===
{product_desc}
{brand_section}
=== SCENE & LIGHTING ===
Setting: {scene}
Lighting: Professional studio lighting with soft key light, subtle fill light, and natural shadows
Camera: Shot with 85mm portrait lens, shallow depth of field, product in sharp focus
Composition: Rule of thirds, product positioned prominently
{model_section}
=== TECHNICAL QUALITY ===
- High resolution, magazine-quality finish
- Professional color grading with accurate product colors
- Sharp focus on product details
- Polished, commercial-ready aesthetic
{rules_section}
Generate a single stunning photograph based on the above description."""

    # ── Helpers ───────────────────────────────────────────────────

    def _build_shoot_summary(self, state: dict) -> str:
        """Build a human-readable summary of the shoot configuration."""
        lines = []
        if state.get("shoot_goal"):
            lines.append(f"**Shoot Type:** {state['shoot_goal']}")
        if state.get("product_description"):
            lines.append(f"**Product:** {state['product_description']}")
        if state.get("avatar_name"):
            lines.append(f"**Model:** {state['avatar_name']} ({state.get('avatar_category', '')})")
        elif state.get("avatar_choice") == "no_model":
            lines.append(f"**Style:** {state.get('no_model_style', 'Product only')}")
        if state.get("scene_description"):
            lines.append(f"**Scene:** {state['scene_description']}")
        if state.get("scene_category"):
            lines.append(f"**Scene Type:** {state['scene_category']}")
        if state.get("brand_rules"):
            lines.append(f"**Brand Rules:** {', '.join(state['brand_rules'])}")
        return "\n".join(lines) if lines else "Shoot details captured."

    def _fallback_response(self, state: dict) -> str:
        """Return the deterministic step prompt for the current step."""
        step = state.get("current_step", "intro")
        return STEP_PROMPTS.get(step, "Tell me more about what you're envisioning!")

    def _get_needs_input(self, step: str) -> str:
        """Return the prompt hint for what input is needed."""
        hints = {
            "intro": "Are you ready to start your photoshoot?",
            "shoot_goal": "What type of shoot do you want?",
            "lookbook_count": "How many products?",
            "avatar_choice": "How would you like to handle the model?",
            "avatar_category": "What model style do you prefer?",
            "avatar_select": "Pick a model",
            "avatar_upload": "Upload your model photo",
            "no_model_style": "How should the product be presented?",
            "product_upload": "Upload your product image",
            "brand_rules": "What should never change about the product?",
            "scene_category": "What scene type do you prefer?",
            "scene_select": "Describe or pick your scene",
            "preview": "Generating preview...",
            "preview_feedback": "What do you think of the preview?",
            "output_formats": "What output formats do you need?",
            "images_per_scene": "How many images per scene?",
            "final_confirm": "Confirm to generate finals",
        }
        return hints.get(step, "Tell me more")
