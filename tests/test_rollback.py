import pytest
from blackboard import AgentRecord, Blackboard, BoardEntry, CounterfactualResult, PXPTag


def test_history_slice_and_simulation_isolation():
    board = Blackboard(task_id="live_session")
    board.register_agent(AgentRecord(agent_id="agent_1", persona="proposer"))
    board.register_agent(AgentRecord(agent_id="agent_2", persona="critic"))

    # Post entries
    e1 = BoardEntry(agent_id="agent_1", tag=PXPTag.REVISE, prediction="Hypothesis 1", explanation="init")
    board.post_entry(e1)

    e2 = BoardEntry(agent_id="agent_2", tag=PXPTag.REFUTE, prediction="Hypothesis 1", explanation="flaw", target_entry_id=e1.entry_id)
    board.post_entry(e2)

    e3 = BoardEntry(agent_id="agent_1", tag=PXPTag.REJECT, prediction="Hypothesis 1", explanation="disagree", target_entry_id=e2.entry_id)
    board.post_entry(e3)

    # 1. Test get_history_slice up to e1
    slice_e1 = board.get_history_slice(cutoff_entry_id=e1.entry_id)
    assert len(slice_e1) == 1
    assert slice_e1[0].entry_id == e1.entry_id

    # 2. Fork an isolated sandbox simulation blackboard at cutoff e1
    sim_board = board.fork_simulation_blackboard(cutoff_entry_id=e1.entry_id)
    assert sim_board.task_id.startswith("live_session_sim_")
    assert len(sim_board.get_history()) == 1

    # 3. Simulate counterfactual path in the sandbox
    sim_alt = BoardEntry(
        agent_id="agent_2",
        tag=PXPTag.RATIFY,
        prediction="Hypothesis 1 Modified",
        explanation="Counterfactual alternative proposal",
        is_counterfactual_sim=True,
    )
    sim_board.post_entry(sim_alt)

    # Verify simulation board received the entry
    assert len(sim_board.get_history()) == 2

    # Verify LIVE board history is UNTOUCHED (Strict isolation)
    assert len(board.get_history()) == 3
    assert all(not e.is_counterfactual_sim for e in board.get_history())

    # 4. Record Counterfactual Result to live board state
    cf_result = CounterfactualResult(
        agent_id="agent_2",
        original_entry_id=e2.entry_id,
        simulated_entry=sim_alt,
        delta_score=0.85,
        applied_to_live=False,
    )
    board.record_counterfactual_result(cf_result)

    state = board.get_state()
    assert len(state.counterfactual_events) == 1
    assert state.counterfactual_events[0].delta_score == 0.85
