"""In-process pub/sub and the global SSE firehose."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


ALLOWED_EVENTS = frozenset(
    {
        "bot_updated",
        "feed_updated",
        "approval_pending",
        "approval_resolved",
        "routine_fired",
        "integration_updated",
        "mcp_updated",
    }
)


def sse_frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'), default=str)}\n\n"


class EventBus:
    def __init__(self):
        self._subscribers: set[asyncio.Queue[tuple[str, dict[str, Any]]]] = set()

    async def publish(self, event: str, data: dict[str, Any]) -> None:
        if event not in ALLOWED_EVENTS:
            raise ValueError(f"Unsupported global event: {event}")
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:
                # A firehose event is a refresh hint; dropping an old hint is safe.
                try:
                    queue.get_nowait()
                    queue.put_nowait((event, data))
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def stream(self, *, heartbeat_seconds: float = 15.0) -> AsyncIterator[str]:
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            while True:
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                    yield sse_frame(event, data)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self._subscribers.discard(queue)
