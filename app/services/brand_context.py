"""
Brand context — loads brand data for a tenant and makes it
available to Eve and every agent.

Usage:
    brand = await get_brand_context(db, tenant_id)
    # Returns dict with brand info or None
"""

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.onboarding import BrandRecord

logger = logging.getLogger(__name__)


async def get_brand_context(db: AsyncSession, tenant_id: str) -> Optional[dict]:
    """
    Load the most recent brand record for a tenant.
    Returns a clean dict that any agent can inject into its prompt.
    """
    try:
        result = await db.execute(
            select(BrandRecord)
            .where(BrandRecord.tenant_id == tenant_id)
            .order_by(BrandRecord.updated_at.desc())
            .limit(1)
        )
        record = result.scalar_one_or_none()
        if not record:
            return None

        return {
            "domain": record.domain,
            "name": record.name or "",
            "description": record.description or "",
            "icon_url": record.icon_url or "",
            "industry": record.industry or "",
            "tone_of_voice": record.tone_of_voice or "",
            "contact_email": record.contact_email,
            "contact_phone": record.contact_phone,
            "contact_address": record.contact_address,
            "region": record.region,
            "language": record.language,
            "colors": record.colors or [],
            "fonts": record.fonts or [],
            "social_links": record.social_links or {},
        }
    except Exception as e:
        logger.warning("Failed to load brand context: %s", e)
        return None


def format_brand_for_prompt(brand: dict) -> str:
    """
    Format brand context into a string block that can be injected
    into any agent's system prompt.
    """
    if not brand:
        return ""

    parts = ["=== BRAND CONTEXT ==="]

    if brand.get("name"):
        parts.append(f"Company: {brand['name']}")
    if brand.get("domain"):
        parts.append(f"Website: {brand['domain']}")
    if brand.get("description"):
        desc = brand["description"]
        if len(desc) > 300:
            desc = desc[:297] + "..."
        parts.append(f"About: {desc}")
    if brand.get("industry"):
        parts.append(f"Industry: {brand['industry']}")
    if brand.get("tone_of_voice"):
        parts.append(f"Tone of voice: {brand['tone_of_voice']}")
    if brand.get("region"):
        parts.append(f"Region: {brand['region']}")
    if brand.get("colors"):
        parts.append(f"Brand colors: {', '.join(brand['colors'][:6])}")
    if brand.get("fonts"):
        parts.append(f"Brand fonts: {', '.join(brand['fonts'][:4])}")
    if brand.get("icon_url"):
        parts.append(f"Logo URL: {brand['icon_url']}")
    if brand.get("social_links"):
        links = ", ".join(f"{k}: {v}" for k, v in list(brand["social_links"].items())[:5])
        parts.append(f"Social: {links}")
    if brand.get("contact_email"):
        parts.append(f"Email: {brand['contact_email']}")
    if brand.get("contact_address"):
        parts.append(f"Location: {brand['contact_address']}")

    parts.append("=== END BRAND CONTEXT ===")
    parts.append(
        "Use this brand context to personalize all responses. "
        "Reference the brand's tone, colors, and industry when relevant. "
        "When creating content, default to the brand's visual identity unless told otherwise."
    )

    return "\n".join(parts)
