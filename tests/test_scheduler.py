import threading

import pytest

from blackboard import AgentRecord, Blackboard, BoardEntry, BoardEvent, BoardEventType, PXPTag
from blackboard.models import IntelligibilityLevel
from blackboard.scheduler import Scheduler


def entry(agent_id):
    return BoardEntry(
        agent_id=agent_id,
        tag=PXPTag.REVISE,
        prediction="42",
        explanation="Initial proposal.",
    )


def test_scheduler_sees_agents_registered_on_board():
    board = Blackboard(task_id="registry")
    board.register_agent(AgentRecord(agent_id="a", persona="proposer"))
    scheduler = Scheduler(board)

    assert scheduler.next_agent().agent_id == "a"
    scheduler.submit_entry(entry("a"))

    # Register after the scheduler was created.
    board.register_agent(AgentRecord(agent_id="b", persona="verifier"))
    assert scheduler.next_agent().agent_id == "b"


def test_scheduler_rejects_entry_from_wrong_agent():
    board = Blackboard(task_id="turn")
    board.register_agent(AgentRecord(agent_id="a", persona="proposer"))
    board.register_agent(AgentRecord(agent_id="b", persona="verifier"))
    scheduler = Scheduler(board)

    assert scheduler.next_agent().agent_id == "a"
    with pytest.raises(ValueError, match="out-of-turn"):
        scheduler.submit_entry(entry("b"))

    assert board.get_history() == []
    scheduler.submit_entry(entry("a"))  # The scheduled turn is still available to retry.


def test_intelligibility_read_does_not_use_get_state(monkeypatch):
    board = Blackboard(task_id="status")
    scheduler = Scheduler(board)

    assert board.current_intelligibility is board.get_state().intelligibility

    def fail_get_state():
        raise AssertionError("get_state() should not be needed for a status read")

    monkeypatch.setattr(board, "get_state", fail_get_state)
    assert board.current_intelligibility is IntelligibilityLevel.UNRESOLVED
    assert scheduler.status is IntelligibilityLevel.UNRESOLVED

def test_next_agent_requires_at_least_one_active_agent():
    board = Blackboard(task_id="no-active-agents")
    board.register_agent(AgentRecord(agent_id="inactive", persona="p", active=False))
    scheduler = Scheduler(board)

    with pytest.raises(ValueError, match="No active agents"):
        scheduler.next_agent()


def test_scheduler_requires_one_scheduled_turn_at_a_time():
    board = Blackboard(task_id="turn-sequencing")
    board.register_agent(AgentRecord(agent_id="a", persona="p"))
    scheduler = Scheduler(board)

    with pytest.raises(RuntimeError, match="Call next_agent"):
        scheduler.submit_entry(entry("a"))

    assert scheduler.next_agent().agent_id == "a"
    with pytest.raises(RuntimeError, match="already has the current turn"):
        scheduler.next_agent()

    scheduler.submit_entry(entry("a"))


def test_rejected_board_entry_keeps_scheduled_turn_for_retry():
    board = Blackboard(task_id="retry")
    board.register_agent(AgentRecord(agent_id="a", persona="p"))
    scheduler = Scheduler(board)
    scheduler.next_agent()

    rejected = BoardEntry(
        agent_id="a",
        tag=PXPTag.RATIFY,
        prediction="42",
        explanation="Agreeing with an entry that does not exist.",
        target_entry_id="missing-entry",
    )
    with pytest.raises(ValueError, match="does not exist"):
        scheduler.submit_entry(rejected)

    assert board.get_history() == []
    scheduler.submit_entry(entry("a"))
    assert len(board.get_history()) == 1


@pytest.mark.parametrize("terminal_case", ["consensus", "deadlock"])
def test_terminal_state_stops_scheduler(terminal_case):
    board = Blackboard(task_id=f"terminal-{terminal_case}")
    board.register_agent(AgentRecord(agent_id="a", persona="p"))
    board.register_agent(AgentRecord(agent_id="b", persona="p"))

    if terminal_case == "consensus":
        e1 = BoardEntry(
            agent_id="a", tag=PXPTag.RATIFY, prediction="42", explanation="Agreed."
        )
        board.post_entry(e1)
        board.post_entry(BoardEntry(
            agent_id="b", tag=PXPTag.RATIFY, prediction="42", explanation="Agreed too.", target_entry_id=e1.entry_id
        ))
    else:
        for index in range(4):
            board.post_entry(BoardEntry(
                agent_id="a" if index % 2 == 0 else "b",
                tag=PXPTag.REFUTE if index % 2 == 0 else PXPTag.REJECT,
                prediction="42",
                explanation="Disagreement persists.",
            ))

    scheduler = Scheduler(board)
    assert not scheduler.is_running()
    with pytest.raises(RuntimeError):
        scheduler.next_agent()


