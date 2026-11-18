"""Live WebSocket integration for the real Blackboard and Scheduler."""

from fastapi.testclient import TestClient
from types import SimpleNamespace
import pytest

from blackboard.core import Blackboard
from blackboard.models import AgentRecord, BoardEntry, BoardEventType, PXPTag
from blackboard.scheduler import Scheduler
from ui.server import board_tap, main


def test_scheduler_event_normalization_validates_and_preserves_session_identity():
    normalized = board_tap.process_scheduler_event({
        "type": "turn_started", "session_id": "scheduler-session",
        "task_id": "task-a", "agent_id": "reviewer", "turn": 2,
        "reason": "review", "priority_score": 0.7,
    }, session_id="ui-session", task_id="task-a")
    assert normalized["session_id"] == "ui-session"
    assert normalized["scheduler_session_id"] == "scheduler-session"
    assert normalized["agent_id"] == "reviewer"
    assert normalized["turn"] == 2
    assert normalized["source"] == "LIVE"
    assert board_tap.process_scheduler_event({
        "type": "turn_started", "agent_id": "reviewer", "turn": 0,
    }) is None
    assert board_tap.process_scheduler_event({"type": "other_event"}) is None


@pytest.mark.parametrize("terminal_case", ["consensus", "deadlock", "limit"])
def test_live_websocket_forwards_real_scheduler_lifecycle_order(monkeypatch, terminal_case):
    """The real scheduler emits start -> entry -> finish and its terminal status reaches /ws."""
    required_events = ("TURN_STARTED", "TURN_FINISHED", "SCHEDULER_STATUS")
    if not all(hasattr(BoardEventType, event) for event in required_events):
        pytest.skip("requires Student 2 scheduler lifecycle events from commit acd36b7")
    import inspect
    if "session_id" not in inspect.signature(Scheduler).parameters or not hasattr(Scheduler, "finish_turn"):
        pytest.skip("requires Student 2 scheduler lifecycle event API")

    monkeypatch.setattr(board_tap, "BOARD_MODE", "LIVE_TAP")
    monkeypatch.setattr(main, "BOARD_MODE", "LIVE_TAP")
    task_id = f"scheduler-wire-{terminal_case}"
    session_id = f"wire-{terminal_case}"
    board = Blackboard(task_id=task_id)
    board.register_agent(AgentRecord(agent_id="a", persona="proposer"))
    board.register_agent(AgentRecord(agent_id="b", persona="verifier"))
    scheduler = None

    with TestClient(main.app) as client:
        board_tap.detach_live_board()
        board_tap.bus.reset_history()
        with client.websocket_connect("/ws") as websocket:
            websocket.receive_json()
            board_tap.attach_live_board(board)
            scheduler = Scheduler(
                board, session_id=session_id,
                max_turns=1 if terminal_case == "limit" else None,
            )

            if terminal_case == "consensus":
                first = scheduler.next_agent()
                scheduler.submit_entry(BoardEntry(
                    agent_id=first.agent_id, tag=PXPTag.RATIFY,
                    prediction="42", explanation="Agreed.",
                ))
                second = scheduler.next_agent()
                scheduler.submit_entry(BoardEntry(
                    agent_id=second.agent_id, tag=PXPTag.RATIFY,
                    prediction="42", explanation="Agreed too.",
                    target_entry_id=board.get_history()[0].entry_id,
                ))
            elif terminal_case == "deadlock":
                for index in range(4):
                    agent = scheduler.next_agent()
                    scheduler.submit_entry(BoardEntry(
                        agent_id=agent.agent_id,
                        tag=PXPTag.REFUTE if index % 2 == 0 else PXPTag.REJECT,
                        prediction="42", explanation="Disagreement persists.",
                    ))
            else:
                agent = scheduler.next_agent()
                scheduler.submit_entry(BoardEntry(
                    agent_id=agent.agent_id, tag=PXPTag.REVISE,
                    prediction="42", explanation="Refined proposal.",
                ))

            lifecycle = []
            terminal_seen = False
            while not terminal_seen:
                event = websocket.receive_json()
                if event["type"] == "token_tally":
                    continue
                if event["type"] in {"scheduler_status", "turn_started", "turn_finished", "board_entry"}:
                    lifecycle.append(event)
                terminal_seen = event.get("type") == "scheduler_status" and event.get("state") != "running"

            expected = ["scheduler_status"]
            turn_count = 2 if terminal_case == "consensus" else 4 if terminal_case == "deadlock" else 1
            for _ in range(turn_count):
                expected.extend(["turn_started", "board_entry", "turn_finished"])
            expected.append("scheduler_status")
            assert [event["type"] for event in lifecycle] == expected
            assert [event["state"] for event in lifecycle if event["type"] == "scheduler_status"] == ["running", terminal_case]
            for event in lifecycle:
                if event["type"] != "board_entry":
                    assert event["session_id"] == session_id
                    assert event["task_id"] == task_id
            for started, entry, finished in zip(lifecycle[1:-1:3], lifecycle[2:-1:3], lifecycle[3:-1:3]):
                assert started["agent_id"] == entry["entry"]["agent_id"] == finished["agent_id"]
                assert started["turn"] == finished["turn"]
                assert finished["outcome"] == "posted"
            terminal = lifecycle[-1]
            assert [agent["agent_id"] for agent in terminal["agents"]] == ["a", "b"]
            assert not scheduler.is_running()
    if scheduler is not None:
        scheduler.close()


def test_live_websocket_receives_scheduler_post_from_real_board(monkeypatch):
    """A scheduled board entry reaches /ws without replay or mock endpoints."""
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

            received = []
            while not any(item["type"] == "board_entry" for item in received):
                event = websocket.receive_json()
                if event["type"] != "token_tally":
                    received.append(event)
            scheduler_events_available = all(
                hasattr(BoardEventType, name)
                for name in ("TURN_STARTED", "TURN_FINISHED", "SCHEDULER_STATUS")
            )
            if scheduler_events_available:
                assert [item["type"] for item in received[:3]] == [
                    "scheduler_status", "turn_started", "board_entry",
                ]
            else:
                assert received[0]["type"] == "board_entry"
            event = next(item for item in received if item["type"] == "board_entry")
            assert event["type"] == "board_entry"
            assert event["entry"]["entry_id"] == entry.entry_id
            assert event["entry"]["prediction"] == "Smoke-test answer"
            assert event["entry"]["token_count_kind"] == "estimated_board_entry_text_only"


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
