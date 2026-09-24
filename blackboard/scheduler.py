"""Round-robin turn scheduling for agents registered on a Blackboard."""

# Turn enforcement applies to entries submitted through Scheduler; direct calls
# to Blackboard.post_entry() bypass the scheduler.
from __future__ import annotations

import threading

from .core import Blackboard
from .models import AgentRecord, BoardEntry, DeadlockEvent, IntelligibilityLevel


class Scheduler:
    """Select registered agents in round-robin order and enforce their turns.

    The Blackboard owns the agent registry and session outcome. The scheduler
    keeps only turn bookkeeping, so registering an agent on the board makes it
    immediately available to the next scheduling decision.
    """

    def __init__(self, board: Blackboard):
        self.board = board
        self._last_agent_id: str | None = None
        self._expected_agent_id: str | None = None
        self._lock = threading.Lock()

    @property
    def status(self) -> IntelligibilityLevel:
        """Expose the Blackboard's canonical terminal-state enum."""
        return self.board.current_intelligibility

    def register_agent(self, agent: AgentRecord) -> None:
        """Register through the Blackboard; no second registry is maintained."""
        self.board.register_agent(agent)

    def next_agent(self) -> AgentRecord:
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

            # Keep the turn assigned if posting fails so the scheduled agent can
            # correct and retry its entry.
            deadlock = self.board.post_entry(entry)
            self._expected_agent_id = None
            return deadlock

    def is_running(self) -> bool:
        return self.status is IntelligibilityLevel.UNRESOLVED
