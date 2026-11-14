"""Reproducible three-agent live checks and labelled deadlock fixtures.

This is a small curated sanity-check workload, not a benchmark or a unit-test suite.
Expected answers are used after inference and never sent to the agents.
"""

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone

from agents.conversation import DISAGREEMENT_AGENTS, run_conversation
from agents.llm_client import BASE_URL, MODEL, OPTIONS, ROOT, OllamaClient
from blackboard.core import Blackboard
from blackboard.models import AgentRecord, BoardEntry, PXPTag
from blackboard.scheduler import Scheduler
from blackboard.store import InMemoryJSONStore


SESSION_CASES = (
    ("gsm8k_download_restart", "A 200 GB file downloads at 2 GB per minute. At 40% complete, "
     "a forced update takes 20 minutes, then the download has to start over. How many minutes pass "
     "before the download finishes? Use only the integer number of minutes as prediction.", "160"),
    ("gsm8k_movie_collection", "Replacing 600 movies costs $10 per movie normally. One-third are "
     "sold as a bundle for $6 each. Of the movies left, 40% are older titles priced at $5 each; "
     "the rest cost the normal price. What is the total cost? Use only the number of dollars as "
     "prediction.", "4400"),
    ("mscore_live_performance_l3", "A live show has both an in-person audience and digital features "
     "for remote viewers. Which option best provides direct contact with the performers, the venue "
     "atmosphere, and the shared experience of the audience?\n\n"
     "A. Watch a recording afterward\nB. Attend the show in person\nC. Read reviews of the show\n"
     "D. Check the schedule\nE. Buy tickets in advance\nF. Listen to a podcast about it\n"
     "G. Join a discussion after the show\nH. Use the digital features during the show\n\n"
     "Use only the letter A-H as prediction.", "B"),
    ("mscore_school_activity_inclusion_l3", "A family visiting Germany wants a school activity that "
     "teaches a practical skill, includes students from varied backgrounds, and involves the wider "
     "community. Which option best meets all three goals?\n\n"
     "A. Join the school swimming program, which accommodates different cultural practices\n"
     "B. Take an advanced mathematics course\nC. Enroll in an intensive German class\n"
     "D. Go on a local history tour\nE. Attend a cooking workshop\nF. Join a school art project\n"
     "G. Visit a multicultural festival\nH. Attend a one-time water-safety talk\n\n"
     "Use only the letter A-H as prediction.", "A"),
)

FIXTURE_MISSING_PREMISE_TASK = (
    "Some museum members receive a guest pass. Noor is a museum member. Do the supplied facts "
    "determine whether Noor receives a guest pass? Use insufficient information as prediction."
)
FIXTURE_CONTRADICTORY_TASK = (
    "Two notes describe the same sealed box at the same instant. One says the box is empty; the "
    "other says the box is not empty. Both are asserted as facts. Are these notes consistent or "
    "inconsistent? Use inconsistent as prediction."
)


def prompt_fingerprints():
    return {path.name: hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            for path in sorted((ROOT / "agents/prompts").glob("*.txt"))}


def consensus_evidence(report, expected):
    """Check every active actor, separately from the prototype board heuristic."""
    board = report["board"]
    active = {key for key, value in board["agents"].items() if value["active"]}
    latest = {entry["agent_id"]: entry for entry in board["entries"]
              if entry["agent_id"] in active and not entry["is_counterfactual_sim"]}
    predictions = {entry["prediction"].strip().casefold() for entry in latest.values()}
    unanimous = (len(active) == 3 and set(latest) == active and len(predictions) == 1
                 and all(entry["tag"] == "RATIFY" for entry in latest.values()))
    return {"unanimous_ratify": unanimous,
            "correct_consensus": (unanimous and report["outcome"] == "scheduler_consensus"
                                  and predictions == {expected.casefold()}),
            "latest_by_agent": {agent: {"tag": entry["tag"], "prediction": entry["prediction"]}
                                for agent, entry in latest.items()}}


