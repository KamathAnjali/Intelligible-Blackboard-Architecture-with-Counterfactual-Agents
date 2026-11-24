"""Live WebSocket integration for the real Blackboard and Scheduler."""

from fastapi.testclient import TestClient
from types import SimpleNamespace

from blackboard.core import Blackboard
from blackboard.models import AgentRecord, BoardEntry, BoardEventType, PXPTag
from blackboard.scheduler import Scheduler
from ui.server import board_tap, main


def test_live_websocket_receives_scheduler_post_from_real_board(monkeypatch):
    """A scheduled board entry and its lifecycle events reach /ws."""
    monkeypatch.setattr(board_tap, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setattr(main, "BOARD_MODE", "LIVE_TAP")

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as websocket:
            ack = websocket.receive_json()
            assert ack["type"] == "connection_ack"
            assert ack["mode"] == "LIVE_TAP"
            assert ack["entry_count"] == 0
            assert ack["last_entry_id"] is None

            board = Blackboard(task_id="live-websocket-smoke")
            board.register_agent(AgentRecord(agent_id="smoke-agent", persona="proposer"))
            board_tap.attach_live_board(board)
            scheduler = Scheduler(board, session_id="smoke-session", max_turns=1)
            agent = scheduler.next_agent()
            entry = BoardEntry(
                agent_id=agent.agent_id,
                tag=PXPTag.REVISE,
                prediction="Smoke-test answer",
                explanation="A real Scheduler submission on the Blackboard.",
            )
            scheduler.submit_entry(entry)

            status = websocket.receive_json()
            assert (status["type"], status["state"], status["session_id"]) == (
                "scheduler_status", "running", "smoke-session",
            )
            started = websocket.receive_json()
            assert (started["type"], started["agent_id"], started["turn"]) == (
                "turn_started", agent.agent_id, 1,
            )
            event = websocket.receive_json()
            assert event["type"] == "board_entry"
            assert event["entry"]["entry_id"] == entry.entry_id
            assert event["entry"]["prediction"] == "Smoke-test answer"
            assert event["entry"]["token_count_kind"] == "estimated_board_entry_text_only"
            assert websocket.receive_json()["type"] == "token_tally"
            finished = websocket.receive_json()
            assert (finished["type"], finished["outcome"], finished["turn"]) == (
                "turn_finished", "posted", 1,
            )
            status = websocket.receive_json()
            assert (status["type"], status["state"]) == ("scheduler_status", "limit")
            scheduler.close()


def test_fake_emitter_forwards_counterfactual_events_without_changing_live_count(monkeypatch):
    """Explicit branch events reach the wire and never become canonical entries."""
    monkeypatch.setattr(board_tap, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setattr(main, "BOARD_MODE", "LIVE_TAP")

    class FakeEmitter:
        task_id = "fake-branch-session"

        def subscribe(self, callback):
            self.callback = callback

        def unsubscribe(self, callback):
            assert callback is self.callback

    with TestClient(main.app) as client:
        board_tap.detach_live_board()
        board_tap.bus.reset_history()
        with client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            board = FakeEmitter()
            board_tap.attach_live_board(board)
            live_entry = BoardEntry(
                agent_id="live-agent", tag=PXPTag.REVISE, prediction="canonical",
                explanation="on the real board",
            )
            board.callback(SimpleNamespace(
                event_type=BoardEventType.ENTRY_POSTED, task_id=board.task_id,
                payload={"entry": live_entry.model_dump(mode="json")},
            ))
            live_event = websocket.receive_json()
            assert live_event["type"] == "board_entry"
            assert live_event["entry_count"] == 1
            board.callback(SimpleNamespace(
                event_type="branch_created", task_id=board.task_id,
                payload={"branch_id": "branch-1", "source_session_id": "session-1", "cutoff_entry_id": "live-1"},
            ))
            created = websocket.receive_json()
            while created["type"] == "token_tally":
                created = websocket.receive_json()
            assert created["type"] == "branch_created"
            assert created["branch_id"] == "branch-1"

            board.callback(SimpleNamespace(
                event_type="branch_entry_posted", task_id=board.task_id,
                payload={"branch_id": "branch-1", "entry": {
                    "entry_id": "sim-1", "agent_id": "agent", "tag": "REVISE",
                    "prediction": "alternative", "explanation": "isolated branch",
                }},
            ))
            branch = websocket.receive_json()
            assert branch["type"] == "branch_entry_posted"
            assert branch["branch_id"] == "branch-1"
            assert branch["entry"]["entry_id"] == "sim-1"
            assert "entry_count" not in branch
            assert board_tap.bus.snapshot() == {"entry_count": 1, "last_entry_id": live_entry.entry_id}
            assert not any(event.get("type") == "board_entry" and event.get("entry", {}).get("entry_id") == "sim-1"
                           for event in board_tap.bus._history)
        board_tap.detach_live_board()
