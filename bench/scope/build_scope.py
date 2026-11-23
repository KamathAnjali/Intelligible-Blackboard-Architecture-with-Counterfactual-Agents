"""Build a reproducible KramaBench task inventory and frozen pilot scope.

This utility reads task metadata, evaluator metric mappings, allowlist entries,
archived run outcomes, documentation exposure, and input availability. It never
loads or emits task answers, reference pipelines, subtasks, or query text.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from bench.kramabench_adapter import KRAMABENCH_REVISION
from bench.kramabench_adapter import DatasetInputError, resolve_subset_files
from bench.scope.load_scope import scope_sha256


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PINNED_URL = "https://github.com/mitdbg/KramaBench.git"
SUPPORTED_SUFFIXES = {
    ".csv", ".tsv", ".json", ".jsonl", ".html", ".htm", ".txt", ".text", ".md",
    ".xml", ".sql", ".log", ".tle", ".dat", ".xlsx", ".pdf",
}
DETERMINISTIC_METRICS = {
    "success", "mean_absolute_error", "mean_squared_error", "rae_score", "precision", "recall", "f1"
}
UNSAFE_COMPATIBILITY_METRICS = {"f1"}
DEFAULT_SEED = 20261110
HELD_OUT_COUNT = 25
TUNING_COUNT = 5
MODEL_TAG = "qwen3:4b-instruct-2507-q4_K_M"
COMPAT_PATCH_SHA256 = "39dc1ea82ad6d7a75ca3a18de7b3efcbeb58250b307b09c45541286d20394897"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _seed_key(seed: int, workload: str, task_id: str) -> str:
    return hashlib.sha256(f"{seed}\0{workload}\0{task_id}".encode("utf-8")).hexdigest()


def select_task_splits(
    candidates: list[dict[str, Any]], seed: int, tuning_count: int = TUNING_COUNT,
    held_out_count: int = HELD_OUT_COUNT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Choose tuning tasks from eligible compatibility tasks and blind held-out tasks.

    Hash ordering makes selection deterministic and independent of scores or answers.
    Held-out tasks must be unexposed and use the unmodified official evaluator.
    """
    tune_pool = [item for item in candidates if item["eligible"] and item["evaluator_mode"] == "compatibility-no-key"]
    pilot_pool = [item for item in candidates if item["eligible"] and not item["prior_exposure"]
                  and item["evaluator_mode"] == "official"]
    order = lambda item: (_seed_key(seed, item["workload"], item["task_id"]), item["workload"], item["task_id"])
    tuning = sorted(tune_pool, key=order)[:tuning_count]
    held_out = sorted(pilot_pool, key=order)[:held_out_count]
    if len(tuning) != tuning_count:
        raise ValueError(f"need {tuning_count} eligible compatibility tuning tasks; found {len(tuning)}")
    if len(held_out) != held_out_count:
        raise ValueError(f"need {held_out_count} eligible unexposed official tasks; found {len(held_out)}")
    if {(x["workload"], x["task_id"]) for x in tuning} & {(x["workload"], x["task_id"]) for x in held_out}:
        raise ValueError("tuning and held-out task selections overlap")
    return tuning, held_out


def _pinned_revision(root: Path) -> str:
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                            text=True, capture_output=True, check=True)
    revision = result.stdout.strip()
    if revision != KRAMABENCH_REVISION:
        raise ValueError(f"pinned checkout revision mismatch: {revision}")
    return revision


def _workload_rows(root: Path) -> list[tuple[str, dict[str, Any]]]:
    rows = []
    for path in sorted((root / "workload").glob("*.json")):
        value = _load_json(path)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                rows.append((path.stem, item))
    return rows


def _metric_map(root: Path) -> dict[str, list[str]]:
    fixture_rows = _load_json(root / "benchmark" / "fixtures" / "answer_type_fixtures.json")
    return {item["name"]: list(item["metrics"]) for item in fixture_rows}


