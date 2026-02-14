"""
Document search tool — full-text search using PostgreSQL tsvector + pg_trgm.
Replaces the entire RAG pipeline with native SQL search.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.flags import get_flags
from .registry import tool, ToolRisk


@tool(
    name="doc_search",
    description=(
        "Search through the user's uploaded documents and knowledge base. "
        "Uses full-text search to find relevant content across all documents. "
        "Returns: Up to 5 matching documents with title, filename, and relevant snippets. "
        "\n\nWhen to use: "
        "- User asks about content in their documents, files, or knowledge base "
        "- User says 'in my documents...', 'according to our files...', 'find in docs...' "
        "- User asks about internal company information that would be in uploaded files "
        "- User references specific reports, guides, or uploaded materials "
        "\n\nSearch tips: "
        "- Use key terms from the user's question, not the full question "
        "- For broader results, use fewer search terms "
        "- If no results found, try synonyms or related terms "
        "\n\nReturns: Matching documents with titles and highlighted snippets. "
        "Returns 'No matching documents found' if nothing matches."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search keywords to find in documents. Use 2-5 key terms. "
                    "Example: 'quarterly revenue report' not 'Can you find the quarterly revenue report from last month?'"
                ),
            },
        },
        "required": ["query"],
    },
    risk=ToolRisk.READ,
    category="data",
)
async def search_documents(query: str, db: AsyncSession = None, tenant_id: str = "", **kwargs) -> str:
    """Search documents using full-text search or ILIKE fallback."""

    if not db:
        return "Error: No database session available. This is an internal issue — please try again."

    if not tenant_id:
        return "Error: Could not identify your workspace. Please ensure you're logged in."

    flags = get_flags()

    try:
        if flags.use_full_text_search:
            result = await db.execute(
                text("""
                    SELECT
                        title,
                        filename,
                        ts_headline('english', full_text, plainto_tsquery('english', :query),
                            'MaxWords=60, MinWords=20, StartSel=**, StopSel=**') as snippet,
                        ts_rank(
                            to_tsvector('english', coalesce(title, '') || ' ' || coalesce(full_text, '')),
                            plainto_tsquery('english', :query)
                        ) as rank
                    FROM documents
                    WHERE tenant_id = :tenant_id
                      AND to_tsvector('english', coalesce(title, '') || ' ' || coalesce(full_text, ''))
                          @@ plainto_tsquery('english', :query)
                    ORDER BY rank DESC
                    LIMIT 5
                """),
                {"query": query, "tenant_id": tenant_id},
            )
        else:
            result = await db.execute(
                text("""
                    SELECT title, filename,
                           substring(full_text from 1 for 300) as snippet,
                           1 as rank
                    FROM documents
                    WHERE tenant_id = :tenant_id
                      AND (full_text ILIKE '%' || :query || '%'
                           OR title ILIKE '%' || :query || '%')
                    LIMIT 5
                """),
                {"query": query, "tenant_id": tenant_id},
            )

        rows = result.fetchall()

        if not rows:
            return (
                f"No documents matching '{query}' found in your workspace. "
                "Try different keywords, use fewer search terms, or check if the document has been uploaded."
            )

        parts = []
        for row in rows:
            title = row[0] or "Untitled"
            filename = row[1] or ""
            snippet = row[2] or ""
            # Cap snippet to save tokens
            if len(snippet) > 400:
                snippet = snippet[:397] + "..."
            parts.append(f"**{title}** ({filename})\n{snippet}")

        return "\n\n---\n\n".join(parts)

    except Exception as e:
        return (
            f"Document search failed: {e}. "
            "This could be a database issue. Try again, or try simpler search terms."
        )
