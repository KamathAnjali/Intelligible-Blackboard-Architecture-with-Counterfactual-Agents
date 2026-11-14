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

    def __init__(self, history_limit: int = 256) -> None:
        self._subscribers: list[asyncio.Queue[dict]] = []
        self._history_limit = history_limit
        self._history: list[dict] = []
        self._entry_ids: list[str] = []
        self._entry_id_set: set[str] = set()
        self._last_entry_id: str | None = None

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue[dict]:
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=maxsize)
        # A new UI client needs the session events published before it connected.
        history = self._history[-maxsize:] if maxsize > 0 else self._history
        for event in history:
            q.put_nowait(event)
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
        """Retain and fan out an event (non-blocking; drops for full client queues)."""
        event = dict(event)
        if event.get("type") == "board_entry" and isinstance(event.get("entry"), dict):
            entry_id = event["entry"].get("entry_id")
            if isinstance(entry_id, str) and entry_id not in self._entry_id_set:
                self._entry_id_set.add(entry_id)
                self._entry_ids.append(entry_id)
                self._last_entry_id = entry_id
        if event.get("type") in {
            "connection_ack", "session_started", "session_summary", "session_error",
            "stream_complete", "heartbeat", "board_entry",
        }:
            event.setdefault("entry_count", len(self._entry_ids))
            event.setdefault("last_entry_id", self._last_entry_id)
        self._history.append(event)
        if len(self._history) > self._history_limit:
            del self._history[:-self._history_limit]
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("EventBus: queue full for a subscriber — event dropped.")

    def reset_history(self) -> None:
        """Start a new replay/session without carrying earlier events forward."""
        self._history.clear()
        self._entry_ids.clear()
        self._entry_id_set.clear()
        self._last_entry_id = None

    def snapshot(self) -> dict:
        """Return parity fields for a connection handshake or heartbeat."""
        return {"entry_count": len(self._entry_ids), "last_entry_id": self._last_entry_id}

    async def stream(self, q: asyncio.Queue[dict]) -> AsyncGenerator[dict, None]:
        """Async generator that yields events from a subscriber queue indefinitely."""
        while True:
            event = await q.get()
            yield event


# Process-global singleton
bus = BoardEventBus()
