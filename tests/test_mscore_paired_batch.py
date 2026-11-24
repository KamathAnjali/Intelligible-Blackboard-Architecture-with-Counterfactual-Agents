import csv
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench import mscore_paired_batch as batch


def _write_fake_attempt(output_dir, task_ids, **kwargs):
    task_id = task_ids[0]
    output_dir.mkdir(parents=True)
    with (output_dir / "results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["task_id", "outcome", "evaluation_status",
                                               "scored", "correct", "total_tokens", "result_label"])
        writer.writeheader()
        writer.writerow({"task_id": task_id, "outcome": "scheduler_consensus",
                         "evaluation_status": "scored", "scored": True,
                         "correct": True, "total_tokens": 10,
                         "result_label": "SYNTHETIC_TEST_FIXTURE"})
    return output_dir


def _setup(monkeypatch, tmp_path):
    ids = [f"task-{i:03d}" for i in range(100)]
    ids_file = tmp_path / "ids.txt"
    ids_file.write_text("\n".join(ids) + "\n")
    source = tmp_path / "data.json"
    source.write_text("fixture data")
    ids_hash = hashlib.sha256(ids_file.read_bytes()).hexdigest()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(batch, "_frozen_cohort", lambda: {
        "task_id_file_sha256": ids_hash, "source_sha256": source_hash,
    })
    monkeypatch.setattr(batch, "load_mscore_file", lambda _: [
        SimpleNamespace(agent_task=SimpleNamespace(task_id=task_id)) for task_id in ids
    ])
    return ids, ids_file, source


def test_paired_batch_checkpoints_and_resumes_without_overwriting(monkeypatch, tmp_path):
    ids, ids_file, source = _setup(monkeypatch, tmp_path)
    output = tmp_path / "out"
    calls = []

    def interrupt_after_five(**kwargs):
        calls.append(kwargs["task_ids"][0])
        _write_fake_attempt(**kwargs)
        if len(calls) == 5:
            raise KeyboardInterrupt

    monkeypatch.setattr(batch, "run_pilot", interrupt_after_five)
    args = dict(input_path=source, task_id_file=ids_file, output_dir=output,
                model="fixture", base_url="http://fixture", model_seed=42,
                counterfactual_seed=1, num_predict=4096, turns=6, retries=1,
                order_seed=17, approve_genuine_run=True)
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(**args)
    first_rows = batch._read_rows(output / "results.csv")
    assert len(first_rows) == 4  # the fifth runner call was interrupted before its checkpoint
    assert all(row["attempt_status"] == "completed" for row in first_rows)

    monkeypatch.setattr(batch, "run_pilot", _write_fake_attempt)
    batch.run_batch(**args)
    rows = batch._read_rows(output / "results.csv")
    assert len(rows) == 200
    assert len({(r["task_id"], r["condition"]) for r in rows}) == 200
    assert sum(int(r["attempt"]) == 2 for r in rows) == 1
    assert {r["condition"] for r in rows} == {"cf00", "cf100"}
    assert sum(r["condition"] == "cf100" and r["retrospective_recovery"] == "True"
               for r in rows) == 100
    # A third invocation finds all pairs completed and does not call the runner.
    monkeypatch.setattr(batch, "run_pilot", lambda **_: pytest.fail("completed pair reran"))
    batch.run_batch(**args)


def test_real_batch_requires_explicit_approval(tmp_path):
    with pytest.raises(ValueError, match="approve-genuine-run"):
        batch.run_batch(input_path=tmp_path / "missing", task_id_file=tmp_path / "ids",
                        output_dir=tmp_path / "out", model="m", base_url="url",
                        model_seed=1, counterfactual_seed=1, num_predict=10,
                        turns=1, retries=0, order_seed=1, approve_genuine_run=False)


def test_fixed_cohort_rejects_modified_task_ids(monkeypatch, tmp_path):
    _, ids_file, source = _setup(monkeypatch, tmp_path)
    ids_file.write_text(ids_file.read_text() + "extra\n")
    with pytest.raises(ValueError, match="cohort mismatch"):
        batch.run_batch(input_path=source, task_id_file=ids_file,
                        output_dir=tmp_path / "out", model="m", base_url="url",
                        model_seed=1, counterfactual_seed=1, num_predict=10,
                        turns=1, retries=0, order_seed=1, approve_genuine_run=True)
