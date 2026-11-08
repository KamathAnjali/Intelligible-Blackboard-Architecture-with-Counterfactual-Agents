import concurrent.futures
import threading

from blackboard import AgentRecord, Blackboard, BoardEntry, PXPTag


def test_concurrent_writes_no_lost_entries():
    """
    Project Milestone Check:
    Tests with four or more simultaneous writes show no lost entries or corrupted state.
    """
    board = Blackboard(task_id="concurrency_task")
    num_agents = 8
    entries_per_agent = 20
    total_expected_entries = num_agents * entries_per_agent

    # Register all agents
    for i in range(num_agents):
        board.register_agent(AgentRecord(agent_id=f"agent_{i}", persona="worker"))

    barrier = threading.Barrier(num_agents)

    def worker_post(agent_index: int):
        barrier.wait()  # Synchronize threads so writes happen concurrently
        agent_id = f"agent_{agent_index}"
        for j in range(entries_per_agent):
            entry = BoardEntry(
                agent_id=agent_id,
                tag=PXPTag.RATIFY,
                prediction=f"val_{agent_index}_{j}",
                explanation=f"explanation from {agent_id} step {j}",
            )
            board.post_entry(entry)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_agents) as executor:
        futures = [executor.submit(worker_post, i) for i in range(num_agents)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    state = board.get_state()
    history = board.get_history()

    # Verify no lost entries
    assert len(state.entries) == total_expected_entries
    assert len(history) == total_expected_entries

    # Verify entry IDs are unique
    entry_ids = [e.entry_id for e in history]
    assert len(entry_ids) == len(set(entry_ids))

    # Verify every agent's entries are preserved in full
    for i in range(num_agents):
        agent_history = board.get_history(agent_id=f"agent_{i}")
        assert len(agent_history) == entries_per_agent
