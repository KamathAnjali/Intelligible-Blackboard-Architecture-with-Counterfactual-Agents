"""Isolated history preparation and interfaces for retrospective replay.

The source state must be a detached snapshot. Implementations must never mutate
the live board, consume held-out answers, or fabricate evaluator scores.
See docs/rollback-contract.md for cutoff and adoption semantics.
"""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import uuid4

from blackboard.core import Blackboard
from blackboard.models import BlackboardState, BoardEntry
from blackboard.store import InMemoryJSONStore


@dataclass(frozen=True)
class RollbackRequest:
    task: str
    source_state: BlackboardState
    cutoff_entry_id: str
    target_agent_id: str
    alternative_instruction: str
    max_replay_turns: int = 6
    max_alternatives: int = 1
    max_retries: int = 2


@dataclass(frozen=True)
class RollbackOutcome:
    original_entry_id: str
    outcome: Literal["consensus", "deadlocked", "turn_limit", "error"]
    candidate_entry: BoardEntry | None = None
    replayed_entries: tuple[BoardEntry, ...] = ()
    delta_score: float | None = None
    score_name: str | None = None
    failure_reason: str | None = None


class CounterfactualSandbox(Protocol):
    def simulate(self, request: RollbackRequest) -> RollbackOutcome:
        """Return an isolated replay result; live adoption belongs to the caller."""
        ...


def create_sandbox(request: RollbackRequest) -> Blackboard:
    """Copy the live prefix strictly before the contribution being replaced.

    The returned board has its own store and agent copies. It contains no
    simulated entries, deadlock records or counterfactual results from the
    source. Generating an alternative and replaying turns are separate steps.
    """
    source = request.source_state.model_copy(deep=True)
    live_entries = [entry for entry in source.entries if not entry.is_counterfactual_sim]
    matches = [i for i, entry in enumerate(live_entries) if entry.entry_id == request.cutoff_entry_id]
    if len(matches) != 1:
        raise ValueError("cutoff_entry_id must identify exactly one live entry")
    index = matches[0]
    target = live_entries[index]
    agent = source.agents.get(request.target_agent_id)
    if agent is None or not agent.active or target.agent_id != request.target_agent_id:
        raise ValueError("target_agent_id must be the active author of the cutoff entry")

    store = InMemoryJSONStore()
    store.save(source)
    source_board = Blackboard(source.task_id, store)
    previous_id = live_entries[index - 1].entry_id if index else None
    return source_board.fork_simulation_blackboard(
        cutoff_entry_id=previous_id,
        sim_task_id=f"{source.task_id}_sim_{uuid4().hex}",
    )
