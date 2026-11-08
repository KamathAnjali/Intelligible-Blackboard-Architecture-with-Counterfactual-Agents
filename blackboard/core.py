"""
Thread-safe Blackboard core with Event Emitter hooks and Rollback Support.

Key capabilities:
- Thread-safe agent registration and PXP-tagged entry posting with validation.
- Board Event Emitter hooks publishing state deltas for the WebSocket/UI layer (Week 2).
- Robust Intelligibility Classification (Strong vs. Ultra-Strong) preventing single-agent impersonation.
- Retrospective Rollback & History Slicing for Student 3's counterfactual sandbox.
"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import Callable

from .models import (
    AgentRecord,
    BlackboardState,
    BoardEntry,
    BoardEvent,
    BoardEventType,
    CounterfactualResult,
    DeadlockEvent,
    IntelligibilityLevel,
    PXPTag,
    TrialMetrics,
)
from .store import BackingStore, InMemoryJSONStore

# Consecutive REFUTE/REJECT tags required to flag a deadlock
DEADLOCK_WINDOW = 4
_NEGATIVE_TAGS = {PXPTag.REFUTE, PXPTag.REJECT}


class Blackboard:
    """Thread-safe PXP Blackboard."""

    def __init__(self, task_id: str, store: BackingStore | None = None):
        self.task_id = task_id
        self._store = store or InMemoryJSONStore()
        self._lock = threading.RLock()
        self._subscribers: list[Callable[[BoardEvent], None]] = []

        existing = self._store.load(task_id)
        self._state = existing or BlackboardState(task_id=task_id)
        # sliding window of the most recent tags for deadlock detection
        self._recent_tags: deque[tuple[str, PXPTag]] = deque(maxlen=DEADLOCK_WINDOW)

        # Re-populate recent tags if loading an existing state
        if existing and existing.entries:
            for entry in existing.entries[-DEADLOCK_WINDOW:]:
                self._recent_tags.append((entry.agent_id, entry.tag))

    # -- event emitter hooks (Week 2 Observability) ----------------------

    def subscribe(self, listener: Callable[[BoardEvent], None]) -> None:
        """Register a callback listener to receive real-time state delta events."""
        with self._lock:
            if listener not in self._subscribers:
                self._subscribers.append(listener)

    def unsubscribe(self, listener: Callable[[BoardEvent], None]) -> None:
        """Remove a registered callback listener."""
        with self._lock:
            if listener in self._subscribers:
                self._subscribers.remove(listener)

    def _emit(self, event_type: BoardEventType, payload: dict) -> None:
        """Internal dispatch of state delta events to all registered subscribers."""
        event = BoardEvent(
            event_type=event_type,
            task_id=self.task_id,
            payload=payload,
        )
        for listener in list(self._subscribers):
            try:
                listener(event)
            except Exception:
                # Listener exceptions must never abort core blackboard state operations
                pass

    # -- registration ---------------------------------------------------

    def register_agent(self, agent: AgentRecord) -> None:
        """Register an agent in the blackboard state registry and notify subscribers."""
        with self._lock:
            self._state.agents[agent.agent_id] = agent
            self._store.save(self._state)

        self._emit(BoardEventType.AGENT_REGISTERED, {"agent": agent.model_dump(mode="json")})

    # -- posting ----------------------------------------------------------

    def post_entry(self, entry: BoardEntry) -> DeadlockEvent | None:
        """
        Validate + append an entry. Returns a DeadlockEvent if this post
        triggers a deadlock condition, else None.

        Raises ValueError if the entry references an unknown agent or an
        unknown target_entry_id.
        """
        intelligibility_changed = False
        prev_intelligibility = None
        new_intelligibility = None
        deadlock = None

        with self._lock:
            if entry.agent_id not in self._state.agents:
                raise ValueError(f"unknown agent_id={entry.agent_id!r} — register before posting")

            if entry.target_entry_id is not None:
                known_ids = {e.entry_id for e in self._state.entries}
                if entry.target_entry_id not in known_ids:
                    raise ValueError(f"target_entry_id={entry.target_entry_id!r} does not exist on the board")

            self._state.entries.append(entry)
            self._recent_tags.append((entry.agent_id, entry.tag))

            prev_intelligibility = self._state.intelligibility
            deadlock = self._check_deadlock(entry)
            self._recompute_intelligibility(entry)
            new_intelligibility = self._state.intelligibility

            if prev_intelligibility != new_intelligibility:
                intelligibility_changed = True

            self._store.save(self._state)

        # Emit events outside lock to prevent deadlock with external handlers
        self._emit(BoardEventType.ENTRY_POSTED, {"entry": entry.model_dump(mode="json")})

        if deadlock:
            self._emit(BoardEventType.DEADLOCK_DETECTED, {"deadlock": deadlock.model_dump(mode="json")})

        if intelligibility_changed:
            self._emit(BoardEventType.INTELLIGIBILITY_CHANGED, {
                "previous": prev_intelligibility.value if prev_intelligibility else None,
                "current": new_intelligibility.value if new_intelligibility else None,
            })

        return deadlock

    # -- reads --------------------------------------------------------------

    def get_state(self) -> BlackboardState:
        with self._lock:
            return self._state.model_copy(deep=True)

    @property
    def current_intelligibility(self) -> IntelligibilityLevel:
        """Return the session outcome without copying full history."""
        with self._lock:
            return self._state.intelligibility

    def get_agents(self, *, active_only: bool = False) -> dict[str, AgentRecord]:
        """Return a safe copy of the registry."""
        with self._lock:
            return {
                agent_id: agent.model_copy(deep=True)
                for agent_id, agent in self._state.agents.items()
                if not active_only or agent.active
            }

    def get_history(self, agent_id: str | None = None) -> list[BoardEntry]:
        with self._lock:
            if agent_id is None:
                return list(self._state.entries)
            return [e for e in self._state.entries if e.agent_id == agent_id]

    # -- persistence & snapshot hooks (Week 2) -----------------------------

    def save_snapshot(self, tag: str | None = None):
        """Save a snapshot to disk and emit SNAPSHOT_SAVED event."""
        if hasattr(self._store, "snapshot_to_disk"):
            path = self._store.snapshot_to_disk(self.task_id, tag=tag)
            self._emit(BoardEventType.SNAPSHOT_SAVED, {"path": str(path), "tag": tag})
            return path
        return None

    # -- rollback & counterfactual sandbox support (Student 3 sync) --------

    def get_history_slice(
        self,
        cutoff_entry_id: str | None = None,
        include_counterfactual: bool = False,
    ) -> list[BoardEntry]:
        """
        Return a chronological slice of the board history up to and including cutoff_entry_id.
        If include_counterfactual is False, sandbox entries are filtered out to preserve live history purity.
        """
        with self._lock:
            entries = self._state.entries
            if not include_counterfactual:
                entries = [e for e in entries if not e.is_counterfactual_sim]

            if cutoff_entry_id is None:
                return [e.model_copy(deep=True) for e in entries]

            sliced = []
            found = False
            for e in entries:
                sliced.append(e.model_copy(deep=True))
                if e.entry_id == cutoff_entry_id:
                    found = True
                    break

            if not found:
                raise ValueError(f"cutoff_entry_id={cutoff_entry_id!r} not found in history")
            return sliced

    def fork_simulation_blackboard(
        self,
        cutoff_entry_id: str,
        sim_task_id: str | None = None,
    ) -> Blackboard:
        """
        Fork an isolated sandbox Blackboard containing state up to cutoff_entry_id.
        This provides Student 3 with the exact history slice needed for counterfactual replay
        without mutating the live board history.
        """
        with self._lock:
            history_slice = self.get_history_slice(cutoff_entry_id=cutoff_entry_id, include_counterfactual=False)
            task_name = sim_task_id or f"{self.task_id}_sim_{cutoff_entry_id[:8]}"
            sim_state = BlackboardState(
                task_id=task_name,
                entries=history_slice,
                agents={aid: a.model_copy(deep=True) for aid, a in self._state.agents.items()},
                intelligibility=IntelligibilityLevel.UNRESOLVED,
            )
            sim_store = InMemoryJSONStore()
            sim_store.save(sim_state)
            sim_board = Blackboard(task_id=task_name, store=sim_store)

        self._emit(BoardEventType.ROLLBACK_FORKED, {
            "cutoff_entry_id": cutoff_entry_id,
            "sim_task_id": task_name,
            "entries_count": len(history_slice),
        })
        return sim_board

    def record_counterfactual_result(self, result: CounterfactualResult) -> None:
        """Attach a completed counterfactual simulation outcome to the board state."""
        with self._lock:
            self._state.counterfactual_events.append(result)
            self._store.save(self._state)

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

    def _recompute_intelligibility(self, latest: BoardEntry | None = None) -> None:
        """
        Refined Intelligibility Classification (Week 2 & Week 3 Deadlock Recovery):
        
        Rules:
        1. If already DEADLOCKED, status is preserved unless a constructive tag
           (PXPTag.REVISE or PXPTag.RATIFY) is posted to initiate recovery.
        2. Consensus requires at least 2 active agents registered.
        3. Every active agent must have submitted at least one entry.
        4. The most recent entry from EACH active agent must be PXPTag.RATIFY.
        5. All active agents must agree on the same prediction.
        6. Repeated agreement messages from a single agent CANNOT impersonate agreement by all agents.
        
        Classification Tiers:
        - ULTRA_STRONG: Unanimous consensus reached where the session involved productive
          revisions (at least one PXPTag.REVISE in history), demonstrating problem-solving refinement.
        - STRONG: Unanimous consensus reached directly without any PXPTag.REVISE in history.
        """
        if self._state.intelligibility == IntelligibilityLevel.DEADLOCKED:
            if latest is not None and latest.tag in {PXPTag.REVISE, PXPTag.RATIFY}:
                self._state.intelligibility = IntelligibilityLevel.UNRESOLVED
            else:
                return

        if not self._state.entries:
            return

        active_agent_ids = {aid for aid, a in self._state.agents.items() if a.active}
        if len(active_agent_ids) < 2:
            return

        is_simulation_board = "sim" in self.task_id.lower()

        # Find the most recent entry from each active agent
        latest_by_agent: dict[str, BoardEntry] = {}
        for entry in self._state.entries:
            if entry.agent_id in active_agent_ids and (is_simulation_board or not entry.is_counterfactual_sim):
                latest_by_agent[entry.agent_id] = entry

        # All active agents must have posted at least once
        if len(latest_by_agent) < len(active_agent_ids):
            return

        # Every active agent's latest entry must be RATIFY
        all_ratified = all(e.tag == PXPTag.RATIFY for e in latest_by_agent.values())
        if not all_ratified:
            return

        # Every active agent's latest ratified prediction must match
        predictions = {e.prediction.strip().lower() for e in latest_by_agent.values()}
        if len(predictions) != 1:
            return

        # Check if productive REVISE occurred in the history
        had_revise = any(
            e.tag == PXPTag.REVISE for e in self._state.entries
            if (is_simulation_board or not e.is_counterfactual_sim)
        )
        self._state.intelligibility = (
            IntelligibilityLevel.ULTRA_STRONG if had_revise else IntelligibilityLevel.STRONG
        )

    # -- trial logging & metrics (Week 3 Deliverable) ----------------------

    def get_trial_metrics(self) -> TrialMetrics:
        """
        Aggregate session outcome, iteration count, deadlock occurrences,
        and counterfactual simulation metrics for trial run reporting.
        """
        with self._lock:
            live_entries = [e for e in self._state.entries if not e.is_counterfactual_sim]
            converged = self._state.intelligibility in {
                IntelligibilityLevel.STRONG,
                IntelligibilityLevel.ULTRA_STRONG,
            }
            start_time = live_entries[0].timestamp if live_entries else None
            end_time = live_entries[-1].timestamp if live_entries else None
            duration = (end_time - start_time).total_seconds() if (start_time and end_time) else 0.0

            return TrialMetrics(
                task_id=self.task_id,
                converged=converged,
                intelligibility=self._state.intelligibility,
                iteration_count=len(live_entries),
                deadlock_count=len(self._state.deadlocks),
                deadlock_flag=len(self._state.deadlocks) > 0,
                counterfactual_attempts=len(self._state.counterfactual_events),
                counterfactual_applied=any(
                    cf.applied_to_live for cf in self._state.counterfactual_events
                ),
                participating_agents=sorted(list(self._state.agents.keys())),
                start_time=start_time,
                end_time=end_time,
                duration_seconds=max(0.0, duration),
            )

    def log_trial_run(self, filepath: str | Path | None = None) -> TrialMetrics:
        """
        Log the completed trial run metrics, optionally persist to disk as JSON,
        and emit a TRIAL_COMPLETED event for external observers.
        """
        metrics = self.get_trial_metrics()

        target_path: Path | None = None
        if filepath:
            target_path = Path(filepath)
        elif hasattr(self._store, "snapshot_dir"):
            target_path = Path(self._store.snapshot_dir) / f"{self.task_id}_trial_metrics.json"

        if target_path:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(metrics.model_dump_json(indent=2))

        self._emit(BoardEventType.TRIAL_COMPLETED, {
            "metrics": metrics.model_dump(mode="json"),
            "path": str(target_path) if target_path else None,
        })
        return metrics
