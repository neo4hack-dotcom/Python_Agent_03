"""
Orchestrator LangGraph — multi-agent workflow with:
  - Planning & decomposition
  - Dynamic routing to worker agents
  - Fan-out / Fan-in parallelism (via Send API)
  - State management with retry loop
  - Human-in-the-loop interruption
  - Synthesis & validation
  - Real analyst sub-pipeline delegation (no placeholders)
"""
import json
import logging
from typing import Any, Dict, List, Literal, Optional, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from .state import OrchestratorState
from .llm_factory import build_llm
from .analyst_graph import analyst_node, sql_tool_node, synthesizer_node as analyst_synthesizer_node
from backend.database import db, COLL_AGENTS, COLL_CONNECTIONS
from backend.tools.sql_clickhouse import ClickHouseSQLTool
from backend.tools.sql_oracle import OracleSQLTool

logger = logging.getLogger(__name__)

# ── System Prompts ────────────────────────────────────────────────────────────

PLANNER_SYSTEM = """You are an expert orchestrator agent. Your job is to:
1. Understand the user's overall objective.
2. Decompose it into an ordered list of concrete sub-tasks.
3. Identify which specialist agent should handle each sub-task.
4. Flag any tasks that require human validation before execution.

IMPORTANT RULES:
- For data analysis tasks, ALWAYS use the available analyst agents listed below (agent_type = clickhouse_analyst or oracle_analyst).
- Specify the exact agent_id from the list for data tasks.
- NEVER use generic descriptions like "table_name" or placeholders — use the EXACT table names the user mentioned.
- If no table name is mentioned, ask the user to clarify (add a human_validation task first).
- Each analyst task must have a concrete, specific description with real table/column names.

{agents_block}

Respond ONLY with a JSON object in this exact format:
{{
  "analysis": "<brief analysis of the request>",
  "tasks": [
    {{
      "id": "task_1",
      "description": "<specific task with exact table/column names>",
      "agent_type": "<clickhouse_analyst|oracle_analyst|orchestrator|human_validation>",
      "agent_id": "<id of the analyst agent to use, or null for orchestrator tasks>",
      "priority": 1,
      "depends_on": [],
      "requires_human_approval": false
    }}
  ]
}}
"""

ROUTER_SYSTEM = """You are a routing agent. Given the current task and available worker results,
decide the next action:
- "continue": proceed to next pending task
- "retry": the last task failed, retry with corrections
- "synthesize": all tasks done, generate final answer
- "human_input": must pause and ask the human for validation/input
- "end": nothing more to do

Respond ONLY with JSON: {"action": "<action>", "reason": "<brief reason>"}
"""

SYNTHESIZER_SYSTEM = """You are a synthesis expert. Compile all worker results into a coherent,
structured final answer for the user. Be comprehensive but concise.
Format your answer in Markdown with clear sections.

IMPORTANT: The worker results contain REAL data fetched from databases. Present this real data
accurately — do NOT replace data with placeholders or templates."""

CORRECTOR_SYSTEM = """You are a correction specialist. A sub-task failed.
Analyze the error and provide an improved, corrected version of the task instructions
that will help the worker agent succeed on the next attempt."""


# ── Analyst delegation helpers ────────────────────────────────────────────────

def _find_analyst_agent(agent_type: str, agent_id_hint: Optional[str] = None) -> Optional[str]:
    """Return the id of the best matching active analyst agent."""
    if agent_id_hint:
        a = db.get(COLL_AGENTS, agent_id_hint)
        if a and a.get("type") == agent_type and a.get("is_active", True):
            return agent_id_hint
    for a in db.get_all(COLL_AGENTS):
        if a.get("type") == agent_type and a.get("is_active", True):
            return a["id"]
    return None


def _get_sql_tool_for_agent(agent_id: str) -> Optional[Any]:
    """Build the SQL tool for a given agent."""
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


