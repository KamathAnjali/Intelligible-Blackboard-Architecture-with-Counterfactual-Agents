"""Frozen interface sketch for retrospective replay; no replay implementation.

The source state must be a detached snapshot. Implementations must never mutate
the live board, consume held-out answers, or fabricate evaluator scores.
See docs/rollback-contract.md for cutoff and adoption semantics.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from blackboard.models import BlackboardState, BoardEntry


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
