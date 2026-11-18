"""Paired, resumable KramaBench ablation orchestration.

No benchmark score is inferred from subprocess exit status. Synthetic test runs
are marked SYNTHETIC and cannot pass the plotting provenance gate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from bench.scope.load_scope import ScopeError, load_scope, result_reporting_labels
from bench.metrics.token_counter import UsageLedger, UsageRecord, count_tokens_for_entry

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCOPE = REPO_ROOT / "bench" / "scope" / "pilot_scope_v2.json"
CONFIGURATIONS = (0.0, 0.33, 0.66, 1.0)

RESULT_FIELDS = [
    "run_id", "task_id", "workload", "dataset_domain", "benchmark_source", "mode", "split", "repetition", "attempt",
    "config_fraction", "config_pct", "effective_enabled_agent_count", "enabled_agent_ids",
    "counterfactual_seed", "run_order_seed", "run_order_index", "attempt_status",
    "completed_without_crash", "correct", "outcome", "failure_reason", "iterations",
    "deadlock_count", "intelligibility_classification", "recovery_attempts", "recovery_success",
    "counterfactual_simulations", "total_tokens", "provider_total_tokens", "estimated_total_tokens",
    "estimated_share_pct", "call_attribution_available", "measurement_label",
    "live_tokens", "simulation_tokens", "token_source", "usage_breakdown_json", "usage_ledger_path",
    "elapsed_time_s", "live_elapsed_time_s", "simulation_elapsed_time_s", "timing_scope",
    "evaluator_metric", "evaluator_score", "metric_scores", "evaluator_mode", "adapted",
    "result_kind", "official_metric", "official_score", "ground_truth_used", "timestamp",
    "return_code", "timed_out", "runner_manifest_path", "task_record_path", "measure_csv_paths",
    "synthetic", "mainline_tokens", "dry_run", "notes", "assignment_seed", "model_seed",
    "result_partition", "num_ctx", "num_predict",
]


@dataclass(frozen=True)
class RunSpec:
    task: dict[str, Any]
    fraction: float
    repetition: int
    order_index: int
    counterfactual_seed: int
    enabled_agent_ids: tuple[str, ...]

    @property
    def config_pct(self) -> int:
        return int(round(self.fraction * 100))

    @property
    def key(self) -> tuple[str, str, int, int]:
        return (self.task["workload"], self.task["task_id"], self.config_pct, self.repetition)


@dataclass(frozen=True)
class ProcessResult:
    return_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None
    elapsed_seconds: float | None = None


@dataclass(frozen=True)
class RunRequest:
    spec: RunSpec
    attempt: int
    attempt_dir: Path
    runner_output_dir: Path
    command: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    model_options: dict[str, int] | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(path)


def effective_enabled_count(agent_count: int, fraction: float) -> int:
    return math.ceil(agent_count * fraction) if fraction else 0


def balanced_agent_assignment(agent_ids: list[str], fraction: float, task_slot: int,
                              rotation_seed: int) -> tuple[str, ...]:
    """Rotate enabled identities evenly; count matches run_conversation's ceil rule."""
    if not agent_ids:
        raise ValueError("scope must identify at least one active agent")
    count = effective_enabled_count(len(agent_ids), fraction)
    if count == 0:
        return ()
    offset = int.from_bytes(hashlib.sha256(f"{rotation_seed}:{fraction}".encode()).digest()[:4], "big") % len(agent_ids)
    start = (offset + task_slot * count) % len(agent_ids)
    return tuple(agent_ids[(start + index) % len(agent_ids)] for index in range(count))


def _seed_matching_assignment(agent_ids: list[str], desired: tuple[str, ...], fraction: float,
                              seed_base: int) -> int:
    """Find a deterministic Ollama seed selecting the balanced assignment in the runner."""
    count = effective_enabled_count(len(agent_ids), fraction)
    if not count:
        return seed_base
    target = set(desired)
    for offset in range(100_000):
        candidate = seed_base + offset
        selected = set(random.Random(candidate).sample(agent_ids, count))
        if selected == target:
            return candidate
    raise RuntimeError("could not find a deterministic seed for the balanced agent assignment")


def runtime_agent_ids(scope: dict[str, Any]) -> list[str]:
    """Resolve IDs from the real runner roster and ensure frozen personas agree."""
    from agents.conversation import DEFAULT_AGENTS

    roster = list(DEFAULT_AGENTS)
    expected_personas = scope.get("personas", [])
    if [persona for _, persona in roster] != expected_personas:
        raise ValueError("frozen scope persona list differs from the per-task runner roster")
    if scope.get("model_agent_count") != len(roster):
        raise ValueError("frozen scope agent count differs from the per-task runner roster")
    return [agent_id for agent_id, _ in roster]


def select_split_tasks(scope: dict[str, Any], split: str, count: int | None = None) -> list[dict[str, Any]]:
    tasks = [task for task in scope["tasks"] if task["split"] == split]
    tasks.sort(key=lambda task: (task["workload"], task["task_id"]))
    if count is not None:
        if count < 3 or count > 5:
            raise ValueError("dry-run task count must be between 3 and 5")
        if len(tasks) < count:
            raise ValueError(f"scope has only {len(tasks)} {split} tasks; requested {count}")
        tasks = tasks[:count]
    return tasks


def build_matrix(scope: dict[str, Any], tasks: list[dict[str, Any]], repetitions: int,
                 order_seed: int, assignment_seed: int) -> list[RunSpec]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    agent_ids = runtime_agent_ids(scope)
    slots = []
    for task_index, task in enumerate(tasks):
        for repetition in range(1, repetitions + 1):
            slot = task_index * repetitions + repetition - 1
            for fraction in CONFIGURATIONS:
                desired = balanced_agent_assignment(agent_ids, fraction, slot, assignment_seed)
                chosen_seed = _seed_matching_assignment(
                    agent_ids, desired, fraction,
                    assignment_seed + slot * 1_000_003 + int(fraction * 1000),
                )
                slots.append((task, fraction, repetition, chosen_seed, desired))
    random.Random(order_seed).shuffle(slots)
    return [RunSpec(task, fraction, rep, index, seed, enabled)
            for index, (task, fraction, rep, seed, enabled) in enumerate(slots, start=1)]


