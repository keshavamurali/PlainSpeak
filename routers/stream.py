"""Stream router - SSE for run updates."""

import asyncio
import json
from fastapi import APIRouter, Request, HTTPException
from sse_starlette.sse import EventSourceResponse

from core.event_bus import event_bus
from routers.runs import _run_to_response
from session.manager import SessionManager
from shared.state import active_sessions

router = APIRouter(tags=["Stream"])
session_manager = SessionManager()


@router.get("/events")
async def event_stream(request: Request):
    """Server-Sent Events endpoint for real-time run updates."""
    queue = await event_bus.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                event = await queue.get()
                yield {"event": "message", "data": json.dumps(event)}
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(queue)

    return EventSourceResponse(event_generator())


def _get_run_data(run_id: str) -> dict | None:
    """Fetch run data for a given run_id."""
    if run_id in active_sessions:
        info = active_sessions[run_id]
        if isinstance(info, dict):
            saved = session_manager.load_session(run_id)
            if saved:
                saved["session_id"] = run_id
                saved["pending_clarification"] = saved.get("pending_clarification") or info.get("pending_clarification")
                return _run_to_response(saved)
            merged = {
                "session_id": run_id,
                "query": info.get("query", ""),
                "status": "waiting_input" if info.get("pending_clarification") else info.get("status", "running"),
                "created_at": info.get("created_at", ""),
                "state": {"query": info.get("query", "")},
                "globals_schema": info.get("globals_schema", {}),
                "artifacts": info.get("artifacts", {}),
                "runs": info.get("runs", []),
                "pending_clarification": info.get("pending_clarification"),
                "error": info.get("error"),
            }
            return _run_to_response(merged)
    data = session_manager.load_session(run_id)
    if data is None:
        return None
    data["session_id"] = run_id
    return _run_to_response(data)


@router.get("/runs/{run_id}/stream")
async def run_stream(request: Request, run_id: str):
    """SSE stream of run updates for a specific run."""
    initial = _get_run_data(run_id)
    if initial is None:
        raise HTTPException(status_code=404, detail="Run not found")

    queue = await event_bus.subscribe()

    async def event_generator():
        try:
            yield {"event": "message", "data": json.dumps(initial)}
            while True:
                if await request.is_disconnected():
                    break
                event = await asyncio.wait_for(queue.get(), timeout=5.0)
                source = event.get("source") == run_id
                data_run_id = (event.get("data") or {}).get("session_id") == run_id
                if source or data_run_id:
                    data = _get_run_data(run_id)
                    if data:
                        yield {"event": "message", "data": json.dumps(data)}
        except asyncio.TimeoutError:
            data = _get_run_data(run_id)
            if data:
                yield {"event": "message", "data": json.dumps(data)}
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(queue)

    return EventSourceResponse(event_generator())
