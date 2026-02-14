"""
Brandfetch API client. Direct HTTP call. One function.
"""

import logging
from typing import Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.flags import get_flags
from ..models.onboarding import BrandRecord

logger = logging.getLogger(__name__)


async def fetch_brand(
    domain: str,
    db: AsyncSession,
    tenant_id: str,
    force_refresh: bool = False,
) -> Optional[dict]:
    """
    Fetch brand data for a domain. Checks DB cache first.
    Returns dict with brand info or None if not found.
    """
    flags = get_flags()
    if not flags.use_brandfetch:
        return None

    # Clean domain
    domain = domain.strip().lower()
    if domain.startswith("http"):
        from urllib.parse import urlparse
        domain = urlparse(domain).hostname or domain
    domain = domain.replace("www.", "")

    # Check cache
    if not force_refresh:
        cached = await _get_cached(db, domain)
        if cached:
            logger.info("Brand cache hit: %s", domain)
            return cached

    # Fetch from API
    settings = get_settings()
    if not settings.brandfetch_api_key:
        logger.warning("BRANDFETCH_API_KEY not set")
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{settings.brandfetch_endpoint}{domain}",
                headers={"Authorization": f"Bearer {settings.brandfetch_api_key}"},
            )

            if resp.status_code == 404:
                logger.info("Brand not found: %s", domain)
                return None

            resp.raise_for_status()
            raw = resp.json()

        # Parse the response
        brand = _parse_brand(raw, domain)

        # Infer tone of voice via LLM (from OG codebase)
        if not brand.get("tone_of_voice"):
            brand["tone_of_voice"] = await _infer_tone_of_voice(brand)

        # Cache it
        await _cache_brand(db, domain, brand, raw, tenant_id)

        logger.info("Brand fetched: %s → %s", domain, brand.get("name"))
        return brand

    except httpx.HTTPStatusError as e:
        logger.error("Brandfetch API error (%s): %s", e.response.status_code, e)
        return None
    except Exception as e:
        logger.error("Brandfetch failed for %s: %s", domain, e)
        return None


def _parse_brand(raw: dict, domain: str) -> dict:
    """Parse Brandfetch API response into a clean dict (matches OG codebase fields)."""
    # Extract logos
    icon_url = ""
    logos = raw.get("logos", [])
    for logo in logos:
        if logo.get("type") == "icon":
            formats = logo.get("formats", [])
            if formats:
                icon_url = formats[0].get("src", "")
                break
    if not icon_url and logos:
        formats = logos[0].get("formats", [])
        if formats:
            icon_url = formats[0].get("src", "")

    # Extract social links
    social_links = {}
    for link in raw.get("links", []):
        name = link.get("name", "").lower()
        url = link.get("url", "")
        if name and url:
            social_links[name] = url

    # Extract company info (richer fields from OG codebase)
    company = raw.get("company", {})
    industries = company.get("industries") or []
    location = company.get("location") or {}

    # Contact info
    contact_email = None
    emails = company.get("emails") or raw.get("emails") or []
    if emails:
        contact_email = emails[0] if isinstance(emails[0], str) else None

    contact_phone = None
    phones = company.get("phoneNumbers") or raw.get("phoneNumbers") or []
    if phones:
        contact_phone = phones[0] if isinstance(phones[0], str) else None

    # Location / region
    address_parts = [location.get("city"), location.get("state"), location.get("country")]
    contact_address = ", ".join([p for p in address_parts if p]).strip(", ") or None
    region = location.get("region") or location.get("country") or location.get("countryCode") or None

    # Colors
    colors = []
    for color_group in raw.get("colors", []):
        for fmt in color_group.get("formats", []):
            if fmt.get("value"):
                colors.append(fmt["value"])

    # Fonts
    fonts = []
    for font in raw.get("fonts", []):
        if font.get("name"):
            fonts.append(font["name"])

    return {
        "domain": domain,
        "name": raw.get("name", ""),
        "description": raw.get("description", ""),
        "icon_url": icon_url,
        "industry": industries[0].get("name") if industries else (company.get("industry", "")),
        "social_links": social_links,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "contact_address": contact_address,
        "region": region,
        "language": company.get("language") or raw.get("language") or None,
        "colors": colors[:6],
        "fonts": fonts[:4],
        "tone_of_voice": "",  # Inferred by LLM below
    }


async def _get_cached(db: AsyncSession, domain: str) -> Optional[dict]:
    """Check DB cache for brand data."""
    result = await db.execute(
        select(BrandRecord).where(BrandRecord.domain == domain)
    )
    record = result.scalar_one_or_none()
    if not record:
        return None

    return {
        "domain": record.domain,
        "name": record.name,
        "description": record.description,
        "icon_url": record.icon_url,
        "industry": record.industry,
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


async def _infer_tone_of_voice(brand: dict) -> str:
    """Use LLM to infer brand tone of voice from metadata (3 adjectives)."""
    try:
        from .llm import chat_simple

        parts = []
        for key in ["name", "description", "industry", "language", "region"]:
            val = brand.get(key)
            if val:
                parts.append(f"{key.title()}: {val}")
        for key in ["instagram", "youtube", "facebook"]:
            val = brand.get("social_links", {}).get(key)
            if val:
                parts.append(f"{key.title()}: {val}")

        if not parts:
            return ""

        system = (
            "You are a branding assistant. Given brand metadata, infer a concise tone-of-voice "
            "description.\n\nFormat: three comma-separated adjectives.\n\n"
            "Examples:\n"
            "- Apple: Minimal, premium, and precise\n"
            "- Nike: Bold, motivational, and energetic\n"
            "- Airbnb: Warm, welcoming, and story-driven\n"
            "- Spotify: Playful, modern, and expressive\n"
            "- Tesla: Visionary, bold, and disruptive\n\n"
            "Only output the three-adjective phrase, under 12 words."
        )

        result = await chat_simple(
            prompt="\n".join(parts),
            system=system,
            temperature=0.4,
            max_tokens=50,
        )
        return (result or "").strip()[:200] or ""
    except Exception as e:
        logger.warning("Tone inference failed: %s", e)
        return ""


async def _cache_brand(
    db: AsyncSession, domain: str, brand: dict, raw: dict, tenant_id: str
) -> None:
    """Save brand data to DB cache."""
    # Check if exists
    result = await db.execute(
        select(BrandRecord).where(BrandRecord.domain == domain)
    )
    record = result.scalar_one_or_none()

    field_map = {
        "name": brand.get("name", ""),
        "description": brand.get("description", ""),
        "icon_url": brand.get("icon_url", ""),
        "industry": brand.get("industry", ""),
        "tone_of_voice": brand.get("tone_of_voice", ""),
        "contact_email": brand.get("contact_email"),
        "contact_phone": brand.get("contact_phone"),
        "contact_address": brand.get("contact_address"),
        "region": brand.get("region"),
        "language": brand.get("language"),
        "colors": brand.get("colors", []),
        "fonts": brand.get("fonts", []),
        "social_links": brand.get("social_links", {}),
        "raw_data": raw,
    }

    if record:
        for k, v in field_map.items():
            setattr(record, k, v)
    else:
        record = BrandRecord(tenant_id=tenant_id, domain=domain, **field_map)
        db.add(record)

    await db.flush()
