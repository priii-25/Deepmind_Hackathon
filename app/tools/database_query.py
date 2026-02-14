"""
Database query tool — lets Eve run read-only SQL with tenant isolation.
Direct async query. No MCP. No subprocess.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .registry import tool, ToolRisk


@tool(
    name="db_query",
    description=(
        "Execute a read-only SQL query against the workspace database. "
        "Only SELECT statements are allowed — no writes, updates, or deletes. "
        "Returns: Query results as a formatted table (columns + rows), capped at 50 rows. "
        "\n\nWhen to use: "
        "- User asks for specific data counts, aggregations, or analytics "
        "- User wants to see their conversations, documents, or agent assignments "
        "- User asks 'what did we talk about', 'show me history', 'what happened' "
        "- User asks 'how many...', 'show me all...', 'list the...' "
        "\n\nAvailable tables and key columns: "
        "- conversations (id, session_id, user_id, title, tenant_id, created_at) "
        "- messages (id, conversation_id, role, content, sequence_number, tenant_id, created_at) "
        "  role = 'user' | 'assistant' | 'system' | 'tool' "
        "- documents (id, title, tenant_id, created_at) "
        "- onboarding_states (id, tenant_id, stage, created_at) "
        "- agent_sessions (id, user_id, agent_name, current_step, is_active, tenant_id, created_at) "
        "\n\nRules: "
        "- ONLY SELECT queries allowed "
        "- Use {TENANT_ID} as a placeholder — it will be auto-replaced with the actual tenant ID "
        "- Keep queries simple and efficient, use LIMIT "
        "\n\nExamples: "
        "- Chat history: SELECT m.role, m.content, m.created_at FROM messages m JOIN conversations c ON m.conversation_id = c.id WHERE c.tenant_id = '{TENANT_ID}' ORDER BY m.created_at DESC LIMIT 20 "
        "- Conversations: SELECT id, title, created_at FROM conversations WHERE tenant_id = '{TENANT_ID}' ORDER BY created_at DESC LIMIT 10"
    ),
    parameters={
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": (
                    "A SELECT SQL query. Use {TENANT_ID} as placeholder for tenant isolation — it's auto-replaced. "
                    "Example: SELECT m.role, m.content FROM messages m JOIN conversations c ON m.conversation_id = c.id "
                    "WHERE c.tenant_id = '{TENANT_ID}' ORDER BY m.created_at DESC LIMIT 20"
                ),
            },
        },
        "required": ["sql"],
    },
    risk=ToolRisk.READ,
    category="data",
)
async def query_database(sql: str, db: AsyncSession = None, tenant_id: str = "", **kwargs) -> str:
    """Run a read-only SQL query with tenant isolation."""

    if not db:
        return "Error: No database session available. This is an internal issue — please try again."

    # ── Auto-inject tenant_id ────────────────────────────────────
    # Replace common placeholders the LLM might use
    if tenant_id:
        sql = sql.replace("{TENANT_ID}", tenant_id)
        sql = sql.replace("<tenant_id>", tenant_id)
        sql = sql.replace("'<tenant_id>'", f"'{tenant_id}'")
        sql = sql.replace("{tenant_id}", tenant_id)

    # ── Guardrail: Only allow SELECT ─────────────────────────────
    sql_stripped = sql.strip().upper()
    if not sql_stripped.startswith("SELECT"):
        return (
            "Error: Only SELECT queries are allowed. "
            "Rewrite your query as a SELECT statement. "
            "Example: SELECT count(*) FROM documents WHERE tenant_id = '...'"
        )

    # ── Guardrail: Block dangerous keywords ──────────────────────
    dangerous = ["DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "CREATE", "TRUNCATE", "GRANT", "REVOKE"]
    for kw in dangerous:
        # Check for keyword as a separate word (not inside column names)
        if f" {kw} " in f" {sql_stripped} " or sql_stripped.startswith(kw):
            return (
                f"Error: {kw} operations are not allowed. "
                "This tool only supports read-only SELECT queries."
            )

    try:
        # Set tenant context for RLS
        if tenant_id:
            await db.execute(text("SET LOCAL app.current_tenant = :tid"), {"tid": tenant_id})

        result = await db.execute(text(sql))
        rows = result.fetchall()

        if not rows:
            return "No rows returned. The query executed successfully but matched no data."

        # Format as readable table
        columns = list(result.keys())
        lines = [" | ".join(str(c) for c in columns)]
        lines.append("-" * len(lines[0]))

        row_limit = 50
        for row in rows[:row_limit]:
            lines.append(" | ".join(str(v) if v is not None else "NULL" for v in row))

        if len(rows) > row_limit:
            lines.append(f"\n... showing {row_limit} of {len(rows)} total rows. Add LIMIT to your query for fewer results.")

        return "\n".join(lines)

    except Exception as e:
        return (
            f"Query error: {e}. "
            "Check your SQL syntax. Common issues: wrong table name, missing quotes around strings, "
            "or column doesn't exist. Available tables: conversations, messages, documents, calls."
        )
