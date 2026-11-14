"""
Backing store for blackboard state.

Supports:
- In-memory dict storage for ultra-fast local state mutations during active sessions.
- JSON snapshot persistence to disk for debugging, replay, and crash recovery.
- Snapshot listing and step-by-step replay trace generation for the UI inspector.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

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
    Keeps live state in a memory dict (thread-safe synchronization handled by Blackboard)
    and provides disk snapshotting and replay capabilities.
    """

    def __init__(self, snapshot_dir: str | Path = "./snapshots"):
        self._states: dict[str, BlackboardState] = {}
        self.snapshot_dir = Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

    def load(self, task_id: str) -> BlackboardState | None:
        return self._states.get(task_id)

    def save(self, state: BlackboardState) -> None:
        self._states[state.task_id] = state

    # -- snapshot & replay features (Week 1 foundation + Week 2 enhancements) --

    def snapshot_to_disk(self, task_id: str, tag: str | None = None) -> Path:
        """
        Persist the current in-memory blackboard state to disk as JSON.
        If `tag` is provided, creates a tagged snapshot (e.g. `task_id_step3.json`).
        Otherwise, writes to the primary snapshot file `task_id.json`.
        """
        state = self._states.get(task_id)
        if state is None:
            raise KeyError(f"no in-memory state for task_id={task_id!r}")
        
        filename = f"{task_id}_{tag}.json" if tag else f"{task_id}.json"
        path = self.snapshot_dir / filename
        path.write_text(state.model_dump_json(indent=2))
        return path

    def load_snapshot_from_disk(self, task_id: str, filepath: str | Path | None = None) -> BlackboardState:
        """
        Load a BlackboardState from a JSON snapshot on disk.
        If filepath is not provided, defaults to `<snapshot_dir>/<task_id>.json`.
        """
        path = Path(filepath) if filepath else self.snapshot_dir / f"{task_id}.json"
        data = json.loads(path.read_text())
        state = BlackboardState.model_validate(data)
        self._states[task_id] = state
        return state

    def list_snapshots(self, task_id: str | None = None) -> list[Path]:
        """
        Return a list of snapshot paths matching task_id, or all snapshots if task_id is None.
        """
        pattern = f"{task_id}*.json" if task_id else "*.json"
        return sorted(self.snapshot_dir.glob(pattern))

    def export_replay_trace(self, task_id: str) -> list[dict[str, Any]]:
        """
        Generate a step-by-step state replay trace up to the current entry count.
        Useful for the UI history inspector to step forward/backward through a session.
        """
        state = self._states.get(task_id)
        if state is None:
            raise KeyError(f"no in-memory state for task_id={task_id!r}")

        trace = []
        entries = state.entries
        for i in range(1, len(entries) + 1):
            sub_entries = entries[:i]
            trace.append({
                "step": i,
                "entry": sub_entries[-1].model_dump(mode="json"),
                "total_entries": i,
                "current_agent": sub_entries[-1].agent_id,
                "tag": sub_entries[-1].tag.value,
            })
        return trace
