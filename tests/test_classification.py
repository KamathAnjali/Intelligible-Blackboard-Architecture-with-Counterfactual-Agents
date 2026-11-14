import pytest
from blackboard import AgentRecord, Blackboard, BoardEntry, IntelligibilityLevel, PXPTag


def setup_three_agent_board():
    board = Blackboard(task_id="class_test")
    board.register_agent(AgentRecord(agent_id="agent_1", persona="proposer"))
    board.register_agent(AgentRecord(agent_id="agent_2", persona="verifier"))
    board.register_agent(AgentRecord(agent_id="agent_3", persona="critic"))
    return board


def test_repeated_agreement_from_single_agent_does_not_impersonate_consensus():
    """
    Project Milestone check:
    Repeated agreement messages from one agent cannot impersonate agreement by all agents.
    """
    board = setup_three_agent_board()

    # Agent 1 proposes
    e1 = BoardEntry(agent_id="agent_1", tag=PXPTag.RATIFY, prediction="X", explanation="init")
    board.post_entry(e1)
    assert board.current_intelligibility == IntelligibilityLevel.UNRESOLVED

    # Agent 1 posts RATIFY multiple times consecutively
    e2 = BoardEntry(agent_id="agent_1", tag=PXPTag.RATIFY, prediction="X", explanation="reinforce", target_entry_id=e1.entry_id)
    board.post_entry(e2)
    e3 = BoardEntry(agent_id="agent_1", tag=PXPTag.RATIFY, prediction="X", explanation="insist", target_entry_id=e2.entry_id)
    board.post_entry(e3)

    # Must STILL be UNRESOLVED because agent_2 and agent_3 have not ratified!
    assert board.current_intelligibility == IntelligibilityLevel.UNRESOLVED

    # Now agent_2 ratifies
    e4 = BoardEntry(agent_id="agent_2", tag=PXPTag.RATIFY, prediction="X", explanation="agree", target_entry_id=e3.entry_id)
    board.post_entry(e4)
    # Still UNRESOLVED because agent_3 has not ratified
    assert board.current_intelligibility == IntelligibilityLevel.UNRESOLVED

    # Agent 3 ratifies
    e5 = BoardEntry(agent_id="agent_3", tag=PXPTag.RATIFY, prediction="X", explanation="unanimous", target_entry_id=e4.entry_id)
    board.post_entry(e5)

    # Now all 3 active agents have ratified -> STRONG (since no REVISE occurred)
    assert board.current_intelligibility == IntelligibilityLevel.STRONG


def test_ultra_strong_classification_with_productive_revise():
    """
    ULTRA_STRONG is assigned when unanimous consensus is reached via productive REVISE loops.
    """
    board = setup_three_agent_board()

    # Agent 1 proposes initial thesis
    e1 = BoardEntry(agent_id="agent_1", tag=PXPTag.REVISE, prediction="Preliminary", explanation="step 1")
    board.post_entry(e1)

    # Agent 2 refines with REVISE
    e2 = BoardEntry(agent_id="agent_2", tag=PXPTag.REVISE, prediction="Final Answer", explanation="step 2", target_entry_id=e1.entry_id)
    board.post_entry(e2)

    # Agent 1 ratifies the revised answer
    e3 = BoardEntry(agent_id="agent_1", tag=PXPTag.RATIFY, prediction="Final Answer", explanation="revised alignment", target_entry_id=e2.entry_id)
    board.post_entry(e3)

    # Agent 3 ratifies
    e4 = BoardEntry(agent_id="agent_3", tag=PXPTag.RATIFY, prediction="Final Answer", explanation="unanimous confirmation", target_entry_id=e3.entry_id)
    board.post_entry(e4)

    # Agent 2 confirms with RATIFY
    e5 = BoardEntry(agent_id="agent_2", tag=PXPTag.RATIFY, prediction="Final Answer", explanation="final signoff", target_entry_id=e4.entry_id)
    board.post_entry(e5)

    # Because REVISE was in the loop, outcome is ULTRA_STRONG
    assert board.current_intelligibility == IntelligibilityLevel.ULTRA_STRONG


def test_consensus_rejected_if_predictions_diverge():
    """
    If agents post RATIFY but their predicted values do not match, consensus is not formed.
    """
    board = Blackboard(task_id="diverge_test")
    board.register_agent(AgentRecord(agent_id="a1", persona="p"))
    board.register_agent(AgentRecord(agent_id="a2", persona="p"))

    board.post_entry(BoardEntry(agent_id="a1", tag=PXPTag.RATIFY, prediction="Option A", explanation="exp"))
    board.post_entry(BoardEntry(agent_id="a2", tag=PXPTag.RATIFY, prediction="Option B", explanation="exp"))

    assert board.current_intelligibility == IntelligibilityLevel.UNRESOLVED
