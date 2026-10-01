import pytest
from pydantic import ValidationError

from blackboard import AgentRecord, Blackboard, BoardEntry, PXPTag


def make_board():
    b = Blackboard(task_id="t")
    b.register_agent(AgentRecord(agent_id="a1", persona="p"))
    b.register_agent(AgentRecord(agent_id="a2", persona="p"))
    return b


def test_reject_empty_explanation():
    with pytest.raises(ValidationError):
        BoardEntry(agent_id="a1", tag=PXPTag.RATIFY, prediction="x", explanation="   ")


def test_unknown_agent_rejected():
    b = make_board()
    with pytest.raises(ValueError):
        b.post_entry(BoardEntry(agent_id="ghost", tag=PXPTag.RATIFY, prediction="x", explanation="y"))


def test_unknown_target_rejected():
    b = make_board()
    with pytest.raises(ValueError):
        b.post_entry(BoardEntry(
            agent_id="a1", tag=PXPTag.RATIFY, prediction="x", explanation="y",
            target_entry_id="does-not-exist",
        ))


def test_consensus_after_ratify_ratify():
    b = make_board()
    e1 = BoardEntry(agent_id="a1", tag=PXPTag.REVISE, prediction="42", explanation="init")
    b.post_entry(e1)
    e2 = BoardEntry(agent_id="a2", tag=PXPTag.RATIFY, prediction="42", explanation="agree", target_entry_id=e1.entry_id)
    b.post_entry(e2)
    e3 = BoardEntry(agent_id="a1", tag=PXPTag.RATIFY, prediction="42", explanation="agree", target_entry_id=e2.entry_id)
    b.post_entry(e3)
    assert "ULTRA_STRONG" in str(b.get_state().intelligibility)


def test_deadlock_detected_on_negative_streak():
    b = make_board()
    deadlock = None
    for i in range(4):
        agent = "a1" if i % 2 == 0 else "a2"
        tag = PXPTag.REFUTE if i % 2 == 0 else PXPTag.REJECT
        deadlock = b.post_entry(BoardEntry(agent_id=agent, tag=tag, prediction="x", explanation="disagree"))
    assert deadlock is not None
    assert deadlock.loop_length == 4
