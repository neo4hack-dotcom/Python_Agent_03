"""
Scheduler service — wraps APScheduler to manage and execute scheduled agent jobs.

Supports four trigger types:
  - cron      : Fixed time (e.g. daily at 09:00, weekdays at 08:30)
  - interval  : Every N minutes / hours / days
  - file_watch: Polls a directory for new files matching a glob pattern
  - sql_condition: Polls a SQL query result and fires when condition is met

On startup, loads all ACTIVE schedules from the database and registers them.
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.database import db
from backend.database.json_db import COLL_AGENTS, COLL_CONNECTIONS

logger = logging.getLogger(__name__)

COLL_SCHEDULES    = "schedules"
COLL_SCHEDULE_RUNS = "schedule_runs"

# Maximum number of run records to keep per schedule in the DB
_MAX_RUNS_PER_SCHEDULE = 200
# How many recent global runs to keep for the SSE log feed
_MAX_GLOBAL_RUNS = 500


class SchedulerService:
    """Singleton service that owns the APScheduler instance."""

    def __init__(self):
        self._scheduler = AsyncIOScheduler(
            job_defaults={"coalesce": True, "max_instances": 1},
            timezone="UTC",
        )
        # SSE log subscribers: list of asyncio.Queue
        self._log_queues: List[asyncio.Queue] = []
        self._started = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self):
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        logger.info("SchedulerService started")
        await self._load_active_schedules()

    async def shutdown(self):
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False

    # ── CRUD helpers ─────────────────────────────────────────────────────────

    def create_schedule(self, schedule: dict) -> dict:
        schedule["id"] = schedule.get("id") or str(uuid.uuid4())
        schedule.setdefault("status", "active")
        schedule.setdefault("run_count", 0)
        schedule.setdefault("last_run", None)
        schedule.setdefault("next_run", None)
        now = datetime.now(timezone.utc).isoformat()
        schedule.setdefault("created_at", now)
        schedule["updated_at"] = now
        db.set(COLL_SCHEDULES, schedule["id"], schedule)
        if schedule["status"] == "active":
            self._register_job(schedule)
        return schedule

    def update_schedule(self, schedule_id: str, updates: dict) -> Optional[dict]:
        schedule = db.get(COLL_SCHEDULES, schedule_id)
        if not schedule:
            return None
        schedule.update(updates)
        schedule["updated_at"] = datetime.now(timezone.utc).isoformat()
        db.set(COLL_SCHEDULES, schedule_id, schedule)
        # Re-register job (handles status change active↔paused)
        self._unregister_job(schedule_id)
        if schedule.get("status") == "active":
            self._register_job(schedule)
        return schedule

    def delete_schedule(self, schedule_id: str) -> bool:
        self._unregister_job(schedule_id)
        return db.delete(COLL_SCHEDULES, schedule_id)

    def get_schedule(self, schedule_id: str) -> Optional[dict]:
        s = db.get(COLL_SCHEDULES, schedule_id)
        if s:
            s = self._enrich_next_run(s)
        return s

    def list_schedules(self) -> List[dict]:
        schedules = db.get_all(COLL_SCHEDULES)
        return [self._enrich_next_run(s) for s in schedules]

    def list_runs(self, schedule_id: Optional[str] = None, limit: int = 100) -> List[dict]:
        runs = db.get_all(COLL_SCHEDULE_RUNS)
        if schedule_id:
            runs = [r for r in runs if r.get("schedule_id") == schedule_id]
        runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
        return runs[:limit]

    # ── Manual trigger ────────────────────────────────────────────────────────

    async def run_now(self, schedule_id: str) -> dict:
        """Immediately execute a schedule regardless of its trigger."""
        schedule = db.get(COLL_SCHEDULES, schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id!r} not found")
        return await self._execute_schedule(schedule_id, trigger_reason="manual")

    # ── SSE log subscription ──────────────────────────────────────────────────

    def subscribe_logs(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._log_queues.append(q)
        return q

    def unsubscribe_logs(self, q: asyncio.Queue):
        try:
            self._log_queues.remove(q)
        except ValueError:
            pass

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _load_active_schedules(self):
        schedules = db.get_all(COLL_SCHEDULES)
        count = 0
        for s in schedules:
            if s.get("status") == "active":
                try:
                    self._register_job(s)
                    count += 1
                except Exception as exc:
                    logger.warning("Could not register schedule %s: %s", s.get("id"), exc)
        logger.info("Loaded %d active schedule(s)", count)

    def _build_trigger(self, schedule: dict):
        tt = schedule.get("trigger_type")
        tc = schedule.get("trigger_config", {})

        if tt == "cron":
            return CronTrigger(
                minute=str(tc.get("minute", "0")),
                hour=str(tc.get("hour", "9")),
                day=str(tc.get("day", "*")),
                month=str(tc.get("month", "*")),
                day_of_week=str(tc.get("day_of_week", "*")),
                timezone="UTC",
            )
        elif tt == "interval":
            d = int(tc.get("days", 0))
            h = int(tc.get("hours", 0))
            m = int(tc.get("minutes", 5))
            s = int(tc.get("seconds", 0))
            total_seconds = d * 86400 + h * 3600 + m * 60 + s
            if total_seconds <= 0:
                total_seconds = 300  # fallback 5 min
            return IntervalTrigger(seconds=total_seconds, timezone="UTC")
        elif tt in ("file_watch", "sql_condition"):
            minutes = int(tc.get("check_interval_minutes", 5))
            return IntervalTrigger(minutes=max(1, minutes), timezone="UTC")
        else:
            raise ValueError(f"Unknown trigger type: {tt!r}")

    def _register_job(self, schedule: dict):
        sid = schedule["id"]
        try:
            trigger = self._build_trigger(schedule)
            self._scheduler.add_job(
                self._execute_schedule,
                trigger=trigger,
                id=sid,
                args=[sid],
                replace_existing=True,
                misfire_grace_time=300,
            )
            logger.info("Registered job %s (%s)", sid, schedule.get("name"))
        except Exception as exc:
            logger.error("Failed to register job %s: %s", sid, exc)

    def _unregister_job(self, schedule_id: str):
        try:
            self._scheduler.remove_job(schedule_id)
        except Exception:
            pass

    def _enrich_next_run(self, schedule: dict) -> dict:
        """Add next_run timestamp from the live APScheduler job."""
        sid = schedule.get("id", "")
        try:
            job = self._scheduler.get_job(sid)
            if job and job.next_run_time:
                schedule["next_run"] = job.next_run_time.isoformat()
            else:
                schedule["next_run"] = None
        except Exception:
            schedule["next_run"] = None
        return schedule

    def _emit_log(self, run: dict):
        """Push a run event to all SSE subscribers (non-blocking)."""
        for q in list(self._log_queues):
            try:
                q.put_nowait(run.copy())
            except asyncio.QueueFull:
                pass

    # ── Condition checkers ────────────────────────────────────────────────────

    def _check_file_condition(self, schedule: dict) -> Optional[str]:
        """Returns the first matching file path if a new file exists, else None."""
        tc = schedule.get("trigger_config", {})
        directory = tc.get("directory", "")
        pattern = tc.get("pattern", "*")
        if not directory:
            return None

        search = os.path.join(directory, pattern)
        matches = sorted(glob.glob(search))
        if not matches:
            return None

        # Exclude files in ./processed/ sub-dir (already handled)
        processed_dir = os.path.join(directory, "processed")
        fresh = [f for f in matches if not f.startswith(processed_dir)]
        return fresh[0] if fresh else None

    async def _check_sql_condition(self, schedule: dict) -> bool:
        """Returns True if the SQL condition evaluates to True."""
        tc = schedule.get("trigger_config", {})
        connection_id = tc.get("connection_id", "")
        query = tc.get("query", "")
        operator = tc.get("operator", ">")
        threshold = float(tc.get("threshold", 0))

        if not connection_id or not query:
            return False

        conn = db.get(COLL_CONNECTIONS, connection_id)
        if not conn:
            logger.warning("SQL condition: connection %s not found", connection_id)
            return False

        try:
            value = await asyncio.to_thread(self._run_sql_scalar, conn, query)
            if value is None:
                return False
            value = float(value)
            ops = {
                "==": value == threshold,
                "!=": value != threshold,
                ">":  value >  threshold,
                ">=": value >= threshold,
                "<":  value <  threshold,
                "<=": value <= threshold,
            }
            result = ops.get(operator, False)
            logger.debug("SQL condition: %.2f %s %.2f → %s", value, operator, threshold, result)
            return result
        except Exception as exc:
            logger.warning("SQL condition check failed: %s", exc)
            return False

    @staticmethod
    def _run_sql_scalar(conn: dict, query: str) -> Optional[float]:
        """Execute a scalar SQL query synchronously. Returns first cell or None."""
        conn_type = conn.get("type", "")
        if conn_type == "clickhouse":
            import clickhouse_connect
            client = clickhouse_connect.get_client(
                host=conn["host"],
                port=int(conn.get("port", 8123)),
                database=conn.get("database", "default"),
                username=conn.get("username", "default"),
                password=conn.get("password", ""),
            )
            result = client.query(query)
            if result.result_rows:
                return result.result_rows[0][0]
        elif conn_type == "oracle":
            import oracledb
            dsn = f"{conn['host']}:{conn.get('port', 1521)}/{conn['database']}"
            with oracledb.connect(
                user=conn["username"],
                password=conn["password"],
                dsn=dsn,
            ) as connection:
                cursor = connection.cursor()
                cursor.execute(query)
                row = cursor.fetchone()
                if row:
                    return row[0]
        return None

    # ── Main execution ────────────────────────────────────────────────────────

    async def _execute_schedule(self, schedule_id: str, trigger_reason: str = "scheduled") -> dict:
        schedule = db.get(COLL_SCHEDULES, schedule_id)
        if not schedule:
            return {"status": "error", "error": "Schedule not found"}
        if schedule.get("status") != "active" and trigger_reason != "manual":
            return {"status": "skipped", "error": "Schedule is paused"}

        # Check condition-based triggers
        tt = schedule.get("trigger_type")
        matched_file = None
        if tt == "file_watch" and trigger_reason != "manual":
            matched_file = self._check_file_condition(schedule)
            if not matched_file:
                return {"status": "skipped"}
            if trigger_reason == "scheduled":
                trigger_reason = f"file_detected:{os.path.basename(matched_file)}"

        elif tt == "sql_condition" and trigger_reason != "manual":
            if not await self._check_sql_condition(schedule):
                return {"status": "skipped"}
            if trigger_reason == "scheduled":
                trigger_reason = "condition_met"

        # Create run record
        run_id = str(uuid.uuid4())
        run = {
            "id": run_id,
            "schedule_id": schedule_id,
            "schedule_name": schedule.get("name", ""),
            "agent_id": schedule.get("agent_id", ""),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "status": "running",
            "output": None,
            "trigger_reason": trigger_reason,
            "error": None,
        }
        db.set(COLL_SCHEDULE_RUNS, run_id, run)
        self._emit_log(run.copy())

        # Build message (inject matched file if applicable)
        message = schedule.get("message", "")
        if matched_file:
            message = f"{message}\n\n[Fichier détecté : {matched_file}]"

        # Execute agent
        try:
            output = await self._invoke_agent(schedule["agent_id"], message)
            run["status"] = "success"
            run["output"] = output
        except Exception as exc:
            logger.error("Schedule %s execution error: %s", schedule_id, exc, exc_info=True)
            run["status"] = "error"
            run["error"] = str(exc)
            run["output"] = f"❌ Erreur : {exc}"

        run["completed_at"] = datetime.now(timezone.utc).isoformat()
        db.set(COLL_SCHEDULE_RUNS, run_id, run)

        # Update schedule metadata
        db.upsert(COLL_SCHEDULES, schedule_id, {
            "last_run": run["completed_at"],
            "run_count": (schedule.get("run_count") or 0) + 1,
        })

        # Move consumed file
        if matched_file and schedule.get("trigger_config", {}).get("consume", True):
            try:
                processed = os.path.join(
                    os.path.dirname(matched_file), "processed",
                    os.path.basename(matched_file),
                )
                os.makedirs(os.path.dirname(processed), exist_ok=True)
                os.rename(matched_file, processed)
            except Exception as exc:
                logger.warning("Could not move processed file: %s", exc)

        # Prune old run records
        self._prune_runs(schedule_id)

        self._emit_log(run.copy())
        return run

    def _prune_runs(self, schedule_id: str):
        """Keep only the N most recent runs per schedule."""
        runs = [r for r in db.get_all(COLL_SCHEDULE_RUNS)
                if r.get("schedule_id") == schedule_id]
        if len(runs) > _MAX_RUNS_PER_SCHEDULE:
            runs.sort(key=lambda r: r.get("started_at", ""))
            for old in runs[:-_MAX_RUNS_PER_SCHEDULE]:
                db.delete(COLL_SCHEDULE_RUNS, old["id"])

    async def _invoke_agent(self, agent_id: str, message: str) -> str:
        """
        Invoke an agent and return its final response as a string.
        Uses the existing chat streaming pipeline but collects the output.
        """
        from backend.graphs.orchestrator_graph import build_orchestrator_graph
        from backend.graphs.analyst_graph import build_analyst_graph
        from backend.graphs.file_graph import build_file_graph
        from backend.database.json_db import COLL_AGENTS
        from langchain_core.messages import HumanMessage

        agent_cfg = db.get(COLL_AGENTS, agent_id)
        if not agent_cfg:
            raise ValueError(f"Agent {agent_id!r} not found")

        agent_type = agent_cfg.get("type", "orchestrator")
        thread_id = f"scheduler_{uuid.uuid4().hex[:8]}"

        # Map agent type to the appropriate graph
        if agent_type == "orchestrator":
            graph = build_orchestrator_graph()
            state = {
                "messages": [HumanMessage(content=message)],
                "user_request": message,
                "worker_results": [],
                "task_backlog": [],
                "current_task": None,
                "final_answer": None,
                "awaiting_human": False,
                "iteration": 0,
                "agent_id": agent_id,
                "session_id": thread_id,
            }
        elif agent_type in ("clickhouse_analyst", "oracle_analyst"):
            from backend.graphs.orchestrator_graph import _run_analyst_subtask
            result = await asyncio.to_thread(_run_analyst_subtask, agent_id, message)
            return result.get("result", "")
        elif agent_type == "file_manager":
            graph = build_file_graph()
            state = {
                "messages": [HumanMessage(content=message)],
                "user_request": message,
                "final_answer": None,
                "iteration_count": 0,
                "agent_id": agent_id,
                "session_id": thread_id,
            }
        else:
            # Generic: use orchestrator graph
            graph = build_orchestrator_graph()
            state = {
                "messages": [HumanMessage(content=message)],
                "user_request": message,
                "worker_results": [],
                "task_backlog": [],
                "current_task": None,
                "final_answer": None,
                "awaiting_human": False,
                "iteration": 0,
                "agent_id": agent_id,
                "session_id": thread_id,
            }

        config = {"configurable": {"thread_id": thread_id}}
        final_state = await asyncio.to_thread(graph.invoke, state, config)
        return final_state.get("final_answer") or "Tâche terminée."


# ── Module-level singleton ────────────────────────────────────────────────────
scheduler_service = SchedulerService()
