"""
Brand lookup tool — fetches brand info via Brandfetch API,
with web search fallback when Brandfetch is unavailable.

Always saves a BrandRecord so brand context is available for all agents.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.flags import get_flags
from ..models.onboarding import BrandRecord
from ..services.brandfetch import fetch_brand
from .registry import tool, ToolRisk

logger = logging.getLogger(__name__)


@tool(
    name="brand_lookup",
    description=(
        "Look up comprehensive brand information for a company using its website domain. "
        "Returns: company name, description, industry, logo URL, brand colors, and social links. "
        "\n\nWhen to use: "
        "- User provides their website during onboarding (THIS IS THE #1 USE CASE) "
        "- User mentions a company or brand by name "
        "- During onboarding when setting up a new workspace "
        "- When creating content that needs brand assets "
        "\n\nInput format: A clean domain like 'nike.com' or 'apple.com' (not full URLs). "
        "If the user gives a company name without a domain, try '<company>.com' first. "
        "\n\nReturns: Formatted brand info. This also SAVES the brand to the database "
        "so all agents have access to the brand context going forward."
    ),
    parameters={
        "type": "object",
        "properties": {
            "domain": {
                "type": "string",
                "description": "Company website domain (e.g. 'nike.com', 'stripe.com'). Not a full URL.",
            },
        },
        "required": ["domain"],
    },
    risk=ToolRisk.EXTERNAL,
    category="data",
)
async def lookup_brand(domain: str, db=None, tenant_id: str = "", **kwargs) -> str:
    """Look up brand info via Brandfetch, with web search fallback."""
    if not db:
        return "Error: Brand lookup requires a database session."

    # Clean input
    domain = domain.strip().lower().removeprefix("http://").removeprefix("https://").removeprefix("www.").split("/")[0]

    # Try Brandfetch first (if enabled and API key set)
    result = await fetch_brand(domain, db, tenant_id)

    if result:
        return _format_brand(result, domain)

    # ── Fallback: web search + save basic record ──────────────────
    logger.info("Brandfetch unavailable for %s, trying web search fallback", domain)
    result = await _web_search_fallback(domain, db, tenant_id)

    if result:
        return _format_brand(result, domain)

    # ── Last resort: save minimal record so brand context exists ──
    minimal = await _save_minimal_brand(domain, db, tenant_id)
    return _format_brand(minimal, domain)


async def _web_search_fallback(domain: str, db: AsyncSession, tenant_id: str) -> dict | None:
    """Use web search to gather basic brand info and save it."""
    flags = get_flags()
    if not flags.use_web_search:
        return None

    try:
        settings = get_settings()
        if not settings.tavily_api_key:
            return None

        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings.tavily_api_key,
                    "query": f"{domain} company about description industry",
                    "max_results": 3,
                    "include_answer": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        answer = data.get("answer", "")
        # Extract what we can
        name = domain.split(".")[0].capitalize()
        description = answer[:500] if answer else ""

        # Try to guess industry from answer text
        industry = ""
        industry_keywords = {
            "technology": ["tech", "software", "saas", "platform", "digital", "cloud", "ai"],
            "fashion": ["fashion", "clothing", "apparel", "wear", "style"],
            "food & beverage": ["food", "restaurant", "beverage", "coffee", "drink"],
            "finance": ["finance", "bank", "fintech", "payment", "insurance"],
            "healthcare": ["health", "medical", "pharma", "wellness"],
            "retail": ["retail", "store", "shop", "ecommerce", "e-commerce"],
            "media": ["media", "news", "entertainment", "streaming"],
            "education": ["education", "learning", "school", "university"],
        }
        answer_lower = answer.lower()
        for ind, keywords in industry_keywords.items():
            if any(kw in answer_lower for kw in keywords):
                industry = ind
                break

        brand = {
            "domain": domain,
            "name": name,
            "description": description,
            "icon_url": f"https://logo.clearbit.com/{domain}",
            "industry": industry,
            "tone_of_voice": "",
            "social_links": {},
            "colors": [],
            "fonts": [],
            "contact_email": None,
            "contact_phone": None,
            "contact_address": None,
            "region": None,
            "language": None,
        }

        # Try LLM tone inference
        try:
            from ..services.brandfetch import _infer_tone_of_voice
            brand["tone_of_voice"] = await _infer_tone_of_voice(brand)
        except Exception:
            pass

        # Save to DB
        await _save_brand_record(brand, db, tenant_id)

        logger.info("Brand saved via web search fallback: %s → %s", domain, name)
        return brand

    except Exception as e:
        logger.warning("Web search fallback failed for %s: %s", domain, e)
        return None


async def _save_minimal_brand(domain: str, db: AsyncSession, tenant_id: str) -> dict:
    """Save a minimal brand record so brand context exists."""
    name = domain.split(".")[0].capitalize()
    brand = {
        "domain": domain,
        "name": name,
        "description": "",
        "icon_url": f"https://logo.clearbit.com/{domain}",
        "industry": "",
        "tone_of_voice": "",
        "social_links": {},
        "colors": [],
        "fonts": [],
        "contact_email": None,
        "contact_phone": None,
        "contact_address": None,
        "region": None,
        "language": None,
    }
    await _save_brand_record(brand, db, tenant_id)
    logger.info("Minimal brand record saved: %s", domain)
    return brand


async def _save_brand_record(brand: dict, db: AsyncSession, tenant_id: str) -> None:
    """Upsert a BrandRecord."""
    result = await db.execute(
        select(BrandRecord).where(BrandRecord.domain == brand["domain"])
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
        "raw_data": {},
    }

    if record:
        for k, v in field_map.items():
            setattr(record, k, v)
    else:
        record = BrandRecord(tenant_id=tenant_id, domain=brand["domain"], **field_map)
        db.add(record)

    await db.flush()


def _format_brand(result: dict, domain: str) -> str:
    """Format brand data into a concise response for the LLM."""
    parts = [f"Brand: {result.get('name', domain)}"]
    parts.append(f"Domain: {domain}")

    if result.get("description"):
        desc = result["description"]
        if len(desc) > 300:
            desc = desc[:297] + "..."
        parts.append(f"About: {desc}")
    if result.get("industry"):
        parts.append(f"Industry: {result['industry']}")
    if result.get("tone_of_voice"):
        parts.append(f"Tone of voice: {result['tone_of_voice']}")
    if result.get("region"):
        parts.append(f"Region: {result['region']}")
    if result.get("icon_url"):
        parts.append(f"Logo: {result['icon_url']}")
    if result.get("colors"):
        parts.append(f"Brand colors: {', '.join(result['colors'][:5])}")
    if result.get("fonts"):
        parts.append(f"Fonts: {', '.join(result['fonts'][:3])}")
    if result.get("social_links"):
        links = ", ".join(f"{k}: {v}" for k, v in list(result["social_links"].items())[:5])
        parts.append(f"Social: {links}")
    if result.get("contact_email"):
        parts.append(f"Email: {result['contact_email']}")
    if result.get("contact_address"):
        parts.append(f"Location: {result['contact_address']}")

    parts.append("\n[Brand saved to database. Brand context is now available for all agents.]")
    return "\n".join(parts)
