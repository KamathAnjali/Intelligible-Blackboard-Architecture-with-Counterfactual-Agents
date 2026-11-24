"""Small, auditable MSCoRe pilot runner using the project's live blackboard.

This is an adapted research runner, not the dataset authors' official harness.
Open-ended records remain unscored until a semantic metric is configured.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.conversation import run_conversation
from agents.llm_client import BASE_URL, MODEL, OllamaClient
from bench.ingest.mscore_parser import load_mscore_file, score_exact


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _usage_for_attempts(attempts: list[dict[str, Any]]) -> tuple[int, int]:
    prompt = completion = 0
    for attempt in attempts:
        raw_outputs = [attempt.get("raw")]
        check = attempt.get("self_check")
        if isinstance(check, dict):
            raw_outputs.append(check.get("raw"))
        for raw in raw_outputs:
            if isinstance(raw, dict):
                prompt += int(raw.get("prompt_eval_count", 0) or 0)
                completion += int(raw.get("eval_count", 0) or 0)
    return prompt, completion


def _elapsed_for_attempts(attempts: list[dict[str, Any]]) -> float:
    elapsed = 0.0
    for attempt in attempts:
        outputs = [attempt.get("raw")]
        check = attempt.get("self_check")
        if isinstance(check, dict):
            outputs.append(check.get("raw"))
        for raw in outputs:
            if isinstance(raw, dict):
                elapsed += float(raw.get("wall_seconds", 0.0) or 0.0)
    return elapsed


def run_pilot(*, input_path: Path, output_dir: Path, limit: int, selection_seed: int,
              model_seed: int, model: str, base_url: str, turns: int, retries: int,
              counterfactual_fraction: float, counterfactual_seed: int,
              approve_genuine_run: bool, task_ids: list[str] | None = None,
              num_predict: int = 2048) -> Path:
    if not approve_genuine_run:
        raise ValueError("real model calls require --approve-genuine-run")
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    if type(num_predict) is not int or not 1 <= num_predict <= 8192:
        raise ValueError("num_predict must be an integer between 1 and 8192")
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite existing output directory: {output_dir}")
    tasks = load_mscore_file(input_path)
    if not tasks:
        raise ValueError("the input file contains no valid MSCoRe tasks")
    if task_ids is not None:
        if not task_ids or any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
            raise ValueError("task_ids must contain one or more nonempty task IDs")
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("task_ids must not contain duplicates")
        by_id = {task.agent_task.task_id: task for task in tasks}
        missing = [task_id for task_id in task_ids if task_id not in by_id]
        if missing:
            raise ValueError(f"task IDs not found in input: {', '.join(missing)}")
        selected = [by_id[task_id] for task_id in task_ids]
    else:
        selected = random.Random(selection_seed).sample(list(tasks), min(limit, len(tasks)))
    client = OllamaClient(model=model, base_url=base_url, model_seed=model_seed)
    client.options["num_predict"] = num_predict
    output_dir.mkdir(parents=True)
    rows: list[dict[str, Any]] = []
    for task in selected:
        agent_task = task.agent_task
        # Only the agent-facing fields cross into the conversation. Keep reference
        # values in this process for scoring after inference, never in a prompt.
        row: dict[str, Any] = {
            "task_id": agent_task.task_id,
            "task_type": agent_task.task_type,
            "domain": agent_task.domain,
            "source_record": agent_task.source_record,
            "source_row_index": agent_task.source_row_index,
            "outcome": "execution_failure",
            "evaluation_status": "not_scored",
            "completed_without_crash": False,
            "failure_reason": "",
            "prediction": "",
            "metric": "",
            "score": "",
            "evaluation_reason": "",
            "correct": "",
            "scored": False,
            "adapted": True,
            "evaluator_mode": "project-adapted-exact",
            "iterations": 0,
            "deadlock_count": 0,
            "intelligibility_classification": "",
            "reviewer_rejections": 0,
            "counterfactual_triggers": 0,
            "counterfactual_simulations": 0,
            "counterfactual_changed_outcome": 0,
            "counterfactual_capable_agents": "[]",
            "live_prompt_tokens": 0,
            "live_completion_tokens": 0,
            "simulation_prompt_tokens": 0,
            "simulation_completion_tokens": 0,
            "total_prompt_tokens": 0,
            "total_completion_tokens": 0,
            "live_elapsed_seconds": 0.0,
            "simulation_elapsed_seconds": 0.0,
            "elapsed_seconds": 0.0,
            "transcript": "",
            "error": "",
        }
        started = time.perf_counter()
        try:
            report, transcript_path = run_conversation(
                client,
                task=(agent_task.prompt + "\n\nGive a short final answer in the task's language. "
                      "Keep the explanation to one concise sentence."),
                task_id=agent_task.task_id,
                output_dir=output_dir / "sessions",
                max_turns=turns,
                max_retries=retries,
                # MSCoRe records here are direct judgment/question prompts, not
                # data-tool tasks. Let the reviewer check task facts rather than
                # presenting the prompt as though it were a tool result.
                review_evidence=None,
                counterfactual_fraction=counterfactual_fraction,
                counterfactual_seed=counterfactual_seed,
            )
            row["outcome"] = report["outcome"]
            row["completed_without_crash"] = report["outcome"] not in {"error", "interrupted"}
            row["iterations"] = len(report.get("turns", []))
            row["deadlock_count"] = len(report.get("board", {}).get("deadlocks", []))
            row["intelligibility_classification"] = report.get("scheduler_status", "")
            counters = report.get("workflow_counters", {})
            for counter in ("reviewer_rejections", "counterfactual_triggers",
                            "counterfactual_simulations", "counterfactual_changed_outcome"):
                row[counter] = counters.get(counter, 0)
            row["counterfactual_capable_agents"] = json.dumps(
                report.get("counterfactual_capable_agents", []), ensure_ascii=False)
            if report.get("failures"):
                row["failure_reason"] = json.dumps(report["failures"], ensure_ascii=False)
            elif report["outcome"] in {"error", "interrupted", "deadlocked", "turn_limit"}:
                row["failure_reason"] = f"conversation_ended:{report['outcome']}"
            row["transcript"] = str(transcript_path.relative_to(output_dir))
            live_attempts = [attempt for turn in report.get("turns", [])
                             for attempt in turn.get("attempts", [])]
            sim_attempts = [attempt for sim in report.get("counterfactual_attempts", [])
                            for attempt in sim.get("attempts", [])]
            row["live_prompt_tokens"], row["live_completion_tokens"] = _usage_for_attempts(live_attempts)
            row["simulation_prompt_tokens"], row["simulation_completion_tokens"] = _usage_for_attempts(sim_attempts)
            row["total_prompt_tokens"] = row["live_prompt_tokens"] + row["simulation_prompt_tokens"]
            row["total_completion_tokens"] = row["live_completion_tokens"] + row["simulation_completion_tokens"]
            row["live_elapsed_seconds"] = round(_elapsed_for_attempts(live_attempts), 3)
            row["simulation_elapsed_seconds"] = round(_elapsed_for_attempts(sim_attempts), 3)
            row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            if report["outcome"] == "scheduler_consensus":
                active_ids = {"proposer", "verifier"}
                final = next((entry for entry in reversed(report["board"]["entries"])
                              if entry["agent_id"] in active_ids
                              and not entry.get("is_counterfactual_sim", False)), None)
                if final:
                    row["prediction"] = final["prediction"]
                    score = score_exact(task, final["prediction"])
                    row.update({"metric": score["metric"] or "",
                                "score": "" if score["score"] is None else score["score"],
                                "evaluation_reason": score["reason"] or "",
                                "scored": score["scored"],
                                "evaluation_status": "scored" if score["scored"] else "completed_unscored",
                                "correct": (bool(score["score"] == 1.0)
                                            if score["scored"] else "")})
                else:
                    row["evaluation_status"] = "failed_before_scoring"
                    row["failure_reason"] = "consensus ended without an active-agent candidate"
            elif report["outcome"] in {"error", "interrupted", "deadlocked", "turn_limit"}:
                row["evaluation_status"] = "failed_before_scoring"
        except Exception as exc:  # preserve a row for every selected task
            row["error"] = f"{type(exc).__name__}: {exc}"
            row["failure_reason"] = row["error"]
            row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        rows.append(row)
        _write_json(output_dir / "records" / f"{agent_task.task_id}.json", row)

    with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "benchmark": "MSCoRe",
        "runner": "project_blackboard_adapter",
        "result_label": "adapted; not the upstream official harness",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(input_path.expanduser().resolve()),
        "source_sha256": tasks.report.source_sha256,
        "model": model,
        "base_url": base_url,
        "selection_seed": selection_seed,
        "model_seed": model_seed,
        "model_options": dict(client.options),
        "counterfactual_fraction": counterfactual_fraction,
        "counterfactual_seed": counterfactual_seed,
        "requested_tasks": len(task_ids) if task_ids is not None else limit,
        "selection_mode": "explicit_task_ids" if task_ids is not None else "seeded_sample",
        "selected_tasks": [task.agent_task.task_id for task in selected],
        "counts": {
            "attempted": len(rows),
            "completed_without_crash": sum(bool(row["completed_without_crash"]) for row in rows),
            "scored": sum(bool(row["scored"]) for row in rows),
            "correct": sum(row["correct"] is True for row in rows),
            "failed_before_scoring": sum(row["evaluation_status"] == "failed_before_scoring" for row in rows),
        },
        "turn_limit": turns,
        "retry_limit": retries,
        "reference_answers_in_agent_context": False,
        "review_mode": "task_facts",
        "scoring": "adapted normalized exact match for objective judgment/choice/order labels; open-ended unscored",
        "token_accounting": "Ollama prompt_eval_count/eval_count, including review calls and counterfactual attempts when present",
    }
    _write_json(output_dir / "run_manifest.json", manifest)
    return output_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--selection-seed", type=int, default=42)
    parser.add_argument("--model-seed", type=int, default=42)
    parser.add_argument("--counterfactual-fraction", type=float, choices=(0, 0.33, 0.66, 1.0), default=0.0)
    parser.add_argument("--counterfactual-seed", type=int, default=42)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--turns", type=int, default=6)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--task-id", action="append", dest="task_ids",
                        help="run this task ID (repeat the flag to select multiple tasks)")
    parser.add_argument("--num-predict", type=int, default=2048,
                        help="maximum generated tokens per Ollama call (default: 2048)")
    parser.add_argument("--approve-genuine-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        path = run_pilot(input_path=args.input, output_dir=args.output_dir,
                         limit=args.limit, selection_seed=args.selection_seed,
                         model_seed=args.model_seed, model=args.model,
                         base_url=args.base_url, turns=args.turns, retries=args.retries,
                         counterfactual_fraction=args.counterfactual_fraction,
                         counterfactual_seed=args.counterfactual_seed,
                         task_ids=args.task_ids, num_predict=args.num_predict,
                         approve_genuine_run=args.approve_genuine_run)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(f"Saved adapted MSCoRe pilot: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
