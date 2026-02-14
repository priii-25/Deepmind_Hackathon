"""
Photo gallery tool — lets Eve query past fashion sessions and generated photos.

Allows cross-session photo editing: user can reference photos from previous
shoots and Eve can present them for selection.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.fashion import FashionImage
from .registry import tool, ToolRisk

logger = logging.getLogger(__name__)


@tool(
    name="photo_gallery",
    description=(
        "Look up previously generated fashion photos and photoshoot sessions for this user. "
        "\n\nWhen to use: User mentions editing a previous photo, wants to see "
        "past photoshoots, references earlier generated images, or says things like "
        "'edit my photo', 'use my last photo', 'show my photos', 'change the model on that photo'. "
        "\n\nReturns: List of past sessions with image URLs and metadata (scene, model, etc.)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max number of images to return. Default 10.",
            },
        },
        "required": [],
    },
    risk=ToolRisk.READ,
    category="data",
)
async def photo_gallery(limit: int = 10, db: AsyncSession = None, tenant_id: str = "", **kwargs) -> str:
    """Query past fashion photos for the tenant."""
    if not db:
        return "Database not available."

    try:
        result = await db.execute(
            select(FashionImage)
            .where(FashionImage.tenant_id == tenant_id)
            .order_by(FashionImage.created_at.desc())
            .limit(limit)
        )
        images = list(result.scalars().all())

        if not images:
            return "No previous fashion photos found. The user hasn't done a photoshoot yet."

        # Group by session
        sessions = {}
        for img in images:
            sid = img.session_id
            if sid not in sessions:
                sessions[sid] = []
            sessions[sid].append(img)

        lines = [f"Found {len(images)} photo(s) across {len(sessions)} session(s):\n"]
        for i, (sid, imgs) in enumerate(sessions.items()):
            lines.append(f"Session {i + 1} ({len(imgs)} images):")
            for img in imgs:
                meta = img.image_metadata or {}
                avatar = meta.get("avatar_name", meta.get("avatar_choice", ""))
                lines.append(
                    f"  - [{img.angle}] {img.s3_url}"
                    f" | scene: {img.scene_description or 'N/A'}"
                    f" | model: {avatar or 'N/A'}"
                )

        return "\n".join(lines)

    except Exception as e:
        logger.error("Photo gallery query failed: %s", e)
        return f"Could not retrieve photos: {e}"
