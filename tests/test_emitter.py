import pytest
from blackboard import AgentRecord, Blackboard, BoardEntry, BoardEvent, BoardEventType, PXPTag


def test_emitter_subscribes_and_emits_events():
    board = Blackboard(task_id="emitter_test")
    events: list[BoardEvent] = []

    def on_event(event: BoardEvent):
        events.append(event)

    board.subscribe(on_event)

    # 1. Register agent
    agent1 = AgentRecord(agent_id="agent_a", persona="proposer")
    board.register_agent(agent1)

    assert len(events) == 1
    assert events[0].event_type == BoardEventType.AGENT_REGISTERED
    assert events[0].payload["agent"]["agent_id"] == "agent_a"

    # 2. Register agent b
    agent2 = AgentRecord(agent_id="agent_b", persona="verifier")
    board.register_agent(agent2)
    assert len(events) == 2

    # 3. Post entry
    e1 = BoardEntry(
        agent_id="agent_a",
        tag=PXPTag.REVISE,
        prediction="42",
        explanation="initial hypothesis",
    )
    board.post_entry(e1)

    # Should have emitted ENTRY_POSTED
    entry_events = [e for e in events if e.event_type == BoardEventType.ENTRY_POSTED]
    assert len(entry_events) == 1
    assert entry_events[0].payload["entry"]["agent_id"] == "agent_a"


def test_emitter_unsubscribes():
    board = Blackboard(task_id="unsub_test")
    events: list[BoardEvent] = []

    def on_event(event: BoardEvent):
        events.append(event)

    board.subscribe(on_event)
    board.register_agent(AgentRecord(agent_id="a1", persona="p"))
    assert len(events) == 1

    board.unsubscribe(on_event)
    board.register_agent(AgentRecord(agent_id="a2", persona="p"))
    # Event count shouldn't increase after unsubscribe
    assert len(events) == 1


def test_emitter_deadlock_and_intelligibility_events():
    board = Blackboard(task_id="deadlock_events")
    events: list[BoardEvent] = []
    board.subscribe(lambda e: events.append(e))

    board.register_agent(AgentRecord(agent_id="a1", persona="p"))
    board.register_agent(AgentRecord(agent_id="a2", persona="p"))

    # Produce deadlock
    for i in range(4):
        aid = "a1" if i % 2 == 0 else "a2"
        tag = PXPTag.REFUTE if i % 2 == 0 else PXPTag.REJECT
        board.post_entry(BoardEntry(agent_id=aid, tag=tag, prediction="val", explanation="conflict"))

    deadlock_events = [e for e in events if e.event_type == BoardEventType.DEADLOCK_DETECTED]
    assert len(deadlock_events) == 1
    assert deadlock_events[0].payload["deadlock"]["loop_length"] == 4

    intelligibility_events = [e for e in events if e.event_type == BoardEventType.INTELLIGIBILITY_CHANGED]
    assert len(intelligibility_events) >= 1
    assert intelligibility_events[-1].payload["current"] == "DEADLOCKED"
