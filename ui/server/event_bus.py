"""
Board Event Bus — Student 4 (UI & Benchmarking)
ui/server/event_bus.py

A lightweight asyncio pub-sub hub that bridges the Blackboard (Student 1/2)
to any number of WebSocket clients.

Architecture
────────────
                                     ┌──────────────────┐
  Blackboard.post_entry()            │   EventBus        │
  (sync, thread-safe)  ──publish()──▶│  (asyncio.Queue   │──▶ WS client 1
                                     │   per subscriber) │──▶ WS client 2
                                     └──────────────────┘

The bus is process-global (one singleton).  A background asyncio task feeds it
from whichever source is active (see board_tap.py).

Consumers call subscribe() / unsubscribe() around a WebSocket connection.
Each subscriber gets its own asyncio.Queue so slow clients cannot block others.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

logger = logging.getLogger("event-bus")


class BoardEventBus:
    """Process-global asyncio pub-sub hub for board events."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict]] = []

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.append(q)
        logger.debug("EventBus: subscriber added  (total=%d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: asyncio.Queue[dict]) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass
        logger.debug("EventBus: subscriber removed (total=%d)", len(self._subscribers))

    async def publish(self, event: dict) -> None:
        """Fan out an event to all current subscribers (non-blocking; drops if full)."""
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("EventBus: queue full for a subscriber — event dropped.")

    async def stream(self, q: asyncio.Queue[dict]) -> AsyncGenerator[dict, None]:
        """Async generator that yields events from a subscriber queue indefinitely."""
        while True:
            event = await q.get()
            yield event


# Process-global singleton
bus = BoardEventBus()
