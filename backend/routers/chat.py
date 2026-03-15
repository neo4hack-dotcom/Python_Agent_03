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
from langchain_core.messages import HumanMessage, AIMessage

from backend.database import db, COLL_AGENTS, COLL_SESSIONS, COLL_MESSAGES
from backend.graphs.orchestrator_graph import build_orchestrator_graph
from backend.graphs.analyst_graph import build_analyst_graph
from backend.graphs.data_analyst_graph import build_data_analyst_graph
from backend.graphs.report_graph import build_report_graph
from backend.graphs.file_graph import build_file_graph
from backend.graphs.powerbi_graph import build_powerbi_graph
from backend.models.agent import AgentType
from backend.models.chat import ChatRequest, ChatMessage, ChatSession, MessageRole

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["Chat"])

# Cache compiled graphs (expensive to rebuild each call)
_orchestrator_graph = None
_analyst_graph = None
_data_analyst_graph = None
_report_graph = None
_file_graph = None
_powerbi_graph = None


def _get_powerbi_agent():
    global _powerbi_graph
    if _powerbi_graph is None:
        _powerbi_graph = build_powerbi_graph()
    return _powerbi_graph


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
        "awaiting_human": False,
        "iteration": 0,
        "max_iterations": agent_cfg.get("max_iterations", 10),
        "agent_id": agent_id,
        "session_id": session_id,
    }

    try:
        async for event in graph.astream(initial_state, config=config, stream_mode="values"):
            msgs = event.get("messages", [])
            if msgs:
                last = msgs[-1]
                if hasattr(last, "content") and last.content:
                    yield json.dumps({"type": "token", "content": last.content}) + "\n"
                    await asyncio.sleep(0)

        # Final state
        final_state = graph.get_state(config)
        final_answer = final_state.values.get("final_answer", "")
        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"
    except Exception as e:
        logger.error("Orchestrator error: %s", e)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_analyst(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    graph = _get_analyst()
    config = {"configurable": {"thread_id": session_id}}
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}

    history = _load_conversation_history(session_id)
    initial_state = {
        "messages": history + [HumanMessage(content=message)],
        "user_question": message,
        "generated_sql": None,
        "query_result": None,
        "retry_count": 0,
        "max_retries": agent_cfg.get("max_retries", 3),
        "last_error": None,
        "final_answer": None,
        "schema_context": None,
        "agent_id": agent_id,
        "session_id": session_id,
        "iteration_count": 0,
    }

    try:
        async for event in graph.astream(initial_state, config=config, stream_mode="values"):
            msgs = event.get("messages", [])
            if msgs:
                last = msgs[-1]
                if hasattr(last, "content") and last.content:
                    yield json.dumps({"type": "token", "content": last.content}) + "\n"
                    await asyncio.sleep(0)

            # Emit SQL when generated
            sql = event.get("generated_sql")
            if sql:
                yield json.dumps({"type": "sql", "content": sql}) + "\n"

            # Emit query result metadata
            qr = event.get("query_result")
            if qr and qr.get("success"):
                yield json.dumps({
                    "type": "query_result",
                    "row_count": qr.get("row_count", 0),
                    "columns": qr.get("columns", []),
                    "rows": qr.get("rows", []),
                    "sql": event.get("generated_sql", ""),
                    "warning": qr.get("warning"),
                }) + "\n"
                await asyncio.sleep(0)

        final_state = graph.get_state(config)
        final_answer = final_state.values.get("final_answer", "")
        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"
    except Exception as e:
        logger.error("Analyst error: %s", e)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_data_analyst(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """Runner SSE pour l'agent analyste de données."""
    graph = _get_data_analyst()
    config = {"configurable": {"thread_id": session_id}}
    agent_cfg = db.get(COLL_AGENTS, agent_id) or {}

    # Pré-chargement du schéma de la DB si l'agent a une connexion configurée
    from backend.graphs.data_analyst_graph import _auto_schema_context
    schema_context = _auto_schema_context(agent_id, message)

    history = _load_conversation_history(session_id)
    initial_state = {
        "messages": history,
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

    try:
        async for event in graph.astream(initial_state, config=config, stream_mode="values"):
            msgs = event.get("messages", [])
            if msgs:
                last = msgs[-1]
                if hasattr(last, "content") and last.content:
                    yield json.dumps({"type": "token", "content": last.content}) + "\n"
                    await asyncio.sleep(0)

            # Emit data results metadata when SQL is executed
            data_results = event.get("data_results")
            if data_results:
                for r in data_results:
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

        final_state = graph.get_state(config)
        final_answer = final_state.values.get("final_answer", "")
        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"
    except Exception as e:
        logger.error("Data analyst error: %s", e)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_report(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """Runner SSE pour l'agent rédacteur de rapports PDF."""
    graph = _get_report()
    config = {"configurable": {"thread_id": f"report_{session_id}"}}

    # Charger l'historique de la session pour contexte
    history = db.get_list(COLL_MESSAGES, session_id)
    context_lines = []
    for msg in history[-30:]:  # max 30 derniers messages
        role = "Utilisateur" if msg.get("role") == "user" else "Assistant"
        content = msg.get("content", "")
        if content:
            context_lines.append(f"**{role}** : {content}")
    session_context = "\n\n".join(context_lines)

    initial_state = {
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

    try:
        async for event in graph.astream(initial_state, config=config, stream_mode="values"):
            msgs = event.get("messages", [])
            if msgs:
                last = msgs[-1]
                if hasattr(last, "content") and last.content:
                    yield json.dumps({"type": "token", "content": last.content}) + "\n"
                    await asyncio.sleep(0)

        final_state = graph.get_state(config)
        vals = final_state.values
        final_answer = vals.get("final_answer", "")
        report_id = vals.get("report_id")

        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"

        # Émettre l'événement pdf_ready pour que le frontend affiche le bouton de téléchargement
        if report_id:
            yield json.dumps({
                "type": "pdf_ready",
                "report_id": report_id,
                "download_url": f"/api/report/{report_id}/download",
                "filename": f"rapport_analyse_{report_id[:8]}.pdf",
            }) + "\n"
    except Exception as e:
        logger.error("Report writer error: %s", e)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_file_agent(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """Runner SSE pour l'agent File Manager."""
    graph = _get_file_agent()
    config = {"configurable": {"thread_id": session_id}}

    history = _load_conversation_history(session_id)
    initial_state = {
        "messages": history + [HumanMessage(content=message)],
        "user_request": message,
        "final_answer": None,
        "iteration_count": 0,
        "agent_id": agent_id,
        "session_id": session_id,
    }

    try:
        async for event in graph.astream(initial_state, config=config, stream_mode="values"):
            msgs = event.get("messages", [])
            if msgs:
                last = msgs[-1]
                if hasattr(last, "content") and last.content:
                    content = last.content
                    # Detect confirmation requests — emit special event for frontend
                    if "CONFIRMATION REQUISE" in content or "confirmed=False" in content:
                        yield json.dumps({"type": "human_validation", "content": content}) + "\n"
                    else:
                        yield json.dumps({"type": "token", "content": content}) + "\n"
                    await asyncio.sleep(0)
                # Emit tool calls as log events
                if hasattr(last, "tool_calls") and last.tool_calls:
                    for tc in last.tool_calls:
                        yield json.dumps({
                            "type": "tool_call",
                            "tool": tc["name"],
                            "args": str(tc.get("args", {}))[:200],
                        }) + "\n"
                        await asyncio.sleep(0)

        final_state = graph.get_state(config)
        final_answer = final_state.values.get("final_answer", "")
        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"
    except Exception as e:
        logger.error("File agent error: %s", e)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


async def _run_powerbi_agent(agent_id: str, session_id: str, message: str) -> AsyncGenerator[str, None]:
    """Runner SSE pour l'agent Power BI Analyst (Playwright)."""
    graph = _get_powerbi_agent()
    config = {"configurable": {"thread_id": session_id}}

    history = _load_conversation_history(session_id)
    initial_state = {
        "messages": history + [HumanMessage(content=message)],
        "user_request": message,
        "final_answer": None,
        "iteration_count": 0,
        "agent_id": agent_id,
        "session_id": session_id,
    }

    try:
        async for event in graph.astream(initial_state, config=config, stream_mode="values"):
            msgs = event.get("messages", [])
            if msgs:
                last = msgs[-1]
                if hasattr(last, "content") and last.content:
                    content = last.content
                    # Detect screenshot captures — emit screenshot_ready event for frontend display
                    if "SCREENSHOT_CAPTURED:" in content:
                        lines = content.split("\n")
                        filename_line = next(
                            (l for l in lines if l.startswith("SCREENSHOT_CAPTURED:")), ""
                        )
                        filename = filename_line.replace("SCREENSHOT_CAPTURED:", "").strip()
                        if filename:
                            yield json.dumps({
                                "type": "screenshot_ready",
                                "filename": filename,
                                "screenshot_url": f"/api/powerbi/screenshot/{filename}",
                            }) + "\n"
                        # Also emit the rest of the tool output as a token (minus the base64)
                        clean = "\n".join(
                            l for l in lines
                            if not l.startswith("SCREENSHOT_CAPTURED:")
                            and not l.startswith("BASE64_IMAGE:")
                        ).strip()
                        if clean:
                            yield json.dumps({"type": "token", "content": clean}) + "\n"
                    else:
                        yield json.dumps({"type": "token", "content": content}) + "\n"
                    await asyncio.sleep(0)
                # Emit tool calls as log events
                if hasattr(last, "tool_calls") and last.tool_calls:
                    for tc in last.tool_calls:
                        yield json.dumps({
                            "type": "tool_call",
                            "tool": tc["name"],
                            "args": str(tc.get("args", {}))[:200],
                        }) + "\n"
                        await asyncio.sleep(0)

        final_state = graph.get_state(config)
        final_answer = final_state.values.get("final_answer", "")
        if final_answer:
            yield json.dumps({"type": "final", "content": final_answer}) + "\n"
    except Exception as e:
        logger.error("PowerBI agent error: %s", e)
        yield json.dumps({"type": "error", "content": str(e)}) + "\n"


def _get_runner(agent_type: str):
    if agent_type == AgentType.ORCHESTRATOR:
        return _run_orchestrator
    if agent_type == AgentType.DATA_ANALYST:
        return _run_data_analyst
    if agent_type == AgentType.REPORT_WRITER:
        return _run_report
    if agent_type == AgentType.FILE_MANAGER:
        return _run_file_agent
    if agent_type == AgentType.POWERBI_ANALYST:
        return _run_powerbi_agent
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
