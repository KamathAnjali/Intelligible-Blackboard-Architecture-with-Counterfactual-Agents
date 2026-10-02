"""
Unified 3-Workstream Integration Demonstration.

This is a scripted software demonstration using fixed board entries. It does
not run the LLM agents or a benchmark evaluator.

Run with:
    python demo.py

Showcases the integrated workflows of:
- Student 1 (Anjali): Thread-safe Blackboard, storage, event emitter, classification, and rollback sandbox.
- Student 2 (Lopez): Round-robin turn scheduling, turn enforcement, and terminal status handling.
- Student 3 (Dhruva): Multi-persona PXP reasoning, disagreement detection, and counterfactual simulation.
"""

import json
from blackboard import (
    AgentRecord,
    Blackboard,
    BoardEntry,
    BoardEvent,
    BoardEventType,
    CounterfactualResult,
    InMemoryJSONStore,
    IntelligibilityLevel,
    PXPTag,
)
from blackboard.scheduler import Scheduler


def print_banner(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def demo_three_agent_consensus():
    print_banner("SCENARIO 1: Three-Agent Convergence through Scheduler (Ultra-Strong)")

    task_id = "task_consensus_demo"
    store = InMemoryJSONStore(snapshot_dir="./snapshots")
    board = Blackboard(task_id=task_id, store=store)

    # Attach live event logger (simulating WebSocket consumer)
    events_log: list[BoardEvent] = []
    board.subscribe(lambda evt: events_log.append(evt))

    # 1. Register 3 personas (Student 3 personas registered via Student 1 registry)
    proposer = AgentRecord(agent_id="proposer", persona="aggressive_proposer", model_name="qwen3:4b-instruct-2507-q4_K_M")
    verifier = AgentRecord(agent_id="verifier", persona="cautious_verifier", model_name="qwen3:4b-instruct-2507-q4_K_M")
    challenger = AgentRecord(agent_id="challenger", persona="counterexample_challenger", model_name="qwen3:4b-instruct-2507-q4_K_M")

    board.register_agent(proposer)
    board.register_agent(verifier)
    board.register_agent(challenger)

    # 2. Attach Scheduler (Student 2)
    scheduler = Scheduler(board)
    print(f"[*] Registered agents: {[a for a in board.get_agents()]}")
    print(f"[*] Initial Scheduler status: {scheduler.status.value}")

    # Turn 1: Proposer (REVISE)
    a1 = scheduler.next_agent()
    print(f"\n[Turn 1] -> {a1.agent_id} ({a1.persona})")
    e1 = BoardEntry(
        agent_id=a1.agent_id,
        tag=PXPTag.REVISE,
        prediction="Acute Bronchitis",
        explanation="Symptoms of acute cough and fever for 3 days without focal lung signs.",
    )
    scheduler.submit_entry(e1)
    print(f"  Posted [{e1.tag.value}]: {e1.prediction}")

    # Turn 2: Verifier (RATIFY)
    a2 = scheduler.next_agent()
    print(f"\n[Turn 2] -> {a2.agent_id} ({a2.persona})")
    e2 = BoardEntry(
        agent_id=a2.agent_id,
        tag=PXPTag.RATIFY,
        prediction="Acute Bronchitis",
        explanation="Absence of chest pain and normal breath sounds support bronchitis.",
        target_entry_id=e1.entry_id,
    )
    scheduler.submit_entry(e2)
    print(f"  Posted [{e2.tag.value}]: {e2.prediction}")

    # Turn 3: Challenger (RATIFY)
    a3 = scheduler.next_agent()
    print(f"\n[Turn 3] -> {a3.agent_id} ({a3.persona})")
    e3 = BoardEntry(
        agent_id=a3.agent_id,
        tag=PXPTag.RATIFY,
        prediction="Acute Bronchitis",
        explanation="Checked for alternate diagnoses (pneumonia, asthma); facts rule them out.",
        target_entry_id=e2.entry_id,
    )
    scheduler.submit_entry(e3)
    print(f"  Posted [{e3.tag.value}]: {e3.prediction}")

    # Turn 4: Proposer confirms unanimous alignment
    a4 = scheduler.next_agent()
    print(f"\n[Turn 4] -> {a4.agent_id} ({a4.persona})")
    e4 = BoardEntry(
        agent_id=a4.agent_id,
        tag=PXPTag.RATIFY,
        prediction="Acute Bronchitis",
        explanation="Unanimous convergence confirmed across all 3 agent viewpoints.",
        target_entry_id=e3.entry_id,
    )
    scheduler.submit_entry(e4)
    print(f"  Posted [{e4.tag.value}]: {e4.prediction}")

    print(f"\n[*] Final Intelligibility Outcome: {board.current_intelligibility.value}")
    print(f"[*] Scheduler Terminal Status: {scheduler.status.value} (is_running = {scheduler.is_running()})")
    print(f"[*] Total Real-time Events Emitted: {len(events_log)}")

    # Snapshot to disk
    snapshot_path = board.save_snapshot(tag="final")
    print(f"[*] Snapshot written to: {snapshot_path}")

    # Export replay trace
    replay_trace = store.export_replay_trace(task_id)
    print(f"[*] UI Replay Trace Steps: {len(replay_trace)}")


def demo_deadlock_and_counterfactual_rollback():
    print_banner("SCENARIO 2: Deadlock Detection & Isolated Counterfactual Replay")

    task_id = "task_deadlock_demo"
    store = InMemoryJSONStore(snapshot_dir="./snapshots")
    board = Blackboard(task_id=task_id, store=store)

    agent_a = AgentRecord(agent_id="agent_alpha", persona="aggressive_proposer", counterfactual_capable=True)
    agent_b = AgentRecord(agent_id="agent_beta", persona="counterexample_challenger", counterfactual_capable=True)

    board.register_agent(agent_a)
    board.register_agent(agent_b)
    scheduler = Scheduler(board)

    # Initial proposal
    e1 = BoardEntry(agent_id="agent_alpha", tag=PXPTag.REVISE, prediction="Option X", explanation="Initial premise.")
    scheduler.next_agent()
    scheduler.submit_entry(e1)

    # Negative tag loop creating deadlock
    tags = [PXPTag.REFUTE, PXPTag.REJECT, PXPTag.REFUTE, PXPTag.REJECT]
    for i, tag in enumerate(tags, start=2):
        agent = scheduler.next_agent()
        entry = BoardEntry(
            agent_id=agent.agent_id,
            tag=tag,
            prediction="Option X" if agent.agent_id == "agent_alpha" else "Option Y",
            explanation=f"Contradicting rationale at turn {i}.",
            target_entry_id=board.get_history()[-1].entry_id,
        )
        deadlock = scheduler.submit_entry(entry)
        if deadlock:
            print(f"\n[!] DEADLOCK DETECTED at turn {i}!")
            print(f"    Loop length: {deadlock.loop_length} negative tags")
            print(f"    Involved agents: {deadlock.involved_agent_ids}")
            print(f"    Status: {scheduler.status.value}")
            break

    # Retrospective Rollback Sandbox Simulation (Student 1 + Student 3)
    print("\n--- Retrospective Rollback Simulation (Student 3 Sandbox) ---")
    print(f"[*] Slicing live history back to cutoff entry: {e1.entry_id[:8]}...")
    history_slice = board.get_history_slice(cutoff_entry_id=e1.entry_id)
    print(f"[*] Isolated history slice count: {len(history_slice)}")

    # Fork sandbox blackboard
    sim_board = board.fork_simulation_blackboard(cutoff_entry_id=e1.entry_id)
    print(f"[*] Spawned sandbox blackboard: {sim_board.task_id}")

    # Explore alternative in sandbox
    sim_entry = BoardEntry(
        agent_id="agent_beta",
        tag=PXPTag.REVISE,
        prediction="Option Z (Compromise)",
        explanation="Re-evaluating constraints suggests Option Z avoids both conflicting assumptions.",
        target_entry_id=e1.entry_id,
        is_counterfactual_sim=True,
    )
    sim_board.post_entry(sim_entry)
    print(f"[*] Simulated alternative posted in sandbox: {sim_entry.prediction}")

    # No counterfactual evaluator is connected, so the demo records no score.
    delta_score = None
    print("[*] Counterfactual delta score: not evaluated (no scorer connected)")

    # Record counterfactual result without corrupting live history
    cf_result = CounterfactualResult(
        agent_id="agent_beta",
        original_entry_id=e1.entry_id,
        simulated_entry=sim_entry,
        delta_score=delta_score,
        applied_to_live=False,
    )
    board.record_counterfactual_result(cf_result)

    print(f"[*] Live board history count: {len(board.get_history())} (preserved)")
    print(f"[*] Sandbox board history count: {len(sim_board.get_history())} (isolated branch)")
    print(f"[*] Live counterfactual audit events: {len(board.get_state().counterfactual_events)}")


if __name__ == "__main__":
    demo_three_agent_consensus()
    demo_deadlock_and_counterfactual_rollback()
    print_banner("INTEGRATION DEMONSTRATION COMPLETE - ALL MODULES WORKING IN HARMONY")
