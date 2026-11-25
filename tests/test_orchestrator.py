"""Offline orchestration tests. Fake results are SYNTHETIC and never benchmark evidence."""

import csv
import json
from pathlib import Path

import pytest

from bench.orchestrator import (
    ProcessResult,
    RunRequest,
    RunSpec,
    _attempt_row,
    _classify_outcome,
    _reconcile_saved_outcomes,
    _correct_from_rule,
    aggregate_run,
    aggregate_rows,
    balanced_agent_assignment,
    build_matrix,
    effective_enabled_count,
    run_experiment,
    select_split_tasks,
)
from bench.scope.load_scope import load_scope
from bench.metrics.plotting import load_results_csv

SCOPE_PATH = Path(__file__).parents[1] / "bench" / "scope" / "pilot_scope_v1.json"


class FakeRunner:
    synthetic_fake_runner = True

    def __init__(self, fail_fraction=None):
        self.calls = []
        self.fail_fraction = fail_fraction

    def __call__(self, request: RunRequest) -> ProcessResult:
        self.calls.append(request)
        if request.spec.fraction == self.fail_fraction and request.attempt == 1:
            raise MemoryError("SYNTHETIC injected OOM")
        run_root = request.runner_output_dir / "SYNTHETIC_fake_run"
        (run_root / "system_scratch" / "system" / "krama_task_records").mkdir(parents=True)
        (run_root / "results" / "system").mkdir(parents=True)
        (run_root / "run_manifest.json").write_text(json.dumps({"status": "success"}), encoding="utf-8")
        transcript = run_root / "SYNTHETIC_transcript.json"
        transcript.write_text(json.dumps({
            "outcome": "scheduler_consensus", "scheduler_status": "STRONG",
            "turns": [{"turn": 1}], "board": {"intelligibility": "STRONG", "entries": []},
            "counterfactual_attempts": [],
        }), encoding="utf-8")
        task_record = {
            "status": "success", "execution_outcome": "scheduler_consensus",
            "conversation_transcript": str(transcript), "runtime_seconds": 0.25,
            "provider_token_usage": {"prompt_tokens": 8, "output_tokens": 2},
            "workflow_counters": {"counterfactual_simulations": 0, "counterfactual_triggers": 0},
        }
        rec = run_root / "system_scratch" / "system" / "krama_task_records" / "SYNTHETIC_record.json"
        rec.write_text(json.dumps(task_record), encoding="utf-8")
        score = 0.0 if request.spec.config_pct == 0 else 1.0
        measures = run_root / "results" / "system" / "SYNTHETIC_measures_fake.csv"
        with measures.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["metric", "value"])
            writer.writeheader()
            for metric in request.spec.task["metric_names"]:
                writer.writerow({"metric": metric, "value": score})
        # Exit code deliberately differs from score: 0% has rc=0 and score 0;
        # 100% has a score of 1 but a nonzero process code.
        return ProcessResult(7 if request.spec.config_pct == 100 else 0)


class InterruptRunner:
    synthetic_fake_runner = True

    def __init__(self):
        self.calls = 0
        self.ok_runner = FakeRunner()

    def __call__(self, request):
        self.calls += 1
        if self.calls == 2:
            raise KeyboardInterrupt()
        return self.ok_runner(request)


def _experiment(tmp_path, runner, *, retry_failed=False, run_id="SYNTHETIC-test"):
    scope = load_scope(SCOPE_PATH)
    task = select_split_tasks(scope, "tuning", 3)[0]
    return run_experiment(
        scope=scope, tasks=[task], run_dir=tmp_path / run_id, run_id=run_id,
        benchmark_root=tmp_path / "SYNTHETIC-checkout", evaluator_python="SYNTHETIC-python",
        base_url="SYNTHETIC-endpoint", model_seed=42, order_seed=101,
        assignment_seed=102, runner=runner, synthetic=True, retry_failed=retry_failed,
    )