def _auto_schema_context(agent_id: str, task_description: str) -> str:
    """
    Fetch the table list for the agent's connection and return schema info
    for any table names mentioned in the task description.
    """
    tool = _get_sql_tool_for_agent(agent_id)
    if not tool:
        return ""

    try:
        # list_tables() returns List[str] directly
        all_tables: List[str] = tool.list_tables()
        # Filter out error strings
        all_tables = [t for t in all_tables if not t.startswith("ERROR:")]
    except Exception as e:
        logger.warning("Could not list tables for schema context: %s", e)
        return ""

    if not all_tables:
        return ""

    # Find tables mentioned in the task description (case-insensitive)
    task_lower = task_description.lower()
    mentioned = [t for t in all_tables if t.lower() in task_lower]

    # If nothing specific is mentioned, provide the full table list
    if not mentioned:
        table_list = ", ".join(all_tables[:50])
        return f"Available tables: {table_list}"

    # Fetch schema for each mentioned table
    schema_parts = []
    for table in mentioned[:5]:  # limit to 5 tables
        try:
            # get_schema() returns {"table":..., "columns":[...], "metadata":{...}} or {"error":...}
            schema_result = tool.get_schema(table)
            if "error" not in schema_result and schema_result.get("columns"):
                cols = schema_result["columns"]
                col_lines = "\n".join(
                    f"  - {c['name']} ({c['type']})" + (f"  -- {c['comment']}" if c.get("comment") else "")
                    for c in cols
                )
                meta = schema_result.get("metadata", {})
                meta_info = ""
                if meta.get("sorting_key"):
                    meta_info += f"\n  Sorting key: {meta['sorting_key']}"
                if meta.get("partition_key"):
                    meta_info += f"\n  Partition key: {meta['partition_key']}"
                schema_parts.append(f"Table `{table}`:\n{col_lines}{meta_info}")
        except Exception as e:
            logger.warning("Could not get schema for table %s: %s", table, e)

    if schema_parts:
        return "\n\n".join(schema_parts)
    table_list = ", ".join(all_tables[:50])
    return f"Available tables: {table_list}"


def _run_analyst_subtask(agent_id: str, task_description: str) -> Dict[str, Any]:
    """
    Execute the real analyst pipeline: analyst_node → sql_tool_node → synthesizer_node.
    Returns a result dict with success flag and actual data.
    """
    max_retries = 3

    # Pre-fetch schema context
    schema_context = _auto_schema_context(agent_id, task_description)
    logger.info("Running analyst subtask for agent %s: %s", agent_id, task_description[:100])
    logger.info("Schema context length: %d chars", len(schema_context))

    # Initial analyst state
    state: Dict[str, Any] = {
        "messages": [HumanMessage(content=task_description)],
        "user_question": task_description,
        "generated_sql": None,
        "query_result": None,
        "retry_count": 0,
        "max_retries": max_retries,
        "last_error": None,
        "final_answer": None,
        "schema_context": schema_context,
        "agent_id": agent_id,
        "session_id": "orchestrator_subtask",
    }

    # Run analyst → sql loop with retries
    for attempt in range(max_retries + 1):
        try:
            analyst_result = analyst_node(state)
            state.update(analyst_result)

            tool_result = sql_tool_node(state)
            state.update(tool_result)

            if state.get("query_result", {}).get("success"):
                logger.info("Analyst subtask succeeded on attempt %d", attempt + 1)
                break

            if attempt < max_retries:
                logger.info("Analyst subtask attempt %d failed, retrying... error: %s",
                            attempt + 1, state.get("last_error"))
        except Exception as e:
            logger.error("Analyst subtask exception on attempt %d: %s", attempt + 1, e)
            state["last_error"] = str(e)
            if attempt >= max_retries:
                break

    # Run synthesizer
    try:
        synth_result = analyst_synthesizer_node(state)
        state.update(synth_result)
    except Exception as e:
        logger.error("Analyst synthesizer failed: %s", e)
        state["final_answer"] = f"Analysis completed but synthesis failed: {e}"

    query_result = state.get("query_result", {})
    success = query_result.get("success", False)

    return {
        "success": success,
        "result": state.get("final_answer") or (
            f"❌ Analysis failed after {max_retries} attempts.\nLast error: {state.get('last_error')}"
        ),
        "sql_executed": state.get("generated_sql"),
        "row_count": query_result.get("row_count", 0) if success else 0,
        "error": state.get("last_error") if not success else None,
    }


# ── Node implementations ──────────────────────────────────────────────────────

