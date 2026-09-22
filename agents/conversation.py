"""Day 6: two local personas taking scheduled turns on the real blackboard."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from agents.llm_client import BASE_URL, MODEL, OPTIONS, ROOT, OllamaClient, timings
from agents.pxp import InitialPXPResponse, PXPResponse
from blackboard.core import Blackboard
from blackboard.models import AgentRecord, PXPTag
from blackboard.scheduler import Scheduler
from blackboard.store import InMemoryJSONStore

DEFAULT_TASK = "All tulips are plants. This item is a tulip. Is it a plant? Use yes or no as prediction."


def attempt_failures(attempt, initial=False):
    """Categorize format failures; explanation relevance needs separate review."""
    raw = attempt.get("raw")
    if raw is None:
        return ["transport_error"]
    if not attempt.get("error"):
        return []
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


def run_conversation(client, task=DEFAULT_TASK, max_turns=6, max_retries=2,
                     output_dir=None):
    """Run a fresh bounded session and checkpoint the transcript after each turn.

    No fabricated fallback entries are posted if generation fails. Reports can
    be written even when Ollama is unavailable; saving never calls the server.
    """
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a nonempty string")
    if type(max_turns) is not int or not 1 <= max_turns <= 12:
        raise ValueError("max_turns must be an integer between 1 and 12")
    if type(max_retries) is not int or not 0 <= max_retries <= 5:
        raise ValueError("max_retries must be an integer between 0 and 5")
    stamp = datetime.now(timezone.utc)
    task_id = f"conversation-{stamp.strftime('%Y%m%dT%H%M%S%fZ')}"
    directory = Path(output_dir) if output_dir is not None else ROOT / "results" / "conversations"
    directory = directory / task_id
    store = InMemoryJSONStore(directory)
    board = Blackboard(task_id, store)
    scheduler = Scheduler(board)
    for agent_id, persona in (("proposer", "aggressive_proposer"), ("verifier", "cautious_verifier")):
        agent = AgentRecord(agent_id=agent_id, persona=persona, model_name=client.model)
        board.register_agent(agent)
        scheduler.register_agent(agent)
    report = {
        "created_at": stamp.isoformat(), "task": task, "task_id": task_id,
        "model": client.model, "base_url": client.base_url, "options": dict(OPTIONS),
        "max_turns": max_turns, "max_retries": max_retries, "turns": [],
        "failures": [], "outcome": "running", "explanation_review": "pending",
        "prompts": {name: (ROOT / "agents" / "prompts" / f"{name}.txt").read_text(encoding="utf-8")
                    for name in ("pxp_template", "aggressive_proposer", "cautious_verifier")},
    }
    path = directory / "transcript.json"

    def checkpoint():
        report["scheduler_status"] = scheduler.status
        report["board"] = board.get_state().model_dump(mode="json")
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        store.snapshot_to_disk(task_id)

    checkpoint()
    try:
        for number in range(1, max_turns + 1):
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
        checkpoint()
        print(f"Outcome: {report['outcome']}\nReport: {path}", flush=True)
    return report, path


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default=DEFAULT_TASK)
    parser.add_argument("--turns", type=int, choices=range(1, 13), default=6)
    parser.add_argument("--retries", type=int, choices=range(6), default=2)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    report, _ = run_conversation(OllamaClient(args.model, args.base_url), args.prompt, args.turns, args.retries)
    return 0 if report["outcome"] == "scheduler_consensus" else 1


if __name__ == "__main__":
    raise SystemExit(main())
