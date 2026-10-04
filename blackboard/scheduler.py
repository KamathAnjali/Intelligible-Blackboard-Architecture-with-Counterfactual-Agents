"""Round-robin turn scheduling for agents registered on a Blackboard."""

# Turn enforcement applies to entries submitted through Scheduler; direct calls
# to Blackboard.post_entry() bypass the scheduler.
from __future__ import annotations

import queue
import threading
import time

from .core import Blackboard
from .models import (
    AgentRecord,
    BoardEntry,
    BoardEvent,
    BoardEventType,
    DeadlockEvent,
    IntelligibilityLevel,
)


_EVENT_NOTIFICATIONS = frozenset(
    {
        BoardEventType.AGENT_REGISTERED,
        BoardEventType.ENTRY_POSTED,
        BoardEventType.INTELLIGIBILITY_CHANGED,
        BoardEventType.DEADLOCK_DETECTED,
    }
)
_INITIAL_NOTIFICATION = object()
_CLOSE_NOTIFICATION = object()


class Scheduler:
    """Select registered agents in round-robin order and enforce their turns.

    The Blackboard owns the agent registry and session outcome. Board events
    wake a single orchestration loop; the caller remains responsible for
    running the selected agent and submitting its entry.
    """

    def __init__(self, board: Blackboard):
        self.board = board
        self._last_agent_id: str | None = None
        self._expected_agent_id: str | None = None
        self._lock = threading.Lock()
        self._notifications: queue.Queue[object] = queue.Queue()
        self._closed = threading.Event()
        self._board_listener = self._on_board_event
        self.board.subscribe(self._board_listener)
        # Prompt an initial state check for agents registered before subscription.
        self._notifications.put(_INITIAL_NOTIFICATION)

    @property
    def status(self) -> IntelligibilityLevel:
        """Expose the Blackboard's canonical terminal-state enum."""
        return self.board.current_intelligibility

    def register_agent(self, agent: AgentRecord) -> None:
        """Register through the Blackboard; no second registry is maintained."""
        self.board.register_agent(agent)

    def _on_board_event(self, event: BoardEvent) -> None:
        """Enqueue scheduling-relevant notifications without taking scheduler locks."""
        if event.event_type in _EVENT_NOTIFICATIONS:
            self._notifications.put(event.event_type)

    def next_agent(self) -> AgentRecord:
        """Select the next active agent immediately, without waiting for an event."""
        with self._lock:
            status = self.status
            if status is not IntelligibilityLevel.UNRESOLVED:
                raise RuntimeError(f"Scheduler is {status.value}")
            if self._expected_agent_id is not None:
                raise RuntimeError(
                    f"Agent {self._expected_agent_id!r} already has the current turn"
                )

            agents = list(self.board.get_agents(active_only=True).values())
            if not agents:
                raise ValueError("No active agents registered on the board")

            agent_ids = [agent.agent_id for agent in agents]
            if self._last_agent_id in agent_ids:
                index = (agent_ids.index(self._last_agent_id) + 1) % len(agents)
            else:
                index = 0

            agent = agents[index]
            self._last_agent_id = agent.agent_id
            self._expected_agent_id = agent.agent_id
            return agent

    def wait_for_next_agent(self, timeout: float | None = None) -> AgentRecord:
        """Wait for board activity, then return the next scheduled active agent.

        With no active agents, wait for registration until ``timeout`` expires.
        ``timeout=None`` waits indefinitely. Raises TimeoutError on timeout and
        RuntimeError if the board is terminal, this scheduler is closed, or a
        previous scheduled turn has not yet been submitted.
        """
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative or None")

        with self._lock:
            if self._expected_agent_id is not None:
                raise RuntimeError(
                    f"Agent {self._expected_agent_id!r} already has the current turn"
                )

        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            if self._closed.is_set():
                raise RuntimeError("Scheduler is closed")

            status = self.status
            if status is not IntelligibilityLevel.UNRESOLVED:
                raise RuntimeError(f"Scheduler is {status.value}")

            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Timed out waiting for an active agent or board event")

            try:
                notification = self._notifications.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(
                    "Timed out waiting for an active agent or board event"
                ) from None

            if notification is _CLOSE_NOTIFICATION or self._closed.is_set():
                raise RuntimeError("Scheduler is closed")

            # Notifications are wake-up hints, not turn tokens. Several board
            # events may describe one already-observable state change, so
            # coalesce signals currently queued before selecting a turn.
            while True:
                try:
                    notification = self._notifications.get_nowait()
                except queue.Empty:
                    break
                if notification is _CLOSE_NOTIFICATION or self._closed.is_set():
                    raise RuntimeError("Scheduler is closed")

            status = self.status
            if status is not IntelligibilityLevel.UNRESOLVED:
                raise RuntimeError(f"Scheduler is {status.value}")

            try:
                # next_agent() checks the current active registry and reserves one turn.
                return self.next_agent()
            except ValueError:
                # No active agents may remain after registration/deactivation events.
                # Wait for another board notification or the caller's timeout.
                continue

    def submit_entry(self, entry: BoardEntry) -> DeadlockEvent | None:
        with self._lock:
            expected_agent_id = self._expected_agent_id
            if expected_agent_id is None:
                raise RuntimeError("Call next_agent() before submitting an entry")
            if entry.agent_id != expected_agent_id:
                raise ValueError(
                    f"out-of-turn entry from {entry.agent_id!r}; "
                    f"expected {expected_agent_id!r}"
                )

            # Board callbacks only enqueue notifications, avoiding lock re-entry.
            # Keep the turn assigned if posting fails so the agent can retry.
            deadlock = self.board.post_entry(entry)
            self._expected_agent_id = None
            return deadlock

    def is_running(self) -> bool:
        return self.status is IntelligibilityLevel.UNRESOLVED

    def close(self) -> None:
        """Unsubscribe from board events and wake any blocked waiter."""
        if self._closed.is_set():
            return
        self._closed.set()
        self.board.unsubscribe(self._board_listener)
        self._notifications.put(_CLOSE_NOTIFICATION)
