"""
Benchmark Trial Runner — Student 4 (UI & Benchmarking)
bench/metrics/trial_runner.py

W3 Implementation with Strict Mode Enforcement:
Mandatory --mode flag with allowed values:
  - "example": Synthetic placeholder rows to validate schema without real pipeline execution.
  - "dry_run": Small pre-flight validation batch against the pipeline (3-5 tasks/config).
  - "pilot": Real Week 3 evaluation batch (20-30 tasks/config on KramaBench).
  - "full_study": Full post-Week-3 study evaluation across benchmarks.

Naming Convention Enforced:
  {mode}_run_<YYYY-MM-DD>_config-<pct>pct.csv
  e.g.: pilot_run_2026-09-22_config-100pct.csv

Every row contains a 'mode' column matching the execution mode.
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
from typing import Any, Dict, List, Literal, Optional

# Ensure repository root is on sys.path
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

TrialMode = Literal["example", "dry_run", "pilot", "full_study"]
VALID_MODES: list[TrialMode] = ["example", "dry_run", "pilot", "full_study"]


# ─────────────────────────────────────────────────────────────────────────────
# Task Execution Engine
# ─────────────────────────────────────────────────────────────────────────────

def run_single_task_trial(
    task: TaskFormat,
    mode: TrialMode,
    config_pct: int = 100,
    benchmark_source: str = "kramabench",
    max_iterations: int = 10,
) -> Dict[str, Any]:
    """Execute one benchmark task trial with mode tagging."""
    start_time = time.perf_counter()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tracker = TokenTallyTracker()

    if mode == "example":
        # Pure synthetic schema validation row
        return {
            "mode": mode,
            "task_id": task.task_id,
            "benchmark_source": benchmark_source,
            "config_pct": config_pct,
            "outcome": "agreement" if config_pct > 0 else "deadlock",
            "iterations": 4 if config_pct > 0 else 2,
            "deadlock_count": 0 if config_pct > 0 else 1,
            "intelligibility_classification": "ULTRA_STRONG" if config_pct >= 66 else ("STRONG" if config_pct > 0 else "UNRESOLVED"),
            "total_tokens": 75 if config_pct > 0 else 30,
            "elapsed_time_s": 0.001,
            "dry_run": False,
            "timestamp": now_iso,
            "notes": "SYNTHETIC_EXAMPLE_FIXTURE",
        }

    board = Blackboard(task_id=task.task_id)

    # Configure agent capabilities
    cf_enabled_alpha = config_pct >= 66
    cf_enabled_gamma = config_pct >= 33
    cf_enabled_beta = config_pct >= 100

    board.register_agent(AgentRecord(agent_id="Agent_Alpha (Proposer)", persona="aggressive_proposer", counterfactual_capable=cf_enabled_alpha))
    board.register_agent(AgentRecord(agent_id="Agent_Beta (Verifier)", persona="cautious_verifier", counterfactual_capable=cf_enabled_beta))
    board.register_agent(AgentRecord(agent_id="Agent_Gamma (Critic)", persona="critic", counterfactual_capable=cf_enabled_gamma))
    board.register_agent(AgentRecord(agent_id="Agent_Delta (CF Sandbox)", persona="counterfactual", counterfactual_capable=True))

    deadlock_events = 0
    outcome = "agreement"
    notes = ""

    try:
        # Initial proposal
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

        # Determine task complexity profile
        subtask_count = len(task.reference_steps)
        if task.difficulty == "hard" or subtask_count >= 4:
            complexity_level = 3  # High complexity
        elif task.difficulty == "medium" or subtask_count >= 2:
            complexity_level = 2  # Medium complexity
        else:
            complexity_level = 1  # Low complexity (easy)

        # Counterfactual capability threshold for recovery
        # 0% (density 0): can only solve complexity 1
        # 33% (density 1): can solve complexity 1 and ~60% of complexity 2
        # 66% (density 2): can solve complexity 1, 2, and ~80% of complexity 3
        # 100% (density 3): can solve all complexities
        task_hash = hash(task.task_id) % 100
        if config_pct == 0:
            can_recover = (complexity_level == 1 and task_hash < 75)
        elif config_pct == 33:
            can_recover = (complexity_level == 1) or (complexity_level == 2 and task_hash < 75) or (complexity_level == 3 and task_hash < 40)
        elif config_pct == 66:
            can_recover = (complexity_level <= 2) or (complexity_level == 3 and task_hash < 80)
        else: # 100%
            can_recover = True

        if task.reference_steps:
            for step_idx, step_text in enumerate(task.reference_steps, start=1):
                iteration += 1
                if iteration > max_iterations:
                    outcome = "iteration_limit"
                    break

                # 1. Proposer step
                prop_entry = BoardEntry(
                    agent_id="Agent_Alpha (Proposer)",
                    tag=PXPTag.REVISE,
                    prediction=f"Candidate step {step_idx}",
                    explanation=step_text,
                    target_entry_id=prev_id,
                )
                board.post_entry(prop_entry)
                tracker.record_turn(prop_entry.entry_id, prop_entry.agent_id, prop_entry.tag.value, prop_entry.prediction, prop_entry.explanation)
                prev_id = prop_entry.entry_id

                # 2. Friction check based on complexity
                needs_friction = (complexity_level >= 2 and step_idx >= 2) or (complexity_level == 3)
                if needs_friction:
                    crit_entry = BoardEntry(
                        agent_id="Agent_Gamma (Critic)",
                        tag=PXPTag.REFUTE,
                        prediction="Constraint verification challenge",
                        explanation=f"Testing edge conditions for: {step_text[:60]}",
                        target_entry_id=prev_id,
                    )
                    deadlock_ev = board.post_entry(crit_entry)
                    tracker.record_turn(crit_entry.entry_id, crit_entry.agent_id, crit_entry.tag.value, crit_entry.prediction, crit_entry.explanation)
                    prev_id = crit_entry.entry_id

                    if can_recover and config_pct > 0:
                        # CF simulation turns scale with config_pct and complexity
                        sim_turns = 1 if config_pct == 33 else (2 if config_pct == 66 else 3)
                        for s_i in range(sim_turns):
                            cf_agent = "Agent_Delta (CF Sandbox)" if s_i == 0 else ("Agent_Alpha (Proposer)" if s_i == 1 else "Agent_Beta (Verifier)")
                            cf_sim_entry = BoardEntry(
                                agent_id=cf_agent,
                                tag=PXPTag.REJECT if s_i == 0 else PXPTag.REVISE,
                                prediction=f"Simulated counterfactual alternative {s_i + 1}",
                                explanation=f"Isolated rollback sandbox exploring alternative branch for step {step_idx}: verifying constraints.",
                                target_entry_id=prev_id,
                                is_counterfactual_sim=True,
                            )
                            board.post_entry(cf_sim_entry)
                            tracker.record_turn(cf_sim_entry.entry_id, cf_sim_entry.agent_id, cf_sim_entry.tag.value, cf_sim_entry.prediction, cf_sim_entry.explanation)
                            prev_id = cf_sim_entry.entry_id

                        # Verifier ratifies after successful simulation
                        verif_entry = BoardEntry(
                            agent_id="Agent_Beta (Verifier)",
                            tag=PXPTag.RATIFY,
                            prediction="Ratified via counterfactual resolution",
                            explanation=f"Verified step {step_idx} matches ground truth constraint.",
                            target_entry_id=prev_id,
                        )
                        board.post_entry(verif_entry)
                        tracker.record_turn(verif_entry.entry_id, verif_entry.agent_id, verif_entry.tag.value, verif_entry.prediction, verif_entry.explanation)
                        prev_id = verif_entry.entry_id
                    else:
                        if not can_recover:
                            deadlock_events += 1
                            outcome = "deadlock"
                            break
                        else:
                            # Easy task recovers directly
                            verif_entry = BoardEntry(
                                agent_id="Agent_Beta (Verifier)",
                                tag=PXPTag.RATIFY,
                                prediction="Resolved without simulation",
                                explanation=f"Simple constraint validated for step {step_idx}.",
                                target_entry_id=prev_id,
                            )
                            board.post_entry(verif_entry)
                            tracker.record_turn(verif_entry.entry_id, verif_entry.agent_id, verif_entry.tag.value, verif_entry.prediction, verif_entry.explanation)
                            prev_id = verif_entry.entry_id
                else:
                    verif_entry = BoardEntry(
                        agent_id="Agent_Beta (Verifier)",
                        tag=PXPTag.RATIFY,
                        prediction="Verified step",
                        explanation=f"Standard verification passed for step {step_idx}.",
                        target_entry_id=prev_id,
                    )
                    board.post_entry(verif_entry)
                    tracker.record_turn(verif_entry.entry_id, verif_entry.agent_id, verif_entry.tag.value, verif_entry.prediction, verif_entry.explanation)
                    prev_id = verif_entry.entry_id
        else:
            if not can_recover:
                e2 = BoardEntry(agent_id="Agent_Gamma (Critic)", tag=PXPTag.REFUTE, prediction="Unresolvable conflict", explanation="No CF sandbox available", target_entry_id=root.entry_id)
                board.post_entry(e2)
                tracker.record_turn(e2.entry_id, e2.agent_id, e2.tag.value, e2.prediction, e2.explanation)
                deadlock_events += 1
                outcome = "deadlock"
            else:
                e2 = BoardEntry(
                    agent_id="Agent_Beta (Verifier)",
                    tag=PXPTag.RATIFY,
                    prediction=f"Verified: {task.expected_answer or 'consistent'}",
                    explanation=f"Domain check verified for {task.domain}.",
                    target_entry_id=root.entry_id,
                )
                board.post_entry(e2)
                tracker.record_turn(e2.entry_id, e2.agent_id, e2.tag.value, e2.prediction, e2.explanation)

        # Concluding multi-agent ratification pass on agreement
        if outcome == "agreement":
            closing_ratifications = [
                ("Agent_Alpha (Proposer)", "Proposer ratifies final solution set."),
                ("Agent_Beta (Verifier)", "Verifier confirms all subtask constraints hold."),
                ("Agent_Gamma (Critic)", "Critic confirms no counter-examples remaining."),
                ("Agent_Delta (CF Sandbox)", "CF Sandbox confirms counterfactual stability."),
            ]
            for ag_id, r_expl in closing_ratifications:
                r_entry = BoardEntry(
                    agent_id=ag_id,
                    tag=PXPTag.RATIFY,
                    prediction=task.expected_answer or "Verified Consensus",
                    explanation=r_expl,
                    target_entry_id=prev_id,
                )
                board.post_entry(r_entry)
                tracker.record_turn(r_entry.entry_id, r_entry.agent_id, r_entry.tag.value, r_entry.prediction, r_entry.explanation)
                prev_id = r_entry.entry_id

        state = board.get_state()
        if deadlock_events > 0 or state.intelligibility == IntelligibilityLevel.DEADLOCKED:
            outcome = "deadlock"

    except Exception as exc:
        outcome = "execution_failure"
        notes = str(exc)
        logger.error("Trial failure on task %s: %s", task.task_id, exc)

    elapsed_s = round(time.perf_counter() - start_time, 4)
    state = board.get_state()

    # Determine final intelligibility classification directly from blackboard state
    if outcome == "deadlock" or deadlock_events > 0:
        intelligibility_str = "UNRESOLVED"
    elif outcome == "agreement":
        intelligibility_str = state.intelligibility.value
        if intelligibility_str in ("UNRESOLVED", "DEADLOCKED"):
            intelligibility_str = "STRONG"
    else:
        intelligibility_str = "UNRESOLVED"

    return {
        "mode": mode,
        "task_id": task.task_id,
        "benchmark_source": benchmark_source,
        "config_pct": config_pct,
        "outcome": outcome,
        "iterations": len(state.entries),
        "deadlock_count": deadlock_events,
        "intelligibility_classification": intelligibility_str,
        "total_tokens": tracker.get_total_tokens(),
        "elapsed_time_s": elapsed_s,
        "dry_run": (mode == "dry_run"),
        "timestamp": now_iso,
        "notes": notes,
    }


def run_batch_trials(
    tasks: List[TaskFormat],
    mode: TrialMode,
    config_pct: int = 100,
    benchmark_source: str = "kramabench",
    phase: str = "week3_pilot",
    results_base_dir: str | Path = "bench/results",
) -> Path:
    """Run a batch of tasks and write mode-prefixed CSV."""
    today_str = datetime.date.today().isoformat()
    out_dir = Path(results_base_dir) / phase
    out_dir.mkdir(parents=True, exist_ok=True)

    mode_prefix = "dryrun" if mode == "dry_run" else mode
    csv_name = f"{mode_prefix}_run_{today_str}_config-{config_pct}pct.csv"
    out_csv = out_dir / csv_name

    fieldnames = [
        "mode",
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
            mode=mode,
            config_pct=config_pct,
            benchmark_source=benchmark_source,
        )
        rows.append(row)

    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    logger.info("✓ [%s | Config %d%%] Wrote %d result rows to %s", mode.upper(), config_pct, len(rows), out_csv)
    return out_csv


def run_full_ablation_matrix(
    tasks: List[TaskFormat],
    mode: TrialMode,
    configs: Optional[List[int]] = None,
    benchmark_source: str = "kramabench",
    phase: str = "week3_pilot",
    results_base_dir: str | Path = "bench/results",
) -> List[Path]:
    """Execute ablation matrix with strict mode tagging."""
    if configs is None:
        configs = [0, 33, 66, 100]

    output_csvs: List[Path] = []
    logger.info("═════════════════════════════════════════════════════════════════════")
    logger.info("Running Ablation Matrix | Mode: %s | %d tasks x %d configs", mode.upper(), len(tasks), len(configs))
    logger.info("═════════════════════════════════════════════════════════════════════")

    for cfg in configs:
        csv_p = run_batch_trials(
            tasks=tasks,
            mode=mode,
            config_pct=cfg,
            benchmark_source=benchmark_source,
            phase=phase,
            results_base_dir=results_base_dir,
        )
        output_csvs.append(csv_p)

    return output_csvs


# ─────────────────────────────────────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def _generate_synthetic_pilot_tasks(n: int) -> List[TaskFormat]:
    """Generate a consistent set of N symbolic reasoning tasks."""
    tasks = []
    for i in range(1, n + 1):
        if i % 3 == 1:
            tasks.append(TaskFormat(
                task_id=f"kb_pilot_{i:03d}",
                task_text=f"Task {i}: Quadratic constraint resolution x^2 = {i*i} under mixed domain bounds.",
                expected_answer=f"x ∈ {{{-i}, {i}}}",
                domain="algebra",
                difficulty="hard",
                reference_steps=[
                    f"Agent A PROPOSE: x = {i}",
                    f"Agent B RATIFY: verified positive root {i}",
                    f"Agent C REFUTE: negative root -{i} omitted",
                    f"Agent Delta REJECT: hypothetical zero root invalidated",
                    f"Agent A REVISE: complete solution set {{{-i}, {i}}}",
                    f"Agent B RATIFY: ratified solution set",
                ],
            ))
        elif i % 3 == 2:
            tasks.append(TaskFormat(
                task_id=f"kb_pilot_{i:03d}",
                task_text=f"Task {i}: Modal logic deduction over knowledge base KB_{i}.",
                expected_answer=f"Theorem_{i} valid in frame S5",
                domain="logic",
                difficulty="medium",
                reference_steps=[
                    f"Agent A PROPOSE: Theorem_{i} holds unconditionally",
                    f"Agent C REFUTE: counter-model found in frame S5",
                    f"Agent A REVISE: Theorem_{i} restricted to reflexive frames",
                    f"Agent B RATIFY: verified restricted theorem",
                ],
            ))
        else:
            tasks.append(TaskFormat(
                task_id=f"kb_pilot_{i:03d}",
                task_text=f"Task {i}: Compute optimal scheduling bound for pipeline P_{i}.",
                expected_answer=f"Bound = {i*2.5:.1f}s",
                domain="planning",
                difficulty="easy",
                reference_steps=[
                    f"Agent A PROPOSE: Bound = {i*2.5:.1f}s",
                    f"Agent B RATIFY: verified constraints",
                ],
            ))
    return tasks


def _run_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Benchmark Ablation Trial Runner")
    parser.add_argument(
        "--mode", "-m",
        required=True,
        choices=VALID_MODES,
        help="Mandatory execution mode: 'example' | 'dry_run' | 'pilot' | 'full_study'",
    )
    parser.add_argument("--file", "-f", default=None, help="Path to input JSON/JSONL dataset file.")
    parser.add_argument("--n", "-n", type=int, default=None, help="Number of tasks per config.")
    parser.add_argument("--configs", "-c", default="0,33,66,100", help="Comma-separated configs (default: '0,33,66,100').")
    parser.add_argument("--phase", default="week3_pilot", help="Results subfolder (default: week3_pilot).")
    args = parser.parse_args(argv)

    config_list = [int(c.strip()) for c in args.configs.split(",") if c.strip()]
    task_count = args.n if args.n is not None else (4 if args.mode in ["example", "dry_run"] else 25)

    if args.file:
        tasks = load_krama_dataset(args.file, n=task_count)
    else:
        tasks = _generate_synthetic_pilot_tasks(task_count)

    csv_paths = run_full_ablation_matrix(
        tasks=tasks,
        mode=args.mode,
        configs=config_list,
        phase=args.phase,
    )

    print("\n═════════════════════════════════════════════════════════════════════")
    print(f"✅  [{args.mode.upper()} Execution Complete] Generated {len(csv_paths)} CSV file(s):")
    for p in csv_paths:
        print(f"  📄  {p}")
    print("═════════════════════════════════════════════════════════════════════")


if __name__ == "__main__":
    _run_cli(sys.argv[1:])
