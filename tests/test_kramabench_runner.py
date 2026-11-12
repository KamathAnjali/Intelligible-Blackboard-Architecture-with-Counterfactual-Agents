"""Safety checks for the one-task runner; no model or benchmark is invoked."""

import hashlib
import json
import subprocess
import io
import stat
from types import SimpleNamespace
from pathlib import Path

import pytest

from bench.kramabench_preflight import PreflightSummary, PreflightTask, WorkloadPreflightError
from bench.kramabench_runner import (
    BenchmarkRevisionError,
    COMPATIBILITY_EVALUATOR_MODE,
    COMPATIBILITY_PATCH_SHA256,
    OfficialHarnessError,
    _evaluator_result_namespace,
    _prepare_compatibility_checkout,
    _require_no_answer_judge_when_pipeline_disabled,
    _evaluator_source_fingerprint,
    _persist_evaluator_failure_logs,
    _run_provenance_metadata,
    _validate_compatibility_task,
    _verify_compatibility_checkout,
    run_one_task,
    verify_benchmark_checkout,
    verify_evaluator_cli,
    verify_evaluator_environment,
    verify_ollama_model,
)


def make_unpinned_checkout(tmp_path):
    root = tmp_path / "kramabench"
    (root / "benchmark" / "fixtures").mkdir(parents=True)
    (root / "evaluate.py").write_text(
        "--no_pipeline_eval --num_workers --use_truth_subset", encoding="utf-8"
    )
    for relative in (
        "benchmark/benchmark_api.py", "benchmark/benchmark.py",
        "benchmark/executor.py", "benchmark/evaluator.py",
        "benchmark/fixtures/answer_type_fixtures.json",
    ):
        (root / relative).write_text("{}", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(root), "-c", "user.name=Fixture", "-c",
        "user.email=fixture@example.invalid", "commit", "-qm", "fixture",
    ], check=True)
    return root


def authoritative_checkout_available(root):
    required = (root / "workload" / "environment.json", root / "data" / "environment" / "input")
    if not root.is_dir() or not required[0].is_file() or not required[1].is_dir():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def test_evaluator_failure_logs_persist_exit_status_and_redact_secrets(tmp_path):
    completed = SimpleNamespace(
        returncode=1,
        stdout="evaluator started\nOPENAI_API_KEY=sk-testcredential123456789\n",
        stderr="Traceback: RuntimeError('bad request')\nAuthorization: Bearer abc.def.ghi\n",
    )

    persisted = _persist_evaluator_failure_logs(tmp_path, completed)

    assert persisted == {
        "exit_status": 1,
        "stdout_log": "evaluator_stdout.log",
        "stderr_log": "evaluator_stderr.log",
    }
    stdout_path = tmp_path / persisted["stdout_log"]
    stderr_path = tmp_path / persisted["stderr_log"]
    stdout_text = stdout_path.read_text(encoding="utf-8")
    stderr_text = stderr_path.read_text(encoding="utf-8")
    assert "evaluator started" in stdout_text
    assert "OPENAI_API_KEY=[REDACTED]" in stdout_text
    assert "sk-testcredential123456789" not in stdout_text
    assert "RuntimeError('bad request')" in stderr_text
    assert "Bearer [REDACTED]" in stderr_text
    assert stat.S_IMODE(stdout_path.stat().st_mode) == 0o600

    with pytest.raises(FileExistsError):
        _persist_evaluator_failure_logs(tmp_path, completed)
    assert stdout_path.read_text(encoding="utf-8") == stdout_text