def live_sessions(client, directory, turns, retries):
    """Run the complete fixed set; preserve errors and interrupted partial work."""
    report = {"source": "live_ollama", "created_at": datetime.now(timezone.utc).isoformat(),
              "model": client.model, "base_url": client.base_url, "options": dict(OPTIONS),
              "prompt_library": "v1", "prompt_sha256": prompt_fingerprints(),
              "max_turns": turns, "max_retries": retries,
              "planned_cases": len(SESSION_CASES), "records": [], "complete": False}
    path = directory / "summary.json"
    try:
        report["ollama"] = client.request("/api/version")
        for case_id, task, expected in SESSION_CASES:
            print(f"\nCase: {case_id}", flush=True)
            try:
                session, transcript = run_conversation(
                    client, task, turns, retries, directory / case_id, agent_count=3)
                usage = {key: sum(turn["usage"].get(key, 0) for turn in session["turns"])
                         for key in ("calls", "prompt_tokens", "output_tokens", "wall_seconds")}
                evidence = consensus_evidence(session, expected)
                record = {"case_id": case_id, "task": task, "expected": expected,
                          "outcome": session["outcome"], "turns": len(session["turns"]),
                          "failures": session["failures"], "usage": usage,
                          "transcript": str(transcript.relative_to(ROOT)), **evidence}
            except Exception as exc:
                # Preserve any transcript written before an unexpected case-level
                # failure, record it in the batch, and continue with the next case.
                partial_reports = sorted((directory / case_id).glob("conversation-*/transcript.json"))
                partial_path = partial_reports[-1] if partial_reports else None
                partial_turns = []
                if partial_path is not None:
                    try:
                        partial_turns = json.loads(partial_path.read_text(encoding="utf-8")).get("turns", [])
                    except (OSError, ValueError):
                        pass
                usage = {key: sum(turn.get("usage", {}).get(key, 0) for turn in partial_turns)
                         for key in ("calls", "prompt_tokens", "output_tokens", "wall_seconds")}
                record = {"case_id": case_id, "task": task, "expected": expected,
                          "outcome": "runner_error", "turns": len(partial_turns),
                          "failures": [{"category": "session_runner_error", "error": str(exc)}],
                          "usage": usage,
                          "transcript": (str(partial_path.relative_to(ROOT))
                                         if partial_path is not None else None),
                          "unanimous_ratify": False, "correct_consensus": False,
                          "latest_by_agent": {}}
                print(f"Runner error recorded for {case_id}; continuing to the next case.", flush=True)
            report["records"].append(record)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Correct three-agent consensus: {record['correct_consensus']}", flush=True)
            if record["outcome"] != "runner_error" and any(
                "transport" in failure["category"] for failure in record["failures"]
            ):
                report["stopped_reason"] = "transport_failure"
                break
        report["complete"] = len(report["records"]) == len(SESSION_CASES)
    except KeyboardInterrupt:
        report["stopped_reason"] = "interrupted"
    except (RuntimeError, OSError, ValueError) as exc:
        report["stopped_reason"] = str(exc)
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["consensus_cases"] = sum(row["unanimous_ratify"] for row in report["records"])
        report["correct_consensus_cases"] = sum(row["correct_consensus"] for row in report["records"])
        report["required_cases"] = math.ceil(len(SESSION_CASES) / 2)
        report["target_met"] = (report["complete"]
                                and report["correct_consensus_cases"] >= report["required_cases"])
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nCorrect consensus: {report['correct_consensus_cases']}/{len(SESSION_CASES)}; "
          f"target met: {report['target_met']}\nReport: {path}", flush=True)
    return 0 if report["target_met"] else 1


def deadlock_fixtures(directory):
    """Record two explicit negative-tag stalemates, without any model calls."""
    scenarios = (
        ("missing_premise", FIXTURE_MISSING_PREMISE_TASK, "yes",
         "Every museum member receives a guest pass.",
         "The needed premise about Noor is missing; this contribution offers no correction."),
        ("contradictory_premises", FIXTURE_CONTRADICTORY_TASK, "consistent",
         "The box is empty and not empty, so both notes can be accepted together.",
         "The claims conflict at the same instant; this contribution offers no correction."),
    )
    records = []
    for case_id, task, prediction, explanation, objection in scenarios:
        task_id = f"fixture-{case_id}"
        store = InMemoryJSONStore(directory / case_id)
        board = Blackboard(task_id, store)
        scheduler = Scheduler(board)
        for agent_id, persona in DISAGREEMENT_AGENTS:
            agent = AgentRecord(agent_id=agent_id, persona=persona, model_name="controlled-fixture")
            board.register_agent(agent)
            scheduler.register_agent(agent)
        target = None
        cutoff = None
        for tag in (PXPTag.REVISE, PXPTag.REFUTE, PXPTag.REJECT, PXPTag.REFUTE, PXPTag.REJECT):
            actor = scheduler.next_agent()
            entry = BoardEntry(agent_id=actor.agent_id, tag=tag, prediction=prediction,
                               explanation=explanation if target is None else objection,
                               target_entry_id=target)
            scheduler.submit_entry(entry)
            cutoff = cutoff or entry.entry_id
            target = entry.entry_id
        state = board.get_state()
        snapshot = store.snapshot_to_disk(task_id)
        deadlocked = state.intelligibility.value == "DEADLOCKED" and not scheduler.is_running()
        record = {"case_id": case_id, "source": "controlled_fixture", "model_calls": 0,
                  "task": task, "cutoff_entry_id": cutoff, "deadlock_verified": deadlocked,
                  "snapshot": str(snapshot.relative_to(ROOT)), "board": state.model_dump(mode="json")}
        records.append(record)
        print(f"{case_id}: deadlock verified={deadlocked}; model calls=0", flush=True)
    path = directory / "deadlock-fixtures.json"
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report: {path}", flush=True)
    return 0 if all(record["deadlock_verified"] for record in records) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deadlock-fixtures", action="store_true")
    parser.add_argument("--turns", type=int, choices=range(4, 13), default=6)
    parser.add_argument("--retries", type=int, choices=range(6), default=2)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = ROOT / "results/sessions" / f"session-checks-{stamp}"
    directory.mkdir(parents=True)
    if args.deadlock_fixtures:
        return deadlock_fixtures(directory)
    return live_sessions(OllamaClient(args.model, args.base_url), directory, args.turns, args.retries)


if __name__ == "__main__":
    raise SystemExit(main())
