"""
Backing store for blackboard state.

Week 1 decision: plain in-memory dict + JSON snapshot-to-disk. No Redis yet —
add a RedisStore later that implements the same BackingStore interface if/when
you need multi-process sharing or persistence beyond a single run.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from .models import BlackboardState


class BackingStore(ABC):
    """Minimal interface every backing store implementation must satisfy."""

    @abstractmethod
    def load(self, task_id: str) -> BlackboardState | None:
        ...

    @abstractmethod
    def save(self, state: BlackboardState) -> None:
        ...


class InMemoryJSONStore(BackingStore):
    """
    Keeps live state in a dict (fast, thread-safe access handled by the
    Blackboard class, not here) and can snapshot/restore to a JSON file
    for debugging, replay, or crash recovery.
    """

    def __init__(self, snapshot_dir: str | Path = "./snapshots"):
        self._states: dict[str, BlackboardState] = {}
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def load(self, task_id: str) -> BlackboardState | None:
        return self._states.get(task_id)

    def save(self, state: BlackboardState) -> None:
        self._states[state.task_id] = state

    # -- snapshot helpers (not part of the abstract interface, but handy) ----

    def snapshot_to_disk(self, task_id: str) -> Path:
        state = self._states.get(task_id)
        if state is None:
            raise KeyError(f"no in-memory state for task_id={task_id!r}")
        path = self.snapshot_dir / f"{task_id}.json"
        path.write_text(state.model_dump_json(indent=2))
        return path

    def load_snapshot_from_disk(self, task_id: str) -> BlackboardState:
        path = self.snapshot_dir / f"{task_id}.json"
        data = json.loads(path.read_text())
        state = BlackboardState.model_validate(data)
        self._states[task_id] = state
        return state
