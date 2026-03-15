"""
Agent management endpoints — CRUD for agent configurations.
"""
from typing import List
from fastapi import APIRouter, HTTPException
from datetime import datetime

from backend.models.agent import AgentConfig, AgentCreate, AgentUpdate
from backend.database import db, COLL_AGENTS

router = APIRouter(prefix="/api/agents", tags=["Agents"])

# Default system prompt templates per agent type
DEFAULT_PROMPTS = {
    "orchestrator": (
        "You are an expert orchestrator. Decompose tasks, delegate to specialists, "
        "synthesize results into clear, actionable answers."
    ),
    "clickhouse_analyst": (
        "You are a ClickHouse SQL expert. Generate optimized, read-only SQL queries "
        "following best practices: explicit columns, partition key filtering, native functions."
    ),
    "oracle_analyst": (
        "You are an Oracle SQL expert. Generate optimized, read-only SQL queries "
        "with proper indexing hints and Oracle-specific functions."
    ),
    "data_analyst": (
        "You are a senior data analyst and business intelligence expert. "
        "Perform statistical analysis, data profiling, trend analysis, KPI computation, "
        "and deliver clear, data-driven business insights with actionable recommendations."
    ),
    "report_writer": (
        "You are a senior consultant specializing in professional report writing. "
        "Transform analysis results into comprehensive, well-structured PDF reports "
        "with executive summary, methodology, findings, and actionable recommendations."
    ),
    "custom": "You are a helpful AI assistant.",
}


@router.get("", response_model=List[AgentConfig])
def list_agents():
    return [AgentConfig(**a) for a in db.get_all(COLL_AGENTS)]


@router.post("", response_model=AgentConfig, status_code=201)
def create_agent(payload: AgentCreate):
    agent = AgentConfig(**payload.model_dump())
    # Inject default system prompt if none provided
    if not agent.system_prompt:
        agent.system_prompt = DEFAULT_PROMPTS.get(agent.type.value, DEFAULT_PROMPTS["custom"])
    db.set(COLL_AGENTS, agent.id, agent.model_dump())
    return agent


@router.get("/{agent_id}", response_model=AgentConfig)
def get_agent(agent_id: str):
    raw = db.get(COLL_AGENTS, agent_id)
    if not raw:
        raise HTTPException(404, "Agent not found")
    return AgentConfig(**raw)


@router.put("/{agent_id}", response_model=AgentConfig)
def update_agent(agent_id: str, payload: AgentUpdate):
    existing = db.get(COLL_AGENTS, agent_id)
    if not existing:
        raise HTTPException(404, "Agent not found")
    updated = dict(existing)
    for k, v in payload.model_dump(exclude_none=True).items():
        updated[k] = v
    updated["updated_at"] = datetime.utcnow().isoformat()
    db.set(COLL_AGENTS, agent_id, updated)
    return AgentConfig(**updated)


@router.delete("/{agent_id}")
def delete_agent(agent_id: str):
    if not db.delete(COLL_AGENTS, agent_id):
        raise HTTPException(404, "Agent not found")
    return {"message": "Deleted"}


@router.get("/{agent_id}/template")
def get_agent_template(agent_id: str):
    """Return the full system prompt + configuration for an agent."""
    raw = db.get(COLL_AGENTS, agent_id)
    if not raw:
        raise HTTPException(404, "Agent not found")
    return {
        "agent_id": agent_id,
        "name": raw.get("name"),
        "type": raw.get("type"),
        "system_prompt": raw.get("system_prompt"),
        "max_retries": raw.get("max_retries", 3),
        "row_limit": raw.get("row_limit", 1000),
    }
