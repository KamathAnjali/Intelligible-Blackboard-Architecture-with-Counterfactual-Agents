"""Offline checks for the two Colab model configurations."""

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from agents.llm_client import BASE_URL, OllamaClient
from bench.kramabench_preflight import preflight_kramabench
from bench.kramabench_runner import (
    _load_selected_records,
    _validate_compatibility_task,
    verify_benchmark_checkout,
)
from bench.orchestrator import DEFAULT_SCOPE, select_split_tasks
from bench.scope.load_scope import load_scope


MODELS = ("qwen3:8b", "qwen3:14b-q4_K_M")


@pytest.mark.parametrize("model", MODELS)
def test_colab_model_sends_structured_generation_without_thinking(model):
    client = OllamaClient(model=model)
    client.request = Mock(return_value={"response": '{"action":"done"}', "context": [1, 2]})
    schema = {"type": "object", "properties": {"action": {"type": "string"}}}

    result = client.generate("Select a data operation.", schema=schema)

    route, payload = client.request.call_args.args
    assert route == "/api/generate"
    assert payload["model"] == model
    assert payload["format"] == schema
    assert payload["think"] is False
    assert payload["options"]["num_ctx"] == 16384
    assert "context" not in result


def test_colab_tuning_tasks_pass_compatibility_preflight_for_both_models():
    checkout = os.environ.get("KRAMABENCH_ROOT")
    if not checkout:
        pytest.skip("Set KRAMABENCH_ROOT to check the pinned dataset")
    root = Path(checkout)
    verify_benchmark_checkout(root)
    tasks = select_split_tasks(load_scope(DEFAULT_SCOPE), "tuning", count=5)

    for task in tasks:
        workload, task_id = task["workload"], task["task_id"]
        workload_path = root / "workload" / f"{workload}.json"
        summary = preflight_kramabench(
            workload_path, root / "data" / workload / "input",
            root / "benchmark" / "fixtures", task_ids=[task_id],
        )
        [reference] = _load_selected_records(workload_path, [task_id])
        for model in MODELS:
            entry = _validate_compatibility_task(
                root, workload, task_id, None, model, BASE_URL,
                False, False, summary, reference,
            )
            assert entry["task_id"] == task_id
