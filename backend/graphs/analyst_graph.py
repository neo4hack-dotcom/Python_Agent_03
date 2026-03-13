"""
ClickHouse / Oracle Analyst LangGraph with retry loop:
  analyst → sql_tool → [success → synthesizer | error → analyst (max 3 retries)]
"""
import json
import logging
from typing import Any, Dict, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from .state import AnalystState
from .llm_factory import build_llm
from backend.tools.sql_clickhouse import ClickHouseSQLTool
from backend.tools.sql_oracle import OracleSQLTool
from backend.database import db, COLL_CONNECTIONS, COLL_AGENTS

logger = logging.getLogger(__name__)

# ── System Prompts ────────────────────────────────────────────────────────────

CLICKHOUSE_ANALYST_SYSTEM = """You are a senior ClickHouse data analyst. Your expertise:

## SQL Directives
- NEVER use SELECT *. Always name explicit columns.
- Apply WHERE filters on partition keys or primary keys first.
- Use ClickHouse native functions: uniq() instead of COUNT(DISTINCT), any(), argMax(), topK().
- Use toStartOfDay(), toStartOfHour(), toYYYYMM() for time aggregations.
- Avoid JOINs when possible. Use IN (SELECT ...) for subqueries or Dictionaries.
- Always format SQL: UPPERCASE keywords, proper indentation.
- Add LIMIT clause (max rows per your configuration).

## Output Format
1. Brief strategy explanation (1-2 sentences)
2. Optimized SQL block
3. Performance note (e.g., "Uses primary key — will be fast")

{schema_context}

If you receive a database error, analyze it carefully and fix the SQL.
Respond with ONLY the SQL query (no markdown fences) when asked for SQL.
"""

ORACLE_ANALYST_SYSTEM = """You are a senior Oracle database analyst. Your expertise:

## SQL Directives
- NEVER use SELECT *. Always name explicit columns.
- Use proper Oracle date functions: TRUNC(), TO_DATE(), SYSDATE.
- Use analytic functions (OVER PARTITION BY) for window calculations.
- Avoid full table scans — use indexed columns in WHERE clauses.
- Use bind variables style in explanations but write literal values in SQL.
- Format SQL: UPPERCASE keywords, proper indentation.

{schema_context}

If you receive a database error, analyze it and fix the SQL.
Respond with ONLY the SQL query (no markdown fences) when asked for SQL.
"""

SYNTHESIZER_SYSTEM = """You are a data analyst communicator.
Given a SQL query result and the original question, provide:
1. A clear narrative answer to the question
2. Key insights from the data
3. The SQL query used (in a code block)
4. Any caveats or limitations

Format your response in clean Markdown."""


# ── Helper: get tool from agent config ───────────────────────────────────────

def _get_sql_tool(agent_id: str) -> Optional[Any]:
    agent_cfg = db.get(COLL_AGENTS, agent_id)
    if not agent_cfg:
        return None

    conn_id = agent_cfg.get("connection_id")
    if not conn_id:
        return None

    conn_cfg = db.get(COLL_CONNECTIONS, conn_id)
    if not conn_cfg:
        return None

    row_limit = agent_cfg.get("row_limit", 1000)
    conn_type = conn_cfg.get("type", "clickhouse")

    if conn_type == "clickhouse":
        return ClickHouseSQLTool(conn_cfg, row_limit=row_limit)
    elif conn_type == "oracle":
        return OracleSQLTool(conn_cfg, row_limit=row_limit)
    return None


def _get_agent_system_prompt(agent_id: str, schema_context: str = "") -> str:
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
    conn_id = agent_cfg.get("connection_id")
    conn_cfg = db.get(COLL_CONNECTIONS, conn_id) if conn_id else {}
    conn_type = (conn_cfg or {}).get("type", "clickhouse")

    custom_prompt = agent_cfg.get("system_prompt", "")
    schema_block = f"\n## Available Schema\n{schema_context}" if schema_context else ""

    if conn_type == "oracle":
        base = ORACLE_ANALYST_SYSTEM.format(schema_context=schema_block)
    else:
        base = CLICKHOUSE_ANALYST_SYSTEM.format(schema_context=schema_block)

    return f"{base}\n\n{custom_prompt}".strip()


# ── Node implementations ──────────────────────────────────────────────────────