def _load_attempts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"malformed attempts log at {path}:{line_number}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"malformed attempts row at {path}:{line_number}")
            rows.append(item)
    return rows


def _key_from_row(row: dict[str, Any]) -> tuple[str, str, int, int]:
    return (row["workload"], row["task_id"], int(row["config_pct"]), int(row["repetition"]))


def _append_attempt(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _reconcile_saved_outcomes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reclassify saved non-scored rows from their immutable task-record evidence."""
    reconciled = []
    for source_row in rows:
        row = dict(source_row)
        if row.get("evaluator_score") in (None, "") and row.get("task_record_path"):
            record = _read_json(Path(str(row["task_record_path"])))
            manifest = _read_json(Path(str(row["runner_manifest_path"]))) if row.get("runner_manifest_path") else None
            if record is not None:
                outcome, reason = _classify_outcome(
                    record, manifest, bool(row.get("timed_out")),
                    row.get("failure_reason") if row.get("timed_out") else None,
                )
                if outcome in {"DEADLOCK", "ITERATION_LIMIT", "TIME_LIMIT"}:
                    row["outcome"] = outcome
                    row["failure_reason"] = reason or row.get("failure_reason", "")
                    row["notes"] = row["failure_reason"]
                    row["deadlock_count"] = int(outcome == "DEADLOCK")
        reconciled.append(row)
    return reconciled


def _find_artifacts(root: Path) -> tuple[Path | None, Path | None, list[Path]]:
    manifests = sorted(root.rglob("run_manifest.json")) if root.exists() else []
    records = sorted(root.rglob("krama_task_records/*.json")) if root.exists() else []
    measures = sorted(root.rglob("*_measures_*.csv")) if root.exists() else []
    return (manifests[-1] if manifests else None,
            records[-1] if records else None,
            measures)


def _read_measure_files(paths: list[Path]) -> dict[str, float]:
    values: dict[str, float] = {}
    for path in paths:
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    name, value = row.get("metric"), row.get("value")
                    if not name or value in (None, ""):
                        continue
                    try:
                        number = float(value)
                    except (TypeError, ValueError):
                        continue
                    if math.isfinite(number):
                        values[name] = number
        except OSError:
            continue
    return values


def _correct_from_rule(task: dict[str, Any], scores: dict[str, float]) -> bool | None:
    rule = task.get("pass_rule")
    if not isinstance(rule, dict) or rule.get("kind") != "all_metrics_equal":
        return None
    expected = float(rule.get("value"))
    names = task.get("metric_names", [])
    if not names or any(name not in scores for name in names):
        return None
    return all(math.isclose(scores[name], expected, rel_tol=0.0, abs_tol=1e-12) for name in names)


def _attempt_usage(attempts: list[dict[str, Any]]) -> tuple[int, int, int]:
    prompt = completion = calls = 0
    for attempt in attempts:
        raw = attempt.get("raw") if isinstance(attempt.get("raw"), dict) else attempt
        checks = [raw]
        self_check = attempt.get("self_check")
        if isinstance(self_check, dict) and isinstance(self_check.get("raw"), dict):
            checks.append(self_check["raw"])
        for item in checks:
            calls += 1
            prompt += int(item.get("prompt_eval_count", 0) or 0)
            completion += int(item.get("eval_count", 0) or 0)
    return calls, prompt, completion


def _text_token_estimate(transcript: dict[str, Any] | None) -> tuple[int, int]:
    if not transcript:
        return 0, 0
    live_chars = sim_chars = 0
    board = transcript.get("board", {})
    for entry in board.get("entries", []) if isinstance(board, dict) else []:
        size = len(str(entry.get("prediction", ""))) + len(str(entry.get("explanation", "")))
        if entry.get("is_counterfactual_sim"):
            sim_chars += size
        elif entry.get("tag") != "FACT":
            live_chars += size
    return math.ceil(live_chars / 4), math.ceil(sim_chars / 4)


def _run_usage_ledger(record: dict[str, Any] | None, transcript: dict[str, Any] | None,
                      task_id: str) -> dict[str, Any]:
    """Use persisted callback ledger, provider aggregate, or explicitly scoped text estimates."""
    stored = record.get("usage_ledger") if record else None
    if isinstance(stored, dict) and isinstance(stored.get("total_tokens"), int):
        return stored
    ledger = UsageLedger()
    usage = record.get("provider_token_usage", {}) if record else {}
    if isinstance(usage, dict) and int(usage.get("calls", 0) or 0) > 0:
        ledger.ingest_aggregate(
            session_id=str(record.get("run_id", "unknown-session")), task_id=task_id,
            source="provider", prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("output_tokens", 0) or 0),
            calls=int(usage.get("calls", 0) or 0),
        )
        report = ledger.export_report()
        report["measurement_label"] = "provider-reported aggregate; per-call attribution unavailable"
        return report
    board = transcript.get("board", {}) if transcript else {}
    entries = board.get("entries", []) if isinstance(board, dict) else []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        agent_id = str(entry.get("agent_id") or "unknown")
        tokens = count_tokens_for_entry(
            str(entry.get("prediction", "")), str(entry.get("explanation", "")), agent_id=agent_id
        )["total_tokens"]
        is_simulation = bool(entry.get("is_counterfactual_sim"))
        ledger.ingest(UsageRecord(
            session_id=str(record.get("run_id", "unknown-session")) if record else "unknown-session",
            task_id=task_id, agent_id=agent_id,
            call_kind="counterfactual_sim" if is_simulation else "other",
            branch_id=entry.get("branch_id"), prompt_tokens=0,
            completion_tokens=tokens, total_tokens=tokens, source="estimated",
            model=str(record.get("model", "unknown")) if record else "unknown",
            started_at=str(entry.get("timestamp") or ""), duration_ms=0,
        ))
    report = ledger.export_report()
    report["measurement_label"] = "estimated, entry text only" if entries else "no usage records"
    report["measurement_scope"] = "prediction and explanation text only; prompts and internal calls are excluded"
    return report


def _classify_outcome(record: dict[str, Any] | None, manifest: dict[str, Any] | None,
                      timed_out: bool, timeout_reason: str | None) -> tuple[str, str]:
    if timed_out:
        return "TIME_LIMIT", timeout_reason or "orchestrator subprocess timeout"
    if record:
        outcome = str(record.get("execution_outcome") or record.get("agent_conversation_outcome") or "").casefold()
        errors = record.get("errors", [])
        message = " | ".join(str(item.get("message", item)) if isinstance(item, dict) else str(item) for item in errors)
        if outcome in {"scheduler_consensus", "agreement", "consensus"}:
            return "AGREEMENT", ""
        if "deadlock" in outcome or "deadlock" in message.casefold():
            return "DEADLOCK", message or "scheduler reported deadlock"
        limit_text = f"{outcome} {message}".casefold().replace("-", "_")
        if any(word in limit_text for word in (
            "iteration limit", "iteration_limit", "max_turns", "turn limit",
            "turn_limit", "maximum turns",
        )):
            return "ITERATION_LIMIT", message
        if any(word in limit_text for word in ("wall_clock", "wall clock", "deadline", "timed out", "timeout")):
            return "TIME_LIMIT", message
        if message:
            return "EXECUTION_FAILURE", message
    manifest_error = str((manifest or {}).get("error", ""))
    if "timeout" in manifest_error.casefold() or "deadline" in manifest_error.casefold():
        return "TIME_LIMIT", manifest_error
    if manifest_error:
        return "EXECUTION_FAILURE", manifest_error
    return "EXECUTION_FAILURE", "runner produced no task record or outcome"


def _attempt_row(run_id: str, request: RunRequest, process: ProcessResult,
                 model_seed: int, order_seed: int, assignment_seed: int,
                 synthetic: bool = False) -> dict[str, Any]:
    manifest_path, record_path, measure_paths = _find_artifacts(request.runner_output_dir)
    manifest = _read_json(manifest_path) if manifest_path else None
    record = _read_json(record_path) if record_path else None
    scores = _read_measure_files(measure_paths)
    task = request.spec.task
    correct = _correct_from_rule(task, scores)
    primary_metric = task.get("metric_names", [""])[0]
    primary_score = scores.get(primary_metric)
    outcome, reason = _classify_outcome(record, manifest, process.timed_out, process.error)
    if record is None and manifest is None and process.error and not process.timed_out:
        outcome, reason = "EXECUTION_FAILURE", process.error
    if primary_score is None and not reason and process.return_code not in (0, None):
        diagnostic = (process.stderr or process.stdout).strip()
        reason = diagnostic[-1200:] if diagnostic else f"runner exited with status {process.return_code} without a score"
        outcome = "EXECUTION_FAILURE"
    transcript = None
    if record and isinstance(record.get("conversation_transcript"), str):
        transcript = _read_json(Path(record["conversation_transcript"]))
    counters = record.get("workflow_counters", {}) if record else {}
    sim_count = int(counters.get("counterfactual_simulations", 0) or 0)
    recovery_attempts = int(counters.get("counterfactual_triggers", 0) or sim_count)
    recovery_success = int(counters.get("counterfactual_changed_outcome", 0) or 0) > 0
    turns = transcript.get("turns", []) if transcript else []
    iterations = len(turns) if transcript else None
    board = transcript.get("board", {}) if transcript else {}
    intelligibility = board.get("intelligibility") if isinstance(board, dict) else None
    if hasattr(intelligibility, "value"):
        intelligibility = intelligibility.value
    deadlocks = 1 if outcome == "DEADLOCK" else 0

    usage_report = _run_usage_ledger(record, transcript, task["task_id"])
    total_tokens = usage_report.get("total_tokens")
    provider_tokens = usage_report.get("provider_total_tokens", 0)
    estimated_tokens = usage_report.get("estimated_total_tokens", 0)
    estimated_share_pct = usage_report.get("estimated_share_pct", 0.0)
    token_source = usage_report.get("source_label", "unavailable")
    by_execution = usage_report.get("by_execution", {})
    simulation_seconds = None
    if usage_report.get("call_attribution_available", True):
        live_tokens = (by_execution.get("live") or {}).get("total_tokens", 0)
        sim_tokens = (by_execution.get("simulation") or {}).get("total_tokens", 0)
    else:
        live_tokens = None
        sim_tokens = None
    usage_ledger_path = request.attempt_dir / "usage_ledger.json"
    usage_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    usage_ledger_path.write_text(json.dumps(usage_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    runtime = record.get("runtime_seconds") if record else process.elapsed_seconds
    try:
        runtime = float(runtime) if runtime is not None else None
    except (TypeError, ValueError):
        runtime = None
    cf_attempts = [
        attempt
        for simulation in (transcript or {}).get("counterfactual_attempts", [])
        for attempt in simulation.get("attempts", [])
    ]
    if transcript:
        _, sim_prompt, sim_completion = _attempt_usage(cf_attempts)
        del sim_prompt, sim_completion
        simulation_seconds = sum(float(attempt.get("wall_seconds", 0) or 0) for attempt in cf_attempts)
        for attempt in cf_attempts:
            check = attempt.get("self_check", {})
            raw = check.get("raw", {}) if isinstance(check, dict) else {}
            simulation_seconds += float(raw.get("wall_seconds", 0) or 0) if isinstance(raw, dict) else 0.0
    live_seconds = max(runtime - simulation_seconds, 0.0) if runtime is not None and simulation_seconds is not None else None

    finished_cleanly = process.return_code == 0 and not process.timed_out and process.error is None
    has_evaluator_score = primary_score is not None
    attempt_status = "completed" if has_evaluator_score else "failed"
    evaluator_mode = task["evaluator_mode"]
    adapted = bool(task.get("adapted", evaluator_mode != "official"))
    labels = result_reporting_labels(evaluator_mode, adapted)
    result_kind = "SYNTHETIC_FAKE_RUNNER" if synthetic else (
        "official_evaluation" if evaluator_mode == "official" and has_evaluator_score
        else "compatibility_evaluation" if has_evaluator_score else "execution_failure"
    )
    timestamp = _utc_now()
    return {
        "run_id": run_id, "task_id": task["task_id"], "workload": task["workload"],
        "dataset_domain": task.get("dataset_domain", task["workload"]),
        "benchmark_source": "KramaBench", "mode": evaluator_mode, "split": task["split"],
        "repetition": request.spec.repetition, "attempt": request.attempt,
        "config_fraction": request.spec.fraction, "config_pct": request.spec.config_pct,
        "effective_enabled_agent_count": len(request.spec.enabled_agent_ids),
        "enabled_agent_ids": json.dumps(list(request.spec.enabled_agent_ids)),
        "counterfactual_seed": request.spec.counterfactual_seed,
        "run_order_seed": order_seed, "run_order_index": request.spec.order_index,
        "attempt_status": attempt_status, "completed_without_crash": finished_cleanly,
        "correct": correct, "outcome": outcome, "failure_reason": reason,
        "iterations": iterations, "deadlock_count": deadlocks,
        "intelligibility_classification": intelligibility or "UNRESOLVED",
        "recovery_attempts": recovery_attempts, "recovery_success": recovery_success,
        "counterfactual_simulations": sim_count, "total_tokens": total_tokens,
        "provider_total_tokens": provider_tokens, "estimated_total_tokens": estimated_tokens,
        "estimated_share_pct": estimated_share_pct,
        "call_attribution_available": usage_report.get("call_attribution_available", True),
        "measurement_label": usage_report.get("measurement_label", "no usage records"),
        "live_tokens": live_tokens, "simulation_tokens": sim_tokens,
        "token_source": token_source,
        "usage_breakdown_json": json.dumps({
            "by_agent": usage_report.get("agents", {}),
            "by_call_kind": usage_report.get("by_call_kind", {}),
            "by_execution": by_execution,
            "measurement_label": usage_report.get("measurement_label"),
            "call_attribution_available": usage_report.get("call_attribution_available", True),
        }, sort_keys=True),
        "usage_ledger_path": str(usage_ledger_path), "elapsed_time_s": runtime,
        "live_elapsed_time_s": live_seconds, "simulation_elapsed_time_s": simulation_seconds,
        "timing_scope": "task_record_runtime; simulation attempt wall time separated when transcript exists",
        "evaluator_metric": primary_metric or "", "evaluator_score": primary_score,
        "metric_scores": json.dumps(scores, sort_keys=True), "evaluator_mode": evaluator_mode,
        "adapted": adapted, "result_kind": result_kind, "official_metric": primary_metric or "",
        "result_partition": labels["result_partition"],
        "official_score": primary_score if result_kind == "official_evaluation" else "",
        "ground_truth_used": False, "timestamp": timestamp, "return_code": process.return_code,
        "timed_out": process.timed_out, "runner_manifest_path": str(manifest_path or ""),
        "task_record_path": str(record_path or ""),
        "measure_csv_paths": json.dumps([str(path) for path in measure_paths]), "synthetic": synthetic,
        "failure_reason": reason or ("subprocess returned nonzero" if process.return_code not in (0, None) else ""),
        "attempt_status": attempt_status,
        "model_seed": model_seed, "mainline_tokens": live_tokens, "dry_run": False,
        "notes": reason or "", "assignment_seed": assignment_seed,
        "num_ctx": (request.model_options or {}).get("num_ctx", ""),
        "num_predict": (request.model_options or {}).get("num_predict", ""),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def _save_manifest(path: Path, manifest: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    counts = {}
    for pct in (0, 33, 66, 100):
        group = [row for row in rows if int(row["config_pct"]) == pct]
        counts[str(pct)] = {
            "attempted": len(group),
            "scored": sum(row.get("evaluator_score") not in (None, "") for row in group),
            "completed_without_crash": sum(bool(row.get("completed_without_crash")) for row in group),
            "failed_before_scoring": sum(row.get("evaluator_score") in (None, "") for row in group),
            "correct": sum(row.get("correct") is True for row in group),
        }
    manifest["counts_by_config_pct"] = counts
    manifest["updated_at"] = _utc_now()
    manifest["attempt_count"] = len(rows)
    _write_json(path, manifest)


def _subprocess_runner(request: RunRequest) -> ProcessResult:
    started = time.monotonic()
    request.attempt_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            list(request.command), cwd=request.cwd, check=False, capture_output=True,
            text=True, timeout=request.timeout_seconds,
        )
        stdout, stderr = result.stdout or "", result.stderr or ""
        (request.attempt_dir / "driver_stdout.log").write_text(stdout, encoding="utf-8")
        (request.attempt_dir / "driver_stderr.log").write_text(stderr, encoding="utf-8")
        return ProcessResult(result.returncode, stdout, stderr, elapsed_seconds=time.monotonic() - started)
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        (request.attempt_dir / "driver_stdout.log").write_text(stdout, encoding="utf-8")
        (request.attempt_dir / "driver_stderr.log").write_text(stderr, encoding="utf-8")
        return ProcessResult(None, stdout, stderr, timed_out=True,
                             error=f"orchestrator timeout after {request.timeout_seconds}s",
                             elapsed_seconds=time.monotonic() - started)
    except OSError as exc:
        return ProcessResult(None, error=f"runner launch failed: {type(exc).__name__}: {exc}",
                             elapsed_seconds=time.monotonic() - started)


def _runner_command(spec: RunSpec, runner_output: Path, benchmark_root: Path,
                    evaluator_python: str, base_url: str, model_seed: int,
                    scope: dict[str, Any]) -> tuple[str, ...]:
    limits = scope["stopping_rules"]
    return (
        sys.executable, "-m", "bench.kramabench_runner",
        "--kramabench-root", str(benchmark_root),
        "--workload", spec.task["workload"], "--task-id", spec.task["task_id"],
        "--output-directory", str(runner_output), "--evaluator-python", evaluator_python,
        "--evaluator-mode", spec.task["evaluator_mode"],
        "--model", scope["model"]["name"], "--base-url", base_url,
        "--num-ctx", str(scope["model_options"]["num_ctx"]),
        "--required-num-ctx", str(scope["model_options"]["num_ctx"]),
        "--num-predict", str(scope["model_options"]["num_predict"]),
        "--model-seed", str(model_seed), "--max-turns", str(limits["max_turns"]),
        "--max-retries", str(limits["max_retries_per_generation"]),
        "--task-wall-clock-cap-seconds", str(limits["task_wall_clock_cap_seconds"]),
        "--counterfactual-fraction", str(spec.fraction),
        "--counterfactual-seed", str(spec.counterfactual_seed),
        "--counterfactual-streak-threshold", str(limits["counterfactual_streak_threshold"]),
        "--max-counterfactual-simulations", str(limits["max_counterfactual_simulations_per_task"]),
        "--approve-genuine-run",
    )


def _planned_attempt_number(rows: list[dict[str, Any]], key: tuple[str, str, int, int]) -> int:
    previous = [int(row.get("attempt", 0)) for row in rows if _key_from_row(row) == key]
    return max(previous, default=0) + 1


def run_experiment(
    *, scope: dict[str, Any], tasks: list[dict[str, Any]], run_dir: Path, run_id: str,
    benchmark_root: Path, evaluator_python: str, base_url: str, model_seed: int,
    repetitions: int = 1, order_seed: int = 20261111, assignment_seed: int = 20261112,
    process_timeout_seconds: int | None = None, retry_failed: bool = False,
    runner: Callable[[RunRequest], ProcessResult] = _subprocess_runner,
    synthetic: bool = False,
) -> list[dict[str, Any]]:
    """Execute paired task/config/repetition cells using an injectable runner."""
    fake_runner = getattr(runner, "synthetic_fake_runner", False)
    if synthetic != bool(fake_runner):
        raise ValueError("injected fake runners must be marked synthetic and cannot create benchmark evidence")
    manifest_path = run_dir / ("SYNTHETIC_results_manifest.json" if synthetic else "results_manifest.json")
    attempts_path = run_dir / ("SYNTHETIC_attempts.jsonl" if synthetic else "attempts.jsonl")
    results_path = run_dir / ("SYNTHETIC_results.csv" if synthetic else "results.csv")
    run_dir.mkdir(parents=True, exist_ok=True)
    matrix = build_matrix(scope, tasks, repetitions, order_seed, assignment_seed)
    timeout = process_timeout_seconds or int(scope["stopping_rules"]["task_wall_clock_cap_seconds"]) + 300
    matrix_signature = hashlib.sha256(json.dumps([
        {"key": spec.key, "fraction": spec.fraction, "order_index": spec.order_index,
         "counterfactual_seed": spec.counterfactual_seed,
         "enabled_agent_ids": spec.enabled_agent_ids}
        for spec in matrix
    ], sort_keys=True, default=list, separators=(",", ":")).encode()).hexdigest()
    scope_hash = scope["content_sha256"]
    model = scope["model"]["name"]
    manifest = _read_json(manifest_path)
    if manifest is None:
        if any(run_dir.iterdir()):
            raise FileExistsError(f"results directory is nonempty and has no valid manifest: {run_dir}")
        manifest = {
            "schema_version": 1, "run_id": run_id, "scope_id": scope["scope_id"],
            "scope_sha256": scope_hash, "git_commit": _git_commit(REPO_ROOT),
            "benchmark_revision": scope["benchmark"]["revision"], "model": model,
            "model_seed": model_seed, "evaluator_python": evaluator_python,
            "base_url": base_url, "repetitions": repetitions, "order_seed": order_seed,
            "assignment_seed": assignment_seed, "matrix_sha256": matrix_signature,
        "model_options": {**scope["model_options"], "seed": model_seed},
            "process_timeout_seconds": timeout, "started_at": _utc_now(), "synthetic": synthetic,
        }
        _write_json(manifest_path, manifest)
    else:
        expected = (manifest.get("scope_sha256"), manifest.get("model"), manifest.get("model_seed"),
                    manifest.get("order_seed"), manifest.get("assignment_seed"), manifest.get("repetitions"),
                    manifest.get("matrix_sha256"), manifest.get("process_timeout_seconds"),
                    manifest.get("model_options"))
        actual = (scope_hash, model, model_seed, order_seed, assignment_seed, repetitions,
                  matrix_signature, timeout, {**scope["model_options"], "seed": model_seed})
        if manifest.get("run_id") != run_id or expected != actual:
            raise ValueError("cannot resume: run id, scope, model seed, ordering seed, assignment seed, or repetitions differ")

    rows = _load_attempts(attempts_path)
    try:
        for spec in matrix:
            previous = [row for row in rows if _key_from_row(row) == spec.key]
            if any(row.get("attempt_status") == "completed" for row in previous):
                continue
            if previous and not retry_failed:
                continue
            attempt_number = _planned_attempt_number(rows, spec.key)
            workload, task_id, config_pct, repetition = spec.key
            attempt_dir = run_dir / "raw" / workload / task_id / f"cf-{config_pct}" / f"rep-{repetition}" / f"attempt-{attempt_number}"
            runner_output_dir = attempt_dir / "runner_output"
            command = _runner_command(spec, runner_output_dir, benchmark_root, evaluator_python,
                                      base_url, model_seed, scope)
            request = RunRequest(spec, attempt_number, attempt_dir, runner_output_dir,
                                 command, REPO_ROOT, timeout,
                                 model_options={**scope["model_options"], "seed": model_seed})
            try:
                process = runner(request)
            except KeyboardInterrupt:
                process = ProcessResult(None, error="orchestrator interrupted while task subprocess was active")
                row = _attempt_row(run_id, request, process, model_seed, order_seed, assignment_seed, synthetic)
                row["attempt_status"] = "failed"
                _append_attempt(attempts_path, row)
                rows.append(row)
                _write_csv(results_path, rows)
                _save_manifest(manifest_path, manifest, rows)
                raise
            except BaseException as exc:
                process = ProcessResult(None, error=f"runner raised {type(exc).__name__}: {exc}")
            row = _attempt_row(run_id, request, process, model_seed, order_seed, assignment_seed, synthetic)
            if synthetic:
                row["result_kind"] = "SYNTHETIC_FAKE_RUNNER"
                row["official_metric"] = ""
                row["official_score"] = ""
                row["correct"] = None
            _append_attempt(attempts_path, row)
            rows.append(row)
            _write_csv(results_path, rows)
            _save_manifest(manifest_path, manifest, rows)
    finally:
        rows = _load_attempts(attempts_path)
        _write_csv(results_path, rows)
        _save_manifest(manifest_path, manifest, rows)
    manifest["finished_at"] = _utc_now()
    _save_manifest(manifest_path, manifest, rows)
    return rows


def _git_commit(root: Path) -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, check=True, timeout=5)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) / total) + z * z / (4 * total * total)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _mean_median(values: list[Any]) -> tuple[float | None, float | None]:
    numbers = [float(value) for value in values if value not in (None, "")]
    return (statistics.mean(numbers), statistics.median(numbers)) if numbers else (None, None)


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summaries = {}
    for pct in (0, 33, 66, 100):
        group = [row for row in rows if int(row["config_pct"]) == pct]
        scored = [row for row in group if row.get("evaluator_score") not in (None, "")]
        correct = sum(row.get("correct") is True for row in group)
        lo, hi = _wilson(correct, len(group))
        scored_correct = sum(row.get("correct") is True for row in scored)
        score_lo, score_hi = _wilson(scored_correct, len(scored))
        iterations_mean, iterations_median = _mean_median([row.get("iterations") for row in group])
        tokens_mean, tokens_median = _mean_median([row.get("total_tokens") for row in group])
        provider_tokens_mean, _ = _mean_median([row.get("provider_total_tokens") for row in group])
        estimated_tokens_mean, _ = _mean_median([row.get("estimated_total_tokens") for row in group])
        estimated_share_mean, _ = _mean_median([row.get("estimated_share_pct") for row in group])
        provider_tokens_total = sum(int(float(row.get("provider_total_tokens") or 0)) for row in group)
        estimated_tokens_total = sum(int(float(row.get("estimated_total_tokens") or 0)) for row in group)
        token_total = provider_tokens_total + estimated_tokens_total
        live_tokens, _ = _mean_median([row.get("live_tokens") for row in group])
        simulation_tokens, _ = _mean_median([row.get("simulation_tokens") for row in group])
        elapsed_mean, elapsed_median = _mean_median([row.get("elapsed_time_s") for row in group])
        live_time, _ = _mean_median([row.get("live_elapsed_time_s") for row in group])
        simulation_time, _ = _mean_median([row.get("simulation_elapsed_time_s") for row in group])
        simulations = sum(int(row.get("counterfactual_simulations", 0) or 0) for row in group)
        recovery_attempts = sum(int(row.get("recovery_attempts", 0) or 0) for row in group)
        recovery_successes = sum(bool(row.get("recovery_success")) for row in group)
        mean_iterations, median_iterations = iterations_mean, iterations_median
        summaries[str(pct)] = {
            "attempted": len(group), "scored": len(scored), "completed_without_crash": sum(bool(row.get("completed_without_crash")) for row in group),
            "failed_before_scoring": len(group) - len(scored),
            "failed_before_scoring_rate": (len(group) - len(scored)) / len(group) if group else None,
            "correct": correct, "correct_rate_attempted": correct / len(group) if group else None,
            "correct_convergence_rate_scored": correct / len(scored) if scored else None,
            "correct_wilson_95": [lo, hi],  # backward-compatible alias: attempted denominator
            "correct_wilson_95_attempted": [lo, hi],
            "correct_convergence_wilson_95_scored": [score_lo, score_hi],
            "deadlock_count": sum(int(row.get("deadlock_count", 0) or 0) for row in group),
            "deadlock_rate": sum(int(row.get("deadlock_count", 0) or 0) > 0 for row in group) / len(group) if group else None,
            "recovery_attempt_rate": sum(int(row.get("recovery_attempts", 0) or 0) > 0 for row in group) / len(group) if group else None,
            "recovery_success_rate_given_attempt": recovery_successes / recovery_attempts if recovery_attempts else None,
            "counterfactual_simulations": simulations,
            "iterations_mean": mean_iterations, "iterations_median": median_iterations,
            "tokens_mean": tokens_mean, "tokens_median": tokens_median,
            "provider_tokens_mean": provider_tokens_mean,
            "estimated_tokens_mean": estimated_tokens_mean,
            "estimated_share_pct_mean": estimated_share_mean,
            "provider_tokens_total": provider_tokens_total,
            "estimated_tokens_total": estimated_tokens_total,
            "estimated_share_pct_weighted": (100 * estimated_tokens_total / token_total) if token_total else 0.0,
            "call_attribution_available_runs": sum(row.get("call_attribution_available") in (True, "True") for row in group),
            "live_tokens_mean": live_tokens, "simulation_tokens_mean": simulation_tokens,
            "elapsed_time_mean_s": elapsed_mean, "elapsed_time_median_s": elapsed_median,
            "live_elapsed_time_mean_s": live_time, "simulation_elapsed_time_mean_s": simulation_time,
            "evaluator_modes": sorted({str(row.get("evaluator_mode")) for row in group}),
            "adapted_count": sum(bool(row.get("adapted")) for row in group),
        }

    baselines: dict[tuple[str, str, int], dict[str, Any]] = {}
    for row in rows:
        if int(row["config_pct"]) == 0 and row.get("attempt_status") == "completed":
            baselines[(row["workload"], row["task_id"], int(row["repetition"]))] = row
    paired = {}
    for pct in (33, 66, 100):
        wins = losses = ties = missing = 0
        comparisons = []
        for row in rows:
            if int(row["config_pct"]) != pct:
                continue
            key = (row["workload"], row["task_id"], int(row["repetition"]))
            baseline = baselines.get(key)
            if baseline is None or row.get("correct") is None or baseline.get("correct") is None:
                missing += 1
                result = "unscored"
            elif row["correct"] is baseline["correct"]:
                ties += 1
                result = "tie"
            elif row["correct"] is True:
                wins += 1
                result = "win"
            else:
                losses += 1
                result = "loss"
            comparisons.append({"workload": key[0], "task_id": key[1], "repetition": key[2], "result": result})
        paired[str(pct)] = {"wins": wins, "losses": losses, "ties": ties, "unscored_or_unpaired": missing, "tasks": comparisons}
    partitions: dict[str, list[dict[str, Any]]] = {"standard": [], "adapted": []}
    modes: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        partition = row.get("result_partition")
        if partition not in partitions:
            partition = result_reporting_labels(
                str(row.get("evaluator_mode") or "unknown"), bool(row.get("adapted"))
            )["result_partition"]
        partitions[partition].append(row)
        modes.setdefault(str(row.get("evaluator_mode") or "unknown"), []).append(row)

    def partition_summary(group: list[dict[str, Any]]) -> dict[str, Any]:
        scored = [row for row in group if row.get("evaluator_score") not in (None, "")]
        return {
            "attempted": len(group), "scored": len(scored),
            "failed_before_scoring": len(group) - len(scored),
            "correct": sum(row.get("correct") is True for row in group),
        }

    return {
        "by_config_pct": summaries, "paired_vs_zero_percent": paired,
        "by_evaluator_partition": {key: partition_summary(value) for key, value in partitions.items()},
        "by_evaluator_mode": {key: partition_summary(value) for key, value in sorted(modes.items())},
    }


def aggregate_run(run_dir: Path) -> dict[str, Any]:
    synthetic = (run_dir / "SYNTHETIC_attempts.jsonl").exists()
    rows = _reconcile_saved_outcomes(
        _load_attempts(run_dir / ("SYNTHETIC_attempts.jsonl" if synthetic else "attempts.jsonl"))
    )
    _write_csv(run_dir / ("SYNTHETIC_results.csv" if synthetic else "results.csv"), rows)
    summary = aggregate_rows(rows)
    summary["run_id"] = run_dir.name
    summary["attempt_rows"] = len(rows)
    run_manifest = _read_json(run_dir / "results_manifest.json") or {}
    old_options = run_manifest.get("model_options", {})
    is_pre_context_fix = (
        run_manifest.get("scope_id") == "kramabench_pilot_v1"
        or int(old_options.get("num_ctx", 0) or 0) < 8192
        or int(old_options.get("num_predict", 0) or 0) < 2048
    )
    summary["context_fix_label"] = "pre-context-fix" if is_pre_context_fix else "context-v2"
    summary["context_fix_note"] = (
        "Historical run retained its original records; it used the v1 scope before the context/output limit increase."
        if is_pre_context_fix else "Run used context-v2 scope settings."
    )
    summary_name = "SYNTHETIC_aggregate_summary" if synthetic else "aggregate_summary"
    _write_json(run_dir / f"{summary_name}.json", summary)
    lines = [f"# {'SYNTHETIC ' if synthetic else ''}Ablation aggregate — {run_dir.name}", "",
             f"**Context configuration: {summary['context_fix_label']}** — {summary['context_fix_note']}", "",
             "Rows are attempt-level; failures remain in denominators. Correctness is based only on evaluator pass rules. Wilson intervals are 95%.", "",
             "| CF | Attempted | Scored | Failed before score | Correct / attempted | Correct / scored | Wilson 95% (attempted) | Wilson 95% (scored) | Deadlock | Recovery attempt | Recovery success / trigger | Mean / median iterations | Mean provider / estimated tokens | Estimated share | Mean live / simulation tokens | Mean live / simulation seconds |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for pct, item in summary["by_config_pct"].items():
        def fmt(key, places=3):
            value = item.get(key)
            return "n/a" if value is None else f"{value:.{places}f}"
        def interval(key):
            values = item.get(key) or [None, None]
            return "n/a" if values[0] is None else f"[{values[0]:.3f}, {values[1]:.3f}]"
        iterations = f"{fmt('iterations_mean', 2)} / {fmt('iterations_median', 2)}"
        tokens = f"{fmt('live_tokens_mean', 1)} / {fmt('simulation_tokens_mean', 1)}"
        source_tokens = f"{fmt('provider_tokens_mean', 1)} / {fmt('estimated_tokens_mean', 1)}"
        times = f"{fmt('live_elapsed_time_mean_s', 2)} / {fmt('simulation_elapsed_time_mean_s', 2)}"
        lines.append(
            f"| {pct}% | {item['attempted']} | {item['scored']} | {item['failed_before_scoring']} ({fmt('failed_before_scoring_rate')}) "
            f"| {item['correct']} ({fmt('correct_rate_attempted')}) | {item['correct']} ({fmt('correct_convergence_rate_scored')}) "
            f"| {interval('correct_wilson_95_attempted')} | {interval('correct_convergence_wilson_95_scored')} "
            f"| {fmt('deadlock_rate')} | {fmt('recovery_attempt_rate')} | {fmt('recovery_success_rate_given_attempt')} "
            f"| {iterations} | {source_tokens} | {fmt('estimated_share_pct_weighted', 1)}% | {tokens} | {times} |"
        )
    lines.extend(["", "## Paired comparison against 0%", ""])
    for pct, item in summary["paired_vs_zero_percent"].items():
        lines.append(f"- {pct}%: wins {item['wins']}, losses {item['losses']}, ties {item['ties']}, unscored/unpaired {item['unscored_or_unpaired']}.")
    (run_dir / f"{summary_name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def _write_plan(scope: dict[str, Any], split: str, count: int | None,
                repetitions: int, order_seed: int, assignment_seed: int) -> list[RunSpec]:
    tasks = select_split_tasks(scope, split, count)
    return build_matrix(scope, tasks, repetitions, order_seed, assignment_seed)


def _print_plan(scope: dict[str, Any], specs: list[RunSpec], split: str, order_seed: int,
                assignment_seed: int) -> None:
    agents = runtime_agent_ids(scope)
    print(f"Scope: {scope['scope_id']} sha256={scope['content_sha256']}")
    print(f"Split: {split}; tasks={len({(s.task['workload'], s.task['task_id']) for s in specs})}; configurations={len(CONFIGURATIONS)}; total runs={len(specs)}")
    print(f"Agents in frozen scope: {len(agents)} ({', '.join(agents)})")
    for fraction in CONFIGURATIONS:
        print(f"  {fraction:.0%}: effective enabled agents={effective_enabled_count(len(agents), fraction)}")
    print(f"Run order seed={order_seed}; assignment seed={assignment_seed}")
    print("order | task | split | fraction | enabled count | enabled ids | repetition")
    for spec in specs:
        print(f"{spec.order_index:04d} | {spec.task['workload']}/{spec.task['task_id']} | {spec.task['split']} | {spec.fraction:.0%} | {len(spec.enabled_agent_ids)} | {','.join(spec.enabled_agent_ids) or '-'} | {spec.repetition}")


def _resolve_setting(flag_value: str | None, env_name: str, name: str) -> str:
    value = flag_value or os.environ.get(env_name)
    if not value:
        raise ValueError(f"provide {name} or set {env_name}")
    return value


def _experiment_cli(args: argparse.Namespace, scope: dict[str, Any]) -> int:
    if not args.approve_genuine_run:
        raise ValueError("real model execution requires --approve-genuine-run")
    benchmark_root = Path(_resolve_setting(args.benchmark_root, "KRAMABENCH_ROOT", "--benchmark-root")).expanduser().resolve()
    evaluator_python = _resolve_setting(args.evaluator_python, "KRAMABENCH_EVALUATOR_PYTHON", "--evaluator-python")
    base_url = _resolve_setting(args.base_url, "OLLAMA_BASE_URL", "--base-url")
    split = "tuning" if args.command == "dry-run" else "held_out_pilot"
    count = args.task_count if args.command == "dry-run" else None
    tasks = select_split_tasks(scope, split, count)
    run_id = args.run_id or f"{args.command}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    results_root = Path(args.results_root).expanduser().resolve()
    run_dir = results_root / run_id
    if not run_dir.exists() and any((results_root / run_id).parent.glob(run_dir.name)):
        raise FileExistsError(f"run directory already exists: {run_dir}")
    order_seed = args.order_seed if args.order_seed is not None else int(scope["selection"]["seed"]) + 1
    assignment_seed = args.assignment_seed if args.assignment_seed is not None else int(scope["selection"]["seed"]) + 2
    timeout = args.process_timeout_seconds or int(scope["stopping_rules"]["task_wall_clock_cap_seconds"]) + 300
    rows = run_experiment(
        scope=scope, tasks=tasks, run_dir=run_dir, run_id=run_id,
        benchmark_root=benchmark_root, evaluator_python=evaluator_python, base_url=base_url,
        model_seed=args.model_seed if args.model_seed is not None else int(scope["model_options"]["seed"]),
        repetitions=args.repetitions, order_seed=order_seed, assignment_seed=assignment_seed,
        process_timeout_seconds=timeout, retry_failed=args.retry_failed,
    )
    print(f"Recorded {len(rows)} attempt rows in {run_dir}; completed/correct counts are in results_manifest.json.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan", help="Print a frozen-scope run matrix without execution.")
    dry_run = subparsers.add_parser("dry-run", help="Run 3-5 tuning tasks under all four configurations.")
    pilot = subparsers.add_parser("pilot", help="Run every frozen held-out task under all four configurations.")
    for command_parser in (plan, dry_run, pilot):
        command_parser.add_argument("--scope", type=Path, default=DEFAULT_SCOPE)
        command_parser.add_argument("--repetitions", type=int, default=1)
        command_parser.add_argument("--order-seed", type=int)
        command_parser.add_argument("--assignment-seed", type=int)
        command_parser.add_argument("--model-seed", type=int)
    plan.add_argument("--split", choices=("tuning", "held_out_pilot"), default="held_out_pilot")
    plan.add_argument("--task-count", type=int, choices=(3, 4, 5))
    for command_parser in (dry_run, pilot):
        command_parser.add_argument("--benchmark-root", type=Path, default=os.environ.get("KRAMABENCH_ROOT"))
        command_parser.add_argument("--evaluator-python")
        command_parser.add_argument("--base-url", default=os.environ.get("OLLAMA_BASE_URL"))
        command_parser.add_argument("--results-root", type=Path, default=REPO_ROOT / "bench" / "results")
        command_parser.add_argument("--run-id")
        command_parser.add_argument("--process-timeout-seconds", type=int)
        command_parser.add_argument("--retry-failed", action="store_true")
        command_parser.add_argument("--approve-genuine-run", action="store_true")
    dry_run.add_argument("--task-count", type=int, choices=(3, 4, 5), default=5)

    status = subparsers.add_parser("status", help="Show saved experiment status.")
    status.add_argument("--results-root", type=Path, default=REPO_ROOT / "bench" / "results")
    status.add_argument("--run-id")
    aggregate = subparsers.add_parser("aggregate", help="Aggregate one saved experiment without running tasks.")
    aggregate.add_argument("--results-root", type=Path, default=REPO_ROOT / "bench" / "results")
    aggregate.add_argument("--run-id", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command in {"plan", "dry-run", "pilot"}:
            scope = load_scope(args.scope)
            order_seed = args.order_seed if args.order_seed is not None else int(scope["selection"]["seed"]) + 1
            assignment_seed = args.assignment_seed if args.assignment_seed is not None else int(scope["selection"]["seed"]) + 2
            if args.command == "plan":
                split = args.split
                specs = _write_plan(scope, split, args.task_count, args.repetitions, order_seed, assignment_seed)
                _print_plan(scope, specs, split, order_seed, assignment_seed)
                return 0
            return _experiment_cli(args, scope)
        results_root = args.results_root.expanduser().resolve()
        if args.command == "status":
            dirs = [results_root / args.run_id] if args.run_id else sorted(
                path for path in results_root.iterdir() if path.is_dir() and (
                    (path / "results_manifest.json").exists()
                    or (path / "SYNTHETIC_results_manifest.json").exists()
                )
            ) if results_root.exists() else []
            if not dirs:
                print("No orchestrator runs found.")
                return 0
            for path in dirs:
                manifest = _read_json(path / "results_manifest.json") or _read_json(path / "SYNTHETIC_results_manifest.json")
                if manifest is None:
                    print(f"{path.name}: invalid or missing results_manifest.json")
                else:
                    print(f"{path.name}: attempts={manifest.get('attempt_count', 0)} counts={json.dumps(manifest.get('counts_by_config_pct', {}), sort_keys=True)}")
            return 0
        run_dir = results_root / args.run_id
        if not run_dir.exists():
            raise FileNotFoundError(f"experiment does not exist: {run_dir}")
        summary = aggregate_run(run_dir)
        print(f"Aggregate written for {args.run_id}: {run_dir / 'aggregate_summary.md'}")
        return 0
    except (ScopeError, OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
