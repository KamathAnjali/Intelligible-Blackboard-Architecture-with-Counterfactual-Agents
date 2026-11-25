"""The unified task catalog exposes saved run metadata without reference answers."""

import csv
import json

from fastapi.testclient import TestClient

from ui.server import main


def test_catalog_lists_mscore_attempts_for_task_specific_replay_without_gold(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_mscore_results_root", lambda: tmp_path)
    monkeypatch.setattr(main, "_mscore_direct_results_root", lambda: tmp_path / "no-direct-results")
    (tmp_path / "results_manifest.json").write_text(json.dumps({
        "benchmark": "MSCoRe", "model": "SYNTHETIC-model", "result_label": "adapted",
    }), encoding="utf-8")
    fields = [
        "task_id", "condition", "attempt_status", "outcome", "evaluation_status", "scored",
        "score", "prediction", "evaluator_mode", "adapted", "failure_reason",
        "counterfactual_fraction", "counterfactual_simulations", "attempt_dir", "transcript",
        "total_tokens", "elapsed_seconds", "iterations", "deadlock_count",
    ]
    rows = []
    for condition, score, prediction in (("cf00", "1.0", "是"), ("cf100", "0.0", "否")):
        attempt = tmp_path / "attempts" / condition / "SYNTHETIC-task" / "attempt-001"
        session = attempt / "sessions" / "SYNTHETIC-task"
        session.mkdir(parents=True)
        (session / "transcript.json").write_text(json.dumps({
            "task": "SYNTHETIC agent-facing task",
            "gold_answer": "SYNTHETIC_SECRET_GOLD",
            "board": {"entries": [{"entry_id": f"entry-{condition}", "agent_id": "proposer", "tag": "RATIFY"}]},
        }), encoding="utf-8")
        rows.append({
            "task_id": "SYNTHETIC-task", "condition": condition, "attempt_status": "completed",
            "outcome": "scheduler_consensus", "evaluation_status": "scored", "scored": "true",
            "score": score, "prediction": prediction, "evaluator_mode": "project-adapted-exact",
            "adapted": "true", "failure_reason": "", "counterfactual_fraction": "1.0" if condition == "cf100" else "0.0",
            "counterfactual_simulations": "0", "attempt_dir": f"attempts/{condition}/SYNTHETIC-task/attempt-001",
            "transcript": "sessions/SYNTHETIC-task/transcript.json",
            "total_tokens": "1234", "elapsed_seconds": "12.5", "iterations": "3", "deadlock_count": "1",
        })
    with (tmp_path / "results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    with TestClient(main.app) as client:
        catalog = client.get("/api/benchmark-dashboard/catalog").json()
        mscore = next(item for item in catalog["benchmarks"] if item["id"] == "mscore")
        task = mscore["tasks"][0]
        assert task["task_id"] == "SYNTHETIC-task"
        assert task["result_count"] == 2
        assert {attempt["run_id"] for attempt in task["attempts"]} == {"cf00", "cf100"}
        assert all(attempt["transcript_available"] for attempt in task["attempts"])
        assert task["attempts"][0]["total_tokens"] == 1234
        assert task["attempts"][0]["elapsed_seconds"] == 12.5
        details = client.get(
            "/api/benchmark-results/mscore/tasks/SYNTHETIC-task?condition=cf00"
        ).json()

    assert details["transcript"]["board"]["entries"][0]["entry_id"] == "entry-cf00"
    serialized = json.dumps([catalog, details])
    assert "SYNTHETIC_SECRET_GOLD" not in serialized
    assert "gold_answer" not in serialized


def test_catalog_and_details_include_math500_and_medagent_saved_records_without_gold(tmp_path, monkeypatch):
    root = tmp_path / "benchmarks"
    math_root = root / "math500" / "latest-20261125"
    med_root = root / "medagentbench" / "latest-20261125"
    math_trace = math_root / "traces" / "algebra" / "7" / "transcript.json"
    med_trace = med_root / "original-results" / "run-1" / "cf-00" / "task7" / "transcript.json"
    math_trace.parent.mkdir(parents=True)
    med_trace.parent.mkdir(parents=True)
    (math_root / "results.jsonl").write_text(json.dumps({
        "task_id": "test/algebra/7.json", "condition": "cf-66", "requested_fraction": 0.66,
        "expected": "SYNTHETIC_GOLD_MATH", "parsed_answer": "42", "correct": True,
        "prediction": "42", "outcome": "scheduler_consensus", "wall_seconds": 3.5,
        "usage": {"prompt_tokens": 10, "output_tokens": 4}, "trace": "traces/algebra/7/transcript.json",
    }) + "\n", encoding="utf-8")
    math_trace.write_text(json.dumps({
        "task": "SYNTHETIC math question", "gold_answer": "SYNTHETIC_GOLD_MATH",
        "board": {"entries": [{"entry_id": "math-entry", "agent_id": "solver", "tag": "RATIFY"}],
                  "counterfactual_events": []},
    }), encoding="utf-8")

    (med_root / "results.jsonl").write_text(json.dumps({
        "task_id": "task7", "condition": "cf-00", "run": "pilot", "requested_fraction": 0,
        "expected": "SYNTHETIC_GOLD_MED", "correct": False, "prediction": "wrong",
        "outcome": "scheduler_consensus", "wall_seconds": 5.0,
        "usage": {"prompt_tokens": 20, "output_tokens": 8},
        "trace": "original-results/run-1/cf-00/task7/transcript.json",
        "replay_snapshot": None,
    }) + "\n", encoding="utf-8")
    med_trace.write_text(json.dumps({
        "task": "SYNTHETIC medical task", "expected": "SYNTHETIC_GOLD_MED",
        "board": {"entries": [{"entry_id": "med-entry", "agent_id": "doctor", "tag": "RATIFY"}],
                  "counterfactual_events": []},
    }), encoding="utf-8")
    monkeypatch.setattr(main, "_BENCHMARK_RESULTS_ROOT", root)

    with TestClient(main.app) as client:
        catalog_response = client.get("/api/benchmark-dashboard/catalog")
        assert catalog_response.status_code == 200
        catalog = catalog_response.json()
        math = next(item for item in catalog["benchmarks"] if item["id"] == "math500")
        med = next(item for item in catalog["benchmarks"] if item["id"] == "medagentbench")
        assert (math["task_count"], math["result_count"]) == (1, 1)
        assert (med["task_count"], med["result_count"]) == (1, 1)
        assert math["tasks"][0]["attempts"][0]["total_tokens"] == 14
        assert med["tasks"][0]["attempts"][0]["total_tokens"] == 28
        math_detail = client.get(
            "/api/benchmark-results/math500/tasks/test%2Falgebra%2F7.json",
            params={"run_id": "cf-66:0"},
        ).json()
        med_detail = client.get(
            "/api/benchmark-results/medagentbench/tasks/task7",
            params={"run_id": "pilot:cf-00:0"},
        ).json()

    assert math_detail["entries"][0]["entry_id"] == "math-entry"
    assert med_detail["entries"][0]["entry_id"] == "med-entry"
    serialized = json.dumps([catalog, math_detail, med_detail])
    assert "SYNTHETIC_GOLD_MATH" not in serialized
    assert "SYNTHETIC_GOLD_MED" not in serialized
    assert '"expected"' not in serialized
    assert '"parsed_answer"' not in serialized
