"""
Chat endpoints — WebSocket streaming + REST fallback.
Handles both orchestrator and analyst agents.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import AsyncGenerator, Dict, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage

from backend.database import db, COLL_AGENTS, COLL_SESSIONS, COLL_MESSAGES
from backend.graphs.orchestrator_graph import build_orchestrator_graph
from backend.graphs.analyst_graph import build_analyst_graph
from backend.graphs.data_analyst_graph import build_data_analyst_graph
from backend.graphs.data_quality_graph import build_data_quality_graph
from backend.graphs.data_dictionary_graph import build_data_dictionary_graph
from backend.graphs.report_graph import build_report_graph
from backend.graphs.file_graph import build_file_graph
from backend.graphs.powerbi_graph import build_powerbi_graph
from backend.graphs.web_scraper_graph import build_web_scraper_graph
from backend.graphs.chart_graph import build_chart_graph
from backend.graphs.llm_factory import build_llm
from backend.models.agent import AgentType
from backend.models.chat import ChatRequest, ChatMessage, ChatSession, MessageRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["Chat"])

# Cache compiled graphs (expensive to rebuild each call)
_orchestrator_graph = None
_analyst_graph = None
_data_analyst_graph = None
_data_quality_graph = None
_data_dictionary_graph = None
_report_graph = None
_file_graph = None
_powerbi_graph = None
_web_scraper_graph = None
_chart_graph = None


def _get_powerbi_agent():
    global _powerbi_graph
    if _powerbi_graph is None:
        _powerbi_graph = build_powerbi_graph()
    return _powerbi_graph


def _get_web_scraper():
    global _web_scraper_graph
    if _web_scraper_graph is None:
        _web_scraper_graph = build_web_scraper_graph()
    return _web_scraper_graph


def _get_chart_agent():
    global _chart_graph
    if _chart_graph is None:
        _chart_graph = build_chart_graph()
    return _chart_graph


def _get_data_quality():
    global _data_quality_graph
    if _data_quality_graph is None:
        _data_quality_graph = build_data_quality_graph()
    return _data_quality_graph


def _get_data_dictionary():
    global _data_dictionary_graph
    if _data_dictionary_graph is None:
        _data_dictionary_graph = build_data_dictionary_graph()
    return _data_dictionary_graph


def _get_file_agent():
    global _file_graph
    if _file_graph is None:
        _file_graph = build_file_graph()
    return _file_graph


def _get_orchestrator():
    global _orchestrator_graph
    if _orchestrator_graph is None:
        _orchestrator_graph = build_orchestrator_graph()
    return _orchestrator_graph


def _get_analyst():
    global _analyst_graph
    if _analyst_graph is None:
        _analyst_graph = build_analyst_graph()
    return _analyst_graph


def _get_data_analyst():
    global _data_analyst_graph
    if _data_analyst_graph is None:
        _data_analyst_graph = build_data_analyst_graph()
    return _data_analyst_graph


def _get_report():
    global _report_graph
    if _report_graph is None:
        _report_graph = build_report_graph()
    return _report_graph


def _merge_state(state: dict, result: dict) -> dict:
    """
    Simulate LangGraph's add_messages reducer for direct node calls.

    Merges the result dict returned by a node into the running state:
      - 'messages' lists are CONCATENATED (not replaced)
      - All other keys are REPLACED (last-write wins)

    Use this instead of graph.astream() to avoid MemorySaver entirely.
    """
    merged = dict(state)
    for key, value in result.items():
        if key == "messages":
            existing = merged.get("messages") or []
            merged["messages"] = list(existing) + list(value or [])
        else:
            merged[key] = value
    return merged


def _load_conversation_history(session_id: str, max_messages: int = 20) -> list:
    """
    Charge l'historique de conversation d'une session et le convertit en
    messages LangChain (HumanMessage / AIMessage) pour injection dans le graphe.

    Limite à max_messages échanges pour ne pas dépasser le context window du LLM.
    Exclut le dernier message (celui qui vient d'être ajouté par l'utilisateur).
    """
    if not session_id:
        return []
    history = db.get_list(COLL_MESSAGES, session_id)
    # On prend les N-1 derniers (le dernier a déjà été ajouté comme HumanMessage)
    past = history[:-1][-max_messages:]
    lc_messages = []
    for msg in past:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        if role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
    return lc_messages


def _auto_session_title(message: str) -> str:
    """Génère un titre court pour la session à partir du premier message."""
    cleaned = message.strip()
    return (cleaned[:48] + "…") if len(cleaned) > 48 else cleaned


def _get_or_create_session(agent_id: str, session_id: Optional[str]) -> str:
    if session_id and db.exists(COLL_SESSIONS, session_id):
        return session_id
    sid = session_id or str(uuid.uuid4())
    session = ChatSession(id=sid, agent_id=agent_id)
    db.set(COLL_SESSIONS, sid, session.model_dump())
    return sid


def _save_message(session_id: str, role: MessageRole, content: str, metadata: dict = None):
    msg = ChatMessage(role=role, content=content, metadata=metadata or {})
    db.append_to_list(COLL_MESSAGES, session_id, msg.model_dump())
    # Update session timestamp
    db.upsert(COLL_SESSIONS, session_id, {"updated_at": datetime.utcnow().isoformat()})
    return msg


async def _run_orchestrator(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    graph = _get_orchestrator()
    config = {"configurable": {"thread_id": session_id}}
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}

    history = _load_conversation_history(session_id)
    initial_state = {
        "messages": history + [HumanMessage(content=message)],
        "user_request": message,
        "task_backlog": [],
        "current_task": None,
        "worker_results": [],
        "final_answer": None,
        "report_id": None,
        "awaiting_human": False,
        "iteration": 0,
        "max_iterations": agent_cfg.get("max_iterations", 10),
        "agent_id": agent_id,
        "session_id": session_id,
    }

    try:
        async for event in graph.astream(initial_state, config=config, stream_mode="values"):
            # Pause the stream when the reasoner needs human input
            if event.get("awaiting_human"):
                task = event.get("current_task") or {}
                yield json.dumps({
                    "type": "human_validation",
                    "content": task.get("description", ""),
                    "options": task.get("options", []),
                }) + "\n"
                await asyncio.sleep(0)
                break  # Stop streaming — resume when user replies

            msgs = event.get("messages", [])
            if msgs:
                last = msgs[-1]
                if hasattr(last, "content") and last.content:
                    yield json.dumps({"type": "token", "content": last.content}) + "\n"
                    await asyncio.sleep(0)

        # Final state
        final_state = graph.get_state(config)
        vals = final_state.values
        final_answer = vals.get("final_answer", "")
        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"

        # Emit pdf_ready if a report was generated during orchestration
        report_id = vals.get("report_id")
        if not report_id:
            # Fallback: scan worker_results in case synthesizer didn't capture it
            for r in vals.get("worker_results", []):
                if r.get("report_id"):
                    report_id = r["report_id"]
                    break
        if report_id:
            yield json.dumps({
                "type": "pdf_ready",
                "report_id": report_id,
                "download_url": f"/api/report/{report_id}/download",
                "filename": f"rapport_analyse_{report_id[:8]}.pdf",
            }) + "\n"
    except Exception as e:
        logger.error("Orchestrator error: %s", e)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_analyst(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    ReAct loop for ClickHouse / Oracle analyst — plain Python, NO LangGraph.

    Why we abandoned the graph.astream() approach completely:
      Local LLMs return content=null for tool-calling responses. Every fix
      that tried to patch Pydantic AIMessage objects (model_copy, object.
      __setattr__, unique thread_id) failed on Windows because LangChain /
      Pydantic v2 rebuilds the object from its checkpoint representation and
      restores content=None before it reaches the next llm.invoke() call.

    This implementation extracts `content` and `tool_calls` as plain Python
    values RIGHT AFTER llm.invoke(), builds a fresh AIMessage(content=content
    or "", tool_calls=tool_calls) from scratch, and appends it to a regular
    Python list.  The original response object is discarded immediately.
    No MemorySaver, no add_messages reducer, no Pydantic serialisation path.
    """
    from backend.graphs.analyst_graph import (
        _build_react_tools,
        _build_react_system_prompt,
    )
    from langchain_core.messages import SystemMessage, ToolMessage

    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}

    try:
        # ── Build tools and LLM ───────────────────────────────────────────
        tools = _build_react_tools(agent_id)
        llm = build_llm()
        if tools:
            llm = llm.bind_tools(tools)
        tool_map = {t.name: t for t in tools}

        system_prompt = _build_react_system_prompt(agent_id)

        # ── Initial message list (plain Python, no LangGraph state) ───────
        history = _load_conversation_history(session_id)
        messages: list = (
            [SystemMessage(content=system_prompt)]
            + history
            + [HumanMessage(content=message)]
        )

        final_answer = ""
        max_iterations = 10

        # ── ReAct loop ────────────────────────────────────────────────────
        for iteration in range(max_iterations):

            # --- call LLM ---
            response = llm.invoke(messages)

            # Extract raw values IMMEDIATELY as plain Python — the response
            # object may have content=None and will NOT be stored anywhere.
            raw_content: str = response.content or ""
            raw_tool_calls: list = list(getattr(response, "tool_calls", None) or [])

            # Build a GUARANTEED-CLEAN AIMessage and append to our list.
            # We never reference `response` again after this point.
            ai_msg = AIMessage(content=raw_content, tool_calls=raw_tool_calls)
            messages.append(ai_msg)

            # ── No tool calls → this is the final answer ─────────────────
            if not raw_tool_calls:
                final_answer = raw_content
                break

            # ── Execute each requested tool ───────────────────────────────
            for tc in raw_tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args") or {}
                tool_call_id = tc.get("id") or f"call_{iteration}_{tool_name}"

                fn = tool_map.get(tool_name)
                if fn is None:
                    tool_result = f"Error: unknown tool '{tool_name}'"
                else:
                    try:
                        tool_result = fn.invoke(tool_args)
                    except Exception as exc:
                        logger.warning("Tool %s raised: %s", tool_name, exc)
                        tool_result = f"Tool error: {exc}"

                # ToolMessage content must always be a non-None string
                tool_content = str(tool_result) if tool_result is not None else ""
                messages.append(ToolMessage(
                    content=tool_content,
                    tool_call_id=tool_call_id,
                    name=tool_name,
                ))

                # Emit SQL / query_result SSE events from execute_query
                if tool_name == "execute_query":
                    try:
                        data = json.loads(tool_content)
                        if data.get("success"):
                            executed_sql = data.get("sql_executed", "")
                            if executed_sql:
                                yield json.dumps({"type": "sql", "content": executed_sql}) + "\n"
                            yield json.dumps({
                                "type": "query_result",
                                "row_count": data.get("row_count", 0),
                                "columns": data.get("columns", []),
                                "rows": data.get("rows", []),
                                "sql": executed_sql,
                                "warning": data.get("warning"),
                            }) + "\n"
                            await asyncio.sleep(0)
                    except Exception:
                        pass

        # ── Guard: max iterations exceeded without a final answer ─────────
        if not final_answer:
            final_answer = (
                "❌ Impossible de générer une réponse en moins de "
                f"{max_iterations} itérations. Reformulez votre question."
            )

        # ── Emit final SSE event ──────────────────────────────────────────
        if final_answer.strip().startswith("CLARIFICATION_NEEDED:"):
            lines = [l.strip() for l in final_answer.strip().splitlines()]
            q_text = lines[0].replace("CLARIFICATION_NEEDED:", "").strip()
            options = []
            for line in lines:
                if line.startswith("OPTIONS:"):
                    options = [o.strip() for o in line.replace("OPTIONS:", "").split("|") if o.strip()]
            yield json.dumps({"type": "human_validation", "content": q_text, "options": options}) + "\n"
        else:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"

    except Exception as e:
        logger.error("Analyst error: %s", e, exc_info=True)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"



