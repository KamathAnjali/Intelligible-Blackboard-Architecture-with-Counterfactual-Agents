"""Local personas taking scheduled turns on the real blackboard."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from agents.llm_client import BASE_URL, MODEL, OPTIONS, ROOT, OllamaClient, inference_usage, timings
from agents.pxp import InitialPXPResponse, PXPResponse
from blackboard.core import Blackboard
from blackboard.models import AgentRecord, BoardEntry, PXPTag
from blackboard.scheduler import Scheduler
from blackboard.store import InMemoryJSONStore

DEFAULT_TASK = "All tulips are plants. This item is a tulip. Is it a plant? Use yes or no as prediction."

DEFAULT_AGENTS = (("proposer", "aggressive_proposer"),
                  ("verifier", "cautious_verifier"))
DISAGREEMENT_AGENTS = (("proposer", "aggressive_proposer"),
                       ("auditor", "evidence_auditor"),
                       ("challenger", "counterexample_challenger"))
DISAGREEMENT_TASK = (
    "Mira has a cedar ticket. Every cedar ticket is valid. Some valid tickets "
    "receive a priority stamp. Does Mira's ticket receive a priority stamp? "
    "Answer with A: yes, B: no, or C: insufficient information."
)
DISAGREEMENT_SEED = {
    "prediction": "A: yes",
    "explanation": "Mira's ticket is valid, so it receives a priority stamp.",
}


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
                     output_dir=None, scenario="default"):
    """Run a bounded session and checkpoint the transcript after each turn.

    No fabricated fallback entries are posted if generation fails. Reports can
    be written even when Ollama is unavailable; saving never calls the server.
    """
    if scenario not in ("default", "disagreement"):
        raise ValueError("scenario must be default or disagreement")
    if scenario == "disagreement" and task is not None:
        raise ValueError("the disagreement scenario has a fixed task and seed")
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
    stamp = datetime.now(timezone.utc)
    task_id = f"conversation-{stamp.strftime('%Y%m%dT%H%M%S%fZ')}"
    directory = Path(output_dir) if output_dir is not None else ROOT / "results" / "conversations"
    directory = directory / task_id
    store = InMemoryJSONStore(directory)
    board = Blackboard(task_id, store)
    scheduler = Scheduler(board)
    participants = DISAGREEMENT_AGENTS if scenario == "disagreement" else DEFAULT_AGENTS
    for agent_id, persona in participants:
        agent = AgentRecord(agent_id=agent_id, persona=persona, model_name=client.model)
        board.register_agent(agent)
        scheduler.register_agent(agent)
    report = {
        "created_at": stamp.isoformat(), "task": task, "task_id": task_id,
        "scenario": scenario,
        "model": client.model, "base_url": client.base_url, "options": dict(OPTIONS),
        "max_turns": max_turns, "max_retries": max_retries, "turns": [],
        "failures": [], "outcome": "running", "explanation_review": "pending",
        "self_check_enabled": True,
        "prompts": {name: (ROOT / "agents" / "prompts" / f"{name}.txt").read_text(encoding="utf-8")
                    for name in ("pxp_template", "explanation_check", *(persona for _, persona in participants))},
    }
    path = directory / "transcript.json"

    def checkpoint():
        report["scheduler_status"] = scheduler.status
        report["board"] = board.get_state().model_dump(mode="json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        store.snapshot_to_disk(task_id)

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
        for number in range(start_turn, max_turns + 1):
            agent = scheduler.next_agent()
            state = board.get_state()
            turn = {"turn": number, "agent_id": agent.agent_id,
                    "history_entry_ids": [entry.entry_id for entry in state.entries],
                    "attempts": [], "posted": False}
            report["turns"].append(turn)
            print(f"Turn {number}: {agent.agent_id} ({agent.persona})...", flush=True)
            try:
                result = client.generate_entry(task, state, agent.agent_id, max_retries=max_retries)
                turn["attempts"] = result["attempts"]
                entry = result["entry"]
                turn["entry"] = entry.model_dump(mode="json")
                scheduler.submit_entry(entry)
                turn["posted"] = True
                turn["metrics"] = timings(result["raw"])
                print(f"  {entry.tag.value}: {entry.prediction}\n  {entry.explanation}", flush=True)
                # Text differences may be paraphrases, so flag for review rather
                # than deciding semantic equivalence automatically.
                if entry.tag == PXPTag.RATIFY and state.entries:
                    target = state.entries[-1]
                    if entry.prediction.strip().casefold() != target.prediction.strip().casefold():
                        report["failures"].append({"turn": number,
                            "category": "ratify_prediction_text_differs", "review": "pending"})
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
                report["outcome"] = "deadlocked" if scheduler.status == "DEADLOCKED" else "scheduler_consensus"
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
    parser.add_argument("--scenario", choices=("default", "disagreement"), default="default")
    parser.add_argument("--demo", action="store_true", help="Require a clean REVISE, RATIFY, RATIFY sequence")
    args = parser.parse_args()
    if args.demo and args.scenario != "default":
        parser.error("--demo requires the default scenario")
    turns = 3 if args.demo else args.turns
    report, _ = run_conversation(OllamaClient(args.model, args.base_url), args.prompt,
                                 turns, args.retries, scenario=args.scenario)
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