def analyst_node(state: AnalystState) -> Dict[str, Any]:
    """Generate or fix SQL based on the user question and optional prior error."""
    llm = build_llm()

    system_prompt = _get_agent_system_prompt(
        state["agent_id"],
        state.get("schema_context", ""),
    )

    history = list(state.get("messages", []))
    if not history:
        history = [HumanMessage(content=state["user_question"])]

    # If retrying, append error context
    if state.get("last_error"):
        history.append(
            HumanMessage(
                content=f"The previous SQL failed with this error:\n```\n{state['last_error']}\n```\n\n"
                f"SQL that failed:\n```sql\n{state.get('generated_sql', '')}\n```\n\n"
                "Please analyze the error and write a corrected SQL query."
            )
        )

    full_messages = [SystemMessage(content=system_prompt)] + history
    response = llm.invoke(full_messages)
    sql = response.content.strip()

    # Clean up any residual markdown fences
    if "```sql" in sql:
        sql = sql.split("```sql")[1].split("```")[0].strip()
    elif "```" in sql:
        sql = sql.split("```")[1].split("```")[0].strip()

    return {
        "generated_sql": sql,
        "messages": [AIMessage(content=f"Generated SQL:\n```sql\n{sql}\n```")],
    }


def sql_tool_node(state: AnalystState) -> Dict[str, Any]:
    """Execute the generated SQL against the configured database."""
    sql = state.get("generated_sql", "")
    if not sql:
        return {
            "query_result": {"success": False, "error": "No SQL was generated."},
            "last_error": "No SQL was generated.",
        }

    tool = _get_sql_tool(state["agent_id"])
    if not tool:
        return {
            "query_result": {
                "success": False,
                "error": "No database connection configured for this agent.",
            },
            "last_error": "No database connection configured.",
        }

    result = tool.execute(sql)
    error = result.get("error") if not result.get("success") else None

    return {
        "query_result": result,
        "last_error": error,
        "retry_count": state.get("retry_count", 0) + (1 if error else 0),
        "messages": [
            AIMessage(
                content=f"Query executed. Rows: {result.get('row_count', 0)}"
                if result.get("success")
                else f"Query failed: {error}"
            )
        ],
    }


def synthesizer_node(state: AnalystState) -> Dict[str, Any]:
    """Turn the raw query result into a human-friendly narrative."""
    llm = build_llm()
    result = state.get("query_result", {})

    result_summary = (
        result.get("markdown_table", "No data")
        if result.get("success")
        else f"Query failed: {result.get('error')}"
    )

    messages = [
        SystemMessage(content=SYNTHESIZER_SYSTEM),
        HumanMessage(
            content=f"Question: {state['user_question']}\n\n"
            f"SQL used:\n```sql\n{state.get('generated_sql', '')}\n```\n\n"
            f"Result:\n{result_summary}\n\n"
            + (f"⚠️ Warning: {result.get('warning')}" if result.get("warning") else "")
        ),
    ]

    response = llm.invoke(messages)
    return {
        "final_answer": response.content,
        "messages": [AIMessage(content=response.content)],
    }


def error_node(state: AnalystState) -> Dict[str, Any]:
    """Max retries exceeded — return a graceful error message."""
    return {
        "final_answer": (
            f"❌ Unable to complete the analysis after {state.get('retry_count', 0)} attempts.\n\n"
            f"**Last error:** {state.get('last_error', 'Unknown')}\n\n"
            f"**Last SQL attempted:**\n```sql\n{state.get('generated_sql', '')}\n```\n\n"
            "Please check the database connection and schema, then retry."
        ),
        "messages": [AIMessage(content="Max retries exceeded.")],
    }


# ── Routing functions ─────────────────────────────────────────────────────────

def route_after_tool(
    state: AnalystState,
) -> Literal["analyst", "synthesizer", "error_handler"]:
    result = state.get("query_result", {})
    max_retries = state.get("max_retries", 3)
    retry_count = state.get("retry_count", 0)

    if result.get("success"):
        return "synthesizer"

    if retry_count >= max_retries:
        return "error_handler"

    return "analyst"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_analyst_graph():
    """Build and compile the analyst LangGraph."""
    builder = StateGraph(AnalystState)

    builder.add_node("analyst", analyst_node)
    builder.add_node("sql_tool", sql_tool_node)
    builder.add_node("synthesizer", synthesizer_node)
    builder.add_node("error_handler", error_node)

    builder.add_edge(START, "analyst")
    builder.add_edge("analyst", "sql_tool")

    builder.add_conditional_edges(
        "sql_tool",
        route_after_tool,
        {
            "analyst": "analyst",
            "synthesizer": "synthesizer",
            "error_handler": "error_handler",
        },
    )

    builder.add_edge("synthesizer", END)
    builder.add_edge("error_handler", END)

    checkpointer = MemorySaver()
    graph = builder.compile(checkpointer=checkpointer)

    return graph
