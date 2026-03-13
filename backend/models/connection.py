from enum import Enum
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import uuid


class ConnectionType(str, Enum):
    CLICKHOUSE = "clickhouse"
    ORACLE = "oracle"


class ConnectionCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: ConnectionType
    host: str
    port: int
    database: str
    username: str
    password: str
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None


class ConnectionConfig(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    type: ConnectionType
    host: str
    port: int
    database: str
    username: str
    password: str  # stored as-is (local app; no secrets manager needed)
    extra_params: Dict[str, Any] = Field(default_factory=dict)
    description: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    is_active: bool = True
