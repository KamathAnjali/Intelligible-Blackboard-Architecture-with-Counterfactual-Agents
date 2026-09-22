"""
KramaBench Ingest Parser — Student 4 (UI & Benchmarking)
bench/ingest/krama_parser.py

Converts KramaBench benchmark records into our internal TaskFormat.

Data Mapping (from docs/data_format_notes.md):
  task_id                          -> TaskFormat.task_id  (auto-generates if absent)
  problem_statement / task_text    -> TaskFormat.task_text  (mandatory)
  expected_answer                  -> TaskFormat.expected_answer
  domain                           -> TaskFormat.domain
  difficulty                       -> TaskFormat.difficulty
  ground_truth_reasoning_steps     -> TaskFormat.reference_steps
  metadata                         -> TaskFormat.metadata

CLI Usage:
  python -m bench.ingest.krama_parser --file bench/data/sample.jsonl --n 50
  python bench/ingest/krama_parser.py                     # self-test on built-in sample data
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("krama-parser")


# ─────────────────────────────────────────────────────────────────────────────
# Internal task contract
# ─────────────────────────────────────────────────────────────────────────────

class TaskFormat(BaseModel):
    """
    Standardised internal task used by the blackboard scheduler and benchmark runner.

    TODO (Week 2 — Student 1 Integration):
      Pass task_id directly to BlackboardState(task_id=task.task_id) when starting a new session.
    """

    task_id: str = Field(default_factory=lambda: f"kb_task_{uuid4().hex[:8]}")
    task_text: str
    expected_answer: str = ""
    domain: str = "general"
    difficulty: str = "medium"
    reference_steps: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("task_text")
    @classmethod
    def _non_empty_task_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("task_text cannot be empty — problem_statement is required.")
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_krama_schema(record: Any) -> Optional[str]:
    """
    Validate a raw record dict.

    Returns:
        None if valid, or an error message string if invalid.
    """
    if not isinstance(record, dict):
        return "Record is not a dict."
    problem = record.get("problem_statement") or record.get("task_text")
    if not problem or not str(problem).strip():
        return "Missing/empty 'problem_statement' or 'task_text'."
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Single-record parser
# ─────────────────────────────────────────────────────────────────────────────

def parse_krama_record(raw: Dict[str, Any]) -> TaskFormat:
    """
    Convert one raw KramaBench dict into a TaskFormat.

    Raises:
        ValueError: if the record fails schema validation.
    """
    err = validate_krama_schema(raw)
    if err:
        raise ValueError(f"Invalid KramaBench record: {err}  raw={raw!r}")

    task_id = str(raw.get("task_id") or f"kb_task_{uuid4().hex[:8]}")
    task_text = str(raw.get("problem_statement") or raw.get("task_text")).strip()
    expected_answer = str(raw.get("expected_answer", "")).strip()
    domain = str(raw.get("domain", "general"))
    difficulty = str(raw.get("difficulty", "medium"))
    reference_steps = list(raw.get("ground_truth_reasoning_steps") or [])
    metadata = dict(raw.get("metadata") or {})

    # TODO (Week 2): domain-specific prompt wrappers / pre-tokenisation
    return TaskFormat(
        task_id=task_id,
        task_text=task_text,
        expected_answer=expected_answer,
        domain=domain,
        difficulty=difficulty,
        reference_steps=reference_steps,
        metadata=metadata,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Dataset loader
# ─────────────────────────────────────────────────────────────────────────────

def load_krama_dataset(
    filepath: str | Path,
    n: Optional[int] = None,
) -> List[TaskFormat]:
    """
    Load up to *n* KramaBench tasks from a .json or .jsonl file.

    Args:
        filepath: Path to dataset file.
        n: Maximum number of tasks to load. None = load all.

    Returns:
        List of TaskFormat objects (malformed records are skipped/logged).
    """
    path = Path(filepath)
    if not path.exists():
        logger.warning("File not found: %s", path)
        return []

    parsed: List[TaskFormat] = []
    skipped = 0

    def _try_parse(raw: Any, line_ref: str) -> None:
        nonlocal skipped
        try:
            parsed.append(parse_krama_record(raw))
        except Exception as exc:
            logger.warning("Skipped malformed record (%s): %s", line_ref, exc)
            skipped += 1

    try:
        if path.suffix == ".jsonl":
            with open(path, encoding="utf-8") as fh:
                for idx, line in enumerate(fh):
                    if n is not None and len(parsed) >= n:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError as exc:
                        logger.warning("JSON parse error at line %d: %s", idx + 1, exc)
                        skipped += 1
                        continue
                    _try_parse(raw, f"line {idx + 1}")
        else:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            records = data if isinstance(data, list) else [data]
            for idx, raw in enumerate(records):
                if n is not None and len(parsed) >= n:
                    break
                _try_parse(raw, f"index {idx}")

    except Exception as exc:
        logger.error("Failed to load dataset from %s: %s", path, exc)

    logger.info(
        "Loaded %d tasks successfully, skipped %d malformed records (from %s).",
        len(parsed), skipped, path,
    )
    return parsed


# ─────────────────────────────────────────────────────────────────────────────
# Live Task Feed Engine (W2 Day 3)
# ─────────────────────────────────────────────────────────────────────────────

async def async_feed_tasks_to_live_board(
    tasks: List[TaskFormat],
    board: Optional[Any] = None,
    interval_s: float = 0.8,
    emit_to_bus: bool = True,
) -> List[Any]:
    """
    Asynchronously feed N parsed tasks into the live Blackboard and EventBus.

    For each task:
      1. Initializes/resets a Blackboard instance.
      2. Registers standard agent roster (Proposer, Verifier, Critic, CF Sandbox).
      3. Posts root PROPOSE entry derived from the task statement.
      4. Progressively executes reasoning steps (RATIFY, REFUTE, REVISE, REJECT),
         publishing each event to ui.server.event_bus so the frontend renders in real-time.
    """
    import asyncio
    from blackboard.core import Blackboard
    from blackboard.models import AgentRecord, BoardEntry, PXPTag

    # Try importing bus if live emission requested
    bus_instance = None
    if emit_to_bus:
        try:
            from ui.server.event_bus import bus
            bus_instance = bus
        except Exception as e:
            logger.warning("EventBus import failed (server may not be initialized): %s", e)

    results = []

    for task_idx, task in enumerate(tasks, start=1):
        logger.info("▶ [LiveFeed] Starting Task %d/%d: %s ('%s')", task_idx, len(tasks), task.task_id, task.domain)

        active_board = board or Blackboard(task_id=task.task_id)

        # Register standard agent personas
        active_board.register_agent(AgentRecord(agent_id="Agent_Alpha (Proposer)", persona="aggressive_proposer", model_name="mistral-7b-instruct"))
        active_board.register_agent(AgentRecord(agent_id="Agent_Beta (Verifier)", persona="cautious_verifier", model_name="mistral-7b-instruct"))
        active_board.register_agent(AgentRecord(agent_id="Agent_Gamma (Critic)", persona="critic", model_name="mistral-7b-instruct"))
        active_board.register_agent(AgentRecord(agent_id="Agent_Delta (CF Sandbox)", persona="counterfactual", model_name="mistral-7b-instruct", counterfactual_capable=True))

        # Helper to post and emit
        async def _post_and_emit(entry: BoardEntry) -> None:
            active_board.post_entry(entry)
            if bus_instance:
                from ui.server.board_tap import _entry_to_event
                event = _entry_to_event(entry.model_dump(mode="json"))
                await bus_instance.publish(event)
            await asyncio.sleep(interval_s)

        # 1. Root Proposal (uses REVISE as initial hypothesis post per blackboard schema)
        root_entry = BoardEntry(
            agent_id="Agent_Alpha (Proposer)",
            tag=PXPTag.REVISE,
            prediction=task.expected_answer or "Initial proposed candidate solution",
            explanation=f"Problem: {task.task_text}",
        )
        await _post_and_emit(root_entry)

        # 2. Sequential steps derived from task reference_steps or simulated reasoning
        if task.reference_steps:
            prev_id = root_entry.entry_id
            for step_text in task.reference_steps:
                # Infer tag and agent from step text or default
                tag = PXPTag.RATIFY
                agent = "Agent_Beta (Verifier)"
                if "REFUTE" in step_text.upper():
                    tag = PXPTag.REFUTE
                    agent = "Agent_Gamma (Critic)"
                elif "REVISE" in step_text.upper():
                    tag = PXPTag.REVISE
                    agent = "Agent_Alpha (Proposer)"
                elif "REJECT" in step_text.upper():
                    tag = PXPTag.REJECT
                    agent = "Agent_Delta (CF Sandbox)"

                entry = BoardEntry(
                    agent_id=agent,
                    tag=tag,
                    prediction=f"Step analysis: {step_text[:60]}...",
                    explanation=step_text,
                    target_entry_id=prev_id,
                    is_counterfactual_sim=(tag == PXPTag.REJECT),
                )
                await _post_and_emit(entry)
                prev_id = entry.entry_id
        else:
            # Default 3-step convergence for unannotated tasks
            e2 = BoardEntry(
                agent_id="Agent_Beta (Verifier)",
                tag=PXPTag.RATIFY,
                prediction=f"Verified: {task.expected_answer or 'consistent'}",
                explanation=f"Cross-checked domain constraints for {task.domain}.",
                target_entry_id=root_entry.entry_id,
            )
            await _post_and_emit(e2)

            e3 = BoardEntry(
                agent_id="Agent_Gamma (Critic)",
                tag=PXPTag.RATIFY,
                prediction="Consensus ratified across all constraints",
                explanation="No refutations identified. Solution satisfies intelligibility criteria.",
                target_entry_id=e2.entry_id,
            )
            await _post_and_emit(e3)

        state = active_board.get_state()
        results.append(state)
        logger.info("✓ [LiveFeed] Completed Task %s with Intelligibility: %s", task.task_id, state.intelligibility)

    if bus_instance:
        await bus_instance.publish({"type": "stream_complete", "source": "KRAMABENCH_LIVE_FEED"})

    return results


def feed_tasks_to_live_board(
    tasks: List[TaskFormat],
    board: Optional[Any] = None,
    interval_s: float = 0.8,
    emit_to_bus: bool = True,
) -> List[Any]:
    """Synchronous wrapper around async_feed_tasks_to_live_board."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            async_feed_tasks_to_live_board(tasks, board=board, interval_s=interval_s, emit_to_bus=emit_to_bus)
        )

    task = loop.create_task(
        async_feed_tasks_to_live_board(tasks, board=board, interval_s=interval_s, emit_to_bus=emit_to_bus)
    )
    return [task]  # type: ignore


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry-point
# ─────────────────────────────────────────────────────────────────────────────

