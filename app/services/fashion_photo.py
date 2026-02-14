"""
Fashion photo generation service — powered by Google Gemini native image generation.

Ported from safad/nano_banana.py. Async wrapper around the sync google-genai SDK.

Features:
  - Multi-turn chat for iterative editing (preview → refine → finals)
  - Multiple aspect ratio support (1:1, 4:5, 9:16, 16:9)
  - Professional fashion photography prompts
  - Brand rule enforcement
"""

import asyncio
import base64
import logging
import os
import tempfile
from io import BytesIO
from typing import Optional

from ..core.config import get_settings

logger = logging.getLogger(__name__)


_gemini_client = None

# In-memory cache of active Gemini chat sessions.
# Keyed by fashion_session_id so they survive across HTTP requests
# within the same process. (Same pattern as NanoBanana keeping client alive.)
_chat_sessions: dict[str, object] = {}


def store_chat_session(session_key: str, chat_session) -> None:
    """Cache a Gemini chat session in memory."""
    _chat_sessions[session_key] = chat_session
    logger.debug("Cached chat session: %s (%d active)", session_key, len(_chat_sessions))


def get_chat_session(session_key: str):
    """Retrieve a cached Gemini chat session."""
    return _chat_sessions.get(session_key)


def remove_chat_session(session_key: str) -> None:
    """Remove a chat session from cache (e.g. on completion)."""
    _chat_sessions.pop(session_key, None)


def _get_gemini_client():
    """Lazy-load and cache the google-genai client as a singleton.

    IMPORTANT: The client MUST be cached — if it gets garbage-collected,
    its internal httpx connection closes and chat sessions break with
    "Cannot send a request, as the client has been closed."
    """
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    try:
        from google import genai
        settings = get_settings()
        api_key = settings.gemini_api_key
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for fashion photo generation")
        _gemini_client = genai.Client(api_key=api_key)
        return _gemini_client
    except ImportError:
        raise ImportError(
            "google-genai package is required for fashion photo generation. "
            "Install it with: pip install google-genai"
        )


def _build_fashion_prompt(
    scene_description: str,
    brand_rules: list[str],
    style: str = "professional fashion photography, hero shot style",
) -> str:
    """Build the detailed fashion photography prompt for Gemini."""
    prompt = f"""Create a professional fashion photograph in {style} style.

=== IMAGE INPUTS ===
IMAGE 1: The PRODUCT (item to showcase - extract exact colors, logos, textures, proportions from this image)
IMAGE 2: The MODEL (person who will wear/hold the product - use their pose, styling, and features from this image)

=== YOUR TASK ===
Compose a fashion photograph showing the MODEL from IMAGE 2 wearing/holding/displaying the PRODUCT from IMAGE 1.

=== SCENE & LIGHTING ===
Setting: {scene_description}
Lighting: Professional studio lighting with soft key light, subtle fill light, and natural shadows
Camera: Shot with 85mm portrait lens, shallow depth of field, product in sharp focus
Composition: Rule of thirds, product positioned prominently, model complements but doesn't overshadow

=== CRITICAL PRODUCT REQUIREMENTS ===
- EXTRACT the exact product from IMAGE 1 (colors, logos, textures, details)
- PRESERVE product proportions and scale accurately relative to model's body
- Product must be THE HERO of the shot - clearly visible, well-lit, in focus
- Product should look naturally integrated with the model's pose
- Maintain all product branding, text, and distinctive features exactly as shown in IMAGE 1

=== MODEL DIRECTION ===
- Use the MODEL's appearance from IMAGE 2 (facial features, styling, body proportions)
- Model should wear/hold product naturally and elegantly
- Pose should showcase the product effectively
- Expression: Confident, natural, professional
- Model is supporting cast - product is the star

=== TECHNICAL QUALITY ===
- High resolution, magazine-quality finish
- Professional color grading with accurate product colors
- Sharp focus on product details (stitching, logos, textures)
- Natural skin tones and professional retouching
- Polished, commercial-ready aesthetic
"""

    if brand_rules:
        prompt += "\nIMPORTANT CONSTRAINTS:\n"
        for rule in brand_rules:
            prompt += f"- {rule}\n"

    prompt += """
Technical requirements:
- High detail and sharpness
- Professional color grading
- Proper depth of field
- Polished and refined final look
"""
    return prompt


