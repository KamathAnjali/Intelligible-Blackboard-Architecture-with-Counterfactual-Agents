import tempfile
from pathlib import Path

import pytest
from blackboard import (
    AgentRecord,
    Blackboard,
    BoardEntry,
    BoardEventType,
    CounterfactualResult,
    InMemoryJSONStore,
    IntelligibilityLevel,
    PXPTag,
    TrialMetrics,
)


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


def test_counterfactual_recovery_breaks_injected_deadlock():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = InMemoryJSONStore(snapshot_dir=tmpdir)
        board = Blackboard(task_id="recovery_test_session", store=store)

        emitted_events = []
        board.subscribe(lambda evt: emitted_events.append(evt))

        # 1. Register agents
        proposer = AgentRecord(agent_id="agent_proposer", persona="proposer")
        critic = AgentRecord(agent_id="agent_critic", persona="critic", counterfactual_capable=True)
        board.register_agent(proposer)
        board.register_agent(critic)

        # 2. Turn 1: Initial proposal
        e1 = BoardEntry(
            agent_id="agent_proposer",
            tag=PXPTag.REVISE,
            prediction="x = 12",
            explanation="Initial hypothesis solving equation x^2 = 144.",
        )
        board.post_entry(e1)

        # 3. Turns 2-5: Injected negative tag loop (REFUTE/REJECT) triggering deadlock
        e2 = BoardEntry(
            agent_id="agent_critic",
            tag=PXPTag.REFUTE,
            prediction="x = 12",
            explanation="Fails to account for negative root.",
            target_entry_id=e1.entry_id,
        )
        board.post_entry(e2)

        e3 = BoardEntry(
            agent_id="agent_proposer",
            tag=PXPTag.REJECT,
            prediction="x = 12",
            explanation="Rejecting critique; positive root suffices in standard domain.",
            target_entry_id=e2.entry_id,
        )
        board.post_entry(e3)

        e4 = BoardEntry(
            agent_id="agent_critic",
            tag=PXPTag.REFUTE,
            prediction="x = 12",
            explanation="Domain unrestricted; negative root must be included.",
            target_entry_id=e3.entry_id,
        )
        board.post_entry(e4)

        e5 = BoardEntry(
            agent_id="agent_proposer",
            tag=PXPTag.REJECT,
            prediction="x = 12",
            explanation="Disputing necessity of negative root.",
            target_entry_id=e4.entry_id,
        )
        deadlock_event = board.post_entry(e5)

        # 4. Verify deadlock detection
        assert deadlock_event is not None
        assert board.current_intelligibility == IntelligibilityLevel.DEADLOCKED
        assert len(board.get_state().deadlocks) == 1
        assert any(e.event_type == BoardEventType.DEADLOCK_DETECTED for e in emitted_events)

        # 5. Fork isolated counterfactual simulation blackboard at cutoff e1
        sim_board = board.fork_simulation_blackboard(cutoff_entry_id=e1.entry_id)
        assert len(sim_board.get_history()) == 1

        # Simulate alternative resolution inside sandbox
        sim_alt = BoardEntry(
            agent_id="agent_critic",
            tag=PXPTag.REVISE,
            prediction="x in {-12, 12}",
            explanation="Alternative hypothesis incorporating both roots.",
            target_entry_id=e1.entry_id,
            is_counterfactual_sim=True,
        )
        sim_board.post_entry(sim_alt)

        sim_ratify_proposer = BoardEntry(
            agent_id="agent_proposer",
            tag=PXPTag.RATIFY,
            prediction="x in {-12, 12}",
            explanation="Ratifying complete solution set in sandbox.",
            target_entry_id=sim_alt.entry_id,
            is_counterfactual_sim=True,
        )
        sim_board.post_entry(sim_ratify_proposer)

        sim_ratify_critic = BoardEntry(
            agent_id="agent_critic",
            tag=PXPTag.RATIFY,
            prediction="x in {-12, 12}",
            explanation="Unanimous agreement reached in sandbox.",
            target_entry_id=sim_ratify_proposer.entry_id,
            is_counterfactual_sim=True,
        )
        sim_board.post_entry(sim_ratify_critic)

        assert sim_board.current_intelligibility == IntelligibilityLevel.ULTRA_STRONG

        # Verify live board is strictly unaltered during simulation
        assert len(board.get_history()) == 5
        assert board.current_intelligibility == IntelligibilityLevel.DEADLOCKED

        # 6. Record counterfactual result on live board
        cf_result = CounterfactualResult(
            agent_id="agent_critic",
            original_entry_id=e2.entry_id,
            simulated_entry=sim_alt,
            delta_score=0.92,
            applied_to_live=True,
        )
        board.record_counterfactual_result(cf_result)

        # 7. Apply recovery to live board: agent posts REVISE based on simulation
        live_revision = BoardEntry(
            agent_id="agent_critic",
            tag=PXPTag.REVISE,
            prediction="x in {-12, 12}",
            explanation="Adopting counterfactual alternative hypothesis with complete root set.",
            target_entry_id=e5.entry_id,
        )
        board.post_entry(live_revision)

        # Deadlock is broken: board transitions out of DEADLOCKED into UNRESOLVED
        assert board.current_intelligibility == IntelligibilityLevel.UNRESOLVED

        # 8. Downstream agents ratify to reach consensus
        live_ratify_proposer = BoardEntry(
            agent_id="agent_proposer",
            tag=PXPTag.RATIFY,
            prediction="x in {-12, 12}",
            explanation="Ratifying revised solution on live board.",
            target_entry_id=live_revision.entry_id,
        )
        board.post_entry(live_ratify_proposer)

        live_ratify_critic = BoardEntry(
            agent_id="agent_critic",
            tag=PXPTag.RATIFY,
            prediction="x in {-12, 12}",
            explanation="Unanimous ratification of revised consensus.",
            target_entry_id=live_ratify_proposer.entry_id,
        )
        board.post_entry(live_ratify_critic)

        # 9. Verify convergence & state integrity
        assert board.current_intelligibility == IntelligibilityLevel.ULTRA_STRONG
        live_history = board.get_history()
        assert len(live_history) == 8
        assert all(not e.is_counterfactual_sim for e in live_history)

        # 10. Verify Trial Metrics and logging
        metrics = board.get_trial_metrics()
        assert isinstance(metrics, TrialMetrics)
        assert metrics.task_id == "recovery_test_session"
        assert metrics.converged is True
        assert metrics.intelligibility == IntelligibilityLevel.ULTRA_STRONG
        assert metrics.iteration_count == 8
        assert metrics.deadlock_count == 1
        assert metrics.deadlock_flag is True
        assert metrics.counterfactual_attempts == 1
        assert metrics.counterfactual_applied is True
        assert metrics.participating_agents == ["agent_critic", "agent_proposer"]
        assert metrics.duration_seconds >= 0.0

        # 11. Test trial log export & event dispatch
        logged_metrics = board.log_trial_run()
        assert logged_metrics.converged is True

        metrics_file = Path(tmpdir) / "recovery_test_session_trial_metrics.json"
        assert metrics_file.exists()
        content = metrics_file.read_text()
        assert "ULTRA_STRONG" in content
        assert '"converged": true' in content.lower()

        assert any(e.event_type == BoardEventType.TRIAL_COMPLETED for e in emitted_events)

