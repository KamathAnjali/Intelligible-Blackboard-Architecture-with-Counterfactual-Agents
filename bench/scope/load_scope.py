"""Load and verify a frozen KramaBench scope without exposing reference data."""

from __future__ import annotations

import hashlib
import argparse
import json
from pathlib import Path
from typing import Any


class ScopeError(ValueError):
    """The frozen scope is malformed, has been edited, or does not cover a task."""


def canonical_scope_bytes(scope: dict[str, Any]) -> bytes:
    """Return canonical JSON bytes, excluding the self-referential hash field."""
    payload = {key: value for key, value in scope.items() if key != "content_sha256"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def scope_sha256(scope: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_scope_bytes(scope)).hexdigest()


def load_scope(path: str | Path) -> dict[str, Any]:
    """Load a frozen scope; fail closed if its contents or task splits changed."""
    scope_path = Path(path)
    try:
        scope = json.loads(scope_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScopeError(f"cannot read frozen scope: {scope_path}") from exc
    if not isinstance(scope, dict) or scope.get("schema_version") != 1:
        raise ScopeError("unsupported or malformed scope schema")
    recorded_hash = scope.get("content_sha256")
    if not isinstance(recorded_hash, str) or recorded_hash != scope_sha256(scope):
        raise ScopeError("frozen scope content hash mismatch; refusing to use modified scope")

    tasks = scope.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ScopeError("frozen scope has no task list")
    ids = [(item.get("workload"), item.get("task_id")) for item in tasks if isinstance(item, dict)]
    if len(ids) != len(tasks) or len(set(ids)) != len(ids):
        raise ScopeError("frozen scope contains malformed or duplicate task identifiers")
    tuning = [task for task in tasks if task.get("split") == "tuning"]
    held_out = [task for task in tasks if task.get("split") == "held_out_pilot"]
    if not 1 <= len(tuning) <= 5 or not 20 <= len(held_out) <= 30:
        raise ScopeError("scope must contain 1-5 tuning tasks and 20-30 held-out pilot tasks")
    if set((t["workload"], t["task_id"]) for t in tuning) & set(
        (t["workload"], t["task_id"]) for t in held_out
    ):
        raise ScopeError("tuning and held-out task sets overlap")
    for task in tasks:
        if task.get("split") not in {"tuning", "held_out_pilot"}:
            raise ScopeError(f"task {task.get('task_id')} has an unknown split")
        if task.get("independent_answer_check") is not True:
            raise ScopeError(f"task {task.get('task_id')} has no independent answer-check provenance")
        if not isinstance(task.get("pass_rule"), dict) or task["pass_rule"].get("kind") != "all_metrics_equal":
            raise ScopeError(f"task {task.get('task_id')} has no machine-readable correctness pass rule")
        provenance = task.get("answer_check_provenance")
        if not isinstance(provenance, dict) or provenance.get("metric_kind") != "deterministic":
            raise ScopeError(f"task {task.get('task_id')} has invalid answer-check provenance")
        if not isinstance(task.get("input_files"), list) or not task["input_files"]:
            raise ScopeError(f"task {task.get('task_id')} has no pinned input hashes")
        expected_adapted = task.get("evaluator_mode") != "official"
        if task.get("adapted") is not expected_adapted:
            raise ScopeError(f"task {task.get('task_id')} has inconsistent adapted/evaluator labels")
    return scope


def task_scope_view(scope: dict[str, Any], workload: str, task_id: str) -> dict[str, Any]:
    """Return only task metadata safe to place in an agent-facing context."""
    task = next((item for item in scope.get("tasks", [])
                 if item.get("workload") == workload and item.get("task_id") == task_id), None)
    if task is None:
        raise ScopeError(f"task is not in the frozen scope: {workload}/{task_id}")
    return {
        "workload": task["workload"],
        "task_id": task["task_id"],
        "split": task["split"],
        "answer_type": task["answer_type"],
        "metric_names": list(task["metric_names"]),
        "evaluator_mode": task["evaluator_mode"],
        "adapted": task["adapted"],
        "adaptation_flags": list(task["adaptation_flags"]),
    }


def counterfactual_selection_view(scope: dict[str, Any]) -> dict[str, Any]:
    """Provide task/config metadata to counterfactual selection, never gold fields."""
    safe_tasks = []
    for task in scope.get("tasks", []):
        safe_tasks.append({
            "workload": task["workload"],
            "task_id": task["task_id"],
            "split": task["split"],
            "answer_type": task["answer_type"],
            "evaluator_mode": task["evaluator_mode"],
            "adapted": task["adapted"],
        })
    return {
        "selection_seed": scope.get("selection", {}).get("seed"),
        "counterfactual_fractions": list(scope.get("counterfactual_fractions", [])),
        "tasks": safe_tasks,
    }


def result_reporting_labels(evaluator_mode: str, adapted: bool) -> dict[str, Any]:
    """Standardize evaluator/adaptation labels for summaries and result rows."""
    if not isinstance(evaluator_mode, str) or not evaluator_mode:
        raise ValueError("evaluator_mode must be a nonempty string")
    if type(adapted) is not bool:
        raise ValueError("adapted must be a boolean")
    is_adapted = adapted or evaluator_mode != "official"
    return {
        "evaluator_mode": evaluator_mode,
        "adapted": is_adapted,
        "result_partition": "adapted" if is_adapted else "standard",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scope_path", type=Path, help="frozen scope JSON to hash-check")
    args = parser.parse_args(argv)
    scope = load_scope(args.scope_path)
    counts: dict[str, int] = {}
    for task in scope["tasks"]:
        counts[task["split"]] = counts.get(task["split"], 0) + 1
    print(f"Scope verified: {scope['scope_id']}")
    print(f"SHA-256: {scope['content_sha256']}")
    print("Tasks: " + ", ".join(f"{split}={count}" for split, count in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
