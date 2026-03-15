"""
Pydantic models for the Scheduler feature.
"""
from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid


class TriggerType(str, Enum):
    CRON      = "cron"           # Fixed time (hour/minute/day_of_week)
    INTERVAL  = "interval"       # Every N minutes / hours / days
    FILE_WATCH = "file_watch"    # New file matching pattern in directory
    SQL_CONDITION = "sql_condition"  # SQL query result meets condition


class ScheduleStatus(str, Enum):
    ACTIVE    = "active"
    PAUSED    = "paused"


class RunStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR   = "error"
    SKIPPED = "skipped"  # Condition not met (file_watch / sql_condition)


# ── Trigger configs ───────────────────────────────────────────────────────────

class CronConfig(BaseModel):
    """Run at a fixed time using cron-style fields."""
    minute: str = "0"          # 0-59 or */N or list
    hour: str = "9"            # 0-23 or */N or list
    day: str = "*"             # 1-31 or */N
    month: str = "*"           # 1-12 or */N
    day_of_week: str = "*"     # 0-6 (mon=0) or mon-fri


class IntervalConfig(BaseModel):
    """Run every N time units."""
    days: int = 0
    hours: int = 0
    minutes: int = 5
    seconds: int = 0


class FileWatchConfig(BaseModel):
    """Poll a directory for new files matching a pattern."""
    directory: str             # Absolute path to watch
    pattern: str = "*"         # Glob pattern (e.g. "*.csv", "data_*.xlsx")
    check_interval_minutes: int = 5
    consume: bool = True       # Move processed file to ./processed/ sub-dir


class SqlConditionConfig(BaseModel):
    """Run a SQL query and trigger if condition matches."""
    connection_id: str         # Which DB connection to use
    query: str                 # SELECT returning a single numeric value
    operator: str = ">"        # ==, !=, >, >=, <, <=
    threshold: float = 0       # Comparison value
    check_interval_minutes: int = 15


# ── Main schedule model ───────────────────────────────────────────────────────

class ScheduleCreate(BaseModel):
    name: str
    description: str = ""
    agent_id: str              # Agent to invoke
    message: str               # Message/instruction sent to the agent
    trigger_type: TriggerType
    trigger_config: Dict[str, Any]   # One of the *Config dicts above
    status: ScheduleStatus = ScheduleStatus.ACTIVE


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    agent_id: Optional[str] = None
    message: Optional[str] = None
    trigger_type: Optional[TriggerType] = None
    trigger_config: Optional[Dict[str, Any]] = None
    status: Optional[ScheduleStatus] = None


class ScheduleConfig(BaseModel):
    id: str
    name: str
    description: str = ""
    agent_id: str
    message: str
    trigger_type: TriggerType
    trigger_config: Dict[str, Any]
    status: ScheduleStatus = ScheduleStatus.ACTIVE
    created_at: str = ""
    updated_at: str = ""
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    run_count: int = 0


# ── Run log model ─────────────────────────────────────────────────────────────

class ScheduleRun(BaseModel):
    id: str
    schedule_id: str
    schedule_name: str
    agent_id: str
    started_at: str
    completed_at: Optional[str] = None
    status: RunStatus = RunStatus.RUNNING
    output: Optional[str] = None
    trigger_reason: str = "scheduled"   # "scheduled", "manual", "condition_met"
    error: Optional[str] = None
