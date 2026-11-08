"""Live WebSocket integration for the real Blackboard and Scheduler."""

from fastapi.testclient import TestClient

from blackboard.core import Blackboard
from blackboard.models import AgentRecord, BoardEntry, PXPTag
from blackboard.scheduler import Scheduler
from ui.server import board_tap, main


def test_live_websocket_receives_scheduler_post_from_real_board(monkeypatch):
    """A scheduled board entry reaches /ws without replay or mock endpoints."""
    monkeypatch.setattr(board_tap, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setattr(main, "BOARD_MODE", "LIVE_TAP")

    with TestClient(main.app) as client:
        with client.websocket_connect("/ws") as websocket:
            ack = websocket.receive_json()
            assert ack["type"] == "connection_ack"
            assert ack["mode"] == "LIVE_TAP"

            board = Blackboard(task_id="live-websocket-smoke")
            board_tap.attach_live_board(board)
            scheduler = Scheduler(board)
            scheduler.register_agent(AgentRecord(agent_id="smoke-agent", persona="proposer"))
            agent = scheduler.next_agent()
            entry = BoardEntry(
                agent_id=agent.agent_id,
                tag=PXPTag.REVISE,
                prediction="Smoke-test answer",
                explanation="A real Scheduler submission on the Blackboard.",
            )
            scheduler.submit_entry(entry)

            event = websocket.receive_json()
            assert event["type"] == "board_entry"
            assert event["entry"]["entry_id"] == entry.entry_id
            assert event["entry"]["prediction"] == "Smoke-test answer"
            assert event["entry"]["token_count_kind"] == "estimated_board_entry_text_only"
