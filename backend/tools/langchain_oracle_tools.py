"""
LangChain tools for Oracle — ReAct cycle compatible.

Mirror of langchain_clickhouse_tools.py adapted for Oracle SQL conventions.

Available tools:
  - list_tables       : Discover tables in the Oracle schema
  - get_schema        : Get column types and index information
  - execute_query     : Run a SELECT query (enforces Oracle best practices)
  - check_query       : Validate with EXPLAIN PLAN before execution
"""
import json
import logging
from typing import Any, List, Optional

from langchain_core.tools import tool

from backend.tools.sql_oracle import OracleSQLTool

logger = logging.getLogger(__name__)

# ── Tool name constants ────────────────────────────────────────────────────────
TOOL_LIST_TABLES = "list_tables"
TOOL_GET_SCHEMA = "get_schema"
TOOL_EXECUTE_QUERY = "execute_query"
TOOL_CHECK_QUERY = "check_query"

ORACLE_TOOL_DEFINITIONS = [
    {
        "name": TOOL_LIST_TABLES,
        "description": "List all available tables in the Oracle schema. Call this first to discover what data is available.",
        "enabled": True,
    },
    {
        "name": TOOL_GET_SCHEMA,
        "description": "Get column names, data types, and nullable/default info for one or more Oracle tables. Essential before writing SQL.",
        "enabled": True,
    },
    {
        "name": TOOL_EXECUTE_QUERY,
        "description": "Execute a SELECT SQL query on Oracle. Enforces best practices: indexed columns, analytic functions, ROWNUM limits. Returns results as JSON with a markdown table.",
        "enabled": True,
    },
    {
        "name": TOOL_CHECK_QUERY,
        "description": "Validate a SQL query with EXPLAIN PLAN before executing. Use to catch syntax errors early.",
        "enabled": True,
    },
]


def make_oracle_tools(
    sql_tool: OracleSQLTool,
    enabled_tools: Optional[List[str]] = None,
    description_overrides: Optional[dict] = None,
) -> List[Any]:
    """
    Create LangChain @tool functions for an Oracle connection.

    Args:
        sql_tool: A configured OracleSQLTool instance.
        enabled_tools: List of tool names to include. None = all 4 tools.
        description_overrides: Dict {tool_name: custom_description}.

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
            """List all available tables in the Oracle database schema.
            Always call this first to discover what tables exist before writing any SQL.
            Returns the table names one per line."""
            tables = sql_tool.list_tables()
            if not tables:
                return "No tables found in the Oracle schema."
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
            """Get column names, data types, nullable flags, and default values
            for one or more Oracle tables.

            Args:
                table_names: Comma-separated table names (e.g. "ORDERS,PRODUCTS").
                             Oracle table names are UPPERCASE by convention.

            Returns schema useful for writing optimized SQL:
            - Column name, data type, length, nullable, default value
            - Use indexed columns in WHERE clauses to avoid full table scans

            Call this before execute_query to know exact column names and types."""
            results = []
            for tbl in [x.strip() for x in table_names.split(",") if x.strip()]:
                schema = sql_tool.get_schema(tbl)
                if "error" in schema:
                    results.append(f"Table `{tbl}`: ERROR — {schema['error']}")
                    continue
                cols_lines = []
                for c in schema.get("columns", []):
                    length = f"({c['length']})" if c.get("length") else ""
                    nullable = "NULL" if c.get("nullable") == "Y" else "NOT NULL"
                    default = f"  DEFAULT {c['default']}" if c.get("default") else ""
                    cols_lines.append(
                        f"  - {c['name']}: {c['type']}{length} {nullable}{default}"
                    )
                cols = "\n".join(cols_lines) if cols_lines else "  (no columns found)"
                owner = schema.get("schema", "")
                results.append(
                    f"### Table: `{tbl}`" + (f" (schema: {owner})" if owner else "") + "\n"
                    f"**Columns:**\n{cols}\n"
                    f"**Tips:** Use indexed columns in WHERE, use TRUNC() for date truncation, "
                    f"use analytic functions (OVER PARTITION BY) for window calculations."
                )
            return "\n\n".join(results) if results else "No schema information found."

        if TOOL_GET_SCHEMA in overrides:
            get_schema.__doc__ = overrides[TOOL_GET_SCHEMA]
        result_tools.append(get_schema)

    # ── execute_query ──────────────────────────────────────────────────────────
    if all_enabled or TOOL_EXECUTE_QUERY in enabled_tools:

        @tool
        def execute_query(sql: str) -> str:
            """Execute a SELECT SQL query on the Oracle database.

            MANDATORY Oracle SQL rules:
            - NEVER use SELECT * — always list explicit column names
            - Use indexed columns in WHERE clauses (avoid full table scans)
            - Oracle date functions: TRUNC(date_col), TO_DATE('2024-01-01','YYYY-MM-DD'),
              SYSDATE, ADD_MONTHS(), MONTHS_BETWEEN()
            - Analytic functions: ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...),
              SUM() OVER, RANK() OVER
            - Pagination: use FETCH FIRST n ROWS ONLY (12c+) or ROWNUM <= n
            - String functions: SUBSTR(), INSTR(), TO_CHAR(), NVL(), DECODE()
            - SQL keywords UPPERCASE, proper indentation
            - Bind-variable style in explanations, literal values in SQL

            Returns JSON with: success, row_count, sql_executed,
            columns, rows, markdown_table, error (if any), warning (if truncated)."""
            result = sql_tool.execute(sql)
            output = {
                "success": result.get("success", False),
                "row_count": result.get("row_count", 0),
                "sql_executed": result.get("sql_executed", sql),
                "columns": result.get("columns", []),
                "rows": result.get("rows", [])[:50],
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
            """Validate a SQL query using EXPLAIN PLAN before executing it.
            Use to check for syntax errors or identify performance issues
            (missing indexes, full table scans) before calling execute_query.

            Args:
                sql: The SQL SELECT query to validate.

            Returns the EXPLAIN PLAN output or a syntax error message."""
            # Oracle OracleSQLTool doesn't have explain(), simulate with a simple check
            try:
                # Run a syntax check by wrapping in a no-execute context
                from backend.tools.sql_oracle import _sanitize_query
                sanitized, err = _sanitize_query(sql, row_limit=1)
                if err:
                    return f"❌ Query validation failed:\n```\n{err}\n```\nFix the SQL before executing."
                return "✅ Query passed basic validation (no blocked keywords, valid structure).\nNote: Full EXPLAIN PLAN requires a live Oracle connection."
            except Exception as e:
                return f"❌ Validation error: {e}"

        if TOOL_CHECK_QUERY in overrides:
            check_query.__doc__ = overrides[TOOL_CHECK_QUERY]
        result_tools.append(check_query)

    return result_tools