def _build_no_model_prompt(
    scene_description: str,
    brand_rules: list[str],
    style: str = "flatlay",
) -> str:
    """Build prompt for product-only shots (no model)."""
    style_descriptions = {
        "flatlay": "overhead flat-lay composition on a clean surface",
        "mannequin": "product displayed on an invisible/ghost mannequin",
        "ghost_mannequin": "product displayed on a ghost mannequin, showing shape and form",
        "product_hero": "dramatic product hero shot with the product as the sole focus",
    }
    style_desc = style_descriptions.get(style, "professional product photography")

    prompt = f"""Create a professional product photograph in {style_desc} style.

=== IMAGE INPUT ===
IMAGE 1: The PRODUCT to showcase - extract exact colors, logos, textures, proportions

=== YOUR TASK ===
Create a {style_desc} of the product from IMAGE 1.

=== SCENE & LIGHTING ===
Setting: {scene_description}
Lighting: Professional product photography lighting, even and flattering
Camera: Sharp focus on product details

=== CRITICAL REQUIREMENTS ===
- EXTRACT the exact product from the image (colors, logos, textures, details)
- PRESERVE product proportions and all branding elements
- Product must be the HERO — clearly visible, well-lit, in sharp focus
- Magazine-quality finish with professional color grading
"""

    if brand_rules:
        prompt += "\nIMPORTANT CONSTRAINTS:\n"
        for rule in brand_rules:
            prompt += f"- {rule}\n"

    return prompt


# ── Sync functions (run in executor for async compatibility) ─────────


def _sync_create_chat():
    """Create a Gemini multi-turn chat session for iterative editing.

    Uses gemini-2.5-flash-image which supports native image generation output
    via response_modalities=["TEXT", "IMAGE"].
    """
    from google.genai import types
    client = _get_gemini_client()
    chat = client.chats.create(
        model="gemini-2.5-flash-image",
        config=types.GenerateContentConfig(
            response_modalities=["TEXT", "IMAGE"],
        ),
    )
    return chat


def _build_model_description_prompt(
    scene_description: str,
    brand_rules: list[str],
    model_name: str,
    model_category: str,
) -> str:
    """Build prompt for product image + text-described model (no model image available)."""
    prompt = f"""Create a professional fashion photograph in editorial style.

=== IMAGE INPUT ===
IMAGE 1: The PRODUCT (item to showcase - extract exact colors, logos, textures, proportions from this image)

=== MODEL DESCRIPTION (no reference image — use description below) ===
Model name: {model_name}
Style: {model_category}
The model should be photorealistic, professional-looking, fitting the {model_category} aesthetic.
They should wear/hold/display the product naturally and elegantly.
Pose should showcase the product effectively.

=== YOUR TASK ===
Compose a fashion photograph showing a {model_category} model wearing/holding/displaying the PRODUCT from IMAGE 1.

=== SCENE & LIGHTING ===
Setting: {scene_description}
Lighting: Professional studio lighting with soft key light, subtle fill light, and natural shadows
Camera: Shot with 85mm portrait lens, shallow depth of field, product in sharp focus
Composition: Rule of thirds, product positioned prominently, model complements but doesn't overshadow

=== CRITICAL PRODUCT REQUIREMENTS ===
- EXTRACT the exact product from IMAGE 1 (colors, logos, textures, details)
- PRESERVE product proportions and scale accurately relative to model's body
- Product must be THE HERO of the shot — clearly visible, well-lit, in focus
- Product should look naturally integrated with the model's pose
- Maintain all product branding, text, and distinctive features exactly as shown in IMAGE 1

=== TECHNICAL QUALITY ===
- High resolution, magazine-quality finish
- Professional color grading with accurate product colors
- Sharp focus on product details (stitching, logos, textures)
- Natural skin tones and professional retouching
- Polished, commercial-ready aesthetic
"""
    if brand_rules:
        prompt += "\nIMPORTANT CONSTRAINTS:\n"
        for rule in brand_rules:
            prompt += f"- {rule}\n"
    return prompt


def _sync_generate_preview(
    product_image: bytes,
    avatar_image: Optional[bytes],
    scene_description: str,
    brand_rules: list[str],
    no_model_style: Optional[str] = None,
    chat_session=None,
    model_name: str = "",
    model_category: str = "",
) -> tuple:
    """
    Generate a preview image using Gemini.

    Supports 3 modes:
    1. Product image + avatar image → full fashion composite
    2. Product image + model name/category (no avatar image) → text-described model
    3. Product image only (no model) → product hero shot

    Returns: (image_bytes, chat_session, response_text)
    """
    from google.genai import types

    if chat_session is None:
        chat_session = _sync_create_chat()

    product_b64 = base64.b64encode(product_image).decode("utf-8")

    if avatar_image and no_model_style is None:
        # Mode 1: With model image
        prompt = _build_fashion_prompt(
            scene_description=scene_description,
            brand_rules=brand_rules,
        )
        avatar_b64 = base64.b64encode(avatar_image).decode("utf-8")
        message_parts = [
            prompt,
            "[IMAGE 1 - THE PRODUCT TO SHOWCASE]:",
            types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=product_b64)),
            "[IMAGE 2 - THE MODEL/PERSON]:",
            types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=avatar_b64)),
        ]
    elif model_name and no_model_style is None:
        # Mode 2: Product image + text-described model (collection avatar, no image)
        prompt = _build_model_description_prompt(
            scene_description=scene_description,
            brand_rules=brand_rules,
            model_name=model_name,
            model_category=model_category or "editorial",
        )
        message_parts = [
            prompt,
            "[IMAGE 1 - THE PRODUCT TO SHOWCASE]:",
            types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=product_b64)),
        ]
    else:
        # Mode 3: No model — product-only hero shot
        prompt = _build_no_model_prompt(
            scene_description=scene_description,
            brand_rules=brand_rules,
            style=no_model_style or "product_hero",
        )
        message_parts = [
            prompt,
            "[IMAGE 1 - THE PRODUCT TO SHOWCASE]:",
            types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=product_b64)),
        ]

    response = chat_session.send_message(message_parts)

    image_bytes = None
    response_text = ""

    for part in response.parts:
        if part.text is not None:
            response_text += part.text
        elif part.inline_data is not None:
            image = part.as_image()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                image.save(tmp.name)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as f:
                image_bytes = f.read()
            os.remove(tmp_path)

    return image_bytes, chat_session, response_text


