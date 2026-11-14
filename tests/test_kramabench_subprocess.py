"""Bootstrap guards for the isolated KramaBench evaluator subprocess."""

import json
import os
from pathlib import Path
import sys

import pytest

import bench.kramabench_runner as runner
import bench.kramabench_subprocess as bootstrap


def _base_environment(monkeypatch, tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    source_root = tmp_path / "pinned-source"
    (source_root / "benchmark" / "fixtures").mkdir(parents=True)
    (source_root / "benchmark" / "fixtures" / "answer_type_fixtures.json").write_text(
        json.dumps([
            {"name": "numeric_exact", "metrics": ["success"]},
            {"name": "string_approximate", "metrics": ["llm_paraphrase"]},
        ]),
        encoding="utf-8",
    )
    run_root = tmp_path / "run"
    run_root.mkdir()
    compatibility_root = run_root / "evaluator_compatibility_no_key"
    compatibility_root.mkdir()
    evaluate_path = compatibility_root / "evaluate.py"
    evaluate_path.write_text("# fixture evaluator", encoding="utf-8")
    monkeypatch.setenv("KRAMABENCH_GENUINE_RUN_APPROVED", "1")
    monkeypatch.setenv("KRAMABENCH_PROJECT_ROOT", str(project_root))
    monkeypatch.setenv("KRAMABENCH_EVALUATE_PATH", str(evaluate_path))
    monkeypatch.setenv("KRAMABENCH_RUN_ROOT", str(run_root))
    monkeypatch.setenv("KRAMABENCH_EVALUATOR_ARGS", json.dumps([
        "--no_pipeline_eval", "--num_workers", "1", "--use_truth_subset",
    ]))
    monkeypatch.setenv("KRAMABENCH_EVALUATOR_MODE", runner.COMPATIBILITY_EVALUATOR_MODE)
    monkeypatch.setenv("KRAMABENCH_PIPELINE_EVAL", "0")
    monkeypatch.setenv("KRAMABENCH_WORKLOAD", "environment")
    monkeypatch.setenv("KRAMABENCH_TASK_ID", "environment-easy-1")
    monkeypatch.setenv("KRAMABENCH_COMPAT_PATCH_SHA256", runner.COMPATIBILITY_PATCH_SHA256)
    monkeypatch.setenv("KRAMABENCH_BENCHMARK_ROOT", str(source_root))
    return source_root, compatibility_root, evaluate_path


def test_subprocess_approval_gate_applies_to_compatibility_mode(monkeypatch):
    monkeypatch.delenv("KRAMABENCH_GENUINE_RUN_APPROVED", raising=False)
    monkeypatch.setenv("KRAMABENCH_EVALUATOR_MODE", runner.COMPATIBILITY_EVALUATOR_MODE)
    with pytest.raises(RuntimeError, match="explicit genuine-run approval"):
        bootstrap.main()


@pytest.mark.parametrize("bad_setting", ["task", "pipeline", "patch"])
def test_compatibility_subprocess_rejects_unsupported_configuration(monkeypatch, tmp_path, bad_setting):
    _base_environment(monkeypatch, tmp_path)
    if bad_setting == "task":
        monkeypatch.setenv("KRAMABENCH_TASK_ID", "different-task")
    elif bad_setting == "pipeline":
        monkeypatch.setenv("KRAMABENCH_PIPELINE_EVAL", "1")
    else:
        monkeypatch.setenv("KRAMABENCH_COMPAT_PATCH_SHA256", "0" * 64)

    expected = "task is not present in the project allowlist" if bad_setting == "task" else "configuration is incomplete or unsupported"
    with pytest.raises(RuntimeError, match=expected):
        bootstrap.main()


def test_compatibility_subprocess_checks_pinned_copy_then_removes_key_requirement(monkeypatch, tmp_path):
    source_root, compatibility_root, evaluate_path = _base_environment(monkeypatch, tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(runner, "verify_benchmark_checkout", lambda root: (root, root / "evaluate.py"))
    monkeypatch.setattr(
        runner,
        "_verify_compatibility_checkout",
        lambda root: {
            "revision": runner.KRAMABENCH_REVISION,
            "patch_sha256": runner.COMPATIBILITY_PATCH_SHA256,
            "patched_evaluator_sha256": "a" * 64,
        },
    )
    observed = {}

    def fake_run_path(path, run_name):
        observed["path"] = path
        observed["run_name"] = run_name
        observed["sys_argv"] = list(bootstrap.sys.argv)

    monkeypatch.setattr(bootstrap.runpy, "run_path", fake_run_path)
    monkeypatch.setattr(bootstrap.sys, "argv", list(sys.argv))
    monkeypatch.setattr(bootstrap.sys, "path", list(sys.path))
    bootstrap.main()

    assert observed["path"] == str(evaluate_path)
    assert observed["run_name"] == "__main__"
    assert "--no_pipeline_eval" in observed["sys_argv"]
    assert "OPENAI_API_KEY" not in os.environ
    assert source_root != compatibility_root


def test_compatibility_subprocess_rejects_allowlisted_llm_metric(monkeypatch, tmp_path):
    source_root, _, _ = _base_environment(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "verify_benchmark_checkout", lambda root: (root, root / "evaluate.py"))
    monkeypatch.setattr(
        runner, "_compatibility_allowlist_entry",
        lambda workload, task_id: {
            "answer_type": "string_approximate",
            "metric_names": ["llm_paraphrase"],
            "evaluator_provenance": {
                "revision": runner.KRAMABENCH_REVISION,
                "patch_path": "benchmark/evaluator.py",
                "patch_sha256": runner.COMPATIBILITY_PATCH_SHA256,
            },
        },
    )
    with pytest.raises(RuntimeError, match="metrics are not approved"):
        bootstrap.main()
