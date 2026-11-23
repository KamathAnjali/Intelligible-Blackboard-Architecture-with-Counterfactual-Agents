"""Fail-fast validation for official KramaBench workload and data inputs.

The returned summary contains identifiers, answer types, and permitted-file
hashes only. Full workload rows are kept on the evaluator side and are never
passed to the System adapter.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from bench.kramabench_adapter import (
    DatasetInputError,
    inspect_permitted_files,
)


class WorkloadPreflightError(ValueError):
    """The official workload or required data inputs are not evaluable."""


@dataclass(frozen=True)
class PreflightTask:
    task_id: str
    answer_type: str
    permitted_files: tuple[dict[str, Any], ...]
    parent_task_id: str | None = None


@dataclass(frozen=True)
class PreflightSummary:
    workload_path: str
    dataset_directory: str
    task_count: int
    requested_task_ids: tuple[str, ...]
    tasks: tuple[PreflightTask, ...]

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)


def _load_json(path: Path, label: str) -> Any:
    if not path.is_file():
        raise WorkloadPreflightError(f"{label} file does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise WorkloadPreflightError(f"{label} file is unreadable or malformed: {path}") from exc


def _sources(task: dict[str, Any]) -> list[str]:
    value = task.get("data_sources")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not value or any(not isinstance(item, str) for item in value):
        raise WorkloadPreflightError(f"task {task.get('id', '<unknown>')} has no valid data_sources allowlist")
    return value


def _iter_tasks(tasks: Iterable[dict[str, Any]], include_subtasks: bool):
    for task in tasks:
        yield task, None
        if include_subtasks:
            subtasks = task.get("subtasks", [])
            if not isinstance(subtasks, list):
                raise WorkloadPreflightError(f"task {task.get('id', '<unknown>')} has malformed subtasks")
            for subtask in subtasks:
                if not isinstance(subtask, dict):
                    raise WorkloadPreflightError(f"task {task.get('id', '<unknown>')} contains a malformed subtask")
                yield subtask, str(task.get("id"))


def preflight_kramabench(workload_path: str | Path,
                         dataset_directory: str | Path,
                         fixtures_directory: str | Path,
                         task_ids: list[str] | None = None,
                         include_subtasks: bool = False) -> PreflightSummary:
    """Validate workload schema, metric types, requested IDs, and input files."""
    workload_file = Path(workload_path).resolve()
    dataset_root = Path(dataset_directory).resolve()
    fixtures_root = Path(fixtures_directory).resolve()
    if not dataset_root.is_dir():
        raise WorkloadPreflightError(f"dataset directory does not exist: {dataset_root}")
    fixture_types = _load_json(fixtures_root / "answer_type_fixtures.json", "answer-type fixtures")
    if not isinstance(fixture_types, list):
        raise WorkloadPreflightError("answer-type fixtures must be a JSON list")
    supported_types = {
        item.get("name") for item in fixture_types
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }

    workload = _load_json(workload_file, "workload")
    if not isinstance(workload, list) or not workload:
        raise WorkloadPreflightError("workload must contain at least one task")
    if any(not isinstance(task, dict) for task in workload):
        raise WorkloadPreflightError("workload contains a non-object task")

    ids = [task.get("id") for task in workload]
    if any(not isinstance(task_id, str) or not task_id.strip() for task_id in ids):
        raise WorkloadPreflightError("every workload task must have a nonempty string id")
    if len(set(ids)) != len(ids):
        raise WorkloadPreflightError("workload contains duplicate top-level task IDs")

    if task_ids is None:
        selected = workload
        requested_ids = tuple(ids)
    else:
        if not task_ids or any(not isinstance(task_id, str) or not task_id for task_id in task_ids):
            raise WorkloadPreflightError("requested task IDs must be a nonempty list of strings")
        if len(set(task_ids)) != len(task_ids):
            raise WorkloadPreflightError("requested task IDs contain duplicates")
        missing = sorted(set(task_ids) - set(ids))
        if missing:
            raise WorkloadPreflightError(f"requested task IDs are absent from workload: {', '.join(missing)}")
        selected = [task for task in workload if task["id"] in set(task_ids)]
        requested_ids = tuple(task_ids)

    summaries: list[PreflightTask] = []
    for task, parent_id in _iter_tasks(selected, include_subtasks):
        task_id = task.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise WorkloadPreflightError("every evaluated task or subtask must have a nonempty string id")
        if not isinstance(task.get("query"), str) or not task["query"].strip():
            raise WorkloadPreflightError(f"task {task_id} has no nonempty query")
        if "answer" not in task:
            raise WorkloadPreflightError(f"task {task_id} has no evaluator answer field")
        answer_type = task.get("answer_type")
        if not isinstance(answer_type, str) or answer_type not in supported_types:
            raise WorkloadPreflightError(f"task {task_id} uses an unsupported answer_type")
        try:
            contexts = inspect_permitted_files(dataset_root, _sources(task))
        except DatasetInputError as exc:
            raise WorkloadPreflightError(f"task {task_id} data preflight failed: {exc}") from exc
        permitted = tuple({
            "path": item["path"],
            "sha256": item["sha256"],
            "size_bytes": item["size_bytes"],
            "format": item["format"],
            "encoding": item["encoding"],
            "tables": item["tables"],
            "page_count": item["page_count"],
            "notes": item["notes"],
        } for item in contexts)
        summaries.append(PreflightTask(
            task_id=task_id,
            answer_type=answer_type,
            permitted_files=permitted,
            parent_task_id=parent_id,
        ))

    if not summaries:
        raise WorkloadPreflightError("preflight selected zero tasks")
    return PreflightSummary(
        workload_path=str(workload_file),
        dataset_directory=str(dataset_root),
        task_count=len(summaries),
        requested_task_ids=requested_ids,
        tasks=tuple(summaries),
    )
