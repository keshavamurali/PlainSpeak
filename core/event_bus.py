"""Event bus for streaming run updates."""

import asyncio
from collections import deque
from datetime import datetime
from typing import Any


class EventBus:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._subscribers: list[asyncio.Queue] = []
            cls._instance._history: deque = deque(maxlen=50)
        return cls._instance

    async def publish(self, event_type: str, source: str, data: dict[str, Any]) -> None:
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "type": event_type,
            "source": source,
            "data": data,
        }
        self._history.append(event)
        for q in list(self._subscribers):
            try:
                await q.put(event)
            except Exception:
                pass

    async def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self._subscribers.append(q)
        for event in list(self._history)[-5:]:
            await q.put(event)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)


event_bus = EventBus()