async def _run_data_analyst(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    Data Analyst — direct node calls, no LangGraph/MemorySaver.
    Pipeline: planner → [sql_executor] → analyst → synthesizer
    """
    from backend.graphs.data_analyst_graph import (
        _auto_schema_context, _has_connection,
        planner_node, sql_executor_node, analyst_node, synthesizer_node,
    )

    try:
        schema_context = _auto_schema_context(agent_id, message)
        state: dict = {
            "messages": list(_load_conversation_history(session_id)),
            "user_question": message,
            "analysis_plan": None,
            "sql_queries": None,
            "data_results": None,
            "analysis_output": None,
            "final_answer": None,
            "schema_context": schema_context,
            "agent_id": agent_id,
            "session_id": session_id,
            "last_error": None,
        }

        state = _merge_state(state, planner_node(state))
        await asyncio.sleep(0)

        if state.get("sql_queries") and _has_connection(agent_id):
            state = _merge_state(state, sql_executor_node(state))
            for r in (state.get("data_results") or []):
                if r.get("success") and r.get("rows"):
                    yield json.dumps({
                        "type": "query_result",
                        "row_count": r.get("row_count", 0),
                        "columns": r.get("columns", []),
                        "rows": r.get("rows", []),
                        "sql": r.get("sql", ""),
                        "description": r.get("description", ""),
                        "warning": r.get("warning"),
                    }) + "\n"
                    await asyncio.sleep(0)

        state = _merge_state(state, analyst_node(state))
        await asyncio.sleep(0)
        state = _merge_state(state, synthesizer_node(state))

        final_answer = state.get("final_answer") or ""
        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"
    except Exception as e:
        logger.error("Data analyst error: %s", e, exc_info=True)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_report(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    Report Writer — direct node calls, no LangGraph/MemorySaver.
    Pipeline: report_writer_node → pdf_node
    """
    from backend.graphs.report_graph import report_writer_node, pdf_node

    try:
        history = db.get_list(COLL_MESSAGES, session_id)
        context_lines = []
        for msg in history[-30:]:
            role = "Utilisateur" if msg.get("role") == "user" else "Assistant"
            content = msg.get("content", "")
            if content:
                context_lines.append(f"**{role}** : {content}")
        session_context = "\n\n".join(context_lines)

        state: dict = {
            "messages": [],
            "user_request": message,
            "session_context": session_context,
            "report_markdown": None,
            "pdf_path": None,
            "report_id": None,
            "final_answer": None,
            "agent_id": agent_id,
            "session_id": session_id,
        }

        state = _merge_state(state, report_writer_node(state))
        await asyncio.sleep(0)
        state = _merge_state(state, pdf_node(state))

        final_answer = state.get("final_answer") or ""
        report_id = state.get("report_id")

        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"
        if report_id:
            yield json.dumps({
                "type": "pdf_ready",
                "report_id": report_id,
                "download_url": f"/api/report/{report_id}/download",
                "filename": f"rapport_analyse_{report_id[:8]}.pdf",
            }) + "\n"
    except Exception as e:
        logger.error("Report writer error: %s", e, exc_info=True)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_file_agent(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    File Manager — plain Python ReAct loop, no LangGraph/MemorySaver.
    """
    from backend.graphs.file_graph import _build_tools, FILE_MANAGER_SYSTEM

    try:
        agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
        custom_prompt = agent_cfg.get("system_prompt", "")
        tools = _build_tools(agent_id)
        llm = build_llm()
        if tools:
            llm = llm.bind_tools(tools)
        tool_map = {t.name: t for t in tools}

        system_prompt = FILE_MANAGER_SYSTEM.format(custom_prompt=custom_prompt or "")
        messages: list = (
            [SystemMessage(content=system_prompt)]
            + _load_conversation_history(session_id)
            + [HumanMessage(content=message)]
        )
        final_answer = ""

        for iteration in range(15):
            response = llm.invoke(messages)
            raw_content = response.content or ""
            raw_tool_calls = list(getattr(response, "tool_calls", None) or [])
            messages.append(AIMessage(content=raw_content, tool_calls=raw_tool_calls))

            if not raw_tool_calls:
                final_answer = raw_content
                break

            for tc in raw_tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args") or {}
                tool_call_id = tc.get("id") or f"call_{iteration}_{tool_name}"

                yield json.dumps({"type": "tool_call", "tool": tool_name,
                                  "args": str(tool_args)[:200]}) + "\n"

                fn = tool_map.get(tool_name)
                tool_content = str(fn.invoke(tool_args) if fn else f"Unknown tool: {tool_name}")
                messages.append(ToolMessage(content=tool_content, tool_call_id=tool_call_id, name=tool_name))

        if not final_answer:
            final_answer = "❌ Opération impossible en moins de 15 itérations."

        if "CONFIRMATION REQUISE" in final_answer or "confirmed=False" in final_answer:
            yield json.dumps({"type": "human_validation", "content": final_answer}) + "\n"
        else:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"
    except Exception as e:
        logger.error("File agent error: %s", e, exc_info=True)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_powerbi_agent(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    Power BI Analyst — plain Python ReAct loop, no LangGraph/MemorySaver.
    """
    from backend.graphs.powerbi_graph import _build_tools, POWERBI_SYSTEM

    try:
        agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
        custom_prompt = agent_cfg.get("system_prompt", "")
        tools = _build_tools(agent_id, session_id)
        llm = build_llm()
        if tools:
            llm = llm.bind_tools(tools)
        tool_map = {t.name: t for t in tools}

        system_prompt = POWERBI_SYSTEM.format(custom_prompt=custom_prompt or "")
        messages: list = (
            [SystemMessage(content=system_prompt)]
            + _load_conversation_history(session_id)
            + [HumanMessage(content=message)]
        )
        final_answer = ""

        for iteration in range(20):
            response = llm.invoke(messages)
            raw_content = response.content or ""
            raw_tool_calls = list(getattr(response, "tool_calls", None) or [])
            messages.append(AIMessage(content=raw_content, tool_calls=raw_tool_calls))

            if not raw_tool_calls:
                final_answer = raw_content
                break

            for tc in raw_tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args") or {}
                tool_call_id = tc.get("id") or f"call_{iteration}_{tool_name}"

                yield json.dumps({"type": "tool_call", "tool": tool_name,
                                  "args": str(tool_args)[:200]}) + "\n"

                fn = tool_map.get(tool_name)
                tool_content = str(fn.invoke(tool_args) if fn else f"Unknown tool: {tool_name}")
                messages.append(ToolMessage(content=tool_content, tool_call_id=tool_call_id, name=tool_name))

                if "SCREENSHOT_CAPTURED:" in tool_content:
                    lines = tool_content.split("\n")
                    fn_line = next((l for l in lines if l.startswith("SCREENSHOT_CAPTURED:")), "")
                    filename = fn_line.replace("SCREENSHOT_CAPTURED:", "").strip()
                    if filename:
                        yield json.dumps({
                            "type": "screenshot_ready",
                            "filename": filename,
                            "screenshot_url": f"/api/powerbi/screenshot/{filename}",
                        }) + "\n"
                await asyncio.sleep(0)

        if not final_answer:
            final_answer = "❌ Analyse impossible en moins de 20 itérations."
        yield json.dumps({"type": "final", "content": final_answer}) + "\n"
    except Exception as e:
        logger.error("PowerBI agent error: %s", e, exc_info=True)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_data_quality(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    Data Quality — direct node calls, no LangGraph/MemorySaver.
    Pipeline: schema_node → stats_node → [volumetric_node] → llm_analysis_node → synthesizer_node
    """
    from backend.graphs.data_quality_graph import (
        schema_node, stats_node, volumetric_node, llm_analysis_node, synthesizer_node,
    )

    try:
        params = json.loads(message)
    except Exception:
        yield json.dumps({"type": "error", "content": "Message invalide : JSON attendu du formulaire Data Quality."}) + "\n"
        return

    try:
        state: dict = {
            "messages": [HumanMessage(content=message)],
            "table": params.get("table", ""),
            "columns": params.get("columns", []),
            "sample_size": params.get("sample_size", 50000),
            "row_filter": params.get("row_filter") or None,
            "time_column": params.get("time_column") or None,
            "db_type": "clickhouse",
            "schema_info": None,
            "column_stats": None,
            "volumetric_stats": None,
            "llm_analysis": None,
            "final_answer": None,
            "agent_id": agent_id,
            "session_id": session_id,
            "last_error": None,
        }

        # schema_node
        state = _merge_state(state, schema_node(state))
        await asyncio.sleep(0)
        if state.get("last_error"):
            yield json.dumps({"type": "error", "content": state["last_error"]}) + "\n"
            return
        for msg in (state.get("messages") or []):
            if hasattr(msg, "content") and msg.content and "[DQ]" in msg.content:
                yield json.dumps({"type": "dq_progress", "content": msg.content}) + "\n"
        await asyncio.sleep(0)

        # stats_node
        state = _merge_state(state, stats_node(state))
        await asyncio.sleep(0)
        if state.get("last_error"):
            yield json.dumps({"type": "error", "content": state["last_error"]}) + "\n"
            return
        for msg in (state.get("messages") or []):
            if hasattr(msg, "content") and msg.content and "[DQ]" in msg.content:
                yield json.dumps({"type": "dq_progress", "content": msg.content}) + "\n"
        await asyncio.sleep(0)

        # volumetric_node (only if time_column is set)
        if state.get("time_column"):
            state = _merge_state(state, volumetric_node(state))
            await asyncio.sleep(0)
            for msg in (state.get("messages") or []):
                if hasattr(msg, "content") and msg.content and "[DQ]" in msg.content:
                    yield json.dumps({"type": "dq_progress", "content": msg.content}) + "\n"
            await asyncio.sleep(0)

        # llm_analysis_node
        state = _merge_state(state, llm_analysis_node(state))
        await asyncio.sleep(0)

        # synthesizer_node
        state = _merge_state(state, synthesizer_node(state))

        if state.get("last_error"):
            yield json.dumps({"type": "error", "content": state["last_error"]}) + "\n"
            return

        final_answer = state.get("final_answer") or ""
        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"

        col_stats = state.get("column_stats")
        if col_stats:
            yield json.dumps({"type": "dq_stats", "stats": col_stats}) + "\n"

        vol_stats = state.get("volumetric_stats")
        if vol_stats and not vol_stats.get("error"):
            yield json.dumps({"type": "dq_volumetric", "stats": vol_stats}) + "\n"

    except Exception as e:
        logger.error("Data quality agent error: %s", e, exc_info=True)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_data_dictionary(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    Data Dictionary — direct node calls, no LangGraph/MemorySaver.
    Pipeline: discover_tables_node → fetch_schemas_node → llm_doc_node → synthesizer_node
    """
    from backend.graphs.data_dictionary_graph import (
        discover_tables_node, fetch_schemas_node, llm_doc_node, synthesizer_node,
    )

    try:
        params = json.loads(message)
    except Exception:
        yield json.dumps({"type": "error", "content": "Message invalide : JSON attendu du formulaire Data Dictionary."}) + "\n"
        return

    try:
        state: dict = {
            "messages": [HumanMessage(content=message)],
            "tables": params.get("tables", []),
            "sample_rows": max(1, min(20, params.get("sample_rows", 5))),
            "language": params.get("language", "fr"),
            "db_type": "clickhouse",
            "discovered_tables": [],
            "table_schemas": None,
            "dictionary": None,
            "final_answer": None,
            "agent_id": agent_id,
            "session_id": session_id,
            "last_error": None,
        }

        # discover_tables_node
        state = _merge_state(state, discover_tables_node(state))
        await asyncio.sleep(0)
        if state.get("last_error"):
            yield json.dumps({"type": "error", "content": state["last_error"]}) + "\n"
            return
        for msg in (state.get("messages") or []):
            if hasattr(msg, "content") and msg.content and "[DD]" in msg.content:
                yield json.dumps({"type": "dd_progress", "content": msg.content}) + "\n"
        await asyncio.sleep(0)

        # fetch_schemas_node
        state = _merge_state(state, fetch_schemas_node(state))
        await asyncio.sleep(0)
        if state.get("last_error"):
            yield json.dumps({"type": "error", "content": state["last_error"]}) + "\n"
            return
        for msg in (state.get("messages") or []):
            if hasattr(msg, "content") and msg.content and "[DD]" in msg.content:
                yield json.dumps({"type": "dd_progress", "content": msg.content}) + "\n"
        await asyncio.sleep(0)

        # llm_doc_node
        state = _merge_state(state, llm_doc_node(state))
        await asyncio.sleep(0)

        # synthesizer_node
        state = _merge_state(state, synthesizer_node(state))

        if state.get("last_error"):
            yield json.dumps({"type": "error", "content": state["last_error"]}) + "\n"
            return

        dictionary = state.get("dictionary")
        if dictionary:
            yield json.dumps({"type": "dd_result", "dictionary": dictionary}) + "\n"

        final_answer = state.get("final_answer") or ""
        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"

    except Exception as e:
        logger.error("Data dictionary agent error: %s", e, exc_info=True)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_web_scraper(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    Web Scraper — plain Python ReAct loop, no LangGraph/MemorySaver.
    """
    from backend.tools.web_scraper_tools import make_web_scraper_tools
    from backend.graphs.web_scraper_graph import WEB_SCRAPER_SYSTEM

    try:
        agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
        extra = agent_cfg.get("extra_config", {})
        allowed_urls = extra.get("allowed_urls", [])
        headless = extra.get("headless", True)
        screenshots_dir = extra.get("screenshots_dir", "data/web_scraper_screenshots")
        custom_prompt = agent_cfg.get("system_prompt", "")

        tools = make_web_scraper_tools(
            agent_id=agent_id,
            session_id=session_id,
            allowed_urls=allowed_urls,
            headless=headless,
            screenshots_dir=screenshots_dir,
        )
        llm = build_llm()
        if tools:
            llm = llm.bind_tools(tools)
        tool_map = {t.name: t for t in tools}

        system_content = custom_prompt if custom_prompt else WEB_SCRAPER_SYSTEM
        if allowed_urls:
            system_content += "\n\n## URLs autorisées pour cette session\n" + "\n".join(f"- {u}" for u in allowed_urls)

        messages: list = (
            [SystemMessage(content=system_content)]
            + _load_conversation_history(session_id)
            + [HumanMessage(content=message)]
        )
        final_answer = ""

        for iteration in range(25):
            response = llm.invoke(messages)
            raw_content = response.content or ""
            raw_tool_calls = list(getattr(response, "tool_calls", None) or [])
            messages.append(AIMessage(content=raw_content, tool_calls=raw_tool_calls))

            if not raw_tool_calls:
                final_answer = raw_content
                break

            for tc in raw_tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args") or {}
                tool_call_id = tc.get("id") or f"call_{iteration}_{tool_name}"

                yield json.dumps({"type": "tool_call", "tool": tool_name,
                                  "args": str(tool_args)[:200]}) + "\n"

                fn = tool_map.get(tool_name)
                try:
                    tool_result = fn.invoke(tool_args) if fn else f"Unknown tool: {tool_name}"
                except Exception as exc:
                    tool_result = f"Tool error: {exc}"
                tool_content = str(tool_result) if tool_result is not None else ""
                messages.append(ToolMessage(content=tool_content, tool_call_id=tool_call_id, name=tool_name))

                if "SCREENSHOT_CAPTURED:" in tool_content:
                    lines = tool_content.split("\n")
                    fn_line = next((l for l in lines if l.startswith("SCREENSHOT_CAPTURED:")), "")
                    filename = fn_line.replace("SCREENSHOT_CAPTURED:", "").strip()
                    if filename:
                        yield json.dumps({
                            "type": "screenshot_ready",
                            "filename": filename,
                            "screenshot_url": f"/api/charts/scraper-screenshot/{filename}",
                        }) + "\n"
                await asyncio.sleep(0)

        if not final_answer:
            final_answer = "❌ Impossible de terminer le scraping en moins de 25 itérations."
        yield json.dumps({"type": "final", "content": final_answer}) + "\n"
    except Exception as e:
        logger.error("Web Scraper agent error: %s", e, exc_info=True)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_chart_agent(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """
    Charts & Presentations — plain Python ReAct loop, no LangGraph/MemorySaver.
    """
    from backend.tools.chart_tools import make_chart_tools
    from backend.graphs.chart_graph import CHART_SYSTEM

    try:
        agent_cfg = db.get(COLL_AGENTS, agent_id) or {}
        custom_prompt = agent_cfg.get("system_prompt", "")

        tools = make_chart_tools(session_id=session_id)
        llm = build_llm()
        if tools:
            llm = llm.bind_tools(tools)
        tool_map = {t.name: t for t in tools}

        system_content = custom_prompt if custom_prompt else CHART_SYSTEM
        messages: list = (
            [SystemMessage(content=system_content)]
            + _load_conversation_history(session_id)
            + [HumanMessage(content=message)]
        )
        final_answer = ""

        for iteration in range(20):
            response = llm.invoke(messages)
            raw_content = response.content or ""
            raw_tool_calls = list(getattr(response, "tool_calls", None) or [])
            messages.append(AIMessage(content=raw_content, tool_calls=raw_tool_calls))

            if not raw_tool_calls:
                final_answer = raw_content
                break

            for tc in raw_tool_calls:
                tool_name = tc.get("name", "")
                tool_args = tc.get("args") or {}
                tool_call_id = tc.get("id") or f"call_{iteration}_{tool_name}"

                yield json.dumps({"type": "tool_call", "tool": tool_name,
                                  "args": str(tool_args)[:200]}) + "\n"

                fn = tool_map.get(tool_name)
                try:
                    tool_result = fn.invoke(tool_args) if fn else f"Unknown tool: {tool_name}"
                except Exception as exc:
                    tool_result = f"Tool error: {exc}"
                tool_content = str(tool_result) if tool_result is not None else ""
                messages.append(ToolMessage(content=tool_content, tool_call_id=tool_call_id, name=tool_name))

                if "CHART_CREATED:" in tool_content:
                    lines = tool_content.split("\n")
                    for line in lines:
                        if line.startswith("CHART_CREATED:"):
                            filename = line.replace("CHART_CREATED:", "").strip()
                            yield json.dumps({
                                "type": "chart_ready",
                                "filename": filename,
                                "chart_url": f"/api/charts/image/{filename}",
                            }) + "\n"

                if "PRESENTATION_SAVED:" in tool_content:
                    lines = tool_content.split("\n")
                    fn_line = next((l for l in lines if l.startswith("PRESENTATION_SAVED:")), "")
                    filename = fn_line.replace("PRESENTATION_SAVED:", "").strip()
                    if filename:
                        yield json.dumps({
                            "type": "presentation_ready",
                            "filename": filename,
                            "download_url": f"/api/charts/presentation/{filename}",
                        }) + "\n"
                await asyncio.sleep(0)

        if not final_answer:
            final_answer = "❌ Impossible de générer les graphiques en moins de 20 itérations."
        yield json.dumps({"type": "final", "content": final_answer}) + "\n"
    except Exception as e:
        logger.error("Chart agent error: %s", e, exc_info=True)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


def _get_runner(agent_type: str):
    if agent_type == AgentType.ORCHESTRATOR:
        return _run_orchestrator
    if agent_type == AgentType.DATA_ANALYST:
        return _run_data_analyst
    if agent_type == AgentType.DATA_QUALITY:
        return _run_data_quality
    if agent_type == AgentType.DATA_DICTIONARY:
        return _run_data_dictionary
    if agent_type == AgentType.REPORT_WRITER:
        return _run_report
    if agent_type == AgentType.FILE_MANAGER:
        return _run_file_agent
    if agent_type == AgentType.POWERBI_ANALYST:
        return _run_powerbi_agent
    if agent_type == AgentType.WEB_SCRAPER:
        return _run_web_scraper
    if agent_type == AgentType.CHART_PRESENTER:
        return _run_chart_agent
    return _run_analyst


# ── REST endpoint ─────────────────────────────────────────────────────────────

@router.post("/message")
async def send_message(request: ChatRequest):
    agent = db.get(COLL_AGENTS, request.agent_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    session_id = _get_or_create_session(request.agent_id, request.session_id)
    # Auto-title session from first user message
    session = db.get(COLL_SESSIONS, session_id)
    if session and not session.get("title"):
        db.upsert(COLL_SESSIONS, session_id, {"title": _auto_session_title(request.message)})
    _save_message(session_id, MessageRole.USER, request.message)

    runner = _get_runner(agent.get("type", "orchestrator"))

    if request.stream:
        async def streamer():
            full_response = []
            async for chunk in runner(request.agent_id, session_id, request.message):
                full_response.append(chunk)
                yield chunk
            # Save final answer
            final_chunks = [json.loads(c) for c in full_response if c.strip()]
            final = next((c["content"] for c in reversed(final_chunks) if c.get("type") == "final"), "")
            if final:
                _save_message(session_id, MessageRole.ASSISTANT, final)

        return StreamingResponse(streamer(), media_type="text/event-stream")

    # Non-streaming: collect all
    full_response = []
    async for chunk in runner(request.agent_id, session_id, request.message):
        full_response.append(json.loads(chunk))

    final = next((c["content"] for c in reversed(full_response) if c.get("type") == "final"), "")
    _save_message(session_id, MessageRole.ASSISTANT, final)

    return {
        "session_id": session_id,
        "answer": final,
        "events": full_response,
    }


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/ws/{agent_id}")
async def websocket_chat(websocket: WebSocket, agent_id: str):
    await websocket.accept()
    agent = db.get(COLL_AGENTS, agent_id)
    if not agent:
        await websocket.send_json({"type": "error", "content": "Agent not found"})
        await websocket.close()
        return

    session_id = str(uuid.uuid4())
    runner = _get_runner(agent.get("type", "orchestrator"))

    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            message = payload.get("message", "")

            if not message:
                continue

            # Handle human-in-the-loop resume
            if payload.get("type") == "human_feedback":
                await websocket.send_json({"type": "info", "content": "Resuming from human feedback..."})

            _save_message(session_id, MessageRole.USER, message)

            full_final = []
            async for chunk in runner(agent_id, session_id, message):
                parsed = json.loads(chunk)
                await websocket.send_json(parsed)
                if parsed.get("type") == "final":
                    full_final.append(parsed["content"])

            if full_final:
                _save_message(session_id, MessageRole.ASSISTANT, full_final[-1])

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for agent %s", agent_id)
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass


# ── Session management ────────────────────────────────────────────────────────

@router.get("/sessions/{agent_id}")
def get_sessions(agent_id: str):
    all_sessions = db.get_all(COLL_SESSIONS)
    return [s for s in all_sessions if s.get("agent_id") == agent_id]


@router.get("/sessions/{agent_id}/{session_id}/messages")
def get_messages(agent_id: str, session_id: str):
    return db.get_list(COLL_MESSAGES, session_id)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    db.delete(COLL_SESSIONS, session_id)
    db.delete(COLL_MESSAGES, session_id)
    return {"message": "Session deleted"}