def test_failed_evaluator_subprocess_logs_and_exit_status_are_recorded_in_manifest(monkeypatch, tmp_path):
    import json
    import bench.kramabench_runner as runner

    _mock_selected_task_path(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "verify_evaluator_environment", lambda *a, **k: "/fake/evaluator/python")
    monkeypatch.setattr(runner, "verify_ollama_model", lambda *a, **k: {
        "endpoint": "http://127.0.0.1:11434", "version": "0.34.4", "model": "fixture-model",
    })
    monkeypatch.setattr(runner, "_stage_run_tree", lambda *a, **k: None)
    def failed_evaluator(args, **kwargs):
        assert kwargs["env"]["KRAMABENCH_COUNTERFACTUAL_FRACTION"] == "0.66"
        assert kwargs["env"]["KRAMABENCH_COUNTERFACTUAL_SEED"] == "17"
        assert kwargs["env"]["KRAMABENCH_COUNTERFACTUAL_STREAK_THRESHOLD"] == "2"
        assert kwargs["env"]["KRAMABENCH_MAX_COUNTERFACTUAL_SIMULATIONS"] == "1"
        assert kwargs["env"]["KRAMABENCH_MODEL_SEED"] == "73"
        record_directory = Path(kwargs["cwd"]) / "system_scratch" / runner.SYSTEM_NAME / "krama_task_records"
        record_directory.mkdir(parents=True)
        (record_directory / "failed-task.json").write_text(json.dumps({
            "task_id": "environment-easy-1",
            "status": "failed",
            "errors": [{
                "type": "DataToolWorkflowError",
                "message": "agent returned an invalid structured data request after bounded retries",
            }],
        }), encoding="utf-8")
        return subprocess.CompletedProcess(
            args, 1, stdout="partial evaluator output\n", stderr="KeyError: workload\n",
        )

    monkeypatch.setattr(runner.subprocess, "run", failed_evaluator)
    output = tmp_path / "results"

    with pytest.raises(OfficialHarnessError, match="adapter failed before producing a result.*DataToolWorkflowError"):
        run_one_task(
            tmp_path / "pinned", "environment", "environment-easy-1", output,
            approve_genuine_run=True, evaluator_mode="official",
            counterfactual_fraction=0.66, counterfactual_seed=17,
            model_seed=73,
        )

    run_root = next(output.iterdir())
    manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["counterfactual_fraction"] == 0.66
    assert manifest["counterfactual_seed"] == 17
    assert manifest["counterfactual_streak_threshold"] == 2
    assert manifest["max_counterfactual_simulations"] == 1
    assert manifest["model_seed"] == 73
    assert manifest["model_options"]["seed"] == 73
    assert manifest["evaluator_process"] == {
        "exit_status": 1,
        "stdout_log": "evaluator_stdout.log",
        "stderr_log": "evaluator_stderr.log",
        "task_failure_summary": (
            "DataToolWorkflowError: agent returned an invalid structured data request after bounded retries"
        ),
    }


def test_missing_task_record_returns_no_result_without_workload_keyerror(monkeypatch, tmp_path):
    import json
    import bench.kramabench_runner as runner
    import bench.archive_run as archive_module

    _mock_selected_task_path(monkeypatch, tmp_path)
    monkeypatch.setattr(runner, "verify_evaluator_environment", lambda *a, **k: "/fake/evaluator/python")
    monkeypatch.setattr(runner, "verify_ollama_model", lambda *a, **k: {
        "endpoint": "http://127.0.0.1:11434", "version": "fixture", "model": "fixture-model",
    })
    monkeypatch.setattr(runner, "_stage_run_tree", lambda *a, **k: None)
    monkeypatch.setattr(archive_module, "REPO_ROOT", tmp_path)
    calls = []
    def failed_without_record(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="KeyError: workload")
    monkeypatch.setattr(runner.subprocess, "run", failed_without_record)

    run_root = run_one_task(
        tmp_path / "pinned", "environment", "environment-easy-1", tmp_path / "results",
        approve_genuine_run=True, evaluator_mode="official", archive_label="runner-archive",
    )
    manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "no_result"
    assert manifest["result"] == "no result"
    assert len(calls) == 1
    assert (run_root / "evaluator_stdout.log").read_text(encoding="utf-8") == ""
    assert (run_root / "evaluator_stderr.log").read_text(encoding="utf-8") == "KeyError: workload"
    archived = tmp_path / "archive" / "runner-archive"
    assert (archived / "run_manifest.json").is_file()
    assert (archived / "driver_logs/evaluator.stderr.log").is_file()
    assert (archived / "checksum_manifest.json").is_file()


def test_unpinned_checkout_is_rejected_before_any_run_output_is_created(tmp_path):
    root = make_unpinned_checkout(tmp_path)
    output = tmp_path / "results"
    with pytest.raises(BenchmarkRevisionError, match="No alternate revision will be substituted"):
        run_one_task(root, "legal", "legal-hard-1", output, approve_genuine_run=True)
    assert not output.exists()


