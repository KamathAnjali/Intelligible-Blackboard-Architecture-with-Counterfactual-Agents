import json

import pytest

from bench import mscore_runner


def test_runner_keeps_gold_out_of_model_context_and_writes_result_row(tmp_path, monkeypatch):
    source = tmp_path / "Data2" / "Construction" / "simple" / "data.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps([
        {"question": "A fictional claim?", "answer": "否"},
    ], ensure_ascii=False), encoding="utf-8")
    calls = []

    def fake_conversation(client, **kwargs):
        calls.append(kwargs)
        report_path = kwargs["output_dir"] / kwargs["task_id"] / "transcript.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text("{}", encoding="utf-8")
        return ({
            "outcome": "scheduler_consensus",
            "board": {"entries": [
                {"agent_id": "proposer", "prediction": "错误", "is_counterfactual_sim": False},
            ]},
            "turns": [],
        }, report_path)

    monkeypatch.setattr(mscore_runner, "run_conversation", fake_conversation)
    out = tmp_path / "results"
    mscore_runner.run_pilot(
        input_path=source, output_dir=out, limit=1, selection_seed=5, model_seed=42,
        model="fixture-model", base_url="http://localhost", turns=2, retries=0,
        counterfactual_fraction=0.0, counterfactual_seed=42,
        approve_genuine_run=True,
    )

    assert len(calls) == 1
    prompt_context = json.dumps({"task": calls[0]["task"], "review": calls[0]["review_evidence"]}, ensure_ascii=False)
    assert "A fictional claim?" in prompt_context
    assert calls[0]["review_evidence"] is None
    assert "否" not in prompt_context
    result = next((out / "records").glob("*.json"))
    row = json.loads(result.read_text())
    assert row["score"] == 1.0 and row["scored"] is True
    assert row["outcome"] == "scheduler_consensus"
    assert row["evaluation_status"] == "scored"
    assert "否" not in result.read_text()
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["reference_answers_in_agent_context"] is False
    assert manifest["review_mode"] == "task_facts"
    assert manifest["counts"] == {
        "attempted": 1, "completed_without_crash": 1, "scored": 1,
        "correct": 1, "failed_before_scoring": 0,
    }


def test_runner_requires_approval_and_refuses_overwrite(tmp_path):
    source = tmp_path / "data.json"
    source.write_text(json.dumps([{"question": "Q", "answer": "A"}]))
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ValueError, match="approve-genuine-run"):
        mscore_runner.run_pilot(
            input_path=source, output_dir=tmp_path / "new", limit=1,
            selection_seed=1, model_seed=42,
            model="m", base_url="http://localhost", turns=1, retries=0,
            counterfactual_fraction=0.0, counterfactual_seed=42,
            approve_genuine_run=False,
        )
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        mscore_runner.run_pilot(
            input_path=source, output_dir=out, limit=1,
            selection_seed=1, model_seed=42,
            model="m", base_url="http://localhost", turns=1, retries=0,
            counterfactual_fraction=0.0, counterfactual_seed=42,
            approve_genuine_run=True,
        )


def test_runner_can_rerun_explicit_tasks_with_larger_output_cap(tmp_path, monkeypatch):
    source = tmp_path / "Data2" / "Construction" / "simple" / "data.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps([
        {"question": "First claim?", "answer": "是"},
        {"question": "Second claim?", "answer": "否"},
    ], ensure_ascii=False), encoding="utf-8")
    seen = []

    def fake_conversation(client, **kwargs):
        seen.append((client.options["num_predict"], kwargs["task_id"]))
        report_path = kwargs["output_dir"] / kwargs["task_id"] / "transcript.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text("{}", encoding="utf-8")
        return ({"outcome": "turn_limit", "turns": [], "board": {"entries": []}}, report_path)

    monkeypatch.setattr(mscore_runner, "run_conversation", fake_conversation)
    out = tmp_path / "explicit-rerun"
    selected_id = mscore_runner.load_mscore_file(source)[1].agent_task.task_id
    mscore_runner.run_pilot(
        input_path=source, output_dir=out, limit=1, selection_seed=99, model_seed=42,
        model="fixture", base_url="http://localhost", turns=4, retries=1,
        counterfactual_fraction=1.0, counterfactual_seed=7,
        approve_genuine_run=True, task_ids=[selected_id], num_predict=2048,
    )
    assert seen == [(2048, selected_id)]
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["selected_tasks"] == [selected_id]
    assert manifest["selection_mode"] == "explicit_task_ids"
    assert manifest["model_options"]["num_predict"] == 2048


def test_usage_sums_proposals_self_checks_and_simulations():
    attempts = [
        {"raw": {"prompt_eval_count": 10, "eval_count": 3},
         "self_check": {"raw": {"prompt_eval_count": 7, "eval_count": 2}}},
        {"raw": {"prompt_eval_count": 5, "eval_count": 4}},
    ]
    assert mscore_runner._usage_for_attempts(attempts) == (22, 9)


def test_deadlock_row_preserves_failure_and_counterfactual_counters(tmp_path, monkeypatch):
    source = tmp_path / "Data2" / "Construction" / "simple" / "data.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps([{"question": "Claim?", "answer": "是"}], ensure_ascii=False))

    def fake_conversation(client, **kwargs):
        report_path = kwargs["output_dir"] / kwargs["task_id"] / "transcript.json"
        report_path.parent.mkdir(parents=True)
        report_path.write_text("{}", encoding="utf-8")
        return ({
            "outcome": "deadlocked", "scheduler_status": "DEADLOCKED",
            "turns": [{"turn": 1, "attempts": []}],
            "board": {"entries": [], "deadlocks": [{}]},
            "failures": [{"category": "reviewer_rejection", "reason": "unsupported claim"}],
            "workflow_counters": {"reviewer_rejections": 2, "counterfactual_triggers": 1,
                                  "counterfactual_simulations": 1, "counterfactual_changed_outcome": 0},
            "counterfactual_capable_agents": ["proposer"],
            "counterfactual_attempts": [{"attempts": []}],
        }, report_path)

    monkeypatch.setattr(mscore_runner, "run_conversation", fake_conversation)
    out = tmp_path / "deadlock-results"
    mscore_runner.run_pilot(
        input_path=source, output_dir=out, limit=1, selection_seed=1, model_seed=42,
        model="fixture", base_url="http://localhost", turns=3, retries=0,
        counterfactual_fraction=1.0, counterfactual_seed=1,
        approve_genuine_run=True,
    )
    row = json.loads(next((out / "records").glob("*.json")).read_text())
    assert row["outcome"] == "deadlocked"
    assert row["evaluation_status"] == "failed_before_scoring"
    assert row["reviewer_rejections"] == 2
    assert row["counterfactual_simulations"] == 1
    assert "unsupported claim" in row["failure_reason"]
