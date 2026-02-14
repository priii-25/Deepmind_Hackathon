"""
Memory tool — lets Eve explicitly store persistent user facts.

Example: user says "I prefer minimalist designs" → Eve calls remember(
    category="style_preference", key="preferred_style", value="minimalist designs"
)
"""

import logging

from .registry import tool, ToolRisk

logger = logging.getLogger(__name__)


@tool(
    name="remember",
    description=(
        "Store a persistent fact about the user that should be remembered across sessions. "
        "\n\nWhen to use: When the user states a preference, shares important context, "
        "or you learn something worth remembering (product type, style preference, "
        "team size, target audience, etc.). "
        "\n\nCategories: style_preference, product_info, personal_fact, project_context, agent_feedback"
        "\n\nExamples:"
        "\n- User says 'I sell skincare products' → category=product_info, key=product_type, value=skincare products"
        "\n- User says 'I prefer clean, minimal design' → category=style_preference, key=design_style, value=clean and minimal"
        "\n- User says 'my target audience is Gen Z women' → category=product_info, key=target_audience, value=Gen Z women"
        "\n\nDo NOT store: temporary requests, conversation-specific context, or things already in brand data."
    ),
    parameters={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Memory category: style_preference, product_info, personal_fact, project_context, or agent_feedback",
                "enum": ["style_preference", "product_info", "personal_fact", "project_context", "agent_feedback"],
            },
            "key": {
                "type": "string",
                "description": "Short key name (e.g. preferred_style, product_type, target_audience, team_size)",
            },
            "value": {
                "type": "string",
                "description": "The fact to remember (keep concise, 1-2 sentences max)",
            },
        },
        "required": ["category", "key", "value"],
    },
    risk=ToolRisk.WRITE,
    category="data",
)
async def remember(
    category: str, key: str, value: str,
    db=None, tenant_id: str = "", **kwargs
) -> str:
    if not db or not tenant_id:
        return "Cannot save memory — no database context."

    from ..services.memory import save_memory

    user_id = kwargs.get("user_id", "")
    await save_memory(db, tenant_id, user_id, category, key, value, source="explicit")
    return f"Got it — I'll remember that {key}: {value}"