@pytest.mark.parametrize("evaluator_mode", ["official", COMPATIBILITY_EVALUATOR_MODE])
def test_genuine_run_is_blocked_by_default_before_preflight_or_output(monkeypatch, tmp_path, evaluator_mode):
    import bench.kramabench_runner as runner

    monkeypatch.setattr(runner, "verify_benchmark_checkout", lambda *_: pytest.fail("preflight must not start"))
    output = tmp_path / "results"
    with pytest.raises(OfficialHarnessError, match="pass --approve-genuine-run"):
        run_one_task(
            tmp_path / "unused", "environment", "environment-easy-1", output,
            evaluator_mode=evaluator_mode,
        )
    assert not output.exists()


def _mock_selected_task_path(monkeypatch, tmp_path):
    import bench.kramabench_runner as runner

    root = tmp_path / "pinned"
    (root / "benchmark" / "fixtures").mkdir(parents=True)
    (root / "evaluate.py").write_text("", encoding="utf-8")
    (root / "benchmark" / "fixtures" / "answer_type_fixtures.json").write_text(
        '[{"name":"numeric_exact","metrics":["success"]}]', encoding="utf-8"
    )
    task = PreflightTask(
        task_id="environment-easy-1",
        answer_type="numeric_exact",
        permitted_files=({"path": "water-body-testing-2013.csv", "sha256": "a" * 64, "format": "csv"},),
    )
    summary = PreflightSummary(
        workload_path=str(root / "workload" / "environment.json"),
        dataset_directory=str(root / "data"),
        task_count=1,
        requested_task_ids=(task.task_id,),
        tasks=(task,),
    )
    monkeypatch.setattr(runner, "verify_benchmark_checkout", lambda *_: (root, root / "evaluate.py"))
    monkeypatch.setattr(runner, "_evaluator_source_fingerprint", lambda *_: "b" * 64)
    monkeypatch.setattr(runner, "preflight_kramabench", lambda *args, **kwargs: summary)
    monkeypatch.setattr(runner, "_load_selected_records", lambda *_: [{"id": task.task_id}])
    monkeypatch.setattr(runner, "verify_evaluator_cli", lambda *args, **kwargs: None)
    return root


def test_missing_credentials_stop_before_run_directory_or_evaluator(monkeypatch, tmp_path):
    import bench.kramabench_runner as runner

    _mock_selected_task_path(monkeypatch, tmp_path)
    output = tmp_path / "results"
    evaluator_called = []

    def missing_key(*args, **kwargs):
        raise OfficialHarnessError("OPENAI_API_KEY is not configured")

    monkeypatch.setattr(runner, "verify_evaluator_environment", missing_key)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: evaluator_called.append(True))
    with pytest.raises(OfficialHarnessError, match="OPENAI_API_KEY"):
        run_one_task(
            tmp_path / "pinned", "environment", "environment-easy-1", output,
            approve_genuine_run=True,
        )
    assert not output.exists()
    assert evaluator_called == []


def test_missing_ollama_readiness_stops_before_run_directory_or_evaluator(monkeypatch, tmp_path):
    import bench.kramabench_runner as runner

    _mock_selected_task_path(monkeypatch, tmp_path)
    output = tmp_path / "results"
    evaluator_called = []
    monkeypatch.setattr(runner, "verify_evaluator_environment", lambda *_, **__: "/fake/evaluator-python")

    def not_ready(*args, **kwargs):
        raise OfficialHarnessError("Ollama readiness check failed")

    monkeypatch.setattr(runner, "verify_ollama_model", not_ready)
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: evaluator_called.append(True))
    with pytest.raises(OfficialHarnessError, match="Ollama readiness"):
        run_one_task(
            tmp_path / "pinned", "environment", "environment-easy-1", output,
            approve_genuine_run=True,
        )
    assert not output.exists()
    assert evaluator_called == []