def planner_node(state: OrchestratorState) -> Dict[str, Any]:
    """Decompose the user request into a backlog of tasks."""
    llm = build_llm()

    # Build analyst agents block for the prompt
    analysts = [
        a for a in db.get_all(COLL_AGENTS)
        if a.get("type") in ("clickhouse_analyst", "oracle_analyst") and a.get("is_active", True)
    ]
    if analysts:
        agents_lines = "\n".join(
            f"  - name={a['name']}  type={a['type']}  id={a['id']}"
            for a in analysts
        )
        agents_block = f"Available analyst agents (use these for data queries):\n{agents_lines}"
    else:
        agents_block = "No analyst agents configured — use agent_type=orchestrator for all tasks."

    system_content = PLANNER_SYSTEM.format(agents_block=agents_block)

    messages = [
        SystemMessage(content=system_content),
        HumanMessage(content=f"User request: {state['user_request']}"),
    ]

    try:
        response = llm.invoke(messages)
        raw = response.content.strip()

        # Extract JSON from markdown code blocks if present
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()

        plan = json.loads(raw)
        tasks = plan.get("tasks", [])

        logger.info("Planner created %d tasks", len(tasks))

        return {
            "messages": [AIMessage(content=f"Plan created: {plan['analysis']}\n\nTasks: {len(tasks)}")],
            "task_backlog": tasks,
            "current_task": None,
            "worker_results": [],
            "iteration": 0,
        }
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Planner failed to parse JSON: %s", e)
        # Fallback: single task routed to first available analyst
        agent_type = "orchestrator"
        agent_id = None
        if analysts:
            agent_type = analysts[0]["type"]
            agent_id = analysts[0]["id"]

        fallback_task = {
            "id": "task_1",
            "description": state["user_request"],
            "agent_type": agent_type,
            "agent_id": agent_id,
            "priority": 1,
            "depends_on": [],
            "requires_human_approval": False,
        }
        return {
            "messages": [AIMessage(content="Could not parse detailed plan, proceeding with single task.")],
            "task_backlog": [fallback_task],
            "current_task": None,
            "worker_results": [],
            "iteration": 0,
        }


def task_dispatcher_node(state: OrchestratorState) -> Dict[str, Any]:
    """Pick the next pending task from the backlog."""
    backlog = state.get("task_backlog", [])
    completed_ids = {r["task_id"] for r in state.get("worker_results", [])}

    # Find next executable task (respecting dependencies)
    next_task = None
    for task in sorted(backlog, key=lambda t: t.get("priority", 99)):
        if task["id"] in completed_ids:
            continue
        deps = task.get("depends_on", [])
        if all(dep in completed_ids for dep in deps):
            next_task = task
            break

    if next_task and next_task.get("requires_human_approval"):
        return {
            "current_task": next_task,
            "awaiting_human": True,
            "messages": [
                AIMessage(
                    content=f"⚠️ **Human approval required** for task: {next_task['description']}\n\nPlease confirm to proceed."
                )
            ],
        }

    return {
        "current_task": next_task,
        "awaiting_human": False,
        "iteration": state.get("iteration", 0) + 1,
    }


def worker_node(state: OrchestratorState) -> Dict[str, Any]:
    """Execute the current task — routes analyst tasks to real SQL pipeline."""
    task = state.get("current_task")
    if not task:
        return {"worker_results": state.get("worker_results", [])}

    agent_type = task.get("agent_type", "orchestrator")

    # ── Real analyst delegation ──────────────────────────────────────────────
    if agent_type in ("clickhouse_analyst", "oracle_analyst"):
        agent_id = task.get("agent_id") or _find_analyst_agent(agent_type)
        if not agent_id:
            result_entry = {
                "task_id": task["id"],
                "task_description": task["description"],
                "agent_type": agent_type,
                "result": f"❌ No active {agent_type} agent found. Please create and configure one.",
                "success": False,
                "error": f"No active {agent_type} agent available.",
            }
        else:
            try:
                subtask_result = _run_analyst_subtask(agent_id, task["description"])
                result_entry = {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "agent_type": agent_type,
                    "result": subtask_result["result"],
                    "success": subtask_result["success"],
                    "sql_executed": subtask_result.get("sql_executed"),
                    "row_count": subtask_result.get("row_count", 0),
                }
                if not subtask_result["success"]:
                    result_entry["error"] = subtask_result.get("error")
            except Exception as e:
                logger.error("Analyst subtask raised exception: %s", e, exc_info=True)
                result_entry = {
                    "task_id": task["id"],
                    "task_description": task["description"],
                    "agent_type": agent_type,
                    "result": f"❌ Analyst execution error: {e}",
                    "success": False,
                    "error": str(e),
                }

        updated_results = state.get("worker_results", []) + [result_entry]
        last_error = result_entry.get("error") if not result_entry["success"] else None
        return {
            "worker_results": updated_results,
            "messages": [AIMessage(content=f"Task '{task['id']}' {'completed' if result_entry['success'] else 'failed'}.")],
            "last_error": last_error,
        }

    # ── Generic LLM worker (orchestrator tasks) ──────────────────────────────
    llm = build_llm()
    context = "\n\n".join(
        f"[Task {r['task_id']}]: {r['result']}"
        for r in state.get("worker_results", [])
    )

    messages = [
        SystemMessage(
            content="You are a skilled assistant. Complete the assigned task thoroughly and accurately. "
            "Use any prior context provided."
        ),
        HumanMessage(
            content=f"Previous context:\n{context}\n\nYour task:\n{task['description']}"
        ),
    ]

    try:
        response = llm.invoke(messages)
        result_entry = {
            "task_id": task["id"],
            "task_description": task["description"],
            "agent_type": agent_type,
            "result": response.content,
            "success": True,
        }
        updated_results = state.get("worker_results", []) + [result_entry]
        return {
            "worker_results": updated_results,
            "messages": [AIMessage(content=f"Task '{task['id']}' completed.")],
            "last_error": None,
        }
    except Exception as e:
        error_entry = {
            "task_id": task["id"],
            "task_description": task["description"],
            "agent_type": agent_type,
            "result": f"ERROR: {e}",
            "success": False,
            "error": str(e),
        }
        updated_results = state.get("worker_results", []) + [error_entry]
        return {
            "worker_results": updated_results,
            "last_error": str(e),
            "messages": [AIMessage(content=f"Task '{task['id']}' failed: {e}")],
        }