def _allowlist(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    data = _load_json(path)
    entries = data.get("entries", [])
    return {(entry["workload"], entry["task_id"]): entry for entry in entries}


def _historical_archive(root: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    history: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for manifest_path in sorted((root / "archive").glob("*/run_manifest.json")):
        try:
            manifest = _load_json(manifest_path)
        except (OSError, ValueError):
            continue
        workload, task_id = manifest.get("workload"), manifest.get("task_id")
        if not isinstance(workload, str) or not isinstance(task_id, str):
            continue
        measures = []
        for csv_path in sorted(manifest_path.parent.rglob("*_measures_*.csv")):
            try:
                with csv_path.open(encoding="utf-8", newline="") as handle:
                    measures.extend(row for row in csv.DictReader(handle) if row.get("metric") == "success")
            except OSError:
                continue
        history[(workload, task_id)].append({
            "archive": manifest_path.parent.name,
            "status": manifest.get("status", "unknown"),
            "evaluator_mode": manifest.get("evaluator_mode", "unknown"),
            "score": measures[0].get("value") if measures else None,
            "error_type": manifest.get("error_type"),
        })
    return history


def _prior_exposures(root: Path, task_ids: set[str]) -> set[str]:
    exposed: set[str] = set()
    for base in (root / "bench" / "results", root / "docs"):
        if not base.exists():
            continue
        for path in base.rglob("*.md"):
            if "scope" in path.parts:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            exposed.update(task_id for task_id in task_ids if task_id in content)
    exposed.update(task_id for _, task_id in _historical_archive(root))
    return exposed


def _answer_check(answer_type: str, metrics: list[str]) -> tuple[bool, str]:
    independent = bool(metrics) and all(metric in DETERMINISTIC_METRICS for metric in metrics)
    return independent, "pinned answer_type_fixtures.json: " + ",".join(metrics)


def _pass_rule(metrics: list[str]) -> dict[str, Any] | None:
    """Return only exact-match pass rules defined by deterministic evaluator metrics."""
    if metrics == ["success"] or set(metrics) == {"f1", "precision", "recall"}:
        return {"kind": "all_metrics_equal", "value": 1.0}
    return None


def _check_data_availability(dataset_root: Path, sources: Any) -> tuple[bool, str, list[dict[str, Any]]]:
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list) or not sources or any(not isinstance(item, str) for item in sources):
        return False, "missing_or_invalid_data_sources", []
    try:
        paths = resolve_subset_files(dataset_root, sources)
    except (DatasetInputError, OSError, ValueError) as exc:
        return False, f"input_resolution_failed:{type(exc).__name__}", []
    files = []
    unavailable: list[str] = []
    for path in paths:
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            unavailable.append(f"unsupported_format:{suffix or 'none'}")
            continue
        if suffix == ".xlsx" and importlib.util.find_spec("openpyxl") is None:
            unavailable.append("parser_dependency_missing:openpyxl")
            continue
        if suffix == ".pdf" and importlib.util.find_spec("pdfplumber") is None:
            unavailable.append("parser_dependency_missing:pdfplumber")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"path": path.relative_to(dataset_root).as_posix(), "sha256": digest,
                      "size_bytes": path.stat().st_size, "format": suffix.lstrip(".")})
    if unavailable:
        return False, ";".join(sorted(set(unavailable))), files
    if not files:
        return False, "no_supported_input_files", []
    if sum(item["size_bytes"] for item in files) > 512 * 1024 * 1024:
        return False, "task_input_exceeds_total_data_tool_limit", files
    if max(item["size_bytes"] for item in files) > 128 * 1024 * 1024:
        return False, "task_input_exceeds_per_file_data_tool_limit", files
    return True, "available_and_supported", files