def test_evaluator_fingerprint_rejects_local_compatibility_patch(tmp_path):
    root = tmp_path / "checkout"
    (root / "benchmark" / "fixtures").mkdir(parents=True)
    for relative in (
        "evaluate.py", "benchmark/benchmark_api.py", "benchmark/benchmark.py",
        "benchmark/executor.py", "benchmark/evaluator.py", "benchmark/metrics.py",
        "benchmark/fixtures/answer_type_fixtures.json",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original source\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(root), "-c", "user.name=Fixture", "-c",
        "user.email=fixture@example.invalid", "commit", "-qm", "fixture",
    ], check=True)
    (root / "benchmark" / "evaluator.py").write_text("compatibility patch\n", encoding="utf-8")
    with pytest.raises(BenchmarkRevisionError, match="source is modified at benchmark/evaluator.py"):
        _evaluator_source_fingerprint(root)


def test_ollama_readiness_uses_only_health_and_model_list_gets(monkeypatch):
    import bench.kramabench_runner as runner

    calls = []
    responses = [
        io.BytesIO(b'{"version":"0.34.4"}'),
        io.BytesIO(b'{"models":[{"name":"qwen3:4b-instruct-2507-q4_K_M"}]}'),
    ]

    def fake_urlopen(url, timeout):
        calls.append((url, timeout))
        return responses.pop(0)

    monkeypatch.setattr(runner.urllib.request, "urlopen", fake_urlopen)
    result = verify_ollama_model("qwen3:4b-instruct-2507-q4_K_M", "http://127.0.0.1:11434")
    assert result["version"] == "0.34.4"
    assert result["model"] == "qwen3:4b-instruct-2507-q4_K_M"
    assert [url.rsplit("/", 1)[-1] for url, _ in calls] == ["version", "tags"]