def _run_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Load, validate, or live-feed KramaBench dataset tasks.",
    )
    parser.add_argument(
        "--file", "-f",
        default=None,
        help="Path to .json or .jsonl dataset file. Defaults to built-in sample data.",
    )
    parser.add_argument(
        "--n", "-n",
        type=int,
        default=None,
        help="Maximum number of records to load. Default: all.",
    )
    parser.add_argument(
        "--print-tasks",
        action="store_true",
        help="Print each parsed task as JSON.",
    )
    parser.add_argument(
        "--feed-live",
        action="store_true",
        help="Feed parsed tasks directly into the live Blackboard session & EventBus stream.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.8,
        help="Seconds between simulated live agent turns (default: 0.8s).",
    )
    args = parser.parse_args(argv)

    if args.file:
        tasks = load_krama_dataset(args.file, n=args.n)
    else:
        sample_records = [
            {
                "task_id": "kb_demo_101",
                "problem_statement": "Evaluate the stability of a 3-agent PXP consensus loop where Agent A proposes, B ratifies, C refutes.",
                "expected_answer": "ULTRA_STRONG consensus is achievable if A revises after C's refutation.",
                "domain": "multi-agent-coordination",
                "difficulty": "hard",
                "ground_truth_reasoning_steps": [
                    "Agent A PROPOSE: x = 12",
                    "Agent B RATIFY: agrees with x = 12",
                    "Agent C REFUTE: x = -12 also satisfies x^2 = 144",
                    "Agent A REVISE: x ∈ {-12, 12}",
                    "Agent B RATIFY: consensus on updated solution",
                ],
            },
            {
                "task_id": "kb_demo_102",
                "problem_statement": "Determine whether three conflicting agents can reach consensus on a modal logic deduction.",
                "expected_answer": "STRONG consensus after one REVISE iteration.",
                "domain": "logic",
                "difficulty": "medium",
            },
        ]
        tasks = [parse_krama_record(r) for r in sample_records]

    print(f"\n✅  Successfully loaded {len(tasks)} task(s).")

    if args.feed_live:
        print(f"\n🚀  Feeding {len(tasks)} task(s) into live Blackboard & EventBus (interval={args.interval}s)...")
        feed_tasks_to_live_board(tasks, interval_s=args.interval)
        print("✓  Live task execution completed.")
    elif args.print_tasks or not args.file:
        for task in tasks:
            print(task.model_dump_json(indent=2))


if __name__ == "__main__":
    _run_cli(sys.argv[1:])
