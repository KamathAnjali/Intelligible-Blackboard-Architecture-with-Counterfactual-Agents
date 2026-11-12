"""UI task API tests use a fake executor and never invoke Ollama or KramaBench."""

import asyncio
import json
import threading

from fastapi.testclient import TestClient

from ui.server import main
from ui.server import board_tap
from ui.server.event_bus import bus


def _reset_ui_state():
    main._task_sessions.clear()
    main._task_session_workers.clear()
    main._active_task_session_id = None
    bus.reset_history()


def _inject_reference_markers(monkeypatch):
    scope = main._load_task_scope()
    task = next(item for item in scope["tasks"] if item["task_id"] == "environment-easy-1")
    task["expected_answer"] = "SYNTHETIC_SECRET_GOLD"
    task["reference_answer"] = "SYNTHETIC_SECRET_REFERENCE"
    monkeypatch.setattr(main, "_load_task_scope", lambda: scope)
    return scope


def test_task_list_is_scope_limited_and_contains_no_reference_fields(monkeypatch):
    _reset_ui_state()
    monkeypatch.setattr(main, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setattr(board_tap, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.delenv("KRAMA_UI_APPROVE_GENUINE_RUN", raising=False)
    with TestClient(main.app) as client:
        response = client.get("/api/tasks")
    assert response.status_code == 200
    data = response.json()
    assert len(data["tasks"]) == 30
    assert {item["split"] for item in data["tasks"]} == {"tuning", "held_out_pilot"}
    serialized = json.dumps(data)
    assert "expected_answer" not in serialized
    assert "reference_answer" not in serialized
    assert "ground_truth_reasoning_steps" not in serialized
    assert "query" not in serialized


def test_task_run_requires_approval_and_rejects_unknown_ids(monkeypatch):
    _reset_ui_state()
    monkeypatch.setattr(main, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setattr(board_tap, "BOARD_MODE", "LIVE_TAP")
    with TestClient(main.app) as client:
        body = {"task_id": "environment-easy-1", "counterfactual_fraction": 0, "mode": "LIVE"}
        assert client.post("/api/tasks/run", json=body).status_code == 403
        monkeypatch.setenv("KRAMA_UI_APPROVE_GENUINE_RUN", "1")
        unknown = client.post("/api/tasks/run", json={**body, "task_id": "unlisted-task"})
        assert unknown.status_code == 404


def test_task_session_streams_lifecycle_and_withholds_score_until_complete(monkeypatch):
    _reset_ui_state()
    _inject_reference_markers(monkeypatch)
    monkeypatch.setattr(main, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setattr(board_tap, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setenv("KRAMA_UI_APPROVE_GENUINE_RUN", "1")
    release = threading.Event()

    async def fake_execute(session, task, scope):
        await asyncio.to_thread(release.wait, 2)
        await bus.publish({
            "type": "board_entry", "source": "LIVE", "session_id": session["session_id"],
            "task_id": task["task_id"], "entry": {
                "entry_id": "synthetic-board-entry", "agent_id": "proposer", "tag": "REVISE",
                "prediction": "SYNTHETIC proposal", "explanation": "SYNTHETIC explanation",
            },
        })
        return {"status": "completed", "summary": {
            "outcome": "agreement", "reason": "", "iterations": 2, "deadlocks": 0,
            "tokens": 12, "evaluator_score_available": True, "evaluator_score": 1.0,
            "evaluator_mode": "compatibility-no-key", "adapted": True,
            "entry_count": 1, "last_entry_id": "synthetic-board-entry",
        }, "archive_path": "SYNTHETIC_ARCHIVE_PATH"}

    monkeypatch.setattr(main, "_execute_scope_task", fake_execute)
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as websocket:
            assert websocket.receive_json()["type"] == "connection_ack"
            started = client.post("/api/tasks/run", json={
                "task_id": "environment-easy-1", "counterfactual_fraction": 0, "mode": "LIVE",
            })
            assert started.status_code == 202
            session_id = started.json()["session_id"]
            visible_while_running = client.get(f"/api/tasks/{session_id}/status").json()
            assert visible_while_running["status"] == "running"
            assert "evaluator_score" not in visible_while_running
            first_event = websocket.receive_json()
            assert first_event["type"] == "session_started"
            assert first_event["session_id"] == session_id
            assert "model" in first_event
            release.set()
            received = []
            for _ in range(4):
                event = websocket.receive_json()
                received.append(event)
                if event["type"] == "stream_complete":
                    break
            assert [event["type"] for event in received] == ["board_entry", "session_summary", "stream_complete"]
            assert received[1]["summary"]["evaluator_score"] == 1.0
            assert received[1]["summary"]["evaluator_mode"] == "compatibility-no-key"
            assert received[1]["summary"]["adapted"] is True
            status = client.get(f"/api/tasks/{session_id}/status").json()
            assert status["status"] == "completed"
            assert status["evaluator_score"] == 1.0
            payloads = json.dumps([started.json(), received, status])
            assert "expected_answer" not in payloads
            assert "SYNTHETIC_SECRET_GOLD" not in payloads
            assert "SYNTHETIC_SECRET_REFERENCE" not in payloads


def test_forced_backend_error_is_published_and_persisted(monkeypatch):
    _reset_ui_state()
    monkeypatch.setattr(main, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setattr(board_tap, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setenv("KRAMA_UI_APPROVE_GENUINE_RUN", "1")

    async def fake_execute(session, task, scope):
        return {"status": "failed", "error": "SYNTHETIC forced backend error", "summary": {
            "outcome": "failure", "reason": "SYNTHETIC forced backend error", "iterations": 0,
            "deadlocks": 0, "tokens": 0, "evaluator_score_available": False,
            "evaluator_mode": task["evaluator_mode"], "adapted": True,
            "entry_count": 0, "last_entry_id": None,
        }}

    monkeypatch.setattr(main, "_execute_scope_task", fake_execute)
    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            started = client.post("/api/tasks/run", json={"task_id": "environment-easy-1"})
            assert started.status_code == 202
            events = [websocket.receive_json() for _ in range(4)]
            assert [event["type"] for event in events] == ["session_started", "session_error", "session_summary", "stream_complete"]
            assert events[1]["error"] == "SYNTHETIC forced backend error"
            status = client.get(f"/api/tasks/{started.json()['session_id']}/status").json()
            assert status["status"] == "failed"
            assert status["error"] == "SYNTHETIC forced backend error"
            assert "evaluator_score" not in status


def test_terminal_timeout_updates_staging_manifest_and_writes_failure_record(tmp_path):
    run_root = tmp_path / "run-partial"
    run_root.mkdir()
    (run_root / "run_manifest.json").write_text(json.dumps({
        "run_id": "run-partial", "task_id": "SYNTHETIC-task", "status": "staging",
    }))
    session = {"task_id": "SYNTHETIC-task"}

    record_path = main._record_terminal_failure(
        run_root, session, "SYNTHETIC timeout", "time_limit",
    )

    record = json.loads(record_path.read_text())
    manifest = json.loads((run_root / "run_manifest.json").read_text())
    assert record["status"] == "failed"
    assert record["execution_outcome"] == "time_limit"
    assert record["errors"][-1]["message"] == "SYNTHETIC timeout"
    assert "provider_token_usage" not in record
    assert manifest["status"] == "failed"
    assert manifest["ui_terminal_failure"] is True