def inventory_candidates(benchmark_root: Path, repository_root: Path, allowlist_path: Path) -> list[dict[str, Any]]:
    revision = _pinned_revision(benchmark_root)
    metrics_by_type = _metric_map(benchmark_root)
    allowed = _allowlist(allowlist_path)
    raw_rows = _workload_rows(benchmark_root)
    ids = {str(row["id"]) for _, row in raw_rows}
    exposed = _prior_exposures(repository_root, ids)
    history = _historical_archive(repository_root)
    candidates = []
    for workload, row in raw_rows:
        task_id = str(row["id"])
        answer_type = str(row.get("answer_type", ""))
        metric_names = metrics_by_type.get(answer_type, [])
        pass_rule = _pass_rule(metric_names)
        evaluator_mode = "compatibility-no-key" if (workload, task_id) in allowed else "official"
        independent, provenance = _answer_check(answer_type, metric_names)
        non_main = workload.endswith("-tiny") or workload == "quick-start-questions"
        data_root = benchmark_root / "data" / workload / "input"
        available, availability_reason, files = _check_data_availability(data_root, row.get("data_sources"))
        prior = task_id in exposed
        adaptation_flags = []
        if evaluator_mode != "official":
            adaptation_flags.append("compatibility_evaluator_patch: conditionally defers GPTInterface construction when pipeline evaluation is disabled")
        if workload.endswith("-tiny") or workload == "quick-start-questions":
            adaptation_flags.append("non_main_workload_subset")
        reasons = []
        if non_main:
            reasons.append("non_main_workload_subset")
        if not available:
            reasons.append(availability_reason)
        if not independent:
            reasons.append("answer_metric_not_independent_deterministic")
        if pass_rule is None:
            reasons.append("no_scope_defined_binary_pass_rule")
        if evaluator_mode == "compatibility-no-key" and any(m in UNSAFE_COMPATIBILITY_METRICS for m in metric_names):
            reasons.append("compatibility_metric_not_reviewed_for_no_key_mode")
        if prior and evaluator_mode == "official":
            reasons.append("previously_exposed; excluded from held-out selection")
        candidates.append({
            "workload": workload,
            "task_id": task_id,
            "dataset_domain": row.get("domain") or workload.removesuffix("-tiny"),
            "difficulty": row.get("difficulty") or _difficulty_from_id(task_id),
            "answer_type": answer_type,
            "metric_names": metric_names,
            "pass_rule": pass_rule,
            "evaluator_mode": evaluator_mode,
            "adapted": evaluator_mode != "official" or non_main,
            "adaptation_flags": adaptation_flags,
            "data_available": available,
            "data_availability_reason": availability_reason,
            "input_files": files,
            "independent_answer_check": independent,
            "answer_check_provenance": provenance,
            "prior_exposure": prior,
            "historical_outcomes": history.get((workload, task_id), []),
            "excluded_reason": ";".join(reasons),
            "eligible": not reasons,
            "benchmark_revision": revision,
        })
    return candidates


def _difficulty_from_id(task_id: str) -> str:
    match = re.search(r"-(easy|medium|hard)-\d+$", task_id)
    return match.group(1) if match else "unknown"