def test_matrix_pairs_same_tasks_all_four_configs_and_rotates_assignments():
    scope = load_scope(SCOPE_PATH)
    tasks = select_split_tasks(scope, "held_out_pilot", 3)
    matrix = build_matrix(scope, tasks, repetitions=1, order_seed=4, assignment_seed=5)
    assert len(matrix) == 12
    for task in tasks:
        rows = [item for item in matrix if item.task["task_id"] == task["task_id"]]
        assert {item.config_pct for item in rows} == {0, 33, 66, 100}
    assert [effective_enabled_count(2, fraction) for fraction in (0, .33, .66, 1)] == [0, 1, 2, 2]
    assignments = [balanced_agent_assignment(["SYNTHETIC_A", "SYNTHETIC_B", "SYNTHETIC_C"], .33, slot, 9)[0] for slot in range(3)]
    assert set(assignments) == {"SYNTHETIC_A", "SYNTHETIC_B", "SYNTHETIC_C"}
    assert balanced_agent_assignment(["SYNTHETIC_A", "SYNTHETIC_B", "SYNTHETIC_C"], .33, 1, 9) == balanced_agent_assignment(["SYNTHETIC_A", "SYNTHETIC_B", "SYNTHETIC_C"], .33, 1, 9)


def test_every_attempt_including_failure_is_saved_resume_and_plot_gate_rejects(tmp_path):
    runner = FakeRunner(fail_fraction=.66)
    rows = _experiment(tmp_path, runner)
    assert len(rows) == 4
    assert "--num-ctx" in runner.calls[0].command
    assert runner.calls[0].command[runner.calls[0].command.index("--num-ctx") + 1] == "4096"
    assert runner.calls[0].command[runner.calls[0].command.index("--num-predict") + 1] == "768"
    assert runner.calls[0].model_options == {
        "num_ctx": 4096, "temperature": 0, "num_predict": 768, "seed": 42,
    }
    failed = [row for row in rows if row["attempt_status"] == "failed"]
    assert len(failed) == 1 and "SYNTHETIC injected OOM" in failed[0]["failure_reason"]
    run_dir = tmp_path / "SYNTHETIC-test"
    manifest = json.loads((run_dir / "SYNTHETIC_results_manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_options"]["num_ctx"] == 4096
    assert manifest["model_options"]["num_predict"] == 768
    aggregate_run(run_dir)
    aggregate = json.loads((run_dir / "SYNTHETIC_aggregate_summary.json").read_text(encoding="utf-8"))
    assert aggregate["context_fix_label"] == "pre-context-fix"
    attempts_before = len((run_dir / "SYNTHETIC_attempts.jsonl").read_text().splitlines())
    assert attempts_before == 4
    # Completed keys and failed keys both stay untouched without --retry-failed.
    _experiment(tmp_path, runner)
    assert len(runner.calls) == 4
    _experiment(tmp_path, runner, retry_failed=True)
    assert len(runner.calls) == 5
    saved = list(csv.DictReader((run_dir / "SYNTHETIC_results.csv").open(encoding="utf-8")))
    assert len(saved) == 5
    assert all(row["synthetic"] == "True" for row in saved)
    assert all(row["result_kind"] == "SYNTHETIC_FAKE_RUNNER" for row in saved)
    assert all(row["adapted"] == "True" and row["result_partition"] == "adapted" for row in saved)
    assert (run_dir / "SYNTHETIC_results.csv").exists()
    assert {
        "result_kind", "official_metric", "official_score", "token_source", "timing_scope",
        "ground_truth_used", "benchmark_source", "mode", "mainline_tokens", "dry_run", "notes",
    } <= set(saved[0])
    with pytest.raises(ValueError, match="not an official evaluator result"):
        load_results_csv(run_dir / "SYNTHETIC_results.csv")


def test_interruption_keeps_rows_and_resumes_without_overwriting(tmp_path):
    runner = InterruptRunner()
    with pytest.raises(KeyboardInterrupt):
        _experiment(tmp_path, runner, run_id="SYNTHETIC-interrupted")
    run_dir = tmp_path / "SYNTHETIC-interrupted"
    rows = (run_dir / "SYNTHETIC_attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 2
    first_key = json.loads(rows[0])["task_id"], json.loads(rows[0])["config_pct"]
    _experiment(tmp_path, runner, retry_failed=True, run_id="SYNTHETIC-interrupted")
    resumed = (run_dir / "SYNTHETIC_attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(resumed) == 5
    assert (json.loads(rows[0])["task_id"], json.loads(rows[0])["config_pct"]) == first_key
    assert len({(json.loads(line)["task_id"], json.loads(line)["config_pct"], json.loads(line)["attempt"]) for line in resumed}) == 5


def test_correctness_uses_score_pass_rule_not_exit_code():
    task = {"metric_names": ["success"], "pass_rule": {"kind": "all_metrics_equal", "value": 1.0}}
    assert _correct_from_rule(task, {"success": 0.0}) is False
    assert _correct_from_rule(task, {"success": 1.0}) is True


def test_turn_limit_is_classified_as_iteration_limit():
    outcome, reason = _classify_outcome(
        {"agent_conversation_outcome": "turn_limit", "errors": ["PXP workflow ended with turn_limit"]},
        None, False, None,
    )
    assert outcome == "ITERATION_LIMIT"
    assert "turn_limit" in reason


def test_saved_turn_limit_is_reconciled_without_mutating_attempt_log(tmp_path):
    record = tmp_path / "record.json"
    record.write_text(json.dumps({
        "agent_conversation_outcome": "turn_limit",
        "errors": ["PXP workflow ended with turn_limit and no verified deterministic answer"],
    }), encoding="utf-8")
    original = {"evaluator_score": None, "task_record_path": str(record), "timed_out": False,
                "outcome": "EXECUTION_FAILURE", "deadlock_count": 0}
    reconciled = _reconcile_saved_outcomes([original])[0]
    assert reconciled["outcome"] == "ITERATION_LIMIT"
    assert reconciled["deadlock_count"] == 0
    assert original["outcome"] == "EXECUTION_FAILURE"


def test_nonzero_exit_does_not_override_evaluator_correctness(tmp_path):
    scope = load_scope(SCOPE_PATH)
    task = select_split_tasks(scope, "tuning", 3)[0]
    spec = next(item for item in build_matrix(scope, [task], 1, 1, 2) if item.config_pct == 100)
    request = RunRequest(
        spec, 1, tmp_path / "SYNTHETIC_attempt", tmp_path / "SYNTHETIC_output",
        (), Path.cwd(), 60,
    )
    result = FakeRunner()(request)
    row = _attempt_row("SYNTHETIC-exit-code", request, result, 42, 1, 2, synthetic=True)
    assert result.return_code == 7
    assert row["evaluator_score"] == 1.0
    assert row["correct"] is True
    assert row["completed_without_crash"] is False
    assert row["result_kind"] == "SYNTHETIC_FAKE_RUNNER"


def test_aggregate_keeps_failed_before_scoring_in_denominator():
    rows = [
        {"config_pct": 0, "workload": "SYNTHETIC", "task_id": "SYNTHETIC-a", "repetition": 1,
         "evaluator_score": "", "correct": None, "deadlock_count": 0},
        {"config_pct": 0, "workload": "SYNTHETIC", "task_id": "SYNTHETIC-a", "repetition": 1,
         "evaluator_score": 1.0, "correct": True, "deadlock_count": 1},
    ]
    summary = aggregate_rows(rows)["by_config_pct"]["0"]
    assert summary["attempted"] == 2
    assert summary["failed_before_scoring"] == 1
    assert summary["failed_before_scoring_rate"] == .5
    assert summary["correct_wilson_95_attempted"] == [summary["correct_wilson_95"][0], summary["correct_wilson_95"][1]]
    assert "correct_convergence_wilson_95_scored" in summary
    assert "standard" in aggregate_rows(rows)["by_evaluator_partition"]
    assert summary["correct_rate_attempted"] == .5
