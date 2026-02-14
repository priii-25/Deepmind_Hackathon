"""
Veo 3.1 video generation service — powered by Google Gemini.

Generates 8-second videos with native audio from text prompts.
Async wrapper around the sync google-genai SDK (same pattern as fashion_photo.py).
"""

import asyncio
import logging
import os
import tempfile
import time

from ..core.config import get_settings

logger = logging.getLogger(__name__)

_gemini_client = None


def _get_gemini_client():
    """Lazy-load and cache the google-genai client as a singleton."""
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client
    try:
        from google import genai
        settings = get_settings()
        api_key = settings.gemini_api_key
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for Veo video generation")
        _gemini_client = genai.Client(api_key=api_key)
        return _gemini_client
    except ImportError:
        raise ImportError(
            "google-genai package is required for Veo video generation. "
            "Install it with: pip install google-genai"
        )


# ── Sync function (run in executor for async compatibility) ──────────


def _sync_generate_video(
    prompt: str,
    aspect_ratio: str = "9:16",
    negative_prompt: str = "",
) -> bytes:
    """
    Generate a video using Veo 3.1. Blocking — polls until complete.

    Returns: video file bytes (MP4)
    """
    from google.genai import types

    client = _get_gemini_client()

    logger.info("Veo generation started: prompt=%s..., aspect=%s",
                prompt[:80], aspect_ratio)

    config = types.GenerateVideosConfig(
        aspect_ratio=aspect_ratio,
        number_of_videos=1,
    )
    if negative_prompt:
        config.negative_prompt = negative_prompt

    operation = client.models.generate_videos(
        model="veo-3.1-generate-preview",
        prompt=prompt,
        config=config,
    )

    # Poll until complete (timeout: 7 minutes)
    start = time.monotonic()
    timeout = 420  # 7 minutes
    poll_interval = 10

    while not operation.done:
        elapsed = time.monotonic() - start
        if elapsed > timeout:
            raise TimeoutError(
                f"Veo generation timed out after {int(elapsed)}s. "
                "The video may still be processing — try again later."
            )
        logger.info("Veo polling... elapsed=%.0fs", elapsed)
        time.sleep(poll_interval)
        operation = client.operations.get(operation)

    elapsed = time.monotonic() - start
    logger.info("Veo generation completed in %.0fs", elapsed)

    # Extract video bytes
    if not operation.response or not operation.response.generated_videos:
        raise RuntimeError("Veo returned no video. The prompt may have been filtered.")

    video = operation.response.generated_videos[0]

    # Download the video file using the SDK (handles auth automatically),
    # then save to a temp file and read the bytes.
    client.files.download(file=video.video)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        video.video.save(tmp.name)
        tmp_path = tmp.name

    with open(tmp_path, "rb") as f:
        video_bytes = f.read()
    os.remove(tmp_path)

    logger.info("Veo video downloaded: %d bytes", len(video_bytes))
    return video_bytes


# ── Async public API ─────────────────────────────────────────────────


async def generate_video(
    prompt: str,
    aspect_ratio: str = "9:16",
    negative_prompt: str = "",
) -> bytes:
    """
    Generate a video using Veo 3.1. Async wrapper.

    Returns: video file bytes (MP4)
    """
    return await asyncio.to_thread(
        _sync_generate_video,
        prompt, aspect_ratio, negative_prompt,
    )