def _csv_rows(candidates: list[dict[str, Any]], tuning: list[dict[str, Any]], held_out: list[dict[str, Any]]) -> list[dict[str, str]]:
    selected = {(item["workload"], item["task_id"]): "tuning" for item in tuning}
    selected.update({(item["workload"], item["task_id"]): "held_out_pilot" for item in held_out})
    fields = ["task_id", "dataset_domain", "workload", "difficulty", "evaluator_mode", "metric_type", "pass_rule",
              "adapted", "adaptation_flags", "data_available", "input_files", "independent_answer_check",
              "historical_outcome", "prior_exposure", "split", "excluded_reason"]
    output = []
    for candidate in candidates:
        key = (candidate["workload"], candidate["task_id"])
        outcome = candidate["historical_outcomes"]
        row = {
            "task_id": candidate["task_id"], "dataset_domain": str(candidate["dataset_domain"]),
            "workload": candidate["workload"], "difficulty": candidate["difficulty"],
            "evaluator_mode": candidate["evaluator_mode"], "metric_type": candidate["answer_type"] + ":" + ",".join(candidate["metric_names"]),
            "pass_rule": json.dumps(candidate["pass_rule"], sort_keys=True),
            "adapted": str(candidate["adapted"]).lower(), "adaptation_flags": json.dumps(candidate["adaptation_flags"], sort_keys=True),
            "data_available": str(candidate["data_available"]).lower(), "input_files": json.dumps(candidate["input_files"], sort_keys=True),
            "independent_answer_check": str(candidate["independent_answer_check"]).lower(),
            "historical_outcome": json.dumps(outcome, sort_keys=True), "prior_exposure": str(candidate["prior_exposure"]).lower(),
            "split": selected.get(key, "excluded"), "excluded_reason": candidate["excluded_reason"] or ("not selected by seeded sampling rule" if key not in selected else ""),
        }
        output.append({field: row[field] for field in fields})
    return output


def build_scope(benchmark_root: Path, repository_root: Path, allowlist_path: Path,
                seed: int = DEFAULT_SEED) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    revision = _pinned_revision(benchmark_root)
    candidates = inventory_candidates(benchmark_root, repository_root, allowlist_path)
    tuning, held_out = select_task_splits(candidates, seed)
    tasks = []
    for split, rows in (("tuning", tuning), ("held_out_pilot", held_out)):
        for candidate in rows:
            tasks.append({
                "workload": candidate["workload"],
                "task_id": candidate["task_id"],
                "dataset_domain": candidate["dataset_domain"],
                "difficulty": candidate["difficulty"],
                "split": split,
                "answer_type": candidate["answer_type"],
                "metric_names": candidate["metric_names"],
                "pass_rule": candidate["pass_rule"],
                "evaluator_mode": candidate["evaluator_mode"],
                "adapted": candidate["adapted"],
                "adaptation_flags": candidate["adaptation_flags"],
                "independent_answer_check": True,
                "answer_check_provenance": {
                    "benchmark_revision": revision,
                    "answer_type_fixture": "benchmark/fixtures/answer_type_fixtures.json",
                    "metric_names": candidate["metric_names"],
                    "metric_kind": "deterministic",
                },
                "input_files": candidate["input_files"],
            })
    tasks.sort(key=lambda task: (task["split"], task["workload"], task["task_id"]))
    excluded = [{"workload": item["workload"], "task_id": item["task_id"],
                 "reason": item["excluded_reason"] or "not selected by seeded sampling rule"}
                for item in candidates if (item["workload"], item["task_id"]) not in {
                    (task["workload"], task["task_id"]) for task in tasks
                }]
    exclusion_counts = dict(sorted(Counter(item["reason"] for item in excluded).items()))
    scope: dict[str, Any] = {
        "schema_version": 1,
        "scope_id": "kramabench_pilot_v1",
        "status": "frozen",
        "benchmark": {"repository_url": PINNED_URL, "revision": revision, "version": revision},
        "evaluators": {
            "official": {"mode": "official", "version": revision,
                         "adapted": False, "pipeline_evaluation_enabled": False,
                         "run_subtasks": False,
                         "credential_prerequisite": "OPENAI_API_KEY must be configured; value is never stored"},
            "compatibility-no-key": {"mode": "compatibility-no-key", "version": revision,
                                      "patch_sha256": COMPAT_PATCH_SHA256, "adapted": True,
                                      "pipeline_evaluation_enabled": False, "run_subtasks": False,
                                      "adaptation": "Defer GPTInterface construction when pipeline evaluation is disabled; no workload/data changes."},
        },
        "model": {"provider": "ollama", "name": MODEL_TAG, "version": MODEL_TAG,
                  "tag_digest": None, "digest_note": "Unavailable in archived manifests; capture /api/show digest on every later run."},
        "model_options": {"num_ctx": 4096, "temperature": 0, "num_predict": 768, "seed": 42},
        "personas": ["aggressive_proposer", "cautious_verifier"],
        "model_agent_count": 2,
        "scheduler_policy": "Round-robin proposer/verifier; consensus requires matching RATIFY predictions from all active agents.",
        "stopping_rules": {
            "max_turns": 6, "max_retries_per_generation": 1,
            "task_wall_clock_cap_seconds": 900, "data_operation_timeout_seconds": 120,
            "data_tool_invocation_cap": 12, "model_context_tokens": 4096,
            "max_output_tokens_per_generation": 768, "total_token_budget": None,
            "total_token_budget_note": "No cumulative task token cap is configured; runs must report actual provider usage.",
            "max_counterfactual_simulations_per_task": 1,
            "counterfactual_streak_threshold": 2,
            "counterfactual_simulation_generation_retries": 0,
        },
        "counterfactual_fractions": [0.0, 0.33, 0.66, 1.0],
        "selection": {
            "seed": seed,
            "tuning_count": len(tuning), "held_out_count": len(held_out),
            "rule": "Select by ascending SHA-256(seed NUL workload NUL task_id); no answer, query text, historical score, or agent outcome is read for ordering. Tuning candidates are compatibility-allowlisted deterministic tasks; held-out candidates are unexposed main-workload deterministic tasks using the unmodified evaluator.",
            "tuning_use": ["prompt/replay-rule tuning", "pipeline dry-run"],
            "dry_run_task_count": 5,
            "held_out_exposure_rule": "Exclude every task ID found in existing archive manifests or prior docs/bench/results reports before sampling.",
        },
        "run_prerequisites": ["Exact pinned checkout and input hashes pass preflight.",
                              "OPENAI_API_KEY is configured for the official evaluator; no credential is embedded in this scope.",
                              "Ollama reports the exact model tag and runtime at launch."],
        "tasks": tasks,
        "excluded_tasks": excluded,
        "exclusion_counts": exclusion_counts,
        "content_sha256": "",
    }
    scope["content_sha256"] = scope_sha256(scope)
    return scope, candidates