def _sync_refine_image(chat_session, feedback: str) -> tuple:
    """
    Refine the image based on user feedback using multi-turn chat.

    Returns: (image_bytes, chat_session, response_text)
    """
    response = chat_session.send_message(feedback)

    image_bytes = None
    response_text = ""

    for part in response.parts:
        if part.text is not None:
            response_text += part.text
        elif part.inline_data is not None:
            image = part.as_image()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                image.save(tmp.name)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as f:
                image_bytes = f.read()
            os.remove(tmp_path)

    return image_bytes, chat_session, response_text


def _sync_generate_finals(chat_session, aspect_ratios: list[str]) -> dict:
    """
    Generate final outputs in multiple aspect ratios.

    Returns: dict of {aspect_ratio: image_bytes}
    """
    finals = {}

    for ratio in aspect_ratios:
        logger.info("Generating %s final...", ratio)
        feedback = f"Generate the same image in {ratio} aspect ratio at high resolution"
        response = chat_session.send_message(feedback)

        for part in response.parts:
            if part.inline_data is not None:
                image = part.as_image()
                buffer = BytesIO()
                image.save(buffer, "PNG")
                finals[ratio] = buffer.getvalue()
                break

    return finals


def _sync_generate_text_preview(
    prompt: str,
    scene_description: str,
    brand_rules: list[str],
) -> tuple:
    """
    Generate a preview image from text description only (no input image).
    Uses Gemini's text-to-image capability.

    Returns: (image_bytes, chat_session, response_text)
    """
    chat = _sync_create_chat()
    response = chat.send_message(prompt)

    image_bytes = None
    response_text = ""

    for part in response.parts:
        if part.text is not None:
            response_text += part.text
        elif part.inline_data is not None:
            image = part.as_image()
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                image.save(tmp.name)
                tmp_path = tmp.name
            with open(tmp_path, "rb") as f:
                image_bytes = f.read()
            os.remove(tmp_path)

    return image_bytes, chat, response_text


# ── Async public API ─────────────────────────────────────────────────


async def generate_text_preview(
    prompt: str,
    scene_description: str,
    brand_rules: list[str],
) -> tuple:
    """
    Generate a fashion photo preview from text description only.
    Returns: (image_bytes, chat_session, response_text)
    """
    return await asyncio.to_thread(
        _sync_generate_text_preview,
        prompt, scene_description, brand_rules,
    )


async def generate_preview(
    product_image: bytes,
    avatar_image: Optional[bytes],
    scene_description: str,
    brand_rules: list[str],
    no_model_style: Optional[str] = None,
    chat_session=None,
    model_name: str = "",
    model_category: str = "",
) -> tuple:
    """
    Generate a fashion photo preview. Runs Gemini SDK in a thread.

    Returns: (image_bytes, chat_session, response_text)
    """
    return await asyncio.to_thread(
        _sync_generate_preview,
        product_image, avatar_image, scene_description,
        brand_rules, no_model_style, chat_session,
        model_name, model_category,
    )


async def refine_image(chat_session, feedback: str) -> tuple:
    """
    Refine the image with user feedback. Returns (image_bytes, chat_session, response_text).
    """
    return await asyncio.to_thread(
        _sync_refine_image, chat_session, feedback,
    )


async def generate_finals(chat_session, aspect_ratios: list[str]) -> dict:
    """
    Generate final outputs in multiple aspect ratios.
    Returns: {aspect_ratio: image_bytes}
    """
    return await asyncio.to_thread(
        _sync_generate_finals, chat_session, aspect_ratios,
    )


async def create_chat_session():
    """Create a new Gemini multi-turn chat session."""
    return await asyncio.to_thread(_sync_create_chat)
