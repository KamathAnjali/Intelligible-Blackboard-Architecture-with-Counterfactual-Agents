"""Print a candidate compatibility allowlist entry for human review only.

This helper reads the pinned workload, resolves its explicit file selections,
and hashes those files. It never edits compat_allowlist.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from bench.kramabench_adapter import KRAMABENCH_REVISION
from bench.kramabench_runner import (
    COMPATIBILITY_EVALUATOR_PATH,
    COMPATIBILITY_PATCH_SHA256,
    _git_blob_hash,
    _load_selected_records,
    verify_benchmark_checkout,
)
from bench.kramabench_adapter import resolve_subset_files


def propose_entry(benchmark_root: str | Path, workload: str, task_id: str) -> dict:
    """Create, but do not persist, a candidate entry from pinned inputs."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", workload):
        raise ValueError("workload must be a simple workload name")
    root, _ = verify_benchmark_checkout(benchmark_root)
    workload_relative = f"workload/{workload}.json"
    workload_path = root / workload_relative
    matches = _load_selected_records(workload_path, [task_id])
    if len(matches) != 1:
        raise ValueError(f"task ID {task_id!r} did not resolve to exactly one workload row")
    task = matches[0]
    sources = task.get("data_sources")
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list) or not sources or any(not isinstance(source, str) for source in sources):
        raise ValueError("task does not have an explicit data_sources file allowlist")

    dataset_root = root / "data" / workload / "input"
    resolved_files = resolve_subset_files(dataset_root, sources)
    input_hashes = {}
    for path in resolved_files:
        relative = path.relative_to(dataset_root).as_posix()
        _git_blob_hash(root, f"data/{workload}/input/{relative}")
        input_hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()

    fixtures_path = root / "benchmark" / "fixtures" / "answer_type_fixtures.json"
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    fixture = next((item for item in fixtures if item.get("name") == task.get("answer_type")), None)
    if fixture is None or not isinstance(fixture.get("metrics"), list):
        raise ValueError("task answer_type has no evaluator metric mapping")

    return {
        "workload": workload,
        "task_id": task_id,
        "workload_git_blob": _git_blob_hash(root, workload_relative),
        "input_sha256": input_hashes,
        "answer_type": task["answer_type"],
        "metric_names": fixture["metrics"],
        "evaluator_provenance": {
            "revision": KRAMABENCH_REVISION,
            "patch_path": COMPATIBILITY_EVALUATOR_PATH,
            "patch_sha256": COMPATIBILITY_PATCH_SHA256,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kramabench-root", required=True)
    parser.add_argument("--workload", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    try:
        candidate = propose_entry(args.kramabench_root, args.workload, args.task_id)
    except Exception as exc:
        parser.error(f"cannot propose allowlist entry: {exc}")
    print(json.dumps(candidate, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