def test_wait_for_next_agent_notices_pre_registered_agents():
    board = Blackboard(task_id="pre-registered-wakeup")
    board.register_agent(AgentRecord(agent_id="a", persona="p"))
    scheduler = Scheduler(board)

    assert scheduler.wait_for_next_agent(timeout=0.1).agent_id == "a"
    scheduler.close()


def test_agent_registration_wakes_waiting_scheduler(monkeypatch):
    board = Blackboard(task_id="registration-wakeup")
    scheduler = Scheduler(board)
    result = []
    errors = []
    waiting_on_empty_queue = threading.Event()
    original_get = scheduler._notifications.get

    def observed_get(*args, **kwargs):
        if scheduler._notifications.empty():
            waiting_on_empty_queue.set()
        return original_get(*args, **kwargs)

    monkeypatch.setattr(scheduler._notifications, "get", observed_get)

    def wait_for_agent():
        try:
            result.append(scheduler.wait_for_next_agent(timeout=2))
        except Exception as error:  # surfaced in the main test thread
            errors.append(error)

    waiter = threading.Thread(target=wait_for_agent, daemon=True)
    waiter.start()
    assert waiting_on_empty_queue.wait(timeout=1)
    board.register_agent(AgentRecord(agent_id="new-agent", persona="p"))
    waiter.join(timeout=1)

    assert not waiter.is_alive(), "registration did not wake the waiting scheduler"
    assert errors == []
    assert [agent.agent_id for agent in result] == ["new-agent"]
    scheduler.close()


def test_entry_posted_event_wakes_scheduler_for_next_round_robin_turn():
    board = Blackboard(task_id="entry-wakeup")
    board.register_agent(AgentRecord(agent_id="a", persona="p"))
    board.register_agent(AgentRecord(agent_id="b", persona="p"))
    scheduler = Scheduler(board)

    assert scheduler.wait_for_next_agent(timeout=0.1).agent_id == "a"
    scheduler.submit_entry(entry("a"))
    assert scheduler.wait_for_next_agent(timeout=0.1).agent_id == "b"
    scheduler.close()


def test_wait_for_next_agent_raises_when_board_is_terminal():
    board = Blackboard(task_id="terminal-wakeup")
    board.register_agent(AgentRecord(agent_id="a", persona="p"))
    board.register_agent(AgentRecord(agent_id="b", persona="p"))
    scheduler = Scheduler(board)
    first = BoardEntry(
        agent_id="a", tag=PXPTag.RATIFY, prediction="42", explanation="Agreed."
    )
    board.post_entry(first)
    board.post_entry(BoardEntry(
        agent_id="b",
        tag=PXPTag.RATIFY,
        prediction="42",
        explanation="Agreed too.",
        target_entry_id=first.entry_id,
    ))

    with pytest.raises(RuntimeError, match="STRONG"):
        scheduler.wait_for_next_agent(timeout=0.1)
    scheduler.close()


def test_wait_for_next_agent_times_out_without_active_agents():
    board = Blackboard(task_id="wait-timeout")
    scheduler = Scheduler(board)

    with pytest.raises(TimeoutError, match="Timed out"):
        scheduler.wait_for_next_agent(timeout=0.01)
    scheduler.close()


def test_duplicate_and_stale_notifications_are_coalesced():
    board = Blackboard(task_id="duplicate-notifications")
    board.register_agent(AgentRecord(agent_id="a", persona="p"))
    board.register_agent(AgentRecord(agent_id="b", persona="p"))
    scheduler = Scheduler(board)

    # Simulate an already-queued duplicate registration notification.
    scheduler._on_board_event(BoardEvent(
        event_type=BoardEventType.AGENT_REGISTERED,
        task_id=board.task_id,
    ))
    assert scheduler.wait_for_next_agent(timeout=0.1).agent_id == "a"

    scheduler.submit_entry(entry("a"))
    # This duplicate is stale by the time the queued ENTRY_POSTED wake is read.
    scheduler._on_board_event(BoardEvent(
        event_type=BoardEventType.ENTRY_POSTED,
        task_id=board.task_id,
    ))
    assert scheduler.wait_for_next_agent(timeout=0.1).agent_id == "b"
    assert scheduler._notifications.empty()
    scheduler.close()


def test_submit_entry_callback_does_not_deadlock_on_scheduler_lock():
    board = Blackboard(task_id="callback-no-deadlock")
    board.register_agent(AgentRecord(agent_id="a", persona="p"))
    scheduler = Scheduler(board)
    scheduler.next_agent()
    completed = threading.Event()
    errors = []

    def submit():
        try:
            scheduler.submit_entry(entry("a"))
        except Exception as error:  # surfaced in the main test thread
            errors.append(error)
        finally:
            completed.set()

    poster = threading.Thread(target=submit, daemon=True)
    poster.start()
    assert completed.wait(timeout=1), "board callback deadlocked during submit_entry()"
    poster.join(timeout=1)
    assert not poster.is_alive()
    assert errors == []
    scheduler.close()

