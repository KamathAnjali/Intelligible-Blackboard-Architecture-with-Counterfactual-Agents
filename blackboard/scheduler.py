"""Round-robin turn scheduling for agents registered on a Blackboard."""

# Turn enforcement applies to entries submitted through Scheduler; direct calls
# to Blackboard.post_entry() bypass the scheduler.
from __future__ import annotations

import queue
import threading
import time
from uuid import uuid4

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

    def __init__(
        self,
        board: Blackboard,
        *,
        session_id: str | None = None,
        max_turns: int | None = None,
    ):
        if max_turns is not None and max_turns < 1:
            raise ValueError("max_turns must be at least 1 or None")
        self.board = board
        self.session_id = session_id if session_id is not None else str(uuid4())
        self.board.session_id = self.session_id
        self.max_turns = max_turns
        self._turn_count = 0
        self._terminal_state: str | None = None
        self._last_agent_id: str | None = None
        self._expected_agent_id: str | None = None
        self._lock = threading.Lock()
        self._notifications: queue.Queue[object] = queue.Queue()
        self._closed = threading.Event()
        self._board_listener = self._on_board_event
        self.board.subscribe(self._board_listener)
        # Prompt an initial state check for agents registered before subscription.
        self._notifications.put(_INITIAL_NOTIFICATION)
        initial_state = self._terminal_state_for_board()
        if initial_state is None:
            initial_state = "running"
        else:
            self._terminal_state = initial_state
        self._emit_scheduler_status(initial_state)

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
            self._raise_if_terminal()
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
            self._turn_count += 1
            self._emit_lifecycle_event(
                BoardEventType.TURN_STARTED,
                agent_id=agent.agent_id,
                turn=self._turn_count,
            )
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

            self._raise_if_terminal()

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
                self._refresh_terminal_state()
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
            self._emit_turn_finished(expected_agent_id, self._turn_count, "posted")
            self._refresh_terminal_state()
            return deadlock

    def finish_turn(self, outcome: str) -> None:
        """Finish a selected turn that did not successfully post an entry.

        The runner calls this with ``no_entry`` when an invocation returns no
        entry, or ``error`` when invoking the agent fails.
        """
        if outcome not in {"no_entry", "error"}:
            raise ValueError("outcome must be 'no_entry' or 'error'")
        with self._lock:
            agent_id = self._expected_agent_id
            if agent_id is None:
                raise RuntimeError("There is no scheduled turn to finish")
            turn = self._turn_count
            self._expected_agent_id = None
            self._emit_turn_finished(agent_id, turn, outcome)
            self._refresh_terminal_state()

    def is_running(self) -> bool:
        self._refresh_terminal_state()
        return (
            self._terminal_state is None
            and self.status is IntelligibilityLevel.UNRESOLVED
        )

    def _raise_if_terminal(self) -> None:
        if self._terminal_state is not None:
            raise RuntimeError(f"Scheduler is {self._terminal_state}")
        status = self.status
        if status is not IntelligibilityLevel.UNRESOLVED:
            self._refresh_terminal_state()
            raise RuntimeError(f"Scheduler is {status.value}")

    def _terminal_state_for_board(self) -> str | None:
        status = self.status
        if status in {IntelligibilityLevel.STRONG, IntelligibilityLevel.ULTRA_STRONG}:
            return "consensus"
        if status is IntelligibilityLevel.DEADLOCKED:
            return "deadlock"
        return None

    def _refresh_terminal_state(self) -> None:
        if self._terminal_state is not None:
            return
        terminal_state = self._terminal_state_for_board()
        if terminal_state is None and self.max_turns is not None:
            if self._turn_count >= self.max_turns and self._expected_agent_id is None:
                terminal_state = "limit"
        if terminal_state is not None:
            self._terminal_state = terminal_state
            self._emit_scheduler_status(terminal_state)

    def _emit_lifecycle_event(self, event_type: BoardEventType, **fields) -> None:
        event = BoardEvent(
            event_type=event_type,
            task_id=self.board.task_id,
            session_id=self.session_id,
            payload={},
            **fields,
        )
        self.board._dispatch_event(event)

    def _emit_turn_finished(self, agent_id: str, turn: int, outcome: str) -> None:
        self._emit_lifecycle_event(
            BoardEventType.TURN_FINISHED,
            agent_id=agent_id,
            turn=turn,
            outcome=outcome,
        )

    def _emit_scheduler_status(self, state: str) -> None:
        agents = [
            {"agent_id": agent.agent_id, "persona": agent.persona}
            for agent in self.board.get_agents().values()
        ]
        self._emit_lifecycle_event(
            BoardEventType.SCHEDULER_STATUS,
            state=state,
            agents=agents,
        )

    def close(self) -> None:
        """Unsubscribe from board events and wake any blocked waiter."""
        if self._closed.is_set():
            return
        self._closed.set()
        self.board.unsubscribe(self._board_listener)
        self._notifications.put(_CLOSE_NOTIFICATION)
