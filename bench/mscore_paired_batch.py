"""Resumable paired MSCoRe run: same fixed task IDs with recovery off/on.

This uses the project's adapted MSCoRe scorer, not the upstream official harness.
Each (task, condition) is an independent one-task run, checkpointed immediately.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bench.mscore_runner import _read_task_id_file, run_pilot
from bench.ingest.mscore_parser import load_mscore_file

CONDITIONS = (
    {"condition": "cf00", "fraction": 0.0, "recovery": False},
    {"condition": "cf100", "fraction": 1.0, "recovery": True},
)
FIELDS = [
    "task_id", "condition", "counterfactual_fraction", "retrospective_recovery",
    "attempt", "attempt_status", "attempt_dir", "outcome", "evaluation_status",
    "completed_without_crash", "scored", "correct", "prediction", "metric", "score",
    "evaluation_reason", "failure_reason", "error", "iterations", "deadlock_count",
    "intelligibility_classification", "reviewer_rejections", "counterfactual_triggers",
    "counterfactual_simulations", "counterfactual_changed_outcome",
    "counterfactual_capable_agents", "num_ctx", "num_predict", "temperature", "model_seed",
    "live_prompt_tokens", "live_completion_tokens", "simulation_prompt_tokens",
    "simulation_completion_tokens", "total_prompt_tokens", "total_completion_tokens",
    "total_tokens", "live_elapsed_seconds", "simulation_elapsed_seconds", "elapsed_seconds",
    "transcript", "git_commit", "adapted", "evaluator_mode",
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _frozen_cohort() -> dict[str, Any]:
    path = Path(__file__).parent / "scope/mscore_construction_complex_100_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _git_provenance() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True,
                                capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "-C", str(root), "status", "--porcelain"], check=True,
                                    capture_output=True, text=True).stdout.strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    return {"git_commit": commit, "git_dirty": dirty}


def _write_manifest(path: Path, *, ids: list[str], ids_hash: str, source: Path,
                    source_hash: str, model: str, base_url: str, model_seed: int,
                    counterfactual_seed: int, num_predict: int, turns: int,
                    retries: int, order_seed: int,
                    started: str, rows: list[dict[str, Any]]) -> None:
    counts: dict[str, dict[str, int]] = {}
    for condition in ("cf00", "cf100"):
        cr = [r for r in rows if r.get("condition") == condition]
        counts[condition] = {
            "attempts": len(cr),
            "scored": sum(str(r.get("scored", "")).lower() in {"true", "1"} for r in cr),
            "correct": sum(str(r.get("correct", "")).lower() in {"true", "1"} for r in cr),
            "failed_before_scoring": sum(r.get("evaluation_status") == "failed_before_scoring" for r in cr),
            "execution_failures": sum(r.get("attempt_status") == "execution_failure" for r in cr),
            "counterfactual_simulations": sum(int(r.get("counterfactual_simulations") or 0) for r in cr),
        }
    data = {
        "benchmark": "MSCoRe", "runner": "project_blackboard_adapter_paired_batch",
        "result_label": "adapted; not the upstream official harness",
        "cohort": "MSCoRe Construction Complex 100-task paired cohort v1",
        "created_at": started, "updated_at": datetime.now(timezone.utc).isoformat(),
        **_git_provenance(), "source_path": str(source), "source_sha256": source_hash,
        "task_ids_sha256": ids_hash, "task_count": len(ids), "task_ids": ids,
        "model": model, "base_url": base_url, "model_seed": model_seed,
        "counterfactual_seed": counterfactual_seed,
        "model_options": {"num_ctx": 16384, "temperature": 0, "seed": model_seed,
                          "num_predict": num_predict},
        "turn_limit": turns, "retry_limit": retries,
        "execution_order_seed": order_seed,
        "conditions": list(CONDITIONS), "reference_answers_in_agent_context": False,
        "scoring": "adapted normalized exact match; open-ended tasks remain unscored",
        "counts": counts,
    }
    _atomic_json(path, data)


def run_batch(*, input_path: Path, task_id_file: Path, output_dir: Path, model: str,
              base_url: str, model_seed: int, counterfactual_seed: int, num_predict: int,
              turns: int, retries: int, order_seed: int, approve_genuine_run: bool,
              retry_failed: bool = False) -> Path:
    if not approve_genuine_run:
        raise ValueError("real model calls require --approve-genuine-run")
    input_path, task_id_file = input_path.expanduser().resolve(), task_id_file.expanduser().resolve()
    output_dir = output_dir.expanduser().resolve()
    if not input_path.is_file() or not task_id_file.is_file():
        raise FileNotFoundError("dataset JSON and fixed task ID file must exist")
    ids = _read_task_id_file(task_id_file)
    ids_hash, source_hash = _sha(task_id_file), _sha(input_path)
    cohort = _frozen_cohort()
    if len(ids) != 100 or cohort.get("task_id_file_sha256") != ids_hash:
        raise ValueError("fixed cohort mismatch: expected the committed 100-task v1 task list")
    if cohort.get("source_sha256") != source_hash:
        raise ValueError("MSCoRe dataset hash does not match the frozen cohort")
    loaded = load_mscore_file(input_path)
    known = {item.agent_task.task_id for item in loaded}
    missing = [task_id for task_id in ids if task_id not in known]
    if missing:
        raise ValueError(f"fixed cohort contains IDs absent from this dataset: {missing[:5]}")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows_path, manifest_path = output_dir / "results.csv", output_dir / "results_manifest.json"
    rows = _read_rows(rows_path)
    existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    expected_identity = {
        "task_ids_sha256": ids_hash, "source_sha256": source_hash, "model": model,
        "base_url": base_url, "model_seed": model_seed,
        "counterfactual_seed": counterfactual_seed, "num_predict": num_predict,
        "turn_limit": turns, "retry_limit": retries, "execution_order_seed": order_seed,
    }
    if existing_manifest:
        recorded_options = existing_manifest.get("model_options", {})
        recorded_identity = {**existing_manifest,
                             "num_predict": recorded_options.get("num_predict")}
        mismatch = [key for key, value in expected_identity.items()
                    if recorded_identity.get(key) != value]
        if mismatch:
            raise ValueError("existing Drive results use different run settings: " + ", ".join(mismatch))
    existing = {(r.get("task_id"), r.get("condition")) for r in rows}
    started = (json.loads(manifest_path.read_text()).get("created_at")
               if manifest_path.exists() else datetime.now(timezone.utc).isoformat())
    pairs = [(task_id, conf) for task_id in ids for conf in CONDITIONS]
    random.Random(order_seed).shuffle(pairs)
    _write_manifest(manifest_path, ids=ids, ids_hash=ids_hash, source=input_path,
                    source_hash=source_hash, model=model, base_url=base_url,
                    model_seed=model_seed, counterfactual_seed=counterfactual_seed,
                    num_predict=num_predict, turns=turns,
                    retries=retries, order_seed=order_seed, started=started, rows=rows)
    for index, (task_id, conf) in enumerate(pairs, 1):
        key = (task_id, conf["condition"])
        previous = [r for r in rows if (r.get("task_id"), r.get("condition")) == key]
        if previous and not (retry_failed and previous[-1].get("attempt_status") == "execution_failure"):
            continue
        attempt_root = output_dir / "attempts" / conf["condition"] / task_id
        prior_dirs = list(attempt_root.glob("attempt-[0-9][0-9][0-9]")) if attempt_root.exists() else []
        attempt = max([len(previous), *(int(p.name.split("-")[-1]) for p in prior_dirs)], default=0) + 1
        attempt_dir = output_dir / "attempts" / conf["condition"] / task_id / f"attempt-{attempt:03d}"
        print(f"[{index}/200] {conf['condition']} {task_id} attempt {attempt}", flush=True)
        before = time.monotonic()
        base_row: dict[str, Any]
        try:
            run_pilot(
                input_path=input_path, output_dir=attempt_dir, limit=1, selection_seed=0,
                model_seed=model_seed, model=model, base_url=base_url, turns=turns,
                retries=retries, counterfactual_fraction=conf["fraction"],
                counterfactual_seed=counterfactual_seed, approve_genuine_run=True,
                task_ids=[task_id], num_predict=num_predict,
                retrospective_recovery=conf["recovery"],
            )
            base_row = _read_rows(attempt_dir / "results.csv")[0]
            status = "completed"
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            status = "execution_failure"
            base_row = {"task_id": task_id, "outcome": "execution_failure",
                        "evaluation_status": "failed_before_scoring", "scored": False,
                        "completed_without_crash": False, "failure_reason": f"{type(exc).__name__}: {exc}",
                        "error": f"{type(exc).__name__}: {exc}", "elapsed_seconds": round(time.monotonic()-before, 3),
                        "adapted": True, "evaluator_mode": "project-adapted-exact"}
        row = {**base_row, "task_id": task_id, "condition": conf["condition"],
               "counterfactual_fraction": conf["fraction"],
               "retrospective_recovery": conf["recovery"], "attempt": attempt,
               "attempt_status": status,
               "attempt_dir": str(attempt_dir.relative_to(output_dir))}
        rows.append(row)
        _write_csv(rows_path, rows)
        _write_manifest(manifest_path, ids=ids, ids_hash=ids_hash, source=input_path,
                        source_hash=source_hash, model=model, base_url=base_url,
                        model_seed=model_seed, counterfactual_seed=counterfactual_seed,
                        num_predict=num_predict, turns=turns,
                        retries=retries, order_seed=order_seed, started=started, rows=rows)
    print(f"Checkpointed {len(rows)} attempts. Results: {rows_path}", flush=True)
    return output_dir


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--task-id-file", type=Path,
                   default=Path(__file__).parent / "scope/mscore_construction_complex_100_v1.taskids")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--model", default="qwen3:4b-instruct-2507-q4_K_M")
    p.add_argument("--base-url", default="http://127.0.0.1:11434")
    p.add_argument("--model-seed", type=int, default=42)
    p.add_argument("--counterfactual-seed", type=int, default=1)
    p.add_argument("--num-predict", type=int, default=4096)
    p.add_argument("--turns", type=int, default=6)
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--order-seed", type=int, default=20261124)
    p.add_argument("--retry-failed", action="store_true",
                   help="append a new attempt for previous whole-run execution failures")
    p.add_argument("--approve-genuine-run", action="store_true")
    args = p.parse_args(argv)
    try:
        run_batch(input_path=args.input, task_id_file=args.task_id_file,
                  output_dir=args.output_dir, model=args.model, base_url=args.base_url,
                  model_seed=args.model_seed, counterfactual_seed=args.counterfactual_seed,
                  num_predict=args.num_predict, turns=args.turns, retries=args.retries,
                  order_seed=args.order_seed, retry_failed=args.retry_failed,
                  approve_genuine_run=args.approve_genuine_run)
    except (OSError, ValueError) as exc:
        p.exit(2, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