def corrector_node(state: OrchestratorState) -> Dict[str, Any]:
    """Analyze last error and inject corrected instructions for retry."""
    llm = build_llm()
    task = state.get("current_task", {})
    error = state.get("last_error", "Unknown error")

    messages = [
        SystemMessage(content=CORRECTOR_SYSTEM),
        HumanMessage(
            content=f"Task that failed:\n{task.get('description', '')}\n\nError:\n{error}\n\n"
            "Provide corrected instructions."
        ),
    ]

    response = llm.invoke(messages)
    corrected_task = dict(task)
    corrected_task["description"] = response.content

    return {
        "current_task": corrected_task,
        "messages": [AIMessage(content=f"Correction applied for task '{task.get('id', '?')}'.")],
    }


def synthesizer_node(state: OrchestratorState) -> Dict[str, Any]:
    """Compile all worker results into a final coherent answer."""
    llm = build_llm()

    results_text = "\n\n".join(
        f"**{r['task_description']}**\n{r['result']}"
        for r in state.get("worker_results", [])
    )

    messages = [
        SystemMessage(content=SYNTHESIZER_SYSTEM),
        HumanMessage(
            content=f"Original request: {state['user_request']}\n\n"
            f"Worker results:\n{results_text}\n\n"
            "Generate the final comprehensive answer based on the REAL data above."
        ),
    ]

    response = llm.invoke(messages)
    return {
        "final_answer": response.content,
        "messages": [AIMessage(content=response.content)],
    }


def human_feedback_node(state: OrchestratorState) -> Dict[str, Any]:
    """Interrupt point — waits for human input (handled by LangGraph interrupt)."""
    return {"awaiting_human": False}


# ── Routing functions ─────────────────────────────────────────────────────────

def route_after_dispatcher(state: OrchestratorState) -> Literal["worker", "synthesizer", "human_feedback"]:
    if state.get("awaiting_human"):
        return "human_feedback"
    if state.get("current_task") is None:
        return "synthesizer"
    if state.get("iteration", 0) >= state.get("max_iterations", 10):
        return "synthesizer"
    return "worker"


def route_after_worker(state: OrchestratorState) -> Literal["dispatcher", "corrector", "synthesizer"]:
    results = state.get("worker_results", [])
    backlog = state.get("task_backlog", [])
    completed_ids = {r["task_id"] for r in results}

    # Check if last task failed
    if results and not results[-1].get("success", True):
        retry_count = sum(1 for r in results if r["task_id"] == results[-1]["task_id"])
        max_retries = 3
        if retry_count < max_retries:
            return "corrector"

    # Check if all tasks complete
    all_done = all(t["id"] in completed_ids for t in backlog)
    if all_done:
        return "synthesizer"

    return "dispatcher"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_orchestrator_graph():
    """Build and compile the orchestrator LangGraph."""
    builder = StateGraph(OrchestratorState)

    # Nodes
    builder.add_node("planner", planner_node)
    builder.add_node("dispatcher", task_dispatcher_node)
    builder.add_node("worker", worker_node)
    builder.add_node("corrector", corrector_node)
    builder.add_node("synthesizer", synthesizer_node)
    builder.add_node("human_feedback", human_feedback_node)

    # Edges
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "dispatcher")

    builder.add_conditional_edges(
        "dispatcher",
        route_after_dispatcher,
        {
            "worker": "worker",
            "synthesizer": "synthesizer",
            "human_feedback": "human_feedback",
        },
    )

    builder.add_conditional_edges(
        "worker",
        route_after_worker,
        {
            "dispatcher": "dispatcher",
            "corrector": "corrector",
            "synthesizer": "synthesizer",
        },
    )

    builder.add_edge("corrector", "worker")
    builder.add_edge("human_feedback", "dispatcher")
    builder.add_edge("synthesizer", END)

    checkpointer = MemorySaver()
    graph = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_feedback"],
    )

    return graph
