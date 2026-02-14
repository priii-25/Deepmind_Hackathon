"""
Tool registry.

Collects all tool functions and formats them for OpenAI function calling.
Follows Anthropic's ACI (Agent-Computer Interface) principles:
  - Tools have clear, distinct purposes
  - Descriptions written like docs for a new hire
  - Namespaced by domain for clarity
  - Helpful error messages with actionable guidance
"""

import logging
from enum import Enum
from typing import Callable, Optional

from ..core.flags import get_flags

logger = logging.getLogger(__name__)


class ToolRisk(str, Enum):
    """Risk classification for guardrails (OpenAI best practice)."""
    READ = "read"       # Read-only, no side effects
    WRITE = "write"     # Creates/modifies data
    EXTERNAL = "external"  # Calls external APIs
    DANGEROUS = "dangerous"  # Irreversible actions


# Each tool: {name, description, parameters, handler, risk, category}
_tools: list[dict] = []


def tool(
    name: str,
    description: str,
    parameters: dict,
    risk: ToolRisk = ToolRisk.READ,
    category: str = "general",
):
    """
    Decorator to register a function as an LLM-callable tool.

    Args:
        name:        Tool name. Use namespace prefixes (brand_lookup, doc_search, etc.)
        description: Detailed description (like docs for a new hire). Include:
                     - What it does
                     - When to use it
                     - Input format expectations
                     - What it returns
        parameters:  JSON Schema for the tool's parameters
        risk:        Risk classification for guardrail decisions
        category:    Grouping category (data, action, orchestration)
    """

    def decorator(func: Callable):
        params = dict(parameters)
        if "type" not in params:
            params["type"] = "object"
        params.setdefault("additionalProperties", False)

        _tools.append({
            "name": name,
            "description": description,
            "parameters": params,
            "handler": func,
            "risk": risk.value,
            "category": category,
        })
        logger.debug("Registered tool: %s [%s/%s]", name, category, risk.value)
        return func

    return decorator


def get_tools_for_llm() -> list[dict]:
    """Get all tools formatted for OpenAI function calling."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
                "strict": False,
            },
        }
        for t in _tools
    ]


def get_tool_handler(name: str) -> Optional[Callable]:
    """Get the handler function for a tool by name."""
    for t in _tools:
        if t["name"] == name:
            return t["handler"]
    return None


def get_tool_names() -> list[str]:
    """Get names of all registered tools."""
    return [t["name"] for t in _tools]


def get_tool_risk(name: str) -> str:
    """Get risk level for a tool (used by guardrails)."""
    for t in _tools:
        if t["name"] == name:
            return t["risk"]
    return ToolRisk.READ.value


def get_tools_by_category(category: str) -> list[dict]:
    """Get tools filtered by category."""
    return [t for t in _tools if t["category"] == category]


def init_tools() -> None:
    """
    Import tool modules to trigger registration.
    Call this once on startup. Order matters: core tools first, then conditional.
    """
    flags = get_flags()

    # ── Core tools (always available) ─────────────────────────────
    from . import document_search  # noqa: F401
    from . import database_query   # noqa: F401
    from . import meeting_search   # noqa: F401

    # ── Conditional tools ─────────────────────────────────────────
    if flags.use_web_search:
        from . import web_search  # noqa: F401

    # Brand lookup is ALWAYS available — it's core to onboarding.
    # The service itself gracefully handles missing API key / flag.
    from . import brandfetch  # noqa: F401

    # ── Onboarding tools (Eve uses these to manage onboarding flow) ──
    from . import onboarding  # noqa: F401

    # ── Agent delegation tools (agents-as-tools pattern) ──────────
    from . import agent_delegation  # noqa: F401

    # ── Photo gallery tool (query past fashion sessions) ──────────
    from . import photo_gallery  # noqa: F401

    # ── Conversation history tool (cross-session memory) ─────────
    from . import conversation_history  # noqa: F401

    logger.info(
        "Tools ready: %d tools [%s]",
        len(_tools),
        ", ".join(get_tool_names()),
    )
