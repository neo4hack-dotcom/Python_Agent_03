"""
REST API router for the Scheduler feature.

Endpoints:
  GET    /api/scheduler/schedules           List all schedules (enriched with next_run)
  POST   /api/scheduler/schedules           Create a new schedule
  GET    /api/scheduler/schedules/{id}      Get a schedule by ID
  PUT    /api/scheduler/schedules/{id}      Update a schedule
  DELETE /api/scheduler/schedules/{id}      Delete a schedule
  POST   /api/scheduler/schedules/{id}/run  Trigger immediately (manual run)
  GET    /api/scheduler/runs                List recent run logs
  GET    /api/scheduler/runs/{schedule_id}  Run logs for a specific schedule
  GET    /api/scheduler/logs/stream         SSE stream of real-time log events
"""
import asyncio
import json
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from backend.database import db
from backend.models.scheduler import ScheduleCreate, ScheduleConfig, ScheduleUpdate
from backend.services.scheduler_service import scheduler_service, COLL_SCHEDULES, COLL_SCHEDULE_RUNS

router = APIRouter(prefix="/api/scheduler", tags=["Scheduler"])
logger = logging.getLogger(__name__)


# ── Schedules CRUD ────────────────────────────────────────────────────────────

@router.get("/schedules", response_model=List[dict])
def list_schedules():
    """Return all schedules ordered by name."""
    schedules = scheduler_service.list_schedules()
    schedules.sort(key=lambda s: s.get("name", "").lower())
    return schedules


@router.post("/schedules", status_code=201)
def create_schedule(payload: ScheduleCreate):
    """Create and register a new schedule."""
    data = payload.model_dump()
    created = scheduler_service.create_schedule(data)
    return created


@router.get("/schedules/{schedule_id}")
def get_schedule(schedule_id: str):
    s = scheduler_service.get_schedule(schedule_id)
    if not s:
        raise HTTPException(404, detail="Schedule not found")
    return s


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: str, payload: ScheduleUpdate):
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    updated = scheduler_service.update_schedule(schedule_id, updates)
    if not updated:
        raise HTTPException(404, detail="Schedule not found")
    return updated


@router.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str):
    ok = scheduler_service.delete_schedule(schedule_id)
    if not ok:
        raise HTTPException(404, detail="Schedule not found")


@router.post("/schedules/{schedule_id}/run")
async def run_schedule_now(schedule_id: str):
    """Immediately trigger a schedule regardless of its next scheduled time."""
    try:
        run = await scheduler_service.run_now(schedule_id)
        return run
    except ValueError as e:
        raise HTTPException(404, detail=str(e))
    except Exception as e:
        logger.error("Manual run error: %s", e, exc_info=True)
        raise HTTPException(500, detail=str(e))


# ── Run logs ──────────────────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(limit: int = Query(default=100, le=500)):
    """Return the most recent run records across all schedules."""
    return scheduler_service.list_runs(limit=limit)


@router.get("/runs/{schedule_id}")
def list_runs_for_schedule(schedule_id: str, limit: int = Query(default=50, le=200)):
    """Return run records for a specific schedule."""
    s = db.get(COLL_SCHEDULES, schedule_id)
    if not s:
        raise HTTPException(404, detail="Schedule not found")
    return scheduler_service.list_runs(schedule_id=schedule_id, limit=limit)


@router.delete("/runs/{schedule_id}")
def clear_runs(schedule_id: str):
    """Delete all run records for a specific schedule."""
    runs = db.get_all(COLL_SCHEDULE_RUNS)
    deleted = 0
    for r in runs:
        if r.get("schedule_id") == schedule_id:
            db.delete(COLL_SCHEDULE_RUNS, r["id"])
            deleted += 1
    return {"deleted": deleted}


# ── SSE Real-time log stream ──────────────────────────────────────────────────

@router.get("/logs/stream")
async def stream_logs():
    """
    Server-Sent Events stream.
    Emits scheduler run events as they happen in real time.
    """
    q = scheduler_service.subscribe_logs()

    async def generate():
        # Send a heartbeat immediately so the client knows the connection is alive
        yield "data: {\"type\": \"connected\"}\n\n"
        try:
            while True:
                try:
                    run = await asyncio.wait_for(q.get(), timeout=25.0)
                    payload = json.dumps({"type": "run_update", "run": run})
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive
                    yield "data: {\"type\": \"heartbeat\"}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            scheduler_service.unsubscribe_logs(q)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
