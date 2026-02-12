"""Runs router - create runs, list, get, stream."""

import asyncio
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from shared.state import get_multi_mcp, active_sessions
from agents.runner import AgentRunner
from context.execution_context import ExecutionContext
from session.manager import SessionManager
from core.event_bus import event_bus

router = APIRouter(tags=["Runs"])
session_manager = SessionManager()


class RunRequest(BaseModel):
    query: str


class RunResponse(BaseModel):
    id: str
    status: str
    created_at: str
    query: str


async def process_run(run_id: str, query: str) -> None:
    """Background task: run agent pipeline and publish events."""
    mcp = get_multi_mcp()
    runner = AgentRunner(multi_mcp=mcp)
    ctx = ExecutionContext.create(session_id=run_id, query=query)
    ctx.set_state("query", query)
    ctx.globals_schema["original_query"] = query

    try:
        active_sessions[run_id] = {"status": "running", "query": query}
        await event_bus.publish("run_started", run_id, {"run_id": run_id, "query": query})

        def save_progress(ctx):
            d = ctx.to_dict()
            d["status"] = "running"
            d["query"] = query
            session_manager.save_session(run_id, d)

        loop = asyncio.get_event_loop()
        ctx = await asyncio.to_thread(
            runner.run,
            ctx=ctx,
            event_loop=loop,
            on_step_complete=save_progress,
        )

        await event_bus.publish("run_completed", run_id, {"run_id": run_id})
        data = ctx.to_dict()
        data["status"] = "completed"
        data["query"] = query
        session_manager.save_session(run_id, data)

    except Exception as e:
        await event_bus.publish("run_failed", run_id, {"run_id": run_id, "error": str(e)})
        data = ctx.to_dict()
        data["status"] = "failed"
        data["query"] = query
        data["error"] = str(e)
        session_manager.save_session(run_id, data)
    finally:
        if run_id in active_sessions:
            del active_sessions[run_id]


def _run_to_response(data: dict) -> dict:
    """Format run/session data for API response."""
    run_id = data.get("session_id", data.get("id", ""))
    return {
        "id": run_id,
        "status": data.get("status", "unknown"),
        "created_at": data.get("created_at", ""),
        "query": data.get("query", data.get("state", {}).get("query", "")),
        "artifacts": data.get("artifacts", {}),
        "runs": data.get("runs", []),
        "error": data.get("error"),
    }


@router.post("/runs")
async def create_run(req: RunRequest, background_tasks: BackgroundTasks):
    """Create a new run and start agent pipeline in background."""
    run_id = str(int(datetime.utcnow().timestamp() * 1000))[-10:]
    active_sessions[run_id] = {"status": "starting", "query": req.query}
    background_tasks.add_task(process_run, run_id, req.query)
    return {
        "id": run_id,
        "status": "starting",
        "created_at": datetime.utcnow().isoformat(),
        "query": req.query,
    }


@router.get("/runs")
async def list_runs(limit: int = 50):
    """List recent runs (sessions)."""
    sessions = session_manager.list_sessions(limit=limit)
    # Merge with active runs
    result = []
    seen = set()
    for rid, info in active_sessions.items():
        if isinstance(info, dict) and rid not in seen:
            seen.add(rid)
            result.append({
                "id": rid,
                "status": info.get("status", "running"),
                "created_at": datetime.utcnow().isoformat(),
                "query": info.get("query", ""),
            })
    for s in sessions:
        sid = s.get("session_id", "").replace("session_", "")
        if sid not in seen:
            seen.add(sid)
            result.append({
                "id": sid,
                "status": s.get("status", "completed"),
                "created_at": s.get("created_at", ""),
                "query": s.get("query", ""),
            })
    result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return result[:limit]


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    """Get run status and context."""
    if run_id in active_sessions:
        info = active_sessions[run_id]
        if isinstance(info, dict):
            # Try to load saved progress if any
            saved = session_manager.load_session(run_id)
            if saved:
                return _run_to_response(saved)
            return {
                "id": run_id,
                "status": info.get("status", "running"),
                "created_at": info.get("created_at", datetime.utcnow().isoformat()),
                "query": info.get("query", ""),
                "artifacts": {},
                "runs": [],
            }
    data = session_manager.load_session(run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_response(data)