def test_ollama_readiness_rejects_missing_exact_model_without_pull(monkeypatch):
    import bench.kramabench_runner as runner

    responses = [io.BytesIO(b'{"version":"0.34.4"}'), io.BytesIO(b'{"models":[]}')]
    calls = []

    def fake_urlopen(url, timeout):
        calls.append(url)
        return responses.pop(0)

    monkeypatch.setattr(runner.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(OfficialHarnessError, match="is not listed"):
        verify_ollama_model("qwen3:4b-instruct-2507-q4_K_M")
    assert len(calls) == 2


def test_run_provenance_names_unmodified_evaluator_and_keeps_patch_distinct():
    summary = PreflightSummary(
        workload_path="workload.json", dataset_directory="data", task_count=1,
        requested_task_ids=("environment-easy-1",),
        tasks=(PreflightTask(
            task_id="environment-easy-1", answer_type="numeric_exact",
            permitted_files=({"path": "input.csv", "sha256": "c" * 64},),
        ),),
    )
    metadata = _run_provenance_metadata(
        summary, "environment-easy-1", "d" * 64, "qwen3:4b-instruct-2507-q4_K_M",
        6, 1, False, False, False,
        {"endpoint": "http://127.0.0.1:11434", "version": "0.34.4", "model": "qwen3:4b-instruct-2507-q4_K_M"},
    )
    assert metadata["run_type"] == "genuine_kramabench_task"
    assert metadata["evaluator_mode"] == "official_unmodified"
    assert metadata["evaluator_tree_fingerprint_sha256"] == "d" * 64
    assert metadata["compatibility_patch"] is None
    assert metadata["answer_type"] == "numeric_exact"
    assert metadata["data_file_hashes"] == [{"task_id": "environment-easy-1", "path": "input.csv", "sha256": "c" * 64}]
    assert metadata["prompt_version"]
    assert metadata["agent_config_sha256"]


def test_compatibility_provenance_is_distinct_from_unmodified_official_output():
    task_id = "environment-easy-1"
    summary = PreflightSummary(
        workload_path="workload.json", dataset_directory="data", task_count=1,
        requested_task_ids=(task_id,),
        tasks=(PreflightTask(
            task_id=task_id, answer_type="numeric_exact",
            permitted_files=({"path": "water-body-testing-2013.csv", "sha256": "c" * 64},),
        ),),
    )
    metadata = _run_provenance_metadata(
        summary, task_id, "d" * 64, "qwen3:4b-instruct-2507-q4_K_M",
        6, 1, False, False, False,
        {"endpoint": "http://127.0.0.1:11434", "version": "0.34.4", "model": "qwen3:4b-instruct-2507-q4_K_M"},
        evaluator_mode=COMPATIBILITY_EVALUATOR_MODE,
        compatibility_info={
            "revision": "b2e0d77540263f8b6119f977fe79a8c4386b5a02",
            "patch_sha256": COMPATIBILITY_PATCH_SHA256,
            "patched_evaluator_sha256": "e" * 64,
        },
        compatibility_allowlist_entry={
            "workload": "environment", "task_id": task_id,
            "input_sha256": {"water-body-testing-2013.csv": "c" * 64},
        },
    )
    assert metadata["evaluator_mode"] == COMPATIBILITY_EVALUATOR_MODE
    assert metadata["result_class"] == "compatibility_evaluator_output"
    assert metadata["compatibility_patch"] == {
        "path": "benchmark/evaluator.py", "sha256": COMPATIBILITY_PATCH_SHA256,
    }
    assert metadata["evaluator_source_sha256"] == "e" * 64
    assert metadata["openai_api_key_required"] is False
    assert len(metadata["compatibility_allowlist_entry_sha256"]) == 64


def test_compatibility_output_field_namespace_never_says_unmodified_official():
    assert _evaluator_result_namespace(COMPATIBILITY_EVALUATOR_MODE) == "compatibility_evaluator"
    assert _evaluator_result_namespace("official") == "official_evaluator"
    with pytest.raises(OfficialHarnessError, match="unsupported evaluator mode"):
        _evaluator_result_namespace("unknown")


def test_compatibility_checkout_isolated_and_rejects_unexpected_source_changes(tmp_path):
    source = Path("/private/tmp/krama-phase2_1-authoritative")
    if not authoritative_checkout_available(source):
        pytest.skip("authoritative pinned KramaBench checkout is unavailable")
    before = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"],
        check=True, capture_output=True, text=True,
    ).stdout
    copy = tmp_path / "compatibility-copy"
    info = _prepare_compatibility_checkout(source, copy)
    assert info["revision"] == "b2e0d77540263f8b6119f977fe79a8c4386b5a02"
    assert info["patch_sha256"] == COMPATIBILITY_PATCH_SHA256
    assert copy != source and copy.resolve() != source.resolve()

    (copy / "evaluate.py").write_text((copy / "evaluate.py").read_text() + "\n# unexpected\n")
    with pytest.raises(BenchmarkRevisionError, match="beyond the reviewed one-line patch"):
        _verify_compatibility_checkout(copy)
    subprocess.run(["git", "-C", str(copy), "checkout", "--", "evaluate.py"], check=True)
    subprocess.run([
        "git", "-C", str(copy), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--allow-empty", "-qm", "unexpected revision",
    ], check=True)
    with pytest.raises(BenchmarkRevisionError, match="compatibility evaluator revision is"):
        _verify_compatibility_checkout(copy)
    after = subprocess.run(
        ["git", "-C", str(source), "status", "--porcelain", "--untracked-files=all"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert after == before


def _compatibility_validation_fixture(tmp_path, *, task_id="fixture-easy-1",
                                      answer_type="numeric_exact", metrics=None,
                                      input_content=b"value\n1\n"):
    root = tmp_path / "pinned-fixture"
    workload_dir = root / "workload"
    dataset_dir = root / "data" / "fixture" / "input"
    fixtures_dir = root / "benchmark" / "fixtures"
    workload_dir.mkdir(parents=True)
    dataset_dir.mkdir(parents=True)
    fixtures_dir.mkdir(parents=True)
    input_path = dataset_dir / "input.csv"
    input_path.write_bytes(input_content)
    workload_file = workload_dir / "fixture.json"
    record = {"id": task_id, "answer_type": answer_type, "data_sources": ["input.csv"]}
    workload_file.write_text(json.dumps([record]), encoding="utf-8")
    metrics = metrics or (["success"] if answer_type == "numeric_exact" else [])
    (fixtures_dir / "answer_type_fixtures.json").write_text(
        json.dumps([{"name": answer_type, "metrics": metrics}]), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run([
        "git", "-C", str(root), "-c", "user.name=Fixture", "-c",
        "user.email=fixture@example.invalid", "commit", "-qm", "fixture",
    ], check=True)
    workload_blob = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD:workload/fixture.json"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    allowlist_path = tmp_path / "compat_allowlist.json"
    entry = {
        "workload": "fixture", "task_id": task_id,
        "workload_git_blob": workload_blob,
        "input_sha256": {"input.csv": hashlib.sha256(input_content).hexdigest()},
        "answer_type": answer_type, "metric_names": metrics,
        "evaluator_provenance": {
            "revision": "b2e0d77540263f8b6119f977fe79a8c4386b5a02",
            "patch_path": "benchmark/evaluator.py",
            "patch_sha256": COMPATIBILITY_PATCH_SHA256,
        },
    }
    allowlist_path.write_text(json.dumps({"schema_version": 1, "entries": [entry]}), encoding="utf-8")
    task = PreflightTask(
        task_id=task_id, answer_type=answer_type,
        permitted_files=({"path": "input.csv", "sha256": hashlib.sha256(input_content).hexdigest()},),
    )
    summary = PreflightSummary(
        workload_path=str(workload_file), dataset_directory=str(dataset_dir), task_count=1,
        requested_task_ids=(task_id,), tasks=(task,),
    )
    return root, dataset_dir, allowlist_path, entry, summary, record


def _validate_fixture(root, dataset_dir, allowlist_path, summary, record, *, workload="fixture", task_id=None,
                      run_subtasks=False, enable_pipeline_evaluation=False):
    task_id = task_id or summary.tasks[0].task_id
    return _validate_compatibility_task(
        root, workload, task_id, dataset_dir,
        "qwen3:4b-instruct-2507-q4_K_M", "http://127.0.0.1:11434",
        run_subtasks, enable_pipeline_evaluation, summary, record, allowlist_path,
    )


def test_compatibility_allowlist_accepts_reviewed_task_and_pipeline_stays_disabled(tmp_path):
    root, dataset_dir, allowlist, _, summary, record = _compatibility_validation_fixture(tmp_path)
    _validate_fixture(root, dataset_dir, allowlist, summary, record)
    with pytest.raises(OfficialHarnessError, match="pipeline evaluation and subtasks"):
        _validate_fixture(root, dataset_dir, allowlist, summary, record, enable_pipeline_evaluation=True)


def test_compatibility_allowlist_accepts_qwen3_8b_override(tmp_path):
    root, dataset_dir, allowlist, _, summary, record = _compatibility_validation_fixture(tmp_path)
    _validate_compatibility_task(
        root, "fixture", record["id"], dataset_dir, "qwen3:8b", "http://127.0.0.1:11434",
        False, False, summary, record, allowlist,
    )


def test_compatibility_allowlist_accepts_another_audited_deterministic_mapping(tmp_path):
    root, dataset_dir, allowlist, _, summary, record = _compatibility_validation_fixture(
        tmp_path,
        answer_type="numeric_approximate",
        metrics=["mean_absolute_error", "mean_squared_error", "rae_score"],
    )
    _validate_fixture(root, dataset_dir, allowlist, summary, record)


def test_compatibility_allowlist_rejects_unlisted_task(tmp_path):
    root, dataset_dir, allowlist, _, summary, record = _compatibility_validation_fixture(tmp_path)
    with pytest.raises(WorkloadPreflightError, match="not explicitly allowlisted"):
        _validate_fixture(root, dataset_dir, allowlist, summary, record, task_id="unlisted")


def test_compatibility_allowlist_rejects_input_hash_mismatch(tmp_path):
    root, dataset_dir, allowlist, entry, summary, record = _compatibility_validation_fixture(tmp_path)
    entry["input_sha256"]["input.csv"] = "0" * 64
    allowlist.write_text(json.dumps({"schema_version": 1, "entries": [entry]}), encoding="utf-8")
    with pytest.raises(WorkloadPreflightError, match="SHA-256 hashes differ"):
        _validate_fixture(root, dataset_dir, allowlist, summary, record)


def test_compatibility_allowlist_rejects_nondeterministic_answer_metric(tmp_path):
    root, dataset_dir, allowlist, entry, summary, record = _compatibility_validation_fixture(
        tmp_path, answer_type="string_approximate", metrics=["llm_paraphrase"],
    )
    with pytest.raises(WorkloadPreflightError, match="non-deterministic or unaudited"):
        _validate_fixture(root, dataset_dir, allowlist, summary, record)


def test_compatibility_allowlist_rejects_deterministic_metric_with_eval_fallback(tmp_path):
    root, dataset_dir, allowlist, _, summary, record = _compatibility_validation_fixture(
        tmp_path, answer_type="list_exact", metrics=["f1", "precision", "recall"],
    )
    with pytest.raises(WorkloadPreflightError, match="may execute model-controlled code"):
        _validate_fixture(root, dataset_dir, allowlist, summary, record)


def test_no_key_evaluator_environment_probe_drops_credentials_and_checks_dependencies(monkeypatch):
    import bench.kramabench_runner as runner

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(runner, "_python_command", lambda _: "isolated-python")
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        captured["probe"] = args[-1]
        return subprocess.CompletedProcess(args, 0, "READY\n", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert verify_evaluator_environment("isolated-python", require_openai_key=False) == "isolated-python"
    assert "OPENAI_API_KEY" not in captured["env"]
    assert "OPENAI_API_KEY" not in captured["probe"]


def test_compatibility_task_preflight_matches_reviewed_pinned_inputs():
    from bench.kramabench_preflight import preflight_kramabench
    from bench.kramabench_runner import _load_selected_records

    root = Path("/private/tmp/krama-phase2_1-authoritative")
    if not authoritative_checkout_available(root):
        pytest.skip("authoritative pinned KramaBench checkout is unavailable")
    workload_path = root / "workload" / "environment.json"
    dataset = root / "data" / "environment" / "input"
    fixtures = root / "benchmark" / "fixtures"
    task_id = "environment-easy-1"
    summary = preflight_kramabench(workload_path, dataset, fixtures, task_ids=[task_id])
    record = _load_selected_records(workload_path, [task_id])[0]
    _validate_compatibility_task(
        root, "environment", task_id, None,
        "qwen3:4b-instruct-2507-q4_K_M", "http://127.0.0.1:11434", False, False,
        summary, record,
    )


def test_isolated_evaluator_bootstrap_requires_explicit_approval(monkeypatch):
    import bench.kramabench_subprocess as bootstrap

    monkeypatch.delenv("KRAMABENCH_GENUINE_RUN_APPROVED", raising=False)
    with pytest.raises(RuntimeError, match="explicit genuine-run approval"):
        bootstrap.main()


def test_revision_verification_rejects_non_repository_path(tmp_path):
    root = tmp_path / "not-a-repo"
    (root / "benchmark" / "fixtures").mkdir(parents=True)
    (root / "evaluate.py").write_text("", encoding="utf-8")
    for relative in (
        "benchmark/benchmark_api.py", "benchmark/benchmark.py",
        "benchmark/executor.py", "benchmark/evaluator.py",
        "benchmark/fixtures/answer_type_fixtures.json",
    ):
        (root / relative).write_text("{}", encoding="utf-8")
    with pytest.raises(BenchmarkRevisionError, match="cannot read the KramaBench checkout revision"):
        verify_benchmark_checkout(root)


def test_cli_preflight_uses_the_evaluators_actual_help_options(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    evaluate = root / "evaluate.py"
    evaluate.write_text(
        "import argparse\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--sut')\n"
        "parser.add_argument('--workload')\n"
        "parser.parse_args()\n",
        encoding="utf-8",
    )

    verify_evaluator_cli(evaluate, root, ["--sut", "system", "--workload", "legal"])
    with pytest.raises(OfficialHarnessError, match="does not advertise required options"):
        verify_evaluator_cli(evaluate, root, ["--pipeline_eval"])


def test_pipeline_disabled_still_rejects_answer_type_that_calls_llm_judge(tmp_path):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "answer_type_fixtures.json").write_text(
        '[{"name":"string_approximate","metrics":["llm_paraphrase"]}]', encoding="utf-8"
    )
    with pytest.raises(OfficialHarnessError, match="invokes an LLM evaluator metric"):
        _require_no_answer_judge_when_pipeline_disabled(fixtures, "string_approximate", False)


def test_evaluator_environment_gate_reports_missing_key_without_logging_value(monkeypatch):
    import subprocess

    import bench.kramabench_runner as runner

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(runner, "_python_command", lambda _: "python")
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 3, "", ""),
    )
    with pytest.raises(OfficialHarnessError, match="OPENAI_API_KEY is not configured") as error:
        verify_evaluator_environment("isolated-python")
    assert "sk-" not in str(error.value)
