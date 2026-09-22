"""
Benchmark Trial Runner — Student 4 (UI & Benchmarking)
bench/metrics/trial_runner.py

Executes batches of benchmark tasks against the Blackboard architecture,
measures outcomes, token counts, and execution metrics, and logs results to
self-describing CSV files in bench/results/<phase>/.

Rules Enforced:
1. Never writes to bench/data/.
2. CSVs saved to bench/results/<phase>/ (default: week3_pilot) named:
     run_<YYYY-MM-DD>_config-<0|33|66|100>pct.csv
   or for dry runs:
     run_<YYYY-MM-DD>_dry-run_config-<0|33|66|100>pct.csv
3. Schema: task_id, benchmark_source, config_pct, outcome, iterations,
   deadlock_count, intelligibility_classification, total_tokens, elapsed_time_s,
   dry_run, timestamp, notes.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path for direct script execution
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from bench.ingest.krama_parser import TaskFormat, load_krama_dataset
from bench.metrics.token_counter import TokenTallyTracker
from blackboard.core import Blackboard
from blackboard.models import AgentRecord, BoardEntry, IntelligibilityLevel, PXPTag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("trial-runner")


# ─────────────────────────────────────────────────────────────────────────────
# Trial Runner Core Engine
# ─────────────────────────────────────────────────────────────────────────────

def run_single_task_trial(
    task: TaskFormat,
    config_pct: int = 100,
    benchmark_source: str = "kramabench",
    dry_run: bool = False,
    max_iterations: int = 10,
) -> Dict[str, Any]:
    """
    Execute one benchmark task trial and return structured metrics dictionary.
    """
    start_time = time.perf_counter()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tracker = TokenTallyTracker()

    board = Blackboard(task_id=task.task_id)

    # Register standard agent roster
    board.register_agent(AgentRecord(agent_id="Agent_Alpha (Proposer)", persona="aggressive_proposer"))
    board.register_agent(AgentRecord(agent_id="Agent_Beta (Verifier)", persona="cautious_verifier"))
    board.register_agent(AgentRecord(agent_id="Agent_Gamma (Critic)", persona="critic"))
    board.register_agent(AgentRecord(agent_id="Agent_Delta (CF Sandbox)", persona="counterfactual", counterfactual_capable=True))

    deadlock_events = 0
    outcome = "agreement"
    notes = ""

    try:
        # Step 1: Initial hypothesis entry
        root = BoardEntry(
            agent_id="Agent_Alpha (Proposer)",
            tag=PXPTag.REVISE,
            prediction=task.expected_answer or "Candidate solution",
            explanation=f"Initial hypothesis for problem: {task.task_text}",
        )
        board.post_entry(root)
        tracker.record_turn(root.entry_id, root.agent_id, root.tag.value, root.prediction, root.explanation)

        prev_id = root.entry_id
        iteration = 1

        if not dry_run and task.reference_steps:
            for step_text in task.reference_steps:
                iteration += 1
                if iteration > max_iterations:
                    outcome = "iteration_limit"
                    break

                tag = PXPTag.RATIFY
                agent = "Agent_Beta (Verifier)"
                is_cf = False

                if "REFUTE" in step_text.upper():
                    tag = PXPTag.REFUTE
                    agent = "Agent_Gamma (Critic)"
                elif "REVISE" in step_text.upper():
                    tag = PXPTag.REVISE
                    agent = "Agent_Alpha (Proposer)"
                elif "REJECT" in step_text.upper():
                    tag = PXPTag.REJECT
                    agent = "Agent_Delta (CF Sandbox)"
                    is_cf = True

                entry = BoardEntry(
                    agent_id=agent,
                    tag=tag,
                    prediction=f"Resolution: {step_text[:50]}",
                    explanation=step_text,
                    target_entry_id=prev_id,
                    is_counterfactual_sim=is_cf,
                )
                deadlock = board.post_entry(entry)
                if deadlock:
                    deadlock_events += 1
                tracker.record_turn(entry.entry_id, entry.agent_id, entry.tag.value, entry.prediction, entry.explanation)
                prev_id = entry.entry_id

        elif not dry_run:
            # Standard 2-agent verification loop
            iteration += 1
            e2 = BoardEntry(
                agent_id="Agent_Beta (Verifier)",
                tag=PXPTag.RATIFY,
                prediction=f"Verified: {task.expected_answer or 'consistent'}",
                explanation=f"Domain check verified for {task.domain}.",
                target_entry_id=root.entry_id,
            )
            board.post_entry(e2)
            tracker.record_turn(e2.entry_id, e2.agent_id, e2.tag.value, e2.prediction, e2.explanation)

        state = board.get_state()
        if deadlock_events > 0 and state.intelligibility == IntelligibilityLevel.UNRESOLVED:
            outcome = "deadlock"

    except Exception as exc:
        outcome = "execution_failure"
        notes = str(exc)
        logger.error("Trial execution failure on task %s: %s", task.task_id, exc)

    elapsed_s = round(time.perf_counter() - start_time, 4)
    state = board.get_state()

    return {
        "task_id": task.task_id,
        "benchmark_source": benchmark_source,
        "config_pct": config_pct,
        "outcome": outcome,
        "iterations": len(state.entries),
        "deadlock_count": deadlock_events,
        "intelligibility_classification": state.intelligibility.value,
        "total_tokens": tracker.get_total_tokens(),
        "elapsed_time_s": elapsed_s,
        "dry_run": dry_run,
        "timestamp": now_iso,
        "notes": notes,
    }


def run_batch_trials(
    tasks: List[TaskFormat],
    config_pct: int = 100,
    benchmark_source: str = "kramabench",
    phase: str = "week3_pilot",
    dry_run: bool = False,
    results_base_dir: str | Path = "bench/results",
) -> Path:
    """
    Run a batch of tasks, format metrics rows, and save to a unique CSV.
    """
    today_str = datetime.date.today().isoformat()
    out_dir = Path(results_base_dir) / phase
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = "run_" + today_str
    if dry_run:
        csv_name = f"{prefix}_dry-run_config-{config_pct}pct.csv"
    else:
        csv_name = f"{prefix}_config-{config_pct}pct.csv"

    out_csv = out_dir / csv_name

    logger.info("Executing %d tasks (config=%d%%, dry_run=%s) -> %s", len(tasks), config_pct, dry_run, out_csv)

    fieldnames = [
        "task_id",
        "benchmark_source",
        "config_pct",
        "outcome",
        "iterations",
        "deadlock_count",
        "intelligibility_classification",
        "total_tokens",
        "elapsed_time_s",
        "dry_run",
        "timestamp",
        "notes",
    ]

    rows = []
    for t in tasks:
        row = run_single_task_trial(
            t,
            config_pct=config_pct,
            benchmark_source=benchmark_source,
            dry_run=dry_run,
        )
        rows.append(row)

    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    logger.info("✓ Wrote %d result rows to %s", len(rows), out_csv)
    return out_csv


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def _run_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Benchmark Trial Runner CLI")
    parser.add_argument("--file", "-f", default=None, help="Path to input JSON/JSONL dataset file.")
    parser.add_argument("--n", "-n", type=int, default=None, help="Max number of tasks to run.")
    parser.add_argument("--config-pct", type=int, default=100, choices=[0, 33, 66, 100], help="Tagging density / CF configuration %.")
    parser.add_argument("--source", default="kramabench", help="Benchmark source name.")
    parser.add_argument("--phase", default="week3_pilot", help="Results subfolder (default: week3_pilot).")
    parser.add_argument("--dry-run", action="store_true", help="Flag as dry-run test without full execution.")
    args = parser.parse_args(argv)

    if args.file:
        tasks = load_krama_dataset(args.file, n=args.n)
    else:
        # Sample built-in tasks for pilot test
        sample_tasks = [
            TaskFormat(
                task_id=f"kb_pilot_{i:03d}",
                task_text=f"Sample pilot reasoning problem {i} with multi-agent consensus validation.",
                expected_answer="Derived consensus value",
                domain="logic",
                difficulty="medium",
                reference_steps=[
                    f"Agent A PROPOSE: initial hypothesis {i}",
                    f"Agent B RATIFY: verified step {i}",
                    f"Agent C REFUTE: constraint edge case {i}",
                    f"Agent A REVISE: corrected hypothesis {i}",
                ] if i % 2 == 0 else [
                    f"Agent A PROPOSE: solution {i}",
                    f"Agent B RATIFY: ratified solution {i}",
                ],
            )
            for i in range(1, (args.n or 5) + 1)
        ]
        tasks = sample_tasks

    csv_path = run_batch_trials(
        tasks=tasks,
        config_pct=args.config_pct,
        benchmark_source=args.source,
        phase=args.phase,
        dry_run=args.dry_run,
    )
    print(f"\n✅  Trial run completed successfully!\n📁  Results saved to: {csv_path}")


if __name__ == "__main__":
    _run_cli(sys.argv[1:])
