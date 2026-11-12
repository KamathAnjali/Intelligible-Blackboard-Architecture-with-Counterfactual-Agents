"""Scope tests use SYNTHETIC candidate metadata and never create benchmark scores."""

import copy
import json
from pathlib import Path

import pytest

from bench.scope.build_scope import select_task_splits, write_outputs
from bench.scope.load_scope import (
    ScopeError,
    counterfactual_selection_view,
    load_scope,
    result_reporting_labels,
    task_scope_view,
)


def _candidate(workload, task_id, evaluator_mode, *, prior_exposure=False):
    return {
        "workload": workload,
        "task_id": task_id,
        "eligible": True,
        "evaluator_mode": evaluator_mode,
        "prior_exposure": prior_exposure,
        "historical_outcomes": [],
        "dataset_domain": "SYNTHETIC",
        "difficulty": "synthetic",
        "answer_type": "synthetic_fixture_only",
        "metric_names": ["synthetic_metric"],
        "pass_rule": {"kind": "all_metrics_equal", "value": 1.0},
        "adapted": evaluator_mode != "official",
        "adaptation_flags": [],
        "data_available": True,
        "input_files": [{"path": "SYNTHETIC.csv", "sha256": "0" * 64}],
        "independent_answer_check": True,
        "excluded_reason": "",
    }


def test_seeded_scope_selection_and_output_are_reproducible(tmp_path):
    candidates = [
        *[_candidate("compat", f"SYNTHETIC-tuning-{i}", "compatibility-no-key") for i in range(5)],
        *[_candidate("official", f"SYNTHETIC-heldout-{i}", "official") for i in range(25)],
    ]
    tuning, held_out = select_task_splits(candidates, seed=731, tuning_count=5, held_out_count=25)
    first = select_task_splits(candidates, seed=731, tuning_count=5, held_out_count=25)
    second = select_task_splits(copy.deepcopy(candidates), seed=731, tuning_count=5, held_out_count=25)
    assert first == second
    assert not ({x["task_id"] for x in first[0]} & {x["task_id"] for x in first[1]})

    sample_scope = {
        "schema_version": 1,
        "tasks": [{"split": "tuning", **task} for task in tuning]
        + [{"split": "held_out_pilot", **task} for task in held_out],
    }
    output_a, output_b = tmp_path / "a", tmp_path / "b"
    write_outputs(sample_scope, candidates, output_a)
    write_outputs(sample_scope, candidates, output_b)
    assert (output_a / "candidate_inventory.csv").read_bytes() == (output_b / "candidate_inventory.csv").read_bytes()
    assert (output_a / "pilot_scope_v1.json").read_bytes() == (output_b / "pilot_scope_v1.json").read_bytes()


def test_frozen_scope_hash_and_agent_views_exclude_reference_fields():
    path = Path(__file__).parents[1] / "bench" / "scope" / "pilot_scope_v1.json"
    scope = load_scope(path)
    tasks = scope["tasks"]
    frozen_text = path.read_text(encoding="utf-8")
    assert '"answer"' not in frozen_text
    assert '"reference_answer"' not in frozen_text
    assert '"query"' not in frozen_text
    assert len([task for task in tasks if task["split"] == "tuning"]) == 5
    assert len([task for task in tasks if task["split"] == "held_out_pilot"]) == 25

    contaminated = copy.deepcopy(scope)
    contaminated["tasks"][0].update({"answer": "SYNTHETIC-GOLD", "reference_answer": "SYNTHETIC-REF", "query": "SYNTHETIC-QUERY"})
    safe_task = task_scope_view(contaminated, tasks[0]["workload"], tasks[0]["task_id"])
    safe_counterfactual = counterfactual_selection_view(contaminated)
    rendered = json.dumps([safe_task, safe_counterfactual])
    assert "SYNTHETIC-GOLD" not in rendered
    assert "SYNTHETIC-REF" not in rendered
    assert "SYNTHETIC-QUERY" not in rendered


def test_loader_rejects_modified_frozen_scope(tmp_path):
    path = Path(__file__).parents[1] / "bench" / "scope" / "pilot_scope_v1.json"
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["model"]["name"] = "tampered"
    changed_path = tmp_path / "scope.json"
    changed_path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ScopeError, match="hash mismatch"):
        load_scope(changed_path)


def test_reporting_labels_separate_standard_and_adapted_results():
    assert result_reporting_labels("official", False) == {
        "evaluator_mode": "official", "adapted": False, "result_partition": "standard"
    }
    assert result_reporting_labels("compatibility-no-key", False)["result_partition"] == "adapted"
    assert result_reporting_labels("official", True)["result_partition"] == "adapted"
