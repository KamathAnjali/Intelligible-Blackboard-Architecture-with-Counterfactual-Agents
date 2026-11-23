"""Workload and dataset preflight tests using a synthetic local fixture only."""

import json

import pytest

from bench.kramabench_preflight import WorkloadPreflightError, preflight_kramabench


def make_inputs(tmp_path, tasks=None):
    root = tmp_path / "benchmark"
    fixtures = root / "benchmark" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "answer_type_fixtures.json").write_text(
        json.dumps([{"name": "numeric_exact", "metrics": ["success"]}]), encoding="utf-8"
    )
    data = root / "data"
    data.mkdir()
    (data / "allowed.csv").write_text("count\n7\n", encoding="utf-8")
    workload = root / "workload.json"
    if tasks is None:
        tasks = [{
            "id": "synthetic-1",
            "query": "Read allowed.csv.",
            "answer_type": "numeric_exact",
            "answer": "EVALUATOR_ONLY_SECRET",
            "data_sources": ["allowed.csv"],
            "subtasks": [{
                "id": "synthetic-1-subtask-1",
                "query": "Inspect the count.",
                "answer_type": "numeric_exact",
                "answer": "REFERENCE_SUBTASK_SECRET",
                "data_sources": ["allowed.csv"],
            }],
        }]
    workload.write_text(json.dumps(tasks), encoding="utf-8")
    return workload, data, fixtures


def test_preflight_validates_one_task_without_returning_reference_fields(tmp_path):
    workload, data, fixtures = make_inputs(tmp_path)
    summary = preflight_kramabench(
        workload, data, fixtures, task_ids=["synthetic-1"]
    )
    serialized = json.dumps(summary.to_jsonable())
    assert summary.task_count == 1
    assert summary.requested_task_ids == ("synthetic-1",)
    assert "EVALUATOR_ONLY_SECRET" not in serialized
    assert "REFERENCE_SUBTASK_SECRET" not in serialized
    assert all("answer" not in task_record for task_record in summary.to_jsonable()["tasks"])
    assert summary.tasks[0].permitted_files[0]["path"] == "allowed.csv"


def test_empty_workload_is_rejected(tmp_path):
    workload, data, fixtures = make_inputs(tmp_path, tasks=[])
    with pytest.raises(WorkloadPreflightError, match="at least one task"):
        preflight_kramabench(workload, data, fixtures)


def test_malformed_workload_is_rejected(tmp_path):
    workload, data, fixtures = make_inputs(tmp_path)
    workload.write_text("{broken", encoding="utf-8")
    with pytest.raises(WorkloadPreflightError, match="malformed"):
        preflight_kramabench(workload, data, fixtures)


def test_unknown_requested_task_id_is_rejected(tmp_path):
    workload, data, fixtures = make_inputs(tmp_path)
    with pytest.raises(WorkloadPreflightError, match="absent from workload"):
        preflight_kramabench(workload, data, fixtures, task_ids=["unknown"])


def test_missing_data_source_is_rejected_without_exposing_gold(tmp_path):
    task = {
        "id": "missing-source",
        "query": "Read the required file.",
        "answer_type": "numeric_exact",
        "answer": "EVALUATOR_ONLY_SECRET",
        "data_sources": ["not-present.csv"],
    }
    workload, data, fixtures = make_inputs(tmp_path, tasks=[task])
    with pytest.raises(WorkloadPreflightError, match="data preflight failed") as error:
        preflight_kramabench(workload, data, fixtures)
    assert "EVALUATOR_ONLY_SECRET" not in str(error.value)


def test_unsupported_answer_type_is_rejected(tmp_path):
    task = {
        "id": "unsupported-type",
        "query": "Read the required file.",
        "answer_type": "unregistered_metric",
        "answer": "EVALUATOR_ONLY_SECRET",
        "data_sources": ["allowed.csv"],
    }
    workload, data, fixtures = make_inputs(tmp_path, tasks=[task])
    with pytest.raises(WorkloadPreflightError, match="unsupported answer_type"):
        preflight_kramabench(workload, data, fixtures)


def test_preflight_can_validate_subtask_sources_when_requested(tmp_path):
    workload, data, fixtures = make_inputs(tmp_path)
    summary = preflight_kramabench(
        workload, data, fixtures, task_ids=["synthetic-1"], include_subtasks=True
    )
    assert summary.task_count == 2
    assert [item.task_id for item in summary.tasks] == [
        "synthetic-1", "synthetic-1-subtask-1"
    ]
