import json

import pytest

from bench.ingest.mscore_parser import load_mscore_file, normalize_judgment, score_exact


def test_loader_separates_reference_from_agent_view_and_detects_judgment(tmp_path):
    source = tmp_path / "MSCoRe-data" / "Data2" / "Construction" / "simple" / "data.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps([
        {"question": "施工单位可以在竣工后修正缺陷吗？", "answer": "否"},
    ], ensure_ascii=False), encoding="utf-8")

    tasks = load_mscore_file(source)
    task = tasks[0]

    assert task.agent_task.task_type == "judgment"
    assert task.agent_task.domain == "Construction"
    assert "answer" not in task.agent_task.model_dump()
    assert "否" not in task.agent_task.model_dump_json(ensure_ascii=False)
    assert task.evaluator_reference.answer == "否"
    assert tasks.report.records_seen == tasks.report.records_loaded == 1
    assert len(tasks.report.source_sha256) == 64


def test_loader_supports_instruction_input_output_and_is_path_reproducible(tmp_path):
    root = tmp_path / "MSCoRe-data"
    source = root / "Software_Engineer" / "Software_Engineering.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps([
        {"instruction": "Order these steps", "input": "A then B", "output": "A-B"},
    ]), encoding="utf-8")

    loaded_a = load_mscore_file(source)
    loaded_b = load_mscore_file(source)

    assert loaded_a[0].agent_task.task_type == "ordering"
    assert loaded_a[0].agent_task.prompt == "Order these steps\n\nA then B"
    assert loaded_a[0].agent_task.task_id == loaded_b[0].agent_task.task_id
    assert "A-B" not in loaded_a[0].agent_task.model_dump_json()


def test_loader_rejects_unhydrated_git_lfs_pointer(tmp_path):
    source = tmp_path / "data"
    source.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 10\n")
    with pytest.raises(ValueError, match="Git LFS pointer"):
        load_mscore_file(source)


@pytest.mark.parametrize(("raw", "normalized"), [
    ("是", "yes"), ("正确", "yes"), ("合理", "yes"), ("yes", "yes"),
    ("否", "no"), ("错误", "no"), ("不合理", "no"), ("false", "no"), ("not sure", None),
])
def test_judgment_normalization_is_conservative(raw, normalized):
    assert normalize_judgment(raw) == normalized


def test_objective_scorer_does_not_claim_open_ended_answer_correctness(tmp_path):
    source = tmp_path / "qa.json"
    source.write_text(json.dumps([{"question": "Explain the process", "answer": "A long answer"}]))
    task = load_mscore_file(source)[0]
    result = score_exact(task, "A plausible answer")
    assert result["scored"] is False
    assert result["score"] is None
    assert result["reason"] == "open_ended_requires_a_configured_semantic_evaluator"


def test_objective_judgment_scoring_uses_only_separated_reference(tmp_path):
    source = tmp_path / "Data2" / "Construction" / "simple" / "data.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps([{"question": "A claim?", "answer": "否"}], ensure_ascii=False))
    task = load_mscore_file(source)[0]
    assert score_exact(task, "错误")["score"] == 1.0
    assert score_exact(task, "是")["score"] == 0.0


def test_unrecognized_judgment_phrase_is_unscored_instead_of_counted_wrong(tmp_path):
    source = tmp_path / "Data2" / "Construction" / "simple" / "data.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps([{"question": "A claim?", "answer": "是"}], ensure_ascii=False))
    task = load_mscore_file(source)[0]
    result = score_exact(task, "maybe")
    assert result["scored"] is False
    assert result["score"] is None
