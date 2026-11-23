"""Local personas taking scheduled turns on the real blackboard."""

import argparse
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from bench.evidence import termination_candidate
from agents.llm_client import (
    BASE_URL, MODEL, OPTIONS, ROOT, GenerationTransportError, OllamaClient, inference_usage,
    sanitized_base_url, timings,
)
from agents.pxp import InitialPXPResponse, PXPResponse
from agents.pxp import PXPGenerationError
from agents.counterfactual import RetrospectiveSandbox, RollbackRequest
from blackboard.core import Blackboard
from blackboard.models import AgentRecord, BoardEntry, CounterfactualResult, IntelligibilityLevel, PXPTag
from blackboard.scheduler import Scheduler
from blackboard.store import InMemoryJSONStore

# Model explanations may contain Unicode symbols such as arrows. Escape only
# characters unavailable in the active Windows console encoding so printing
# a valid response cannot interrupt the session.
for _console_stream in (sys.stdout, sys.stderr):
    try:
        _console_stream.reconfigure(errors="backslashreplace")
    except (AttributeError, OSError, ValueError):
        pass

DEFAULT_TASK = """A philosopher says every simple idea comes from experience, and that we never directly perceive a hidden force
connecting cause to effect. After seeing the same kind of event repeatedly, we expect it to happen again. Which philosopher would
explain that expectation as habit rather than proof of a necessary connection?
A. Kant
B. Hume
C. Plato
D. Descartes
"""

DEFAULT_AGENTS = (("proposer", "aggressive_proposer"),
                  ("verifier", "cautious_verifier"))
DISAGREEMENT_AGENTS = (("proposer", "aggressive_proposer"),
                       ("auditor", "evidence_auditor"),
                       ("challenger", "counterexample_challenger"))
AGENT_ROSTERS = {
    2: DEFAULT_AGENTS,
    3: DISAGREEMENT_AGENTS,
    6: (*DEFAULT_AGENTS, *DISAGREEMENT_AGENTS[1:],
        ("second_proposer", "aggressive_proposer"), ("second_verifier", "cautious_verifier")),
}
COUNTERFACTUAL_FRACTIONS = (0, 0.33, 0.50, 0.66, 1.0)
DISAGREEMENT_TASK = (
    "Mira has a cedar ticket. Every cedar ticket is valid. Some valid tickets "
    "receive a priority stamp. Does Mira's ticket receive a priority stamp? "
    "Answer with A: yes, B: no, or C: insufficient information."
)
DISAGREEMENT_SEED = {
    "prediction": "A: yes",
    "explanation": "Mira's ticket is valid, so it receives a priority stamp.",
}
MAX_TERMINATION_GUARD_RETRIES = 2


def attempt_failures(attempt, initial=False):
    """Distinguish output-format failures from model self-check failures."""
    raw = attempt.get("raw")
    if raw is None:
        return ["transport_error"]
    if not attempt.get("error"):
        return []
    check = attempt.get("self_check")
    if check is not None:
        if check.get("error"):
            return ["self_check_transport_error" if check.get("raw") is None else "self_check_invalid_output"]
        if check.get("parsed", {}).get("supported") is False:
            return ["unsupported_explanation"]
    if raw.get("done") is not True or raw.get("done_reason") == "length":
        return ["incomplete_output"]
    if not isinstance(raw.get("response"), str):
        return ["missing_response"]
    schema = InitialPXPResponse if initial else PXPResponse
    try:
        schema.model_validate_json(raw["response"])
    except ValidationError as exc:
        categories = set()
        for issue in exc.errors(include_input=False, include_url=False):
            if issue["type"] == "json_invalid":
                categories.add("malformed_json")
            elif issue["type"] == "extra_forbidden":
                categories.add("hallucinated_fields")
            elif issue["loc"] == ("tag",):
                categories.add("malformed_tag")
            else:
                categories.add("invalid_fields")
        return sorted(categories)
    return ["validation_error"]


