"""
Web search tool — searches the internet via Tavily API.
Direct HTTP call. No MCP. No subprocess.
"""

import httpx

from ..core.config import get_settings
from .registry import tool, ToolRisk


@tool(
    name="web_search",
    description=(
        "Search the web for current, real-time information. "
        "Returns: A summary answer plus up to 5 source results with titles and snippets. "
        "\n\nWhen to use — USE THIS LIBERALLY: "
        "- ANY question that is NOT about Teems, the user's workspace, brand, or agents "
        "- User asks about news, people, companies, facts, how-to, trends, prices, science, sports, weather "
        "- User asks about recent events or current data "
        "- You need up-to-date facts you're unsure about "
        "- User asks 'what is...', 'who is...', 'how to...', 'what happened...', 'tell me about...' "
        "- User asks about competitors, market data, or industry trends "
        "- Basically: if the answer is NOT in the conversation or brand context, SEARCH "
        "\n\nSearch tips: "
        "- Start with SHORT, BROAD queries (2-4 words) for better coverage "
        "- Then narrow down with specific terms if needed "
        "- Don't append the year unless specifically searching for a date range "
        "- Use natural language, not keyword stuffing "
        "\n\nReturns: Summary answer + source results. Always cite sources when using results."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query. Keep it concise (2-6 words for best results). "
                    "Example: 'Nike revenue 2025' not 'What is Nike's total revenue for fiscal year 2025'"
                ),
            },
        },
        "required": ["query"],
    },
    risk=ToolRisk.EXTERNAL,
    category="data",
)
async def web_search(query: str, **kwargs) -> str:
    """Search the web via Tavily API."""
    settings = get_settings()

    if not settings.tavily_api_key:
        return (
            "Web search is not configured. Set TAVILY_API_KEY in your environment. "
            "For now, I'll answer based on my training knowledge."
        )

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": settings.tavily_api_key,
                    "query": query,
                    "max_results": 5,
                    "include_answer": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return "Web search timed out. Try a simpler or shorter query."
    except httpx.HTTPStatusError as e:
        return f"Web search failed (HTTP {e.response.status_code}). Try again or rephrase the query."
    except Exception as e:
        return f"Web search error: {e}. Try again or rephrase the query."

    # Format results (Anthropic: optimize for token efficiency)
    parts = []
    if data.get("answer"):
        parts.append(f"Summary: {data['answer']}")

    sources = data.get("results", [])[:5]
    if sources:
        parts.append("\nSources:")
        for r in sources:
            title = r.get("title", "Untitled")
            content = r.get("content", "")[:200]
            url = r.get("url", "")
            parts.append(f"- [{title}]({url}): {content}")

    return "\n".join(parts) if parts else "No search results found. Try different keywords."
