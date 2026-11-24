"""The unified task catalog exposes saved run metadata without reference answers."""

import csv
import json

from fastapi.testclient import TestClient

from ui.server import main


def test_catalog_lists_mscore_attempts_for_task_specific_replay_without_gold(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_mscore_results_root", lambda: tmp_path)
    (tmp_path / "results_manifest.json").write_text(json.dumps({
        "benchmark": "MSCoRe", "model": "SYNTHETIC-model", "result_label": "adapted",
    }), encoding="utf-8")
    fields = [
        "task_id", "condition", "attempt_status", "outcome", "evaluation_status", "scored",
        "score", "prediction", "evaluator_mode", "adapted", "failure_reason",
        "counterfactual_fraction", "counterfactual_simulations", "attempt_dir", "transcript",
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
        details = client.get(
            "/api/benchmark-results/mscore/tasks/SYNTHETIC-task?condition=cf00"
        ).json()

    assert details["transcript"]["board"]["entries"][0]["entry_id"] == "entry-cf00"
    serialized = json.dumps([catalog, details])
    assert "SYNTHETIC_SECRET_GOLD" not in serialized
    assert "gold_answer" not in serialized
