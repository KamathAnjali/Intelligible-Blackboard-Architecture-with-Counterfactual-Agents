"""Isolated history preparation and interfaces for retrospective replay.

The source state must be a detached snapshot. Implementations must never mutate
the live board, consume held-out answers, or fabricate evaluator scores.
See docs/rollback-contract.md for cutoff and adoption semantics.
"""

from dataclasses import dataclass
import json
from typing import Literal, Protocol
from uuid import uuid4

from blackboard.core import Blackboard
from blackboard.models import BlackboardState, BoardEntry, IntelligibilityLevel, PXPTag
from blackboard.scheduler import Scheduler
from blackboard.store import InMemoryJSONStore
from agents.llm_client import inference_usage


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
    review_evidence: list | None = None
    rejected_proposal: BoardEntry | None = None


@dataclass(frozen=True)
class RollbackOutcome:
    original_entry_id: str
    outcome: Literal["consensus", "deadlocked", "turn_limit", "error"]
    candidate_entry: BoardEntry | None = None
    replayed_entries: tuple[BoardEntry, ...] = ()
    delta_score: float | None = None
    score_name: str | None = None
    failure_reason: str | None = None
    attempts: tuple[dict, ...] = ()
    usage: dict | None = None


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
    proposal = request.rejected_proposal
    if proposal is not None and (
        target.tag != PXPTag.REJECT
        or proposal.tag != PXPTag.REVISE
        or proposal.agent_id != target.agent_id
        or proposal.prediction != target.prediction
        or proposal.is_counterfactual_sim
    ):
        raise ValueError("rejected_proposal must match the live rejection and its author")

    store = InMemoryJSONStore()
    store.save(source)
    source_board = Blackboard(source.task_id, store)
    previous_id = live_entries[index - 1].entry_id if index else None
    return source_board.fork_simulation_blackboard(
        cutoff_entry_id=previous_id,
        sim_task_id=f"{source.task_id}_sim_{uuid4().hex}",
    )


def consensus_progress(state: BlackboardState) -> float:
    """Largest matching RATIFY group divided by the number of active agents."""
    active = {agent_id for agent_id, agent in state.agents.items() if agent.active}
    if len(active) < 2:
        raise ValueError("consensus progress requires at least two active agents")
    latest = {entry.agent_id: entry for entry in state.entries if entry.agent_id in active}
    counts: dict[str, int] = {}
    for entry in latest.values():
        if entry.tag == PXPTag.RATIFY:
            prediction = entry.prediction.strip().casefold()
            counts[prediction] = counts.get(prediction, 0) + 1
    return max(counts.values(), default=0) / len(active)


class RetrospectiveSandbox:
    """Generate an altered contribution and replay agents on an isolated board."""

    def __init__(self, client):
        self.client = client

    def simulate(self, request: RollbackRequest) -> RollbackOutcome:
        if not request.task.strip() or not request.alternative_instruction.strip():
            raise ValueError("task and alternative instruction must be nonempty")
        if (any(type(value) is not int for value in
                (request.max_replay_turns, request.max_alternatives, request.max_retries))
                or request.max_replay_turns < 1 or request.max_alternatives < 1 or request.max_retries < 0):
            raise ValueError("replay budgets must be positive and retries nonnegative")
        board = create_sandbox(request)
        baseline = request.source_state.model_copy(deep=True)
        baseline.entries = [entry for entry in baseline.entries if not entry.is_counterfactual_sim]
        original = next(entry for entry in baseline.entries if entry.entry_id == request.cutoff_entry_id)
        proposal = request.rejected_proposal or original
        baseline_score = consensus_progress(baseline)
        attempts: list[dict] = []
        replayed: list[BoardEntry] = []
        scheduler = Scheduler(board)
        outcome = "turn_limit"
        error = None
        try:
            scheduler.prioritize_next_turn(request.target_agent_id)
            for turn in range(request.max_replay_turns):
                agent = scheduler.next_agent()
                guidance = None
                if turn == 0:
                    prior = {"tag": proposal.tag.value, "prediction": proposal.prediction,
                             "explanation": proposal.explanation}
                    if request.rejected_proposal is not None:
                        prior["reviewer_rejection"] = original.explanation
                    guidance = ("Reconsider this unverified earlier contribution: "
                                + json.dumps(prior, ensure_ascii=False) + "\n"
                                + request.alternative_instruction + "\n"
                                + "Propose an alternative tag or explanation using only supported facts.")
                generated = None
                for _ in range(request.max_alternatives if turn == 0 else 1):
                    kwargs = {"max_retries": request.max_retries, "is_counterfactual_sim": True}
                    if request.review_evidence is not None:
                        kwargs["review_evidence"] = request.review_evidence
                    if guidance is not None:
                        kwargs["guidance"] = guidance
                    generated = self.client.generate_entry(request.task, board.get_state(), agent.agent_id, **kwargs)
                    attempts.extend(generated.get("attempts", []))
                    entry = generated["entry"]
                    if (entry.agent_id != agent.agent_id or not entry.is_counterfactual_sim
                            or any(old.entry_id == entry.entry_id for old in baseline.entries + replayed)):
                        raise ValueError("generated entry has invalid simulation identity or provenance")
                    if turn or (
                        entry.tag != proposal.tag
                        or entry.prediction.strip() != proposal.prediction.strip()
                        or entry.explanation.strip() != proposal.explanation.strip()
                    ):
                        break
                else:
                    raise ValueError("no alternative contribution generated")
                scheduler.submit_entry(entry)
                replayed.append(entry.model_copy(deep=True))
                if board.current_intelligibility in (IntelligibilityLevel.STRONG, IntelligibilityLevel.ULTRA_STRONG):
                    outcome = "consensus"
                    break
                if board.current_intelligibility == IntelligibilityLevel.DEADLOCKED:
                    outcome = "deadlocked"
                    break
        except Exception as exc:
            attempts.extend(getattr(exc, "attempts", []))
            outcome, error = "error", f"{type(exc).__name__}: {exc}"
        finally:
            scheduler.close()
        return RollbackOutcome(
            original_entry_id=original.entry_id, outcome=outcome,
            candidate_entry=replayed[0] if replayed else None, replayed_entries=tuple(replayed),
            delta_score=None if error else consensus_progress(board.get_state()) - baseline_score,
            score_name=None if error else "matching_ratify_fraction_delta",
            failure_reason=error, attempts=tuple(attempts), usage=inference_usage(attempts),
        )
