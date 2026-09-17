"""
Run with: python demo.py

Demonstrates, with no LLMs involved yet, that the schema + store + core
actually work together: two agents converge (RATIFY) on task-1, and two
agents deadlock (REFUTE/REJECT loop) on task-2.
"""

from blackboard import AgentRecord, Blackboard, BoardEntry, PXPTag


def demo_consensus():
    board = Blackboard(task_id="task-1")
    board.register_agent(AgentRecord(agent_id="agent-a", persona="proposer"))
    board.register_agent(AgentRecord(agent_id="agent-b", persona="verifier"))

    e1 = BoardEntry(
        agent_id="agent-a",
        tag=PXPTag.REVISE,
        prediction="answer=42",
        explanation="derived from the first two constraints in the prompt",
    )
    board.post_entry(e1)

    e2 = BoardEntry(
        agent_id="agent-b",
        tag=PXPTag.RATIFY,
        prediction="answer=42",
        explanation="checked against the third constraint, consistent",
        target_entry_id=e1.entry_id,
    )
    board.post_entry(e2)

    e3 = BoardEntry(
        agent_id="agent-a",
        tag=PXPTag.RATIFY,
        prediction="answer=42",
        explanation="agent-b's check confirms it",
        target_entry_id=e2.entry_id,
    )
    board.post_entry(e3)

    state = board.get_state()
    print(f"[task-1] intelligibility = {state.intelligibility}")


def demo_deadlock():
    board = Blackboard(task_id="task-2")
    board.register_agent(AgentRecord(agent_id="agent-x", persona="aggressive_proposer"))
    board.register_agent(AgentRecord(agent_id="agent-y", persona="cautious_verifier"))

    tags = [PXPTag.REFUTE, PXPTag.REJECT, PXPTag.REFUTE, PXPTag.REJECT]
    agents = ["agent-x", "agent-y", "agent-x", "agent-y"]

    deadlock = None
    for agent_id, tag in zip(agents, tags):
        entry = BoardEntry(
            agent_id=agent_id,
            tag=tag,
            prediction="answer=17",
            explanation="disagrees with prior reasoning",
        )
        deadlock = board.post_entry(entry)

    state = board.get_state()
    print(f"[task-2] intelligibility = {state.intelligibility}")
    if deadlock:
        print(f"[task-2] DEADLOCK detected: {deadlock.loop_length} negative tags, "
              f"agents involved = {deadlock.involved_agent_ids}")
        print("         -> this is the hook point for the counterfactual rollback sandbox (Week 3)")


if __name__ == "__main__":
    demo_consensus()
    demo_deadlock()