def run_conversation(client, task=None, max_turns=6, max_retries=2,
                     output_dir=None, scenario="default", on_board_created: Callable | None = None,
                     task_id=None, initial_facts: list[str] | None = None,
                     termination_trace: list[dict] | None = None,
                     termination_guard: Callable[[list[dict], str], bool] | None = None,
                     termination_guard_reason: Callable[[list[dict], str], str | None] | None = None,
                     review_evidence=None, on_rejection: Callable | None = None,
                     counterfactual_fraction: float = 0.0, counterfactual_seed: int = 42,
                     counterfactual_streak_threshold: int = 2,
                     max_counterfactual_simulations: int = 1, agent_count=None,
                     retrospective_recovery: bool = False,
                     counterfactual_include_first_agent: bool = False):
    """Run a bounded session and checkpoint the transcript after each turn.

    No fabricated fallback entries are posted if generation fails. Reports can
    be written even when Ollama is unavailable; saving never calls the server.
    """
    if scenario not in ("default", "disagreement"):
        raise ValueError("scenario must be default or disagreement")
    if scenario == "disagreement" and task is not None:
        raise ValueError("the disagreement scenario has a fixed task and seed")
    if agent_count is None:
        agent_count = 3 if scenario == "disagreement" else 2
    if type(agent_count) is not int or agent_count not in AGENT_ROSTERS:
        raise ValueError("agent_count must be 2, 3, or 6")
    if scenario == "disagreement" and agent_count != 3:
        raise ValueError("the disagreement scenario requires three agents")
    task = task if task is not None else (
        DISAGREEMENT_TASK if scenario == "disagreement" else DEFAULT_TASK
    )
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a nonempty string")
    if type(max_turns) is not int or not 1 <= max_turns <= 12:
        raise ValueError("max_turns must be an integer between 1 and 12")
    if scenario == "disagreement" and max_turns < 5:
        raise ValueError("the disagreement scenario needs at least five turns")
    if type(max_retries) is not int or not 0 <= max_retries <= 5:
        raise ValueError("max_retries must be an integer between 0 and 5")
    if counterfactual_fraction not in COUNTERFACTUAL_FRACTIONS:
        raise ValueError("counterfactual_fraction must be one of 0, 0.33, 0.50, 0.66, or 1.0")
    if type(retrospective_recovery) is not bool:
        raise ValueError("retrospective_recovery must be a bool")
    if type(counterfactual_include_first_agent) is not bool:
        raise ValueError("counterfactual_include_first_agent must be a bool")
    if type(counterfactual_seed) is not int:
        raise ValueError("counterfactual_seed must be an integer")
    if type(counterfactual_streak_threshold) is not int or counterfactual_streak_threshold < 2:
        raise ValueError("counterfactual_streak_threshold must be an integer of at least 2")
    if type(max_counterfactual_simulations) is not int or max_counterfactual_simulations < 0:
        raise ValueError("max_counterfactual_simulations must be a non-negative integer")
    if initial_facts is not None and (
        not isinstance(initial_facts, list)
        or not all(isinstance(item, str) and item.strip() for item in initial_facts)
    ):
        raise ValueError("initial_facts must be a list of nonempty strings")
    if termination_guard is not None and not callable(termination_guard):
        raise ValueError("termination_guard must be callable")
    stamp = datetime.now(timezone.utc)
    if task_id is None:
        task_id = f"conversation-{stamp.strftime('%Y%m%dT%H%M%S%fZ')}"
    elif (not isinstance(task_id, str) or not task_id.strip()
          or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for char in task_id)
          or ".." in task_id):
        raise ValueError("task_id must be a nonempty path-safe identifier")
    directory = Path(output_dir) if output_dir is not None else ROOT / "results" / "conversations"
    directory = directory / task_id
    store = InMemoryJSONStore(directory)
    board = Blackboard(task_id, store)
    scheduler = Scheduler(board)
    participants = AGENT_ROSTERS[agent_count]
    participant_ids = [agent_id for agent_id, _ in participants]
    capability_count = math.ceil(len(participant_ids) * counterfactual_fraction)
    capability_order = random.Random(counterfactual_seed).sample(participant_ids, len(participant_ids))
    if counterfactual_include_first_agent and capability_count:
        first_agent_id = participant_ids[0]
        capability_order.remove(first_agent_id)
        capability_order.insert(0, first_agent_id)
    capable_ids = set(capability_order[:capability_count])
    for agent_id, persona in participants:
        agent = AgentRecord(agent_id=agent_id, persona=persona, model_name=client.model,
                            counterfactual_capable=agent_id in capable_ids)
        board.register_agent(agent)
        scheduler.register_agent(agent)
    if on_board_created is not None:
        on_board_created(board)
    initial_facts = list(initial_facts or [])
    evidence_agent_id = None
    if initial_facts:
        evidence_agent_id = "__data_evidence__"
        suffix = 1
        registered = board.get_agents()
        while evidence_agent_id in registered:
            suffix += 1
            evidence_agent_id = f"__data_evidence_{suffix}__"
        evidence_agent = AgentRecord(
            agent_id=evidence_agent_id,
            persona="verified_data_evidence",
            model_name="application",
            active=False,
        )
        board.register_agent(evidence_agent)
        scheduler.register_agent(evidence_agent)
        for message in initial_facts:
            board.post_entry(BoardEntry(
                agent_id=evidence_agent_id,
                tag=PXPTag.FACT,
                prediction=message,
                explanation="Application-posted structured data evidence; no reference answer supplied.",
            ))
    self_check_enabled = getattr(client, "review_explanations", True)
    prompt_names = ("pxp_template", "explanation_check") if self_check_enabled else ("pxp_template",)
    report = {
        "created_at": stamp.isoformat(), "task": task, "task_id": task_id,
        "scenario": scenario, "agent_count": agent_count,
        "model": client.model, "base_url": sanitized_base_url(client.base_url),
        "options": dict(getattr(client, "options", OPTIONS)),
        "max_turns": max_turns, "effective_max_turns": max_turns,
        "max_retries": max_retries, "turns": [],
        "failures": [], "outcome": "running",
        "explanation_review": "pending" if self_check_enabled else "disabled",
        "workflow_counters": {
            "guard_activations": [],
            "refusal_despite_evidence_retries": 0,
            "guard_retries": 0,
            "reviewer_rejections": 0,
            "counterfactual_triggers": 0,
            "counterfactual_simulations": 0,
            "counterfactual_changed_outcome": 0,
            "max_guard_retries": MAX_TERMINATION_GUARD_RETRIES,
        },
        "self_check_enabled": self_check_enabled,
        "counterfactual_fraction": float(counterfactual_fraction),
        "retrospective_recovery": retrospective_recovery,
        "counterfactual_seed": counterfactual_seed,
        "counterfactual_include_first_agent": counterfactual_include_first_agent,
        "counterfactual_capable_agents": sorted(capable_ids),
        "counterfactual_policy": (
            "request_live_revision_if_valid_alternative_differs; changed_outcome counts a posted live "
            "prediction differing from its rejected candidate"
        ),
        "counterfactual_attempts": [],
        "counterfactual_skips": [],
        "prompts": {name: (ROOT / "agents" / "prompts" / f"{name}.txt").read_text(encoding="utf-8")
                    for name in (*prompt_names, *(persona for _, persona in participants))},
    }
    if retrospective_recovery:
        report["prompts"]["rollback"] = (ROOT / "agents/prompts/rollback.txt").read_text(encoding="utf-8")
        report["counterfactual_policy"] = "apply unverified guidance only after replay consensus with a positive agreement delta"
    path = directory / "transcript.json"

    def checkpoint():
        status_val = scheduler.status.value if isinstance(scheduler.status, IntelligibilityLevel) else str(scheduler.status)
        report["scheduler_status"] = status_val
        report["board"] = board.get_state().model_dump(mode="json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        store.snapshot_to_disk(task_id)

    rejection_streak_target = None
    rejection_streak_count = 0
    rejection_streak_number = 0
    simulated_streaks = set()
    counterfactual_simulations = 0
    pending_counterfactual_revisions = {}
    pending_recovery = None
    rejected_proposals = {}

    def maybe_run_counterfactual(rejection_entry, original_candidate, proposer_id, reason):
        nonlocal rejection_streak_target, rejection_streak_count
        nonlocal rejection_streak_number, counterfactual_simulations
        if retrospective_recovery:
            return
        if rejection_entry.tag not in (PXPTag.REJECT, PXPTag.REFUTE):
            rejection_streak_target = None
            rejection_streak_count = 0
            return
        target_id = rejection_entry.target_entry_id
        if target_id == rejection_streak_target:
            rejection_streak_count += 1
        else:
            rejection_streak_number += 1
            rejection_streak_target = target_id
            rejection_streak_count = 1
        if (not capable_ids or rejection_streak_count < counterfactual_streak_threshold
                or counterfactual_simulations >= max_counterfactual_simulations):
            return
        streak_key = (rejection_streak_number, target_id)
        if streak_key in simulated_streaks:
            return
        simulated_streaks.add(streak_key)
        report["workflow_counters"]["counterfactual_triggers"] += 1
        counterfactual_simulations += 1
        report["workflow_counters"]["counterfactual_simulations"] = counterfactual_simulations
        sim_task_id = f"{task_id}-cf-{rejection_streak_number}-{counterfactual_simulations}"
        try:
            sim_board = board.fork_simulation_blackboard(
                cutoff_entry_id=target_id,
                sim_task_id=sim_task_id,
            )
            sim_agent_id = sorted(capable_ids)[0]
            sim_task = (
                f"{task}\n\nCounterfactual alternative proposal: the independent reviewer rejected "
                f"the prior candidate for this reason: {reason}\n"
                "Propose one alternative candidate. Frame the explanation directly around the "
                "available evidence and state how that evidence supports the prediction."
            )
            generated = client.generate_entry(
                sim_task, sim_board.get_state(), sim_agent_id,
                target_entry_id=target_id,
                max_retries=0,
                is_counterfactual_sim=True,
                review_evidence=review_evidence,
            )
            simulated_entry = generated["entry"].model_copy(update={"is_counterfactual_sim": True})
            sim_board.post_entry(simulated_entry)
            board.record_counterfactual_result(CounterfactualResult(
                agent_id=sim_agent_id,
                original_entry_id=rejection_entry.entry_id,
                simulated_entry=simulated_entry,
                delta_score=None,
                applied_to_live=False,
            ))
            attempts = generated.get("attempts", [])
            report["counterfactual_attempts"].append({
                "streak": rejection_streak_number,
                "target_entry_id": target_id,
                "review_reason": reason,
                "sim_task_id": sim_task_id,
                "agent_id": sim_agent_id,
                "attempts": attempts,
                "simulated_entry": simulated_entry.model_dump(mode="json"),
                "revision_requested": False,
            })
            changed = simulated_entry.prediction.strip().casefold() != original_candidate.prediction.strip().casefold()
            if changed and scheduler.is_running():
                scheduler.prioritize_next_turn(proposer_id)
                report["counterfactual_attempts"][-1]["revision_requested"] = True
                pending_counterfactual_revisions[proposer_id] = {
                    "attempt_index": len(report["counterfactual_attempts"]) - 1,
                    "original_prediction": original_candidate.prediction,
                }
            checkpoint()
        except Exception as exc:
            report["counterfactual_attempts"].append({
                "streak": rejection_streak_number,
                "target_entry_id": target_id,
                "review_reason": reason,
                "sim_task_id": sim_task_id,
                "attempts": getattr(exc, "attempts", []),
                "error": f"{type(exc).__name__}: {exc}",
            })
            checkpoint()

    if scenario == "disagreement":
        first_agent = scheduler.next_agent()
        seed = BoardEntry(agent_id=first_agent.agent_id, tag=PXPTag.REVISE,
                          **DISAGREEMENT_SEED)
        scheduler.submit_entry(seed)
        report["expected_prediction"] = "C: insufficient information"
        report["turns"].append({
            "turn": 1, "agent_id": first_agent.agent_id,
            "source": "synthetic_seed", "history_entry_ids": [],
            "attempts": [], "entry": seed.model_dump(mode="json"),
            "posted": True, "usage": inference_usage([]),
        })
        print(f"Turn 1: synthetic {first_agent.agent_id} claim ({seed.prediction})", flush=True)
    checkpoint()
    try:
        start_turn = 2 if scenario == "disagreement" else 1
        number = start_turn - 1
        effective_turn_limit = max_turns
        guard_retry_count = 0
        while number < effective_turn_limit:
            number += 1
            agent = scheduler.next_agent()
            state = board.get_state()
            turn = {"turn": number, "agent_id": agent.agent_id,
                    "history_entry_ids": [entry.entry_id for entry in state.entries],
                    "attempts": [], "posted": False}
            report["turns"].append(turn)
            print(f"Turn {number}: {agent.agent_id} ({agent.persona})...", flush=True)
            try:
                call_options = {"max_retries": max_retries, "review_evidence": review_evidence}
                if pending_recovery is not None:
                    call_options["guidance"] = pending_recovery["guidance"]
                    call_options["enforce_target_agreement"] = True
                    turn["recovery_guidance"] = pending_recovery["guidance"]
                result = client.generate_entry(task, state, agent.agent_id, **call_options)
                turn["attempts"] = result["attempts"]
                entry = result["entry"]
                if (capable_ids and not retrospective_recovery and state.entries
                        and entry.tag in (PXPTag.REJECT, PXPTag.REFUTE)):
                    if state.entries[-1].tag in (PXPTag.REJECT, PXPTag.REFUTE):
                        entry = entry.model_copy(update={
                            "target_entry_id": state.entries[-1].target_entry_id,
                        })
                turn["entry"] = entry.model_dump(mode="json")
                scheduler.submit_entry(entry)
                turn["posted"] = True
                if pending_recovery is not None:
                    board.record_counterfactual_result(pending_recovery["audit"].model_copy(update={"applied_to_live": True}))
                    detail = report["counterfactual_attempts"][pending_recovery["attempt_index"]]
                    detail["applied_to_live"] = True
                    detail["live_revision_changed_prediction"] = (
                        entry.prediction.strip().casefold() != pending_recovery["original_prediction"].strip().casefold()
                    )
                    if detail["live_revision_changed_prediction"]:
                        report["workflow_counters"]["counterfactual_changed_outcome"] += 1
                    pending_recovery = None
                turn["metrics"] = timings(result["raw"])
                print(f"  {entry.tag.value}: {entry.prediction}\n  {entry.explanation}", flush=True)
                pending_revision = pending_counterfactual_revisions.pop(agent.agent_id, None)
                if pending_revision is not None:
                    changed_live_prediction = (
                        entry.prediction.strip().casefold()
                        != pending_revision["original_prediction"].strip().casefold()
                    )
                    if changed_live_prediction:
                        report["workflow_counters"]["counterfactual_changed_outcome"] += 1
                    cf_attempt = report["counterfactual_attempts"][pending_revision["attempt_index"]]
                    cf_attempt["live_revision_prediction"] = entry.prediction
                    cf_attempt["live_revision_changed_prediction"] = changed_live_prediction
                maybe_run_counterfactual(entry, entry, agent.agent_id, entry.explanation)
                # Text differences may be paraphrases, so flag for review rather
                # than deciding semantic equivalence automatically.
                if entry.tag == PXPTag.RATIFY and state.entries:
                    target = state.entries[-1]
                    if entry.prediction.strip().casefold() != target.prediction.strip().casefold():
                        report["failures"].append({"turn": number,
                            "category": "ratify_prediction_text_differs", "review": "pending"})
            except (PXPGenerationError, GenerationTransportError) as exc:
                turn["attempts"] = exc.attempts
                rejected = next((item for item in reversed(exc.attempts)
                                 if item.get("self_check", {}).get("parsed", {}).get("supported") is False
                                 and isinstance(item.get("parsed"), dict)), None)
                if rejected is None:
                    turn["error"] = str(exc)
                    report["outcome"] = "error"
                    report["failures"].append({"turn": number, "category": "turn_error", "error": str(exc)})
                else:
                    review = rejected["self_check"]["parsed"]
                    candidate = rejected["parsed"]
                    rejection = BoardEntry(
                        agent_id=agent.agent_id, tag=PXPTag.REJECT,
                        prediction=candidate["prediction"], explanation=review["reason"],
                        target_entry_id=(
                            (state.entries[-1].target_entry_id
                             if state.entries[-1].tag in (PXPTag.REJECT, PXPTag.REFUTE)
                             else state.entries[-1].entry_id)
                            if state.entries and capable_ids
                            else (state.entries[-1].entry_id if state.entries else None)
                        ),
                    )
                    scheduler.submit_entry(rejection)
                    if pending_recovery is not None:
                        board.record_counterfactual_result(pending_recovery["audit"])
                        pending_recovery = None
                    turn["entry"] = rejection.model_dump(mode="json")
                    turn["posted"] = True
                    turn["review_rejected"] = True
                    report["workflow_counters"]["reviewer_rejections"] += 1
                    if on_rejection is not None:
                        on_rejection(rejection, review)
                    original_candidate = BoardEntry(
                        agent_id=agent.agent_id, tag=candidate["tag"],
                        prediction=candidate["prediction"], explanation=candidate["explanation"],
                        target_entry_id=rejection.target_entry_id,
                    )
                    if retrospective_recovery and original_candidate.tag == PXPTag.REVISE:
                        rejected_proposals[rejection.entry_id] = original_candidate
                    maybe_run_counterfactual(
                        rejection, original_candidate, agent.agent_id, review["reason"]
                    )
                    report["failures"].append({"turn": number, "category": "reviewer_rejection",
                                               "reason": review["reason"]})
                    if isinstance(exc, GenerationTransportError):
                        turn["error"] = str(exc)
                        report["outcome"] = "error"
                        report["failures"].append({"turn": number, "category": "turn_error",
                                                   "error": str(exc)})
            except (RuntimeError, ValueError, OSError) as exc:
                turn["attempts"] = getattr(exc, "attempts", turn["attempts"])
                turn["error"] = str(exc)
                report["outcome"] = "error"
                report["failures"].append({"turn": number, "category": "turn_error", "error": str(exc)})
            turn["usage"] = inference_usage(turn["attempts"])
            for index, attempt in enumerate(turn["attempts"], start=1):
                for category in attempt_failures(attempt, initial=not state.entries):
                    report["failures"].append({"turn": number, "attempt": index, "category": category})
            if report["outcome"] == "error":
                break
            if not scheduler.is_running():
                is_dl = scheduler.status in (IntelligibilityLevel.DEADLOCKED, "DEADLOCKED")
                report["outcome"] = "deadlocked" if is_dl else "scheduler_consensus"
                if is_dl and retrospective_recovery:
                    skip_reason = None
                    if not capable_ids:
                        skip_reason = "no_eligible_agents"
                    elif number >= effective_turn_limit:
                        skip_reason = "no_live_turns_left"
                    elif counterfactual_simulations >= max_counterfactual_simulations:
                        skip_reason = "replay_limit_reached"
                    else:
                        snapshot = board.get_state()
                        proposals = [
                            entry for entry in reversed(snapshot.entries)
                            if not entry.is_counterfactual_sim
                            and (entry.tag == PXPTag.REVISE or entry.entry_id in rejected_proposals)
                        ]
                        eligible = [entry for entry in proposals if entry.agent_id in capable_ids]
                        original = next((entry for entry in eligible if entry.tag == PXPTag.REVISE), None)
                        if original is None and eligible:
                            # Start before the earliest rejected proposal to replay the rejection sequence.
                            original = eligible[-1]
                        if original is None:
                            skip_reason = "proposal_authors_ineligible" if proposals else "no_proposal_for_replay"
                    if skip_reason:
                        report["counterfactual_skips"].append({"turn": number, "reason": skip_reason})
                        print(f"  Counterfactual replay skipped ({skip_reason}).", flush=True)
                    else:
                        rejected_proposal = rejected_proposals.get(original.entry_id)
                        proposal_source = "rejected_proposal" if rejected_proposal is not None else "posted_revision"
                        print(f"  Counterfactual replay for {original.agent_id} ({proposal_source})...", flush=True)
                        report["workflow_counters"]["counterfactual_triggers"] += 1
                        report["workflow_counters"]["counterfactual_simulations"] += 1
                        counterfactual_simulations += 1
                        request = RollbackRequest(task, snapshot, original.entry_id, original.agent_id,
                            (ROOT / "agents/prompts/rollback.txt").read_text(encoding="utf-8"),
                            max_replay_turns=max_turns, max_retries=max_retries,
                            review_evidence=review_evidence, rejected_proposal=rejected_proposal)
                        try:
                            replay = RetrospectiveSandbox(client).simulate(request)
                        except Exception as exc:
                            report["counterfactual_attempts"].append({
                                "kind": "retrospective", "original_entry_id": original.entry_id,
                                "proposal_source": proposal_source,
                                "outcome": "error", "failure_reason": f"{type(exc).__name__}: {exc}",
                                "accepted": False, "applied_to_live": False,
                            })
                            checkpoint()
                            break
                        accepted = (replay.outcome == "consensus" and replay.delta_score is not None
                                    and replay.delta_score > 0 and replay.candidate_entry is not None)
                        detail = {"kind": "retrospective", "original_entry_id": original.entry_id,
                                  "proposal_source": proposal_source,
                                  "outcome": replay.outcome, "delta_score": replay.delta_score,
                                  "score_name": replay.score_name, "failure_reason": replay.failure_reason,
                                  "entries": [e.model_dump(mode="json") for e in replay.replayed_entries],
                                  "attempts": list(replay.attempts), "usage": replay.usage,
                                  "accepted": accepted, "applied_to_live": False}
                        report["counterfactual_attempts"].append(detail)
                        if replay.candidate_entry is not None:
                            audit = CounterfactualResult(agent_id=original.agent_id,
                                original_entry_id=original.entry_id, simulated_entry=replay.candidate_entry,
                                delta_score=replay.delta_score, applied_to_live=False)
                            if accepted and board.reopen_after_deadlock(expected_state=snapshot):
                                scheduler.prioritize_next_turn(original.agent_id)
                                pending_recovery = {"audit": audit, "attempt_index": len(report["counterfactual_attempts"])-1,
                                    "original_prediction": original.prediction,
                                    "guidance": "An isolated alternative suggested this unverified claim: "
                                    + json.dumps({"prediction": replay.candidate_entry.prediction,
                                                  "explanation": replay.candidate_entry.explanation}, ensure_ascii=False)
                                    + ". Reconsider it against the original task and evidence; do not assume it is correct. "
                                    + "The target is the latest live entry, not the simulated entry. "
                                    + "Use REVISE if changing its prediction or correcting its reasoning; "
                                    + "use RATIFY only if both its prediction and reasoning are supported."}
                                report["outcome"] = "running"
                                checkpoint()
                                continue
                            board.record_counterfactual_result(audit)
                if report["outcome"] == "scheduler_consensus" and termination_guard is not None:
                    active_ids = set(board.get_agents(active_only=True))
                    answer_entry = next(
                        (entry for entry in reversed(board.get_state().entries)
                         if entry.agent_id in active_ids),
                        None,
                    )
                    answer = answer_entry.prediction if answer_entry is not None else ""
                    guard_answer = termination_candidate(
                        answer,
                        answer_entry.explanation if answer_entry is not None else "",
                    )
                    try:
                        allowed_to_terminate = termination_guard(list(termination_trace or []), guard_answer)
                    except Exception as exc:
                        report["outcome"] = "error"
                        report["failures"].append({
                            "turn": number,
                            "category": "termination_guard_error",
                            "error": f"{type(exc).__name__}: {exc}",
                        })
                        break
                    if not allowed_to_terminate:
                        reason = (
                            termination_guard_reason(list(termination_trace or []), guard_answer)
                            if termination_guard_reason is not None else None
                        ) or "refusal_despite_evidence"
                        if guard_retry_count >= MAX_TERMINATION_GUARD_RETRIES:
                            report["outcome"] = "consensus_needs_review"
                            report["failures"].append({
                                "turn": number,
                                "category": "termination_guard_exhausted",
                                "reason": reason,
                                "retry_count": guard_retry_count,
                            })
                            checkpoint()
                            break
                        if answer_entry is None:
                            report["outcome"] = "error"
                            report["failures"].append({
                                "turn": number,
                                "category": "termination_guard_missing_answerer",
                                "reason": reason,
                            })
                            break
                        guard_retry_count += 1
                        if reason.startswith("refusal_") or reason == "refusal_despite_evidence":
                            report["workflow_counters"]["refusal_despite_evidence_retries"] += 1
                        report["workflow_counters"]["guard_retries"] = guard_retry_count
                        report["workflow_counters"]["guard_activations"].append({
                            "retry_count": guard_retry_count,
                            "reason": reason,
                            "answerer_agent_id": answer_entry.agent_id,
                            "rejected_answer": answer,
                            "rejected_explanation": answer_entry.explanation,
                        })
                        if not board.reopen_for_review():
                            report["outcome"] = "error"
                            report["failures"].append({
                                "turn": number,
                                "category": "termination_guard_could_not_reopen_board",
                                "reason": reason,
                            })
                            break
                        if reason == "refusal_missing_specific_data":
                            rejection_guidance = (
                                "State the specific value, measurement, date, or record that is missing and why; "
                                "a bare refusal is not enough evidence."
                            )
                        elif reason == "refusal_ignored_successful_text":
                            rejection_guidance = (
                                "A successful text read/search is available. Extract requested values from its "
                                "returned text; absence of a literal column name does not prove the values are missing. "
                                "If a required value is truly absent, identify that value and source."
                            )
                        else:
                            rejection_guidance = (
                                "The application rejected this consensus because verified evidence in the task "
                                "context supports a different conclusion."
                            )
                        rejection = BoardEntry(
                            agent_id=answer_entry.agent_id,
                            tag=PXPTag.REJECT,
                            prediction=answer,
                            explanation=f"{reason}: {rejection_guidance}",
                            target_entry_id=answer_entry.entry_id,
                        )
                        board.post_entry(rejection)
                        maybe_run_counterfactual(
                            rejection, answer_entry, answer_entry.agent_id, rejection.explanation
                        )
                        scheduler.prioritize_next_turn(answer_entry.agent_id)
                        report["workflow_counters"]["guard_activations"][-1]["rejection_entry_id"] = rejection.entry_id
                        effective_turn_limit += 3
                        report["effective_max_turns"] = effective_turn_limit
                        report["outcome"] = "running"
                        checkpoint()
                        continue
                if report["outcome"] == "scheduler_consensus" and any(
                    f["category"] == "ratify_prediction_text_differs" for f in report["failures"]
                ):
                    report["outcome"] = "consensus_needs_review"
                break
            checkpoint()
        else:
            report["outcome"] = "turn_limit"
    except KeyboardInterrupt:
        report["outcome"] = "interrupted"
    finally:
        if pending_recovery is not None:
            board.record_counterfactual_result(pending_recovery["audit"])
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        if scenario == "disagreement":
            report["scenario_passed"] = is_successful_disagreement(report)
        checkpoint()
        print(f"Outcome: {report['outcome']}\nReport: {path}", flush=True)
    return report, path


def is_successful_disagreement(report):
    """Require a corrected seed and agreement by all three model agents."""
    turns = report["turns"]
    expected = report.get("expected_prediction", "").casefold()
    return (
        report["outcome"] == "scheduler_consensus"
        and not report["failures"]
        and len(turns) >= 5
        and turns[0].get("source") == "synthetic_seed"
        and turns[1]["entry"]["tag"] == "REVISE"
        and turns[1]["entry"]["prediction"].strip().casefold() == expected
        and len({turn["agent_id"] for turn in turns[-3:]}) == 3
        and all(turn["entry"]["tag"] == "RATIFY"
                and turn["entry"]["prediction"].strip().casefold() == expected
                for turn in turns[-3:])
    )


def is_clean_ratify_demo(report):
    """Check the short live sequence used for the Week 1 demonstration."""
    turns = report["turns"]
    expected = [("proposer", "REVISE"), ("verifier", "RATIFY"),
                ("proposer", "RATIFY")]
    return (
        report["outcome"] == "scheduler_consensus"
        and not report["failures"]
        and len(turns) == len(expected)
        and [(turn["agent_id"], turn["entry"]["tag"]) for turn in turns] == expected
        and all(len(turn["attempts"]) == 1 for turn in turns)
        and len({turn["entry"]["prediction"].strip().casefold() for turn in turns}) == 1
    )


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt")
    parser.add_argument("--turns", type=int, choices=range(1, 13), default=6)
    parser.add_argument("--retries", type=int, choices=range(6), default=2)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--no-explanation-review", dest="review_explanations", action="store_false",
                        help="Skip the model explanation check before posting")
    parser.add_argument("--scenario", choices=("default", "disagreement"), default="default")
    parser.add_argument("--agents", type=int, choices=AGENT_ROSTERS, help="Default: two agents, or three for disagreement")
    parser.add_argument("--retrospective-recovery", action="store_true", help="Try isolated replay after a deadlock")
    parser.add_argument("--counterfactual-fraction", type=float, choices=COUNTERFACTUAL_FRACTIONS, default=0.0)
    parser.add_argument("--demo", action="store_true", help="Require a clean REVISE, RATIFY, RATIFY sequence")
    args = parser.parse_args()
    if args.demo and args.scenario != "default":
        parser.error("--demo requires the default scenario")
    if args.demo and args.agents not in (None, 2):
        parser.error("--demo requires two agents")
    turns = 3 if args.demo else args.turns
    client = OllamaClient(args.model, args.base_url, review_explanations=args.review_explanations)
    report, _ = run_conversation(client, args.prompt, turns, args.retries,
                                 scenario=args.scenario, agent_count=args.agents,
                                 retrospective_recovery=args.retrospective_recovery,
                                 counterfactual_fraction=args.counterfactual_fraction)
    if args.demo:
        passed = is_clean_ratify_demo(report)
        print(f"Demo sequence: {'PASS' if passed else 'FAIL'}", flush=True)
        return 0 if passed else 1
    if args.scenario == "disagreement":
        print(f"Disagreement scenario: {'PASS' if report['scenario_passed'] else 'FAIL'}", flush=True)
        return 0 if report["scenario_passed"] else 1
    return 0 if report["outcome"] == "scheduler_consensus" else 1


if __name__ == "__main__":
    raise SystemExit(main())
