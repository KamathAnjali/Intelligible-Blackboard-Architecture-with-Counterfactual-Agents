import pytest

from blackboard import AgentRecord, Blackboard, BoardEntry, PXPTag
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
        board.post_entry(BoardEntry(
            agent_id="a", tag=PXPTag.RATIFY, prediction="42", explanation="Agreed."
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

