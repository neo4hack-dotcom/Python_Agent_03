from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


class AgentType(str, Enum):
    ORCHESTRATOR = "orchestrator"
    CLICKHOUSE_ANALYST = "clickhouse_analyst"
    ORACLE_ANALYST = "oracle_analyst"
    DATA_ANALYST = "data_analyst"
    REPORT_WRITER = "report_writer"
    FILE_MANAGER = "file_manager"
    POWERBI_ANALYST = "powerbi_analyst"
    CUSTOM = "custom"


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: AgentType
    description: Optional[str] = None
    connection_id: Optional[str] = Field(None, description="DB connection to use (for analyst agents)")
    toolkit_id: Optional[str] = Field(None, description="Toolkit to use for tool selection/customization")
    system_prompt: Optional[str] = None
    max_retries: int = Field(default=3, ge=1, le=10)
    max_iterations: int = Field(default=10, ge=1, le=20, description="Max consecutive actions for orchestrator")
    row_limit: int = Field(default=1000, ge=1, le=50000, description="Max rows returned from DB queries")
    extra_config: Dict[str, Any] = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    connection_id: Optional[str] = None
    toolkit_id: Optional[str] = None
    system_prompt: Optional[str] = None
    max_retries: Optional[int] = None
    max_iterations: Optional[int] = None
    row_limit: Optional[int] = None
    extra_config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class AgentConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    type: AgentType
    description: Optional[str] = None
    connection_id: Optional[str] = None
    toolkit_id: Optional[str] = Field(None, description="Toolkit to use for tool selection/customization")
    system_prompt: Optional[str] = None
    max_retries: int = 3
    max_iterations: int = 10
    row_limit: int = 1000
    extra_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
