"""
KramaBench Ingest Parser — Student 4 (UI & Benchmarking)

Skeleton for converting KramaBench benchmark records into our internal task format.

Data Mapping Strategy (from docs/data_format_notes.md):
------------------------------------------------------
KramaBench raw fields -> Internal TaskFormat fields:
  1. `task_id` (str)               -> `TaskFormat.task_id` (auto-generates UUID if missing)
  2. `problem_statement` (str)     -> `TaskFormat.task_text` (mandatory prompt text)
  3. `expected_answer` (str)       -> `TaskFormat.expected_answer` (canonical solution)
  4. `domain` (str)                -> `TaskFormat.domain` (e.g. math, logic, planning)
  5. `difficulty` (str)            -> `TaskFormat.difficulty` (easy, medium, hard)
  6. `ground_truth_reasoning_steps` -> `TaskFormat.reference_steps` (list of step strings)
  7. `metadata` (dict)             -> `TaskFormat.metadata` (extra execution params)

Usage Example:
--------------
>>> from bench.ingest.krama_parser import load_krama_dataset, parse_krama_record
>>> task = parse_krama_record({"task_id": "math_1", "problem_statement": "Solve x^2=144", "expected_answer": "12 or -12"})
>>> tasks = load_krama_dataset("bench/data/kramabench_sample.jsonl")
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("krama-parser")


class TaskFormat(BaseModel):
    """Internal standardized task contract used by the blackboard scheduler & benchmark runner."""

    task_id: str = Field(default_factory=lambda: f"kb_task_{uuid4().hex[:8]}")
    task_text: str
    expected_answer: str
    domain: str = "general"
    difficulty: str = "medium"
    reference_steps: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


def validate_krama_schema(record: Dict[str, Any]) -> bool:
    """
    Validate that a raw dictionary record contains minimum required KramaBench fields.

    Required fields:
      - 'problem_statement' or 'task_text' (non-empty string)
      - 'expected_answer' (string or representable as string)
    """
    if not isinstance(record, dict):
        logger.error("Record is not a dictionary.")
        return False

    problem_text = record.get("problem_statement") or record.get("task_text")
    if not problem_text or not isinstance(problem_text, str) or not problem_text.strip():
        logger.error("Missing or empty 'problem_statement' / 'task_text' in record.")
        return False

    return True


def parse_krama_record(raw_record: Dict[str, Any]) -> TaskFormat:
    """
    Convert a single raw KramaBench dictionary record into an internal TaskFormat instance.

    Args:
        raw_record: Raw dictionary from JSON dataset.

    Returns:
        TaskFormat: Standardized internal task object.

    Raises:
        ValueError: If record validation fails.
    """
    if not validate_krama_schema(raw_record):
        raise ValueError("Invalid KramaBench record format: missing mandatory problem_statement.")

    # Extract fields according to mapping plan
    task_id = str(raw_record.get("task_id") or f"kb_task_{uuid4().hex[:8]}")
    task_text = str(raw_record.get("problem_statement") or raw_record.get("task_text")).strip()
    expected_answer = str(raw_record.get("expected_answer", "")).strip()
    domain = str(raw_record.get("domain", "general"))
    difficulty = str(raw_record.get("difficulty", "medium"))
    reference_steps = list(raw_record.get("ground_truth_reasoning_steps") or [])
    metadata = dict(raw_record.get("metadata") or {})

    # TODO (Week 2): Add domain-specific prompt wrappers or token pre-counting
    return TaskFormat(
        task_id=task_id,
        task_text=task_text,
        expected_answer=expected_answer,
        domain=domain,
        difficulty=difficulty,
        reference_steps=reference_steps,
        metadata=metadata,
    )


def load_krama_dataset(filepath: str | Path) -> List[TaskFormat]:
    """
    Load and parse a full KramaBench dataset file (.json or .jsonl format).

    Args:
        filepath: Path to the dataset file.

    Returns:
        List[TaskFormat]: List of parsed task objects.
    """
    path = Path(filepath)
    if not path.exists():
        logger.warning(f"File not found: {path}. Returning empty task list.")
        return []

    parsed_tasks: List[TaskFormat] = []

    try:
        if path.suffix == ".jsonl":
            with open(path, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw_dict = json.loads(line)
                        parsed_tasks.append(parse_krama_record(raw_dict))
                    except Exception as err:
                        logger.error(f"Error parsing line {line_idx} in {path}: {err}")
        else:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for raw_dict in data:
                        parsed_tasks.append(parse_krama_record(raw_dict))
                elif isinstance(data, dict):
                    parsed_tasks.append(parse_krama_record(data))
    except Exception as e:
        logger.error(f"Failed to load KramaBench dataset from {path}: {e}")

    logger.info(f"Successfully loaded {len(parsed_tasks)} KramaBench tasks from {path}.")
    return parsed_tasks


# Quick module self-test
if __name__ == "__main__":
    sample_record = {
        "task_id": "kb_demo_101",
        "problem_statement": "Evaluate the stability of a 3-agent consensus loop.",
        "expected_answer": "ULTRA_STRONG consensus reached",
        "domain": "multi-agent-coordination",
        "difficulty": "hard",
        "ground_truth_reasoning_steps": ["Agent_Alpha proposes", "Agent_Beta ratifies"],
    }
    parsed = parse_krama_record(sample_record)
    print("Parsed KramaBench Task successfully:")
    print(parsed.model_dump_json(indent=2))
