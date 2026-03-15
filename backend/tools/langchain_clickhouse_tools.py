"""
LangChain tools for ClickHouse — ReAct cycle compatible.

These tools are created via factory functions so each agent's connection
is resolved at call time. They are designed to be used with LLM.bind_tools()
in a LangGraph ReAct (Reasoning + Acting) loop.

Available tools:
  - list_tables       : Discover tables in the database
  - get_schema        : Get column types and ORDER BY / PARTITION keys
  - execute_query     : Run a SELECT query (enforces ClickHouse best practices)
  - check_query       : Validate syntax with EXPLAIN before execution

Tool name constants (for toolkit enable/disable system):
  TOOL_LIST_TABLES, TOOL_GET_SCHEMA, TOOL_EXECUTE_QUERY, TOOL_CHECK_QUERY
"""
import json
import logging
from typing import Any, List, Optional

from langchain_core.tools import tool

from backend.tools.sql_clickhouse import ClickHouseSQLTool

logger = logging.getLogger(__name__)

# ── Tool name constants (used by the Toolkit system) ──────────────────────────
TOOL_LIST_TABLES = "list_tables"
TOOL_GET_SCHEMA = "get_schema"
TOOL_EXECUTE_QUERY = "execute_query"
TOOL_CHECK_QUERY = "check_query"

# Default tool definitions for seeding default toolkits
CLICKHOUSE_TOOL_DEFINITIONS = [
    {
        "name": TOOL_LIST_TABLES,
        "description": "List all available tables in the ClickHouse database. Call this first to discover what data is available.",
        "enabled": True,
    },
    {
        "name": TOOL_GET_SCHEMA,
        "description": "Get column names, data types, and ORDER BY / PARTITION keys for one or more tables. Essential before writing SQL.",
        "enabled": True,
    },
    {
        "name": TOOL_EXECUTE_QUERY,
        "description": "Execute a SELECT SQL query on ClickHouse. Enforces best practices: uniqCombined(), ORDER BY filters, LIMIT. Returns results as JSON with a markdown table.",
        "enabled": True,
    },
    {
        "name": TOOL_CHECK_QUERY,
        "description": "Validate a SQL query with EXPLAIN before executing. Use to catch syntax errors early.",
        "enabled": True,
    },
]


