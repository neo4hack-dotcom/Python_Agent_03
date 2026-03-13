"""
Orchestrator LangGraph — multi-agent workflow with:
  - Planning & decomposition
  - Dynamic routing to worker agents
  - Fan-out / Fan-in parallelism (via Send API)
  - State management with retry loop
  - Human-in-the-loop interruption
  - Synthesis & validation
"""
import json
import logging
from typing import Any, Dict, List, Literal, Optional, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from .state import OrchestratorState
from .llm_factory import build_llm

logger = logging.getLogger(__name__)

# ── System Prompts ────────────────────────────────────────────────────────────

PLANNER_SYSTEM = """You are an expert orchestrator agent. Your job is to:
1. Understand the user's overall objective.
2. Decompose it into an ordered list of concrete sub-tasks.
3. Identify which specialist agent should handle each sub-task.
4. Flag any tasks that require human validation before execution.

Respond ONLY with a JSON object in this exact format:
{
  "analysis": "<brief analysis of the request>",
  "tasks": [
    {
      "id": "task_1",
      "description": "<what needs to be done>",
      "agent_type": "<clickhouse_analyst|oracle_analyst|orchestrator|human_validation>",
      "priority": <1=highest>,
      "depends_on": [],
      "requires_human_approval": false
    }
  ]
}
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
Format your answer in Markdown with clear sections."""

CORRECTOR_SYSTEM = """You are a correction specialist. A sub-task failed.
Analyze the error and provide an improved, corrected version of the task instructions
that will help the worker agent succeed on the next attempt."""


# ── Node implementations ──────────────────────────────────────────────────────

def planner_node(state: OrchestratorState) -> Dict[str, Any]:
    """Decompose the user request into a backlog of tasks."""
    llm = build_llm()
    messages = [
        SystemMessage(content=PLANNER_SYSTEM),
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
        # Fallback: single task
        fallback_task = {
            "id": "task_1",
            "description": state["user_request"],
            "agent_type": "orchestrator",
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
    """Execute the current task using an LLM as generic worker."""
    task = state.get("current_task")
    if not task:
        return {"worker_results": state.get("worker_results", [])}

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
            "agent_type": task.get("agent_type", "orchestrator"),
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
            "agent_type": task.get("agent_type", "orchestrator"),
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
            "Generate the final comprehensive answer."
        ),
    ]

    response = llm.invoke(messages)
    return {
        "final_answer": response.content,
        "messages": [AIMessage(content=response.content)],
    }


def human_feedback_node(state: OrchestratorState) -> Dict[str, Any]:
    """Interrupt point — waits for human input (handled by LangGraph interrupt)."""
    # This node is reached when awaiting_human=True
    # The graph will be interrupted here; resumption happens externally
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