def write_outputs(scope: dict[str, Any], candidates: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    scope_path = output_dir / "pilot_scope_v1.json"
    inventory_path = output_dir / "candidate_inventory.csv"
    if scope_path.exists() or inventory_path.exists():
        raise FileExistsError(f"scope outputs already exist under {output_dir}; refusing to overwrite")
    tuning = [task for task in scope["tasks"] if task["split"] == "tuning"]
    held_out = [task for task in scope["tasks"] if task["split"] == "held_out_pilot"]
    inventory = _csv_rows(candidates, tuning, held_out)
    with inventory_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(inventory[0]))
        writer.writeheader()
        writer.writerows(inventory)
    scope_path.write_text(json.dumps(scope, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-root", type=Path,
                        default=Path(os.environ.get("KRAMABENCH_ROOT", Path.home() / "kramabench-pinned")))
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--allowlist", type=Path, default=REPOSITORY_ROOT / "bench" / "compat_allowlist.json")
    parser.add_argument("--output-dir", type=Path, default=REPOSITORY_ROOT / "bench" / "scope")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    scope, candidates = build_scope(args.benchmark_root.expanduser().resolve(),
                                    args.repository_root.resolve(), args.allowlist.resolve(), args.seed)
    write_outputs(scope, candidates, args.output_dir.resolve())
    print(f"Candidates: {len(candidates)}; tuning: {scope['selection']['tuning_count']}; "
          f"held-out: {scope['selection']['held_out_count']}; excluded: {len(scope['excluded_tasks'])}")
    print(f"Scope SHA-256: {scope['content_sha256']}")
    print(f"Inventory: {args.output_dir / 'candidate_inventory.csv'}")
    print(f"Frozen scope: {args.output_dir / 'pilot_scope_v1.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