def make_clickhouse_tools(
    sql_tool: ClickHouseSQLTool,
    enabled_tools: Optional[List[str]] = None,
    description_overrides: Optional[dict] = None,
) -> List[Any]:
    """
    Create LangChain @tool functions for a ClickHouse connection.

    Each call returns new function objects (closures over sql_tool), so
    multiple agents can have independent tool sets without conflicts.

    Args:
        sql_tool: A configured ClickHouseSQLTool instance.
        enabled_tools: List of tool names to include. None = all 4 tools.
        description_overrides: Dict {tool_name: custom_description} to
            override default tool docstrings shown to the LLM.

    Returns:
        List of LangChain tool functions ready for llm.bind_tools().
    """
    all_enabled = enabled_tools is None
    overrides = description_overrides or {}
    result_tools = []

    # ── list_tables ────────────────────────────────────────────────────────────
    if all_enabled or TOOL_LIST_TABLES in enabled_tools:

        @tool
        def list_tables() -> str:
            """List all available tables in the ClickHouse database.
            Always call this first to discover what tables exist before writing any SQL.
            Returns the table names one per line."""
            tables = sql_tool.list_tables()
            if not tables:
                return "No tables found in the database."
            valid = [t for t in tables if not t.startswith("ERROR:")]
            errors = [t for t in tables if t.startswith("ERROR:")]
            if errors and not valid:
                return f"Error listing tables: {errors[0]}"
            output = "Available tables:\n" + "\n".join(f"- {t}" for t in valid)
            if errors:
                output += f"\n\n(Some tables could not be listed: {errors[0]})"
            return output

        if TOOL_LIST_TABLES in overrides:
            list_tables.__doc__ = overrides[TOOL_LIST_TABLES]
        result_tools.append(list_tables)

    # ── get_schema ─────────────────────────────────────────────────────────────
    if all_enabled or TOOL_GET_SCHEMA in enabled_tools:

        @tool
        def get_schema(table_names: str) -> str:
            """Get column names, data types, comments, and ORDER BY / PARTITION keys
            for one or more ClickHouse tables.

            Args:
                table_names: Comma-separated table names (e.g. "orders,products").

            Returns schema including:
            - Column name and type (LowCardinality, Array, Tuple, Nullable variants)
            - ORDER BY (sorting_key): ALWAYS filter on these for best performance
            - PARTITION BY: useful for date-range queries
            - Engine type (MergeTree, ReplicatedMergeTree, etc.)

            Call this before execute_query to know exact column names and types."""
            results = []
            for tbl in [x.strip() for x in table_names.split(",") if x.strip()]:
                schema = sql_tool.get_schema(tbl)
                if "error" in schema:
                    results.append(f"Table `{tbl}`: ERROR — {schema['error']}")
                    continue
                cols_lines = []
                for c in schema.get("columns", []):
                    line = f"  - {c['name']}: {c['type']}"
                    if c.get("comment"):
                        line += f"  # {c['comment']}"
                    cols_lines.append(line)
                cols = "\n".join(cols_lines) if cols_lines else "  (no columns found)"
                meta = schema.get("metadata", {})
                order_by = meta.get("sorting_key") or "N/A"
                partition = meta.get("partition_key") or "N/A"
                primary = meta.get("primary_key") or "N/A"
                engine = meta.get("engine") or "N/A"
                results.append(
                    f"### Table: `{tbl}` ({engine})\n"
                    f"**Columns:**\n{cols}\n"
                    f"**ORDER BY (sorting_key):** `{order_by}`  ← filter on these for fast queries\n"
                    f"**PARTITION BY:** `{partition}`\n"
                    f"**PRIMARY KEY:** `{primary}`"
                )
            return "\n\n".join(results) if results else "No schema information found."

        if TOOL_GET_SCHEMA in overrides:
            get_schema.__doc__ = overrides[TOOL_GET_SCHEMA]
        result_tools.append(get_schema)

    # ── execute_query ──────────────────────────────────────────────────────────
    if all_enabled or TOOL_EXECUTE_QUERY in enabled_tools:

        @tool
        def execute_query(sql: str) -> str:
            """Execute a SELECT SQL query on the ClickHouse database.

            MANDATORY ClickHouse SQL rules:
            - NEVER use SELECT * — always list explicit column names
            - Use uniqCombined(col) instead of COUNT(DISTINCT col) — much faster
            - ALWAYS filter on ORDER BY (sorting_key) columns in WHERE clause
            - Add LIMIT (default 100 rows, max per agent config)
            - Native ClickHouse functions: toStartOfDay(), toStartOfHour(),
              toYYYYMM(), argMax(val, ts), topK(10)(col), any(col)
            - For time series: GROUP BY toStartOfDay(ts_col) AS day
            - Avoid JOINs; prefer IN (SELECT ...) subqueries or Dictionaries
            - Array columns: use arrayJoin() or arraySum(), arrayFilter()
            - SQL keywords UPPERCASE, proper indentation

            Returns JSON with: success, row_count, sql_executed,
            columns, rows, markdown_table, error (if any), warning (if truncated)."""
            result = sql_tool.execute(sql)
            # Return JSON so tools_react_node can parse structured data
            # while the LLM can still read it as text
            output = {
                "success": result.get("success", False),
                "row_count": result.get("row_count", 0),
                "sql_executed": result.get("sql_executed", sql),
                "columns": result.get("columns", []),
                "rows": result.get("rows", [])[:50],  # cap rows in tool output
                "markdown_table": result.get("markdown_table", ""),
                "error": result.get("error"),
                "warning": result.get("warning"),
            }
            return json.dumps(output, ensure_ascii=False, default=str)

        if TOOL_EXECUTE_QUERY in overrides:
            execute_query.__doc__ = overrides[TOOL_EXECUTE_QUERY]
        result_tools.append(execute_query)

    # ── check_query ────────────────────────────────────────────────────────────
    if all_enabled or TOOL_CHECK_QUERY in enabled_tools:

        @tool
        def check_query(sql: str) -> str:
            """Validate a SQL query using EXPLAIN before executing it.
            Use this to check for syntax errors or performance issues
            before calling execute_query. Does not consume data resources.

            Args:
                sql: The SQL SELECT query to validate.

            Returns the EXPLAIN output or a syntax error message."""
            result = sql_tool.explain(sql)
            if not result.get("success"):
                return f"❌ Query validation failed:\n```\n{result.get('error', 'Unknown error')}\n```\nFix the SQL before executing."
            plan = result.get("plan", "")
            return f"✅ Query is syntactically valid.\n\nExecution plan:\n```\n{plan}\n```"

        if TOOL_CHECK_QUERY in overrides:
            check_query.__doc__ = overrides[TOOL_CHECK_QUERY]
        result_tools.append(check_query)

    return result_tools
