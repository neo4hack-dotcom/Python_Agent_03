"""
Shared LangGraph state definitions.
"""
from typing import Annotated, Any, Dict, List, Optional, Sequence
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class OrchestratorState(TypedDict):
    """State for the orchestrator graph."""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    # Original user request
    user_request: str
    # Decomposed tasks (backlog)
    task_backlog: List[Dict[str, Any]]
    # Currently active task
    current_task: Optional[Dict[str, Any]]
    # Results collected from workers
    worker_results: List[Dict[str, Any]]
    # Final synthesized answer
    final_answer: Optional[str]
    # Human-in-the-loop flag
    awaiting_human: bool
    # Iteration counter (anti-loop)
    iteration: int
    # Max iterations allowed
    max_iterations: int
    # Agent ID being used
    agent_id: str
    # Session metadata
    session_id: str


class AnalystState(TypedDict):
    """State for the ClickHouse/Oracle analyst graph."""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    # User's analytical question
    user_question: str
    # Generated SQL
    generated_sql: Optional[str]
    # Query execution result
    query_result: Optional[Dict[str, Any]]
    # Retry counter
    retry_count: int
    # Max retries
    max_retries: int
    # Last error from DB execution
    last_error: Optional[str]
    # Final narrative answer
    final_answer: Optional[str]
    # Schema context injected for this session
    schema_context: Optional[str]
    # Agent config
    agent_id: str
    session_id: str
