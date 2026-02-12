"""Stream router - SSE for run updates."""

import asyncio
import json
from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from core.event_bus import event_bus

router = APIRouter(tags=["Stream"])


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
