"""
Thread-safe Blackboard core.

This is Student 1's main deliverable for Week 1: a place agents can register
themselves and post PXP-tagged entries, with a lock around every mutation so
concurrent agent threads can't corrupt state.

Deliberately NOT doing scheduling/priority logic here — that's a separate
Scheduler class that watches this Blackboard and decides who acts next.
Keeping them separate means Student 1 can unit-test state transitions
independently of scheduling policy.
"""

from __future__ import annotations

import threading
from collections import deque

from .models import (
    AgentRecord,
    BlackboardState,
    BoardEntry,
    DeadlockEvent,
    IntelligibilityLevel,
    PXPTag,
)
from .store import BackingStore, InMemoryJSONStore

# How many consecutive REFUTE/REJECT tags (regardless of which agent) before
# we flag a deadlock. Tune this once you have real multi-agent runs.
DEADLOCK_WINDOW = 4
_NEGATIVE_TAGS = {PXPTag.REFUTE, PXPTag.REJECT}


class Blackboard:
    def __init__(self, task_id: str, store: BackingStore | None = None):
        self.task_id = task_id
        self._store = store or InMemoryJSONStore()
        self._lock = threading.Lock()

        existing = self._store.load(task_id)
        self._state = existing or BlackboardState(task_id=task_id)
        # sliding window of the most recent tags, for deadlock detection
        self._recent_tags: deque[tuple[str, PXPTag]] = deque(maxlen=DEADLOCK_WINDOW)

    # -- registration ---------------------------------------------------

    def register_agent(self, agent: AgentRecord) -> None:
        with self._lock:
            self._state.agents[agent.agent_id] = agent
            self._store.save(self._state)

    # -- posting ----------------------------------------------------------

    def post_entry(self, entry: BoardEntry) -> DeadlockEvent | None:
        """
        Validate + append an entry. Returns a DeadlockEvent if this post
        triggers a deadlock condition, else None.

        Raises ValueError if the entry references an unknown agent or an
        unknown target_entry_id — fail loudly rather than silently accepting
        malformed state (this is effectively the "PXP grammatical parser"
        gate for anything that gets this far).
        """
        with self._lock:
            if entry.agent_id not in self._state.agents:
                raise ValueError(f"unknown agent_id={entry.agent_id!r} — register before posting")

            if entry.target_entry_id is not None:
                known_ids = {e.entry_id for e in self._state.entries}
                if entry.target_entry_id not in known_ids:
                    raise ValueError(f"target_entry_id={entry.target_entry_id!r} does not exist on the board")

            self._state.entries.append(entry)
            self._recent_tags.append((entry.agent_id, entry.tag))

            deadlock = self._check_deadlock(entry)
            self._recompute_intelligibility()
            self._store.save(self._state)
            return deadlock

    # -- reads --------------------------------------------------------------

    def get_state(self) -> BlackboardState:
        with self._lock:
            return self._state.model_copy(deep=True)

    def get_history(self, agent_id: str | None = None) -> list[BoardEntry]:
        with self._lock:
            if agent_id is None:
                return list(self._state.entries)
            return [e for e in self._state.entries if e.agent_id == agent_id]

    # -- internal classification logic --------------------------------------

    def _check_deadlock(self, latest: BoardEntry) -> DeadlockEvent | None:
        if len(self._recent_tags) < DEADLOCK_WINDOW:
            return None
        if all(tag in _NEGATIVE_TAGS for _, tag in self._recent_tags):
            involved = list({agent_id for agent_id, _ in self._recent_tags})
            event = DeadlockEvent(
                triggered_at_entry_id=latest.entry_id,
                loop_length=len(self._recent_tags),
                involved_agent_ids=involved,
            )
            self._state.deadlocks.append(event)
            self._state.intelligibility = IntelligibilityLevel.DEADLOCKED
            return event
        return None

    def _recompute_intelligibility(self) -> None:
        """
        Very first-pass heuristic — replace with something more principled
        once you have real session data:
          - all RATIFY at the end -> consensus
          - consensus reached with at least one productive REVISE along the
            way -> ULTRA_STRONG
          - consensus reached with no REVISE (agents just happened to agree,
            or agreed after REFUTE/REJECT friction) -> STRONG
        """
        if self._state.intelligibility == IntelligibilityLevel.DEADLOCKED:
            return
        if not self._state.entries:
            return

        tail_len = min(len(self._state.entries), len(self._state.agents) or 1)
        tail = self._state.entries[-tail_len:]
        if tail and all(e.tag == PXPTag.RATIFY for e in tail):
            had_revise = any(e.tag == PXPTag.REVISE for e in self._state.entries)
            self._state.intelligibility = (
                IntelligibilityLevel.ULTRA_STRONG if had_revise else IntelligibilityLevel.STRONG
            )
