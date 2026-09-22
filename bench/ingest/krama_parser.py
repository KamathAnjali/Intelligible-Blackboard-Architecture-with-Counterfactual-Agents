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
# CLI entry-point
# ─────────────────────────────────────────────────────────────────────────────

def _run_cli(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Load and validate a KramaBench dataset file.",
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
    args = parser.parse_args(argv)

    if args.file:
        tasks = load_krama_dataset(args.file, n=args.n)
    else:
        # Built-in sample data for self-test / CI smoke-test
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
            {
                # malformed — missing problem_statement — should be skipped
                "task_id": "kb_bad_001",
                "expected_answer": "N/A",
            },
        ]
        parsed_tasks: List[TaskFormat] = []
        for raw in sample_records:
            try:
                parsed_tasks.append(parse_krama_record(raw))
            except ValueError as e:
                logger.warning("Skipped malformed record: %s", e)
        tasks = parsed_tasks

    print(f"\n✅  Successfully loaded {len(tasks)} task(s).")

    if args.print_tasks or not args.file:
        for task in tasks:
            print(task.model_dump_json(indent=2))


if __name__ == "__main__":
    _run_cli(sys.argv[1:])
