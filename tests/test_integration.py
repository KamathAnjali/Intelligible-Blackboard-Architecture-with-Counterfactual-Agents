import tempfile
from pathlib import Path

from blackboard import (
    AgentRecord,
    Blackboard,
    BoardEntry,
    BoardEvent,
    BoardEventType,
    InMemoryJSONStore,
    IntelligibilityLevel,
    PXPTag,
)
from blackboard.scheduler import Scheduler


def test_end_to_end_three_agent_session_with_events_and_snapshots():
    with tempfile.TemporaryDirectory() as tmpdir:
        store = InMemoryJSONStore(snapshot_dir=tmpdir)
        board = Blackboard(task_id="integration_task_1", store=store)

        emitted_events: list[BoardEvent] = []
        board.subscribe(lambda evt: emitted_events.append(evt))

        # 1. Register 3 agents
        a1 = AgentRecord(agent_id="agent_alpha", persona="proposer")
        a2 = AgentRecord(agent_id="agent_beta", persona="verifier")
        a3 = AgentRecord(agent_id="agent_gamma", persona="critic")

        board.register_agent(a1)
        board.register_agent(a2)
        board.register_agent(a3)

        scheduler = Scheduler(board)

        # 2. Turn 1: agent_alpha proposes (REVISE)
        turn1_agent = scheduler.next_agent()
        assert turn1_agent.agent_id == "agent_alpha"
        e1 = BoardEntry(
            agent_id="agent_alpha",
            tag=PXPTag.REVISE,
            prediction="Acute Bronchitis",
            explanation="Initial proposal based on 3-day cough.",
        )
        scheduler.submit_entry(e1)

        # 3. Turn 2: agent_beta reviews and ratifies (RATIFY)
        turn2_agent = scheduler.next_agent()
        assert turn2_agent.agent_id == "agent_beta"
        e2 = BoardEntry(
            agent_id="agent_beta",
            tag=PXPTag.RATIFY,
            prediction="Acute Bronchitis",
            explanation="Confirmed absence of focal consolidation.",
            target_entry_id=e1.entry_id,
        )
        scheduler.submit_entry(e2)

        # 4. Turn 3: agent_gamma confirms and ratifies (RATIFY)
        turn3_agent = scheduler.next_agent()
        assert turn3_agent.agent_id == "agent_gamma"
        e3 = BoardEntry(
            agent_id="agent_gamma",
            tag=PXPTag.RATIFY,
            prediction="Acute Bronchitis",
            explanation="Vital signs and chest examination agree.",
            target_entry_id=e2.entry_id,
        )
        scheduler.submit_entry(e3)

        # 5. Turn 4: agent_alpha ratifies the converged consensus
        turn4_agent = scheduler.next_agent()
        assert turn4_agent.agent_id == "agent_alpha"
        e4 = BoardEntry(
            agent_id="agent_alpha",
            tag=PXPTag.RATIFY,
            prediction="Acute Bronchitis",
            explanation="Consensus ratified across all three agents.",
            target_entry_id=e3.entry_id,
        )
        scheduler.submit_entry(e4)

        # Session should now be terminal ULTRA_STRONG (since e1 was REVISE)
        assert board.current_intelligibility == IntelligibilityLevel.ULTRA_STRONG
        assert not scheduler.is_running()

        # 6. Verify event stream
        event_types = [e.event_type for e in emitted_events]
        assert BoardEventType.AGENT_REGISTERED in event_types
        assert BoardEventType.ENTRY_POSTED in event_types
        assert BoardEventType.INTELLIGIBILITY_CHANGED in event_types

        # 7. Save snapshot to disk
        snapshot_path = board.save_snapshot(tag="final")
        assert snapshot_path is not None
        assert Path(snapshot_path).exists()

        # 8. Reload snapshot into fresh store & board
        new_store = InMemoryJSONStore(snapshot_dir=tmpdir)
        loaded_state = new_store.load_snapshot_from_disk(
            "integration_task_1", filepath=snapshot_path
        )
        assert loaded_state.task_id == "integration_task_1"
        assert len(loaded_state.entries) == 4
        assert len(loaded_state.agents) == 3
        assert loaded_state.intelligibility == IntelligibilityLevel.ULTRA_STRONG

        # 9. Verify replay trace export
        trace = new_store.export_replay_trace("integration_task_1")
        assert len(trace) == 4
        assert trace[0]["tag"] == "REVISE"
        assert trace[-1]["tag"] == "RATIFY"
