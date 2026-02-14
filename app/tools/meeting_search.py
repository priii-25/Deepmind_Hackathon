"""
Meeting search tool — full-text search over call transcripts and summaries.
Replaces the Meeting RAG MCP server with direct SQL.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .registry import tool, ToolRisk


@tool(
    name="meeting_search",
    description=(
        "Search through meeting recordings, transcripts, and summaries. "
        "Finds relevant meetings based on what was discussed, action items, or topics covered. "
        "Returns: Up to 5 matching meetings with title, date, platform, and relevant snippet. "
        "\n\nWhen to use: "
        "- User asks 'what was discussed in the meeting about...' "
        "- User asks about action items, decisions, or meeting outcomes "
        "- User references a past meeting or call "
        "- User asks 'did we talk about...' or 'who said...' "
        "\n\nSearch tips: "
        "- Use key topic words, not full questions "
        "- For recent meetings, try broad terms first "
        "- Meeting titles, transcript text, and summaries are all searched "
        "\n\nReturns: Matching meetings with title, date, platform, and content snippet. "
        "Returns 'No matching meetings found' if nothing matches."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search keywords for meeting content. Use 2-5 key terms. "
                    "Example: 'Q4 budget review' not 'What did we decide about the Q4 budget in last week's review meeting?'"
                ),
            },
        },
        "required": ["query"],
    },
    risk=ToolRisk.READ,
    category="data",
)
async def search_meetings(query: str, db: AsyncSession = None, tenant_id: str = "", **kwargs) -> str:
    """Search meeting transcripts using full-text search."""

    if not db:
        return "Error: No database session available. This is an internal issue — please try again."

    if not tenant_id:
        return "Error: Could not identify your workspace. Please ensure you're logged in."

    try:
        result = await db.execute(
            text("""
                SELECT
                    title,
                    start_time,
                    platform,
                    CASE
                        WHEN summary IS NOT NULL THEN substring(summary from 1 for 300)
                        ELSE substring(transcript from 1 for 300)
                    END as snippet
                FROM calls
                WHERE tenant_id = :tenant_id
                  AND (
                    transcript ILIKE '%' || :query || '%'
                    OR summary ILIKE '%' || :query || '%'
                    OR title ILIKE '%' || :query || '%'
                  )
                ORDER BY start_time DESC NULLS LAST
                LIMIT 5
            """),
            {"query": query, "tenant_id": tenant_id},
        )

        rows = result.fetchall()

        if not rows:
            return (
                f"No meetings matching '{query}' found. "
                "Try different keywords, or use broader search terms. "
                "Note: only meetings with transcripts or summaries can be searched."
            )

        parts = []
        for row in rows:
            title = row[0] or "Untitled Meeting"
            start = str(row[1]) if row[1] else "Unknown date"
            platform = row[2] or "Unknown platform"
            snippet = row[3] or ""
            if len(snippet) > 300:
                snippet = snippet[:297] + "..."
            parts.append(f"**{title}** ({platform}, {start})\n{snippet}")

        return "\n\n---\n\n".join(parts)

    except Exception as e:
        return (
            f"Meeting search failed: {e}. "
            "This could be a database issue. Try again, or try simpler search terms."
        )
